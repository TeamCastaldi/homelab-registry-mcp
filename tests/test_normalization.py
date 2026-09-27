"""Tests for the normalization engine: rules, formatter, generator, scanner,
and engine. Git and notification providers are faked so nothing touches the
network — same duck-typed style as ``test_proposal_engine.py``.
"""

import asyncio
import re
import threading
from datetime import datetime

import pytest
from apscheduler.triggers.interval import IntervalTrigger

from conftest import IsolatedSettings
from registry_mcp.models import ProposalStatus
from registry_mcp.normalization.engine import (
    DEFAULT_SCHEDULE,
    NormalizationEngine,
    schedule_trigger,
)
from registry_mcp.normalization.formatter import _comments
from registry_mcp.normalization.formatter import normalize as format_file
from registry_mcp.normalization.generator import NormalizationGenerator
from registry_mcp.normalization.rules import (
    canonical_projection,
    check,
    is_equivalent,
    network_names,
)
from registry_mcp.normalization.scanner import scan
from registry_mcp.proposal.store import ProposalStore
from registry_mcp.providers.git import GitError, OpenedPR
from registry_mcp.registry import RegistryStore

# ---------------------------------------------------------------------------
# engine.py: schedule_trigger
# ---------------------------------------------------------------------------


def _runs(schedule: str, count: int = 4) -> list[str]:
    """The next ``count`` run times of ``schedule`` after a fixed start, as
    ``"Wed 07:00"`` strings."""
    trigger = schedule_trigger(schedule)
    now = datetime(2026, 9, 21, 12, 0, tzinfo=trigger.timezone)  # a Monday, noon
    runs, previous = [], None
    for _ in range(count):
        previous = trigger.get_next_fire_time(previous, now)
        runs.append(previous.strftime("%a %d %H:%M"))
        now = previous
    return runs


def test_default_schedule_runs_wednesday_and_saturday_at_seven():
    assert _runs(DEFAULT_SCHEDULE) == [
        "Wed 23 07:00",
        "Sat 26 07:00",
        "Wed 30 07:00",
        "Sat 03 07:00",
    ]


def test_named_presets_run_at_fixed_times():
    assert _runs("daily", 2) == ["Tue 22 07:00", "Wed 23 07:00"]
    assert _runs("weekly", 2) == ["Sat 26 07:00", "Sat 03 07:00"]
    assert _runs("monthly", 1) == ["Thu 01 07:00"]


def test_a_crontab_is_accepted():
    assert _runs("30 6 * * mon", 1) == ["Mon 28 06:30"]


def test_plain_seconds_is_an_interval():
    trigger = schedule_trigger("120")
    assert isinstance(trigger, IntervalTrigger)
    assert trigger.interval.total_seconds() == 120


def test_an_invalid_schedule_falls_back_to_the_default():
    default = str(schedule_trigger(DEFAULT_SCHEDULE))
    for bad in ("bogus", "0", "-5", "99 99 * * *", "1 2 3"):
        assert str(schedule_trigger(bad)) == default, bad


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


def test_canonical_projection_compares_label_booleans_as_compose_does():
    as_bool = {"services": {"a": {"labels": {"traefik.enable": True}}}}
    as_string = {"services": {"a": {"labels": {"traefik.enable": "true"}}}}
    python_cased = {"services": {"a": {"labels": {"traefik.enable": "True"}}}}
    assert canonical_projection(as_bool) == canonical_projection(as_string)
    assert canonical_projection(as_bool) != canonical_projection(python_cased)


def test_canonical_projection_keeps_long_syntax_ports_as_mappings():
    as_mapping = {"services": {"a": {"ports": [{"target": 80, "published": 8080}]}}}
    as_string = {"services": {"a": {"ports": [str({"target": 80, "published": 8080})]}}}
    assert canonical_projection(as_mapping) != canonical_projection(as_string)


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


_PORTS_DOC = {"services": {"a": {"image": "x:1", "restart": "always", "ports": ["8080:80"]}}}


