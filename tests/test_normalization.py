"""Tests for the normalization engine: rules, formatter, generator, scanner,
and engine. Git and notification providers are faked so nothing touches the
network — same duck-typed style as ``test_proposal_engine.py``.
"""

from conftest import IsolatedSettings
from registry_mcp.normalization.engine import NormalizationEngine
from registry_mcp.normalization.formatter import normalize as format_file
from registry_mcp.normalization.generator import NormalizationGenerator
from registry_mcp.normalization.rules import canonical_projection, check, is_equivalent
from registry_mcp.normalization.scanner import scan
from registry_mcp.proposal.store import ProposalStore
from registry_mcp.providers.git import GitError, OpenedPR
from registry_mcp.registry import RegistryStore

# ---------------------------------------------------------------------------
# rules.py
# ---------------------------------------------------------------------------


def test_canonical_projection_treats_label_list_and_mapping_as_equal():
    as_list = {"services": {"a": {"labels": ["k=v"]}}}
    as_mapping = {"services": {"a": {"labels": {"k": "v"}}}}
    assert canonical_projection(as_list) == canonical_projection(as_mapping)


def test_canonical_projection_treats_port_int_and_string_as_equal():
    as_int = {"services": {"a": {"ports": [8080]}}}
    as_str = {"services": {"a": {"ports": ["8080"]}}}
    assert canonical_projection(as_int) == canonical_projection(as_str)


def test_canonical_projection_drops_version_key():
    with_version = {"version": "3.8", "services": {"a": {}}}
    without_version = {"services": {"a": {}}}
    assert canonical_projection(with_version) == canonical_projection(without_version)


def test_canonical_projection_detects_a_real_value_change():
    before = {"services": {"a": {"image": "x:1"}}}
    after = {"services": {"a": {"image": "x:2"}}}
    assert canonical_projection(before) != canonical_projection(after)


def test_is_equivalent_true_for_reformatted_text():
    before = "services:\n  a:\n    ports:\n      - 8080\n"
    after = 'services:\n  a:\n    ports:\n      - "8080"\n'
    assert is_equivalent(before, after)


def test_is_equivalent_false_when_a_value_changes():
    before = "services:\n  a:\n    image: x:1\n"
    after = "services:\n  a:\n    image: x:2\n"
    assert not is_equivalent(before, after)


def test_is_equivalent_false_for_invalid_yaml():
    assert not is_equivalent("a: 1\n", "a: [unclosed\n")


def test_check_flags_latest_tag():
    doc = {"services": {"plex": {"image": "plexinc/pms-docker:latest", "restart": "always"}}}
    findings = check(doc, raw_text="", path="x/compose.yaml")
    assert any(f.rule_id == "R-001" for f in findings)


def test_check_flags_missing_tag():
    doc = {"services": {"plex": {"image": "plexinc/pms-docker", "restart": "always"}}}
    findings = check(doc, raw_text="", path="x/compose.yaml")
    assert any(f.rule_id == "R-001" for f in findings)


def test_check_accepts_pinned_tag():
    doc = {"services": {"plex": {"image": "plexinc/pms-docker:1.2.3", "restart": "always"}}}
    findings = check(doc, raw_text="", path="x/compose.yaml")
    assert not any(f.rule_id == "R-001" for f in findings)


def test_check_flags_build_key():
    doc = {"services": {"a": {"image": "x:1", "restart": "always", "build": "."}}}
    findings = check(doc, raw_text="", path="x/compose.yaml")
    assert any(f.rule_id == "R-002" for f in findings)


def test_check_flags_missing_restart():
    doc = {"services": {"a": {"image": "x:1"}}}
    findings = check(doc, raw_text="", path="x/compose.yaml")
    assert any(f.rule_id == "R-003" for f in findings)


def test_check_flags_unflagged_ports():
    raw = "services:\n  a:\n    ports:\n      - 8080:80\n"
    doc = {"services": {"a": {"image": "x:1", "restart": "always", "ports": ["8080:80"]}}}
    findings = check(doc, raw_text=raw, path="x/compose.yaml")
    assert any(f.rule_id == "R-004" for f in findings)


