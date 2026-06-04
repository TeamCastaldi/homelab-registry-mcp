"""Tests for the proposal engine: create, dry-run, reject, verify, cancel.

Git and notification providers are faked so nothing touches the network. The
reasoning layer is faked through the real PatchGenerator.
"""

from conftest import IsolatedSettings
from registry_mcp.models import Proposal, ProposalStatus, Service
from registry_mcp.proposal import PatchGenerator, ProposalEngine, ProposalStore
from registry_mcp.providers.git import OpenedPR

VALID_PATCH = {
    "patch": "services:\n  plex:\n    image: plex\n",
    "commit_message": "fix: attach authentik-auth middleware",
    "pr_title": "Secure plex",
    "pr_body": "Adds authentik-auth@file to the router.",
    "confidence": 0.95,
    "reasoning": "router had no auth middleware",
}


class FakeReasoner:
    def __init__(self, result=VALID_PATCH):
        self.result = result

    def generate_remediation_patch(self, **kwargs):
        return self.result


class FakeGit:
    def __init__(self, files=None):
        self.files = files or {}
        self.branches = []
        self.commits = []
        self.opened = []
        self.closed = []

    async def read_file(self, repo, path, ref):
        return self.files.get(path, "services: {}\n")

    async def create_branch(self, repo, branch, base):
        self.branches.append(branch)

    async def commit_file(self, repo, path, content, branch, message):
        self.commits.append({"path": path, "content": content, "branch": branch})

    async def open_pr(self, repo, title, body, branch, base, label=None):
        number = 100 + len(self.opened)
        self.opened.append({"title": title, "body": body, "branch": branch, "label": label})
        return OpenedPR(url=f"https://git.test/pulls/{number}", number=number)

    async def list_open_prs(self, repo, label=None):
        return []

    async def close_pr(self, repo, number):
        self.closed.append(number)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, title, body, url=None):
        self.sent.append({"title": title, "body": body, "url": url})


def _settings(**overrides):
    base = dict(
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
        apply_mode="ansible",
    )
    base.update(overrides)
    return IsolatedSettings(**base)


_UNSET = object()


def _engine(store, *, settings=None, git=_UNSET, reasoner=None, notifier=None):
    settings = settings or _settings()
    proposals = ProposalStore(store.engine)
    engine = ProposalEngine(
        settings=settings,
        store=store,
        proposals=proposals,
        generator=PatchGenerator(
            reasoner or FakeReasoner(), threshold=settings.proposal_confidence_threshold
        ),
        notifier=notifier or FakeNotifier(),
        git=FakeGit() if git is _UNSET else git,
    )
    return engine, proposals


def _conflicted(store, name="plex", host="workload-01"):
    return store.create_service(
        Service(
            name=name,
            display_name=name.title(),
            host=host,
            traefik_router=f"{name}@docker",
            authentik_app_slug=name,
            auth_mode_conflict=True,
        )
    )


# --- create -----------------------------------------------------------------


async def test_create_opens_pr_and_records_proposal(store):
    service = _conflicted(store)
    notifier = FakeNotifier()
    git = FakeGit()
    engine, proposals = _engine(store, git=git, notifier=notifier)

    result = await engine.create_for_service(service.id)

    assert result["status"] == "open"
    assert result["pr_url"].startswith("https://git.test/pulls/")
    assert git.branches and git.commits and git.opened
    assert git.opened[0]["label"] == "homelab-registry-mcp"
    # Ansible apply footer was appended to the DSPy body.
    assert "Ansible" in git.opened[0]["body"]
    assert len(proposals.list_open()) == 1
    assert notifier.sent[0]["url"] == result["pr_url"]


async def test_create_dry_run_opens_no_pr(store):
    service = _conflicted(store)
    git = FakeGit()
    engine, proposals = _engine(store, settings=_settings(proposal_dry_run=True), git=git)

    result = await engine.create_for_service(service.id)

    assert result["dry_run"] is True
    assert result["patch"].startswith("services:")
    assert git.opened == []  # no PR opened in dry-run
    assert proposals.list_open() == []  # nothing persisted