def test_check_flags_ports_with_no_comment():
    raw = "services:\n  a:\n    ports:\n      - 8080:80\n    # about volumes\n    volumes: []\n"
    findings = check(_PORTS_DOC, raw_text=raw, path="x/compose.yaml")
    assert any(f.rule_id == "R-004" for f in findings)


@pytest.mark.parametrize(
    "raw",
    [
        "services:\n  a:\n    ports:\n      - 8080:80  # temporary\n",
        "services:\n  a:\n    ports:  # LAN access for the MCP client\n      - 8080:80\n",
        "services:\n  a:\n    # published on purpose: DNS\n    ports:\n      - 53:53\n",
        "services:\n  a:\n    ports:\n      # the admin UI\n      - 8080:80\n",
        "services:\n  a:\n    ports:\n    - 8080:80  # indentless list\n",
    ],
)
def test_check_accepts_ports_with_any_comment(raw):
    findings = check(_PORTS_DOC, raw_text=raw, path="x/compose.yaml")
    assert not any(f.rule_id == "R-004" for f in findings)


def _networks(networks, **kwargs):
    doc = {"services": {"a": {"image": "x:1", "restart": "always"}}, "networks": networks}
    return [
        f for f in check(doc, raw_text="", path="x/compose.yaml", **kwargs) if f.rule_id == "R-005"
    ]


def test_check_flags_a_shared_network_not_declared_external():
    findings = _networks({"swarm-net": {"driver": "overlay"}, "proxy-net": None})
    assert [f.detail for f in findings] == [
        "shared network 'swarm-net' is not declared external: true",
        "shared network 'proxy-net' is not declared external: true",
    ]


@pytest.mark.parametrize(
    "networks",
    [
        {"swarm-net": {"external": True}},
        {"swarm-net": {"name": "swarm-net", "external": True}},
        {"proxy": {"name": "${PROXY_NETWORK:-swarm-net}", "external": True}},
        {"app-internal": {"driver": "bridge"}},  # a stack's own network
        {"proxy": {"name": "${PROXY_NETWORK}"}},  # name unknown until deploy
    ],
)
def test_check_accepts_external_shared_and_private_networks(networks):
    assert _networks(networks) == []


def test_check_resolves_an_interpolated_name_to_its_default():
    findings = _networks({"proxy": {"name": "${PROXY_NETWORK:-swarm-net}"}})
    assert [f.detail for f in findings] == ["shared network 'proxy' is not declared external: true"]


def test_check_uses_the_configured_shared_networks():
    networks = {"traefik": {"driver": "bridge"}, "swarm-net": {"driver": "overlay"}}
    findings = _networks(networks, shared_networks=network_names(" traefik , ,"))
    assert [f.detail for f in findings] == [
        "shared network 'traefik' is not declared external: true"
    ]


def test_check_flags_hardcoded_secret():
    raw = "services:\n  a:\n    environment:\n      TOKEN: abcdefghijklmnopqrstuvwx\n"
    doc = {"services": {"a": {"image": "x:1", "restart": "always"}}}
    findings = check(doc, raw_text=raw, path="x/compose.yaml")
    assert any(f.rule_id == "R-006" for f in findings)


def test_check_flags_container_name_mismatch():
    doc = {"services": {"a": {"image": "x:1", "restart": "always", "container_name": "b"}}}
    findings = check(doc, raw_text="", path="x/compose.yaml")
    assert any(f.rule_id == "R-007" for f in findings)


def test_check_reports_a_file_that_isnt_compose():
    for doc in ({"foo": "bar"}, "TBD", None):
        findings = check(doc, raw_text="", path="x.yaml")
        assert [(f.rule_id, f.detail) for f in findings] == [
            ("R-008", "not a compose file: no top-level services: mapping")
        ]


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