def test_check_accepts_ports_with_temporary_comment():
    raw = "services:\n  a:\n    ports:\n      - 8080:80  # temporary\n"
    doc = {"services": {"a": {"image": "x:1", "restart": "always", "ports": ["8080:80"]}}}
    findings = check(doc, raw_text=raw, path="x/compose.yaml")
    assert not any(f.rule_id == "R-004" for f in findings)


def test_check_flags_hardcoded_proxy_network():
    doc = {
        "services": {"a": {"image": "x:1", "restart": "always"}},
        "networks": {"traefik": {"external": True}},
    }
    findings = check(doc, raw_text="", path="x/compose.yaml")
    assert any(f.rule_id == "R-005" for f in findings)


def test_check_accepts_interpolated_proxy_network():
    doc = {
        "services": {"a": {"image": "x:1", "restart": "always"}},
        "networks": {"${PROXY_NETWORK:-proxy-net}": {"external": True}},
    }
    findings = check(doc, raw_text="", path="x/compose.yaml")
    assert not any(f.rule_id == "R-005" for f in findings)


def test_check_flags_hardcoded_secret():
    raw = "services:\n  a:\n    environment:\n      TOKEN: abcdefghijklmnopqrstuvwx\n"
    doc = {"services": {"a": {"image": "x:1", "restart": "always"}}}
    findings = check(doc, raw_text=raw, path="x/compose.yaml")
    assert any(f.rule_id == "R-006" for f in findings)


def test_check_flags_container_name_mismatch():
    doc = {"services": {"a": {"image": "x:1", "restart": "always", "container_name": "b"}}}
    findings = check(doc, raw_text="", path="x/compose.yaml")
    assert any(f.rule_id == "R-007" for f in findings)


def test_check_returns_nothing_for_non_compose_yaml():
    assert check({"foo": "bar"}, raw_text="", path="x.yaml") == []
    assert check("not a dict", raw_text="", path="x.yaml") == []


# ---------------------------------------------------------------------------
# formatter.py
# ---------------------------------------------------------------------------


def test_formatter_reorders_top_level_and_service_keys():
    text = "networks:\n  proxy: {}\nservices:\n  a:\n    restart: unless-stopped\n    image: x:1\n"
    result = format_file(text)
    assert result is not None
    assert result.changed
    assert result.content.index("services:") < result.content.index("networks:")
    assert result.content.index("image:") < result.content.index("restart:")


def test_formatter_removes_version_key():
    text = 'version: "3.8"\nservices:\n  a:\n    image: x:1\n'
    result = format_file(text)
    assert result is not None
    assert "version" not in result.content


def test_formatter_converts_labels_list_to_sorted_mapping():
    text = "services:\n  a:\n    image: x:1\n    labels:\n      - b=2\n      - a=1\n"
    result = format_file(text)
    assert result is not None
    idx_a = result.content.index("a: ")
    idx_b = result.content.index("b: ")
    assert idx_a < idx_b


def test_formatter_quotes_ports_as_strings():
    text = "services:\n  a:\n    image: x:1\n    ports:\n      - 8080\n"
    result = format_file(text)
    assert result is not None
    # ruamel is free to pick either quote style for a numeric-looking
    # string; what matters is that it's no longer a bare, re-parseable int.
    assert "'8080'" in result.content or '"8080"' in result.content
    assert "- 8080\n" not in result.content


def test_formatter_converts_environment_list_to_mapping():
    text = "services:\n  a:\n    image: x:1\n    environment:\n      - PUID=1000\n"
    result = format_file(text)
    assert result is not None
    assert "PUID:" in result.content
    assert "PUID=1000" not in result.content


def test_formatter_preserves_temporary_comment_on_ports():
    text = "services:\n  a:\n    image: x:1\n    ports:\n      - 8080:80  # temporary\n"
    result = format_file(text)
    assert result is not None
    assert "# temporary" in result.content


def test_formatter_skips_unsafe_reorder_when_comment_present():
    text = (
        "services:\n  a:\n    restart: unless-stopped\n"
        "    # pin this to a real tag before merging\n    image: x:1\n"
    )
    result = format_file(text)
    assert result is not None
    assert "N-006" in result.skipped_rules
    assert "# pin this to a real tag before merging" in result.content


def test_formatter_is_idempotent():
    text = 'version: "3.8"\nservices:\n  a:\n    restart: unless-stopped\n    image: x:1\n'
    first = format_file(text)
    second = format_file(first.content)
    assert second.changed is False