async def test_low_confidence_records_rejected_proposal(store):
    service = _conflicted(store)
    notifier = FakeNotifier()
    engine, proposals = _engine(
        store, reasoner=FakeReasoner({**VALID_PATCH, "confidence": 0.4}), notifier=notifier
    )

    result = await engine.create_for_service(service.id)

    assert "rejected" in result
    proposal = proposals.get(result["proposal"]["id"])
    assert proposal.status == ProposalStatus.rejected
    assert "manual review" in notifier.sent[0]["title"]


async def test_duplicate_open_proposal_is_skipped(store):
    service = _conflicted(store)
    engine, _ = _engine(store)
    await engine.create_for_service(service.id)
    again = await engine.create_for_service(service.id)
    assert "skipped" in again


async def test_not_configured_returns_error(store):
    service = _conflicted(store)
    engine, _ = _engine(store, git=None)
    result = await engine.create_for_service(service.id)
    assert "error" in result
    assert engine.configured is False


async def test_service_without_finding_returns_error(store):
    service = store.create_service(Service(name="calm", display_name="Calm", host="workload-01"))
    engine, _ = _engine(store)
    result = await engine.create_for_service(service.id)
    assert "no open finding" in result["error"]


async def test_unknown_host_cannot_resolve_file(store):
    service = store.create_service(
        Service(name="ghost", display_name="Ghost", auth_mode_conflict=True)
    )
    engine, _ = _engine(store)
    result = await engine.create_for_service(service.id)
    assert "cannot resolve" in result["error"]


# --- verification + cancel --------------------------------------------------


async def test_sweep_marks_verified_when_conflict_clears(store):
    service = _conflicted(store)
    notifier = FakeNotifier()
    engine, proposals = _engine(store, notifier=notifier)
    await engine.create_for_service(service.id)

    # The next discovery pass clears the conflict.
    store.update_service(service.id, {"auth_mode_conflict": False}, actor="discovery:traefik")
    verified = await engine.sweep_verifications()

    assert len(verified) == 1
    refreshed = proposals.list_all()[0]
    assert refreshed.status == ProposalStatus.verified
    assert refreshed.resolved_at is not None
    assert any("secured" in s["title"] for s in notifier.sent)


async def test_cancel_closes_pr_and_marks_cancelled(store):
    service = _conflicted(store)
    git = FakeGit()
    engine, proposals = _engine(store, git=git)
    created = await engine.create_for_service(service.id)

    result = await engine.cancel(created["id"])

    assert result["status"] == "cancelled"
    assert git.closed == [created["pr_number"]]


async def test_after_discovery_auto_creates_for_each_conflict(store):
    _conflicted(store, name="plex")
    _conflicted(store, name="sonarr")
    git = FakeGit()
    engine, proposals = _engine(store, settings=_settings(proposal_auto_create=True), git=git)

    await engine.after_discovery()

    assert len(proposals.list_open()) == 2
    assert len(git.opened) == 2


async def test_after_discovery_disabled_when_not_configured(store):
    _conflicted(store)
    engine, proposals = _engine(store, settings=_settings(proposal_auto_create=True), git=None)
    await engine.after_discovery()  # no-op, must not raise
    assert proposals.list_open() == []


# --- store ------------------------------------------------------------------


def test_store_find_open_scopes_by_service_and_type(store):
    proposals = ProposalStore(store.engine)
    from registry_mcp.models import FindingType

    proposals.create(Proposal(service_id="svc-1", finding_type=FindingType.auth_mode_conflict))
    assert proposals.find_open("svc-1", FindingType.auth_mode_conflict) is not None
    assert proposals.find_open("svc-2", FindingType.auth_mode_conflict) is None