def test_formatter_moves_a_comment_with_its_key():
    text = (
        "services:\n  a:\n    restart: unless-stopped\n"
        "    # pin this to a real tag before merging\n    image: x:1\n"
    )
    result = format_file(text)
    assert result.skipped_rules == []
    assert result.content == (
        "services:\n  a:\n    # pin this to a real tag before merging\n    image: x:1\n"
        "    restart: unless-stopped\n"
    )


def test_formatter_keeps_blank_lines_in_place_and_the_file_header_on_top():
    text = (
        "# my stack\n"
        "networks:\n  swarm-net:\n    external: true\n"
        "\n"
        "# the app\n"
        "services:\n  a:\n    image: x:1\n"
    )
    result = format_file(text)
    assert result.content == (
        "# my stack\n"
        "# the app\n"
        "services:\n  a:\n    image: x:1\n"
        "\n"
        "networks:\n  swarm-net:\n    external: true\n"
    )


def test_formatter_sorts_labels_with_their_comments():
    text = (
        "services:\n  a:\n    image: x:1\n    labels:\n"
        '      z.last: "1"\n      # why this router\n      a.first: "2"\n'
        "\nnetworks:\n  n:\n    external: true\n"
    )
    result = format_file(text)
    assert result.skipped_rules == []
    assert (
        '    labels:\n      # why this router\n      a.first: "2"\n      z.last: "1"\n\nnetworks:'
        in result.content
    )


def _squeeze(text: str) -> str:
    """``text`` with the run of spaces before each inline comment cut to one."""
    return re.sub(r"(\S) +#", r"\1 #", text)


def test_formatter_converts_a_commented_label_list():
    text = (
        "services:\n  a:\n    image: x:1\n    labels:\n"
        "      # turn it on\n"
        '      - "traefik.enable=true"  # required\n'
        '      - "b.x=2"\n'
    )
    result = format_file(text)
    assert result.skipped_rules == []
    # ruamel keeps an inline comment at its original column; only the spacing may move.
    assert _squeeze(result.content).endswith(
        '    labels:\n      b.x: "2"\n      # turn it on\n      traefik.enable: "true" # required\n'
    )


def test_formatter_converts_a_commented_environment_list_and_quotes_ambiguous_values():
    text = (
        "services:\n  a:\n    image: x:1\n    environment:\n"
        "      - ALLOW_EMPTY_PASSWORD=yes  # dev only\n"
        "      - PUID=1000\n"
        "      - EMPTY=\n"
        "      - NAME=it's plain\n"
        "      - FROM_HOST\n"
    )
    result = format_file(text)
    assert result.skipped_rules == []
    assert (
        '    environment:\n      ALLOW_EMPTY_PASSWORD: "yes" # dev only\n      PUID: "1000"\n'
        '      EMPTY: ""\n      NAME: it\'s plain\n      FROM_HOST:\n'
    ) in _squeeze(result.content)


def test_formatter_writes_label_booleans_as_compose_sees_them():
    text = "services:\n  a:\n    image: x:1\n    labels:\n      on: true\n      off: false\n"
    result = format_file(text)
    assert '      off: "false"\n      on: "true"\n' in result.content


def test_formatter_leaves_long_syntax_ports_alone():
    text = (
        "services:\n  a:\n    image: x:1\n    ports:\n      - 8080:80\n"
        "      - target: 80\n        published: 8081\n"
    )
    result = format_file(text)
    assert '      - "8080:80"\n      - target: 80\n        published: 8081\n' in result.content


def test_formatter_keeps_anchors_above_their_aliases():
    text = (
        "name: demo\nx-env: &env\n  A: '1'\n"
        "networks:\n  n:\n    external: true\n"
        "services:\n  a:\n    environment: *env\n    image: x:1\n"
    )
    result = format_file(text)
    assert result.skipped_rules == []
    assert result.content == (
        "name: demo\nx-env: &env\n  A: '1'\n"
        "services:\n  a:\n    image: x:1\n    environment: *env\n"
        "networks:\n  n:\n    external: true\n"
    )