def test_formatter_returns_none_for_non_compose_yaml():
    assert format_file("just: a mapping\n") is None


def test_formatter_returns_none_for_invalid_yaml():
    assert format_file("services: [unclosed\n") is None


# ---------------------------------------------------------------------------
# generator.py (DSPy escalation)
# ---------------------------------------------------------------------------

VALID_NORMALIZATION = {
    "normalized_file": "services:\n  a:\n    image: x:1\n",
    "commit_message": "style: normalize",
    "confidence": 0.9,
    "reasoning": "reordered keys",
}


class FakeReasoner:
    def __init__(self, result=VALID_NORMALIZATION):
        self.result = result

    def normalize_config(self, **kwargs):
        return self.result


async def test_generator_escalate_success():
    gen = NormalizationGenerator(FakeReasoner(), threshold=0.8)
    result = gen.escalate(
        file_path="x/compose.yaml",
        current_file="services:\n  a:\n    image: x:1\n",
        violations=["N-006"],
    )
    assert result.ok
    assert result.content == VALID_NORMALIZATION["normalized_file"]


async def test_generator_escalate_rejects_low_confidence():
    gen = NormalizationGenerator(
        FakeReasoner({**VALID_NORMALIZATION, "confidence": 0.4}), threshold=0.8
    )
    result = gen.escalate(
        file_path="x/compose.yaml", current_file="services:\n  a: {}\n", violations=["N-006"]
    )
    assert not result.ok
    assert "confidence" in result.rejection_reason


async def test_generator_escalate_rejects_invalid_yaml():
    gen = NormalizationGenerator(
        FakeReasoner({**VALID_NORMALIZATION, "normalized_file": "a: [unclosed"}), threshold=0.8
    )
    result = gen.escalate(
        file_path="x/compose.yaml", current_file="services:\n  a: {}\n", violations=["N-006"]
    )
    assert not result.ok
    assert "not valid YAML" in result.rejection_reason


async def test_generator_escalate_rejects_non_equivalent_output():
    gen = NormalizationGenerator(
        FakeReasoner(
            {**VALID_NORMALIZATION, "normalized_file": "services:\n  a:\n    image: x:2\n"}
        ),
        threshold=0.8,
    )
    result = gen.escalate(
        file_path="x/compose.yaml",
        current_file="services:\n  a:\n    image: x:1\n",
        violations=["N-006"],
    )
    assert not result.ok
    assert "not behavior-equivalent" in result.rejection_reason


async def test_generator_escalate_reasoner_disabled_returns_none():
    class DisabledReasoner:
        def normalize_config(self, **kwargs):
            return None

    gen = NormalizationGenerator(DisabledReasoner(), threshold=0.8)
    result = gen.escalate(file_path="x", current_file="a: 1\n", violations=[])
    assert not result.ok
    assert "reasoning layer unavailable" in result.rejection_reason


# ---------------------------------------------------------------------------
# scanner.py
# ---------------------------------------------------------------------------


class FakeGit:
    def __init__(self, files=None, truncated=False):
        self.files = dict(files or {})
        self.branches = []
        self.commits = []
        self.deletes = []
        self.opened = []
        self._truncated = truncated

    async def list_files(self, repo, ref):
        if self._truncated:
            raise GitError("truncated")
        return list(self.files.keys())

    async def read_file(self, repo, path, ref):
        return self.files[path]

    async def create_branch(self, repo, branch, base):
        self.branches.append(branch)

    async def commit_file(self, repo, path, content, branch, message):
        self.commits.append({"path": path, "branch": branch, "content": content})
        self.files[path] = content

    async def delete_file(self, repo, path, branch, message):
        self.deletes.append({"path": path, "branch": branch})
        self.files.pop(path, None)

    async def open_pr(self, repo, title, body, branch, base, label=None):
        number = 100 + len(self.opened)
        self.opened.append({"title": title, "branch": branch, "label": label, "body": body})
        return OpenedPR(url=f"https://git.test/pulls/{number}", number=number)