def test_formatter_leaves_a_service_with_a_merge_key_in_its_order():
    text = (
        "x-base: &base\n  restart: always\n"
        "services:\n  a:\n    <<: *base\n    labels: {}\n    image: x:1\n"
    )
    result = format_file(text)
    assert result.skipped_rules == []
    assert "    <<: *base\n    labels: {}\n    image: x:1\n" in result.content


def test_formatter_skips_a_commented_version_key():
    text = 'version: "3.8"  # legacy\nservices:\n  a:\n    image: x:1\n'
    result = format_file(text)
    assert result.skipped_rules == ["N-004"]
    assert "# legacy" in result.content


_THREAD_FILES = [
    (
        "networks:\n  n:\n    external: true\n\nservices:\n  a:\n    restart: always\n"
        "    # why\n    image: x:1\n    labels:\n      - b=1  # b\n      - a=2\n"
    ),
    (
        'version: "3"\nservices:\n  b:\n    environment:\n      - A=yes\n      - B=1\n'
        "    ports:\n      - 8080:80\n    image: y:2\n"
    ),
    "name: s\nx-e: &e\n  A: '1'\nservices:\n  c:\n    environment: *e\n    image: z:3\n",
]


def test_formatter_is_safe_to_run_in_several_threads_at_once():
    # Each file is formatted in a worker thread, and sweeps can overlap. A
    # ruamel YAML object shared across threads corrupted its own parser and
    # emitter state, failing most files with errors like "expected NodeEvent,
    # but got DocumentStartEvent".
    expected = [format_file(text).content for text in _THREAD_FILES]
    mismatches, errors = [], []

    def work():
        try:
            for _ in range(20):
                for text, want in zip(_THREAD_FILES, expected, strict=True):
                    result = format_file(text)
                    if result is None or result.content != want:
                        mismatches.append(text)
        except Exception as exc:  # a failure here must fail the test, not vanish
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert mismatches == []


def test_comments_ignores_hashes_inside_values():
    text = (
        'a: "x # not a comment"  # real one\n'
        "b: it's # after an apostrophe\n"
        "c: http://host/#fragment\n"
        "# full line\n"
    )
    assert sorted(_comments(text)) == ["# after an apostrophe", "# full line", "# real one"]


def test_formatter_is_idempotent():
    text = 'version: "3.8"\nservices:\n  a:\n    restart: unless-stopped\n    image: x:1\n'
    first = format_file(text)
    second = format_file(first.content)
    assert second.changed is False


def test_formatter_returns_none_for_non_compose_yaml():
    assert format_file("just: a mapping\n") is None


def test_formatter_returns_none_for_invalid_yaml():
    assert format_file("services: [unclosed\n") is None


def test_formatter_is_idempotent_on_a_commented_file():
    text = (
        'version: "3"\nnetworks:\n  n:\n    external: true\n\nservices:\n  a:\n'
        "    # why\n    restart: always\n    labels:\n      - b=1  # b\n      - a=2\n"
        "    image: x:1\n"
    )
    first = format_file(text)
    assert first.skipped_rules == []
    assert _comments(first.content) == _comments(text)
    assert format_file(first.content).changed is False


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
        self.reads = []
        self.branches = []
        self.commits = []
        self.deletes = []
        self.opened = []
        self.pr_states = {}
        self._truncated = truncated

    async def get_pr_state(self, repo, number):
        return self.pr_states.get(number, "open")

    async def list_files(self, repo, ref):
        if self._truncated:
            raise GitError("truncated")
        return list(self.files.keys())

    async def read_file(self, repo, path, ref):
        self.reads.append(path)
        return self.files[path]

    async def create_branch(self, repo, branch, base):
        # Gitea (409) and GitHub (422) both refuse a branch that already exists.
        if branch in self.branches:
            raise GitError(f"branch {branch!r} already exists")
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


async def test_scan_reads_only_the_requested_nodes_files():
    git = FakeGit(
        files={
            "nodes/pi/plex/compose.yaml": "services:\n  plex:\n    image: x:1\n",
            "nodes/waldorf/sonarr/compose.yaml": "services:\n  sonarr:\n    image: x:1\n",
        }
    )
    grouped = await scan(git, "o/r", "main", path_glob="nodes/*/*/compose.yaml", node="pi")
    assert list(grouped) == ["pi"]
    assert git.reads == ["nodes/pi/plex/compose.yaml"]


async def test_scan_reports_files_that_arent_compose():
    git = FakeGit(
        files={
            "nodes/pi/mealie-mcp/compose.yaml": "TBD\n",
            "nodes/pi/broken/compose.yaml": "services:\n  a: [unclosed\n",
        }
    )
    grouped = await scan(git, "o/r", "main", path_glob="nodes/*/*/compose.yaml")
    details = {r.path: [(f.rule_id, f.detail) for f in r.findings] for r in grouped["pi"]}
    assert details == {
        "nodes/pi/mealie-mcp/compose.yaml": [
            ("R-008", "not a compose file: no top-level services: mapping")
        ],
        "nodes/pi/broken/compose.yaml": [
            ("R-008", "not a compose file: doesn't parse as YAML (line 3)")
        ],
    }


async def test_scan_checks_the_configured_shared_networks():
    git = FakeGit(
        files={
            "nodes/pi/a/compose.yaml": (
                "services:\n  a:\n    image: x:1\n    restart: always\n"
                "networks:\n  traefik:\n    driver: bridge\n"
            )
        }
    )
    grouped = await scan(
        git, "o/r", "main", path_glob="nodes/*/*/compose.yaml", shared_networks=("traefik",)
    )
    assert [f.rule_id for f in grouped["pi"][0].findings] == ["R-005"]


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


async def test_run_sweep_opens_prs_under_the_normalization_label_not_the_security_one():
    """NZ1: normalization and security proposals must never be bundled under
    the same label (CLAUDE.md's "never bundled" rule) — nothing else in this
    file checks which label actually reaches `open_pr`, so a PR opened under
    `PROPOSAL_LABEL` by mistake would pass every other test here."""
    settings = IsolatedSettings(
        registry_db_path=":memory:",
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
        normalization_label="normalization",
        proposal_label="homelab-registry-mcp",
    )
    engine, proposals, git = _engine(
        {"nodes/pi/plex/compose.yaml": _compose("plex")}, settings=settings
    )
    await engine.run_sweep(actor="manual:proposal_normalize")
    assert git.opened[-1]["label"] == "normalization"
    assert git.opened[-1]["label"] != settings.proposal_label


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


async def test_run_sweep_reopens_node_once_its_pr_is_merged():
    """A merged normalization PR used to stay `open` forever, so every later
    sweep for that node was skipped as a duplicate."""
    files = {"nodes/pi/plex/compose.yaml": _compose("plex")}
    engine, proposals, git = _engine(files)
    first = await engine.run_sweep()
    first_pr = first["items"][0]["pr_number"]

    git.pr_states[first_pr] = "merged"
    git.files["nodes/pi/plex/compose.yaml"] = _compose("plex")  # drifted again
    second = await engine.run_sweep()

    assert "pr_number" in second["items"][0]
    assert len(git.opened) == 2
    retired = next(p for p in proposals.list_all() if p.pr_number == first_pr)
    assert retired.status == ProposalStatus.merged


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


class BlockingGit(FakeGit):
    """A FakeGit whose listing waits until the test lets it go, so a sweep can
    be held open."""

    def __init__(self, files):
        super().__init__(files)
        self.listing = asyncio.Event()
        self.release = asyncio.Event()

    async def list_files(self, repo, ref):
        self.listing.set()
        await self.release.wait()
        return await super().list_files(repo, ref)