async def test_scan_groups_files_by_node():
    git = FakeGit(
        files={
            "nodes/pi/plex/compose.yaml": "services:\n  plex:\n    image: x:1\n",
            "nodes/waldorf/sonarr/compose.yaml": "services:\n  sonarr:\n    image: x:1\n",
            "README.md": "not a compose file",
        }
    )
    grouped = await scan(git, "nathan/homelab", "main", path_glob="nodes/*/*/compose.yaml")
    assert set(grouped.keys()) == {"pi", "waldorf"}
    assert grouped["pi"][0].path == "nodes/pi/plex/compose.yaml"


async def test_scan_ignores_deeper_paths_out_of_scope():
    git = FakeGit(files={"nodes/pi/traefik/dynamic/middleware.yml": "http:\n  routers: {}\n"})
    grouped = await scan(git, "nathan/homelab", "main", path_glob="nodes/*/*/compose.yaml")
    assert grouped == {}


async def test_scan_includes_misnamed_compose_files():
    git = FakeGit(files={"nodes/pi/sonarr/docker-compose.yml": "services:\n  sonarr: {}\n"})
    grouped = await scan(git, "nathan/homelab", "main", path_glob="nodes/*/*/compose.yaml")
    assert grouped["pi"][0].misnamed is True


async def test_scan_collects_tier2_findings():
    git = FakeGit(files={"nodes/pi/plex/compose.yaml": "services:\n  plex:\n    image: x:latest\n"})
    grouped = await scan(git, "nathan/homelab", "main", path_glob="nodes/*/*/compose.yaml")
    assert any(f.rule_id == "R-001" for f in grouped["pi"][0].findings)


# ---------------------------------------------------------------------------
# engine.py
# ---------------------------------------------------------------------------


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, title, body, url=None, diff=None):
        self.sent.append({"title": title, "body": body, "url": url})


class NoOpReasoner:
    def normalize_config(self, **kwargs):
        return None


def _compose(name: str) -> str:
    """A minimal compose file with a `version:` key to strip — a small, real
    Tier 1 fix for the engine to make."""
    return f'version: "3"\nservices:\n  {name}:\n    restart: unless-stopped\n    image: x:1\n'


def _engine(files, *, settings=None, git=None, generator=None):
    settings = settings or IsolatedSettings(
        registry_db_path=":memory:",
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
    )
    store = RegistryStore(settings.registry_db_path)
    proposals = ProposalStore(store.engine)
    fake_git = git if git is not None else FakeGit(files)
    gen = generator or NormalizationGenerator(NoOpReasoner(), threshold=0.8)
    engine = NormalizationEngine(
        settings=settings, proposals=proposals, generator=gen, notifier=FakeNotifier(), git=fake_git
    )
    return engine, proposals, fake_git


async def test_run_sweep_opens_one_pr_per_node():
    files = {
        "nodes/pi/plex/compose.yaml": _compose("plex"),
        "nodes/waldorf/sonarr/compose.yaml": _compose("sonarr"),
    }
    engine, proposals, git = _engine(files)
    result = await engine.run_sweep(actor="manual:proposal_normalize")
    assert len(result["items"]) == 2
    assert len(git.branches) == 2
    assert len(git.opened) == 2
    assert any(b.startswith("normalize/pi-") for b in git.branches)
    assert any(b.startswith("normalize/waldorf-") for b in git.branches)


async def test_run_sweep_skips_already_canonical_file():
    files = {"nodes/pi/plex/compose.yaml": "services:\n  plex:\n    image: x:1\n"}
    engine, proposals, git = _engine(files)
    result = await engine.run_sweep()
    assert result["items"][0]["skipped"] == "no changes needed"
    assert git.branches == []


async def test_run_sweep_dedupes_against_open_proposal():
    files = {"nodes/pi/plex/compose.yaml": _compose("plex")}
    engine, proposals, git = _engine(files)
    first = await engine.run_sweep()
    assert "pr_number" in first["items"][0]

    second = await engine.run_sweep()
    assert "skipped" in second["items"][0]
    assert len(git.branches) == 1  # no second branch/PR


async def test_run_sweep_dry_run_makes_no_git_writes():
    files = {"nodes/pi/plex/compose.yaml": _compose("plex")}
    engine, proposals, git = _engine(files)
    result = await engine.run_sweep(dry_run=True)
    assert result["items"][0]["dry_run"] is True
    assert git.branches == []
    assert git.commits == []
    assert git.opened == []


async def test_run_sweep_rename_commits_new_and_deletes_old():
    files = {"nodes/pi/sonarr/docker-compose.yml": "services:\n  sonarr:\n    image: x:1\n"}
    settings = IsolatedSettings(
        registry_db_path=":memory:",
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
        normalization_rename_misnamed=True,
    )
    engine, proposals, git = _engine(files, settings=settings)
    await engine.run_sweep()
    assert git.commits[-1]["path"] == "nodes/pi/sonarr/compose.yaml"
    assert git.deletes[-1]["path"] == "nodes/pi/sonarr/docker-compose.yml"


async def test_run_sweep_reports_misnamed_when_rename_disabled():
    files = {"nodes/pi/sonarr/docker-compose.yml": "services:\n  sonarr:\n    image: x:1\n"}
    engine, proposals, git = _engine(files)  # normalization_rename_misnamed defaults False
    result = await engine.run_sweep()
    # The file is already canonically formatted, so nothing to fix except the
    # rename itself — which is disabled, so the node has no PR to open but
    # the N-100 notice still surfaces in both the per-node and top-level
    # findings lists.
    assert result["items"][0]["skipped"] == "no changes needed"
    assert any(f["rule_id"] == "N-100" for f in result["items"][0]["findings"])
    assert any(f["rule_id"] == "N-100" for f in result["findings"])


async def test_run_sweep_caps_files_per_pr():
    files = {f"nodes/pi/svc{i}/compose.yaml": _compose(f"svc{i}") for i in range(3)}
    settings = IsolatedSettings(
        registry_db_path=":memory:",
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
        normalization_max_files_per_pr=2,
    )
    engine, proposals, git = _engine(files, settings=settings)
    await engine.run_sweep()
    assert len(git.commits) == 2


async def test_run_sweep_not_configured_returns_error():
    settings = IsolatedSettings(registry_db_path=":memory:")
    engine, proposals, git = _engine({}, settings=settings, git=None)
    result = await engine.run_sweep()
    assert "error" in result
    assert engine.configured is False


async def test_run_sweep_scoped_to_single_node():
    files = {
        "nodes/pi/plex/compose.yaml": _compose("plex"),
        "nodes/waldorf/sonarr/compose.yaml": _compose("sonarr"),
    }
    engine, proposals, git = _engine(files)
    result = await engine.run_sweep(node="pi")
    assert len(result["items"]) == 1
    assert result["items"][0]["node"] == "pi"


_PLEX_NORMALIZED = {
    "normalized_file": "services:\n  plex:\n    image: x:1\n    restart: unless-stopped\n",
    "commit_message": "style: normalize",
    "confidence": 0.9,
    "reasoning": "reordered image before restart",
}


async def test_run_sweep_escalates_to_dspy_when_formatter_skips_rules():
    files = {
        "nodes/pi/plex/compose.yaml": (
            "services:\n  plex:\n    restart: unless-stopped\n"
            "    # pin this before merging\n    image: x:1\n"
        )
    }
    gen = NormalizationGenerator(FakeReasoner(_PLEX_NORMALIZED), threshold=0.8)
    engine, proposals, git = _engine(files, generator=gen)
    result = await engine.run_sweep()
    assert "pr_number" in result["items"][0]
    assert git.commits[-1]["content"] == _PLEX_NORMALIZED["normalized_file"]


async def test_run_sweep_keeps_deterministic_partial_when_dspy_rejects():
    # Two things wrong: an obsolete `version:` key (the formatter can always
    # remove this safely) and a key order the formatter must skip because of
    # the comment in the way. When DSPy also fails to finish the job, the
    # version removal it already made should still be committed rather than
    # discarding the whole file's progress.
    files = {
        "nodes/pi/plex/compose.yaml": (
            'version: "3"\nservices:\n  plex:\n    restart: unless-stopped\n'
            "    # pin this before merging\n    image: x:1\n"
        )
    }
    gen = NormalizationGenerator(
        FakeReasoner({**_PLEX_NORMALIZED, "confidence": 0.1}), threshold=0.8
    )
    engine, proposals, git = _engine(files, generator=gen)
    result = await engine.run_sweep()
    assert "pr_number" in result["items"][0]
    assert "version" not in git.commits[-1]["content"]
    assert "# pin this before merging" in git.commits[-1]["content"]