async def test_a_second_sweep_is_refused_while_one_is_running():
    git = BlockingGit({"nodes/pi/plex/compose.yaml": _compose("plex")})
    engine, proposals, _ = _engine({}, git=git)

    first = asyncio.create_task(engine.run_sweep())
    await git.listing.wait()
    try:
        # Without the single-sweep rule this would wait on the held listing.
        second = await asyncio.wait_for(engine.run_sweep(dry_run=True), timeout=5)
    finally:
        git.release.set()
    first_result = await first

    assert second == {
        "error": "a normalization sweep is already running; try again when it finishes"
    }
    assert "pr_number" in first_result["items"][0]
    assert len(git.opened) == 1
    # Once it has finished, the next sweep runs.
    assert "items" in await engine.run_sweep(dry_run=True)


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
    assert git.reads == ["nodes/pi/plex/compose.yaml"]


class FailingReasoner:
    def normalize_config(self, **kwargs):
        raise AssertionError("a file that isn't compose must never reach DSPy")


async def test_run_sweep_never_escalates_a_file_that_isnt_compose():
    files = {"nodes/ollama/mealie-mcp/compose.yaml": "TBD\n"}
    gen = NormalizationGenerator(FailingReasoner(), threshold=0.8)
    engine, proposals, git = _engine(files, generator=gen)
    result = await engine.run_sweep()
    item = result["items"][0]
    assert item["skipped"] == "no changes needed"
    assert [f["rule_id"] for f in item["findings"]] == ["R-008"]
    assert git.branches == []


_PLEX_NORMALIZED = {
    "normalized_file": "services:\n  plex:\n    image: x:1\n    restart: unless-stopped\n",
    "commit_message": "style: normalize",
    "confidence": 0.9,
    "reasoning": "reordered image before restart",
}


async def test_run_sweep_escalates_to_dspy_when_formatter_skips_rules():
    files = {
        "nodes/pi/plex/compose.yaml": (
            'version: "3"  # legacy\nservices:\n  plex:\n    restart: unless-stopped\n'
            "    image: x:1\n"
        )
    }
    gen = NormalizationGenerator(FakeReasoner(_PLEX_NORMALIZED), threshold=0.8)
    engine, proposals, git = _engine(files, generator=gen)
    result = await engine.run_sweep()
    assert "pr_number" in result["items"][0]
    assert git.commits[-1]["content"] == _PLEX_NORMALIZED["normalized_file"]


async def test_run_sweep_keeps_deterministic_partial_when_dspy_rejects():
    # Two things wrong: a key order the formatter can always fix, and an
    # obsolete `version:` key it must leave because removing it would drop
    # the comment on that line. When DSPy also fails to finish the job, the
    # reorder it already made should still be committed rather than
    # discarding the whole file's progress.
    files = {
        "nodes/pi/plex/compose.yaml": (
            'version: "3"  # legacy\nservices:\n  plex:\n    restart: unless-stopped\n'
            "    image: x:1\n"
        )
    }
    gen = NormalizationGenerator(
        FakeReasoner({**_PLEX_NORMALIZED, "confidence": 0.1}), threshold=0.8
    )
    engine, proposals, git = _engine(files, generator=gen)
    result = await engine.run_sweep()
    assert "pr_number" in result["items"][0]
    committed = git.commits[-1]["content"]
    assert committed.index("image:") < committed.index("restart:")
    assert 'version: "3"  # legacy' in committed


async def test_run_sweep_escalation_runs_off_the_event_loop():
    from conftest import BlockingCall

    files = {
        "nodes/pi/plex/compose.yaml": (
            'version: "3"  # legacy\nservices:\n  plex:\n    restart: unless-stopped\n'
            "    image: x:1\n"
        )
    }
    reasoner = FakeReasoner(_PLEX_NORMALIZED)
    reasoner.normalize_config = BlockingCall(_PLEX_NORMALIZED)
    engine, _, git = _engine(files, generator=NormalizationGenerator(reasoner, threshold=0.8))

    result = await reasoner.normalize_config.assert_off_loop(engine.run_sweep())

    assert "pr_number" in result["items"][0]
