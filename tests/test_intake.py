"""Tests for repo intake (conversational deploy Phase 1).

Nothing here clones over the network: `check_repo_url` and `collect_from_dir`
are exercised directly, and `fetch_repo`'s orchestration is tested with the
clone faked at its module boundary.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from conftest import IsolatedSettings
from registry_mcp.intake import (
    IntakeError,
    RepoSnapshot,
    check_repo_url,
    collect_from_dir,
    fetch_repo,
    parse_compose,
    parse_dockerfile,
    parse_snapshot,
)
from registry_mcp.intake import fetch as fetch_mod
from registry_mcp.tools import intake as intake_tools_mod
from registry_mcp.tools.intake import register_intake_tools


class TestCheckRepoUrl:
    def test_accepts_https(self):
        url = "https://github.com/owner/project.git"
        assert check_repo_url(url) == url

    def test_accepts_self_hosted_private_host(self):
        # A homelab's own Gitea is a legitimate intake target; the scheme is
        # the boundary, not the address.
        url = "https://gitea.lan/owner/project"
        assert check_repo_url(url) == url

    def test_strips_surrounding_whitespace(self):
        assert check_repo_url("  https://github.com/o/p  ") == "https://github.com/o/p"

    @pytest.mark.parametrize("url", ["", "   ", None])
    def test_rejects_empty(self, url):
        with pytest.raises(IntakeError, match="required"):
            check_repo_url(url)

    def test_rejects_leading_dash(self):
        # git would read this as a flag rather than a URL.
        with pytest.raises(IntakeError, match="may not start with"):
            check_repo_url("--upload-pack=touch /tmp/pwned")

    def test_rejects_ext_transport(self):
        # `ext::` runs an arbitrary command — the sharpest edge on git clone.
        with pytest.raises(IntakeError, match="Unsupported URL scheme"):
            check_repo_url("ext::sh -c 'id > /tmp/pwned'")

    def test_rejects_file_scheme(self):
        with pytest.raises(IntakeError, match="Unsupported URL scheme"):
            check_repo_url("file:///etc/passwd")

    def test_rejects_ssh_scheme(self):
        # Would spend this node's control-plane SSH key on a foreign host.
        with pytest.raises(IntakeError, match="Unsupported URL scheme"):
            check_repo_url("ssh://git@evil.example.com/repo.git")

    def test_rejects_plain_http(self):
        with pytest.raises(IntakeError, match="Unsupported URL scheme"):
            check_repo_url("http://github.com/owner/project")

    def test_rejects_scp_style_without_scheme(self):
        # `git@host:path` resolves over SSH despite looking scheme-less.
        with pytest.raises(IntakeError, match="must specify a scheme"):
            check_repo_url("git@github.com:owner/project.git")

    def test_rejects_url_without_host(self):
        with pytest.raises(IntakeError, match="must include a host"):
            check_repo_url("https:///owner/project")


class TestCollectFromDir:
    def test_reads_all_three_files(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
        (tmp_path / "compose.yaml").write_text("services:\n  app:\n    image: x\n")
        (tmp_path / "README.md").write_text("# Project\n")

        snap = collect_from_dir(tmp_path, repo_url="https://example.com/o/p")

        assert snap.dockerfile == "FROM python:3.12\n"
        assert snap.compose_path == "compose.yaml"
        assert snap.readme == "# Project\n"
        assert snap.repo_url == "https://example.com/o/p"
        assert snap.skipped == []
        assert not snap.is_empty

    def test_empty_repo_reports_empty(self, tmp_path):
        snap = collect_from_dir(tmp_path)
        assert snap.is_empty
        assert snap.dockerfile is None
        assert snap.compose is None

    def test_prefers_canonical_compose_name(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text("services: {legacy: {}}\n")
        (tmp_path / "compose.yaml").write_text("services: {canonical: {}}\n")

        snap = collect_from_dir(tmp_path)

        assert snap.compose_path == "compose.yaml"
        assert "canonical" in snap.compose

    def test_falls_back_to_legacy_compose_name(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text("services: {legacy: {}}\n")
        snap = collect_from_dir(tmp_path)
        assert snap.compose_path == "docker-compose.yml"

    def test_refuses_symlink_escape(self, tmp_path):
        # A cloned repo is untrusted content and git stores symlinks happily;
        # following one would hand this node's files back to the caller.
        secret = tmp_path / "host-secret.txt"
        secret.write_text("PRIVATE KEY MATERIAL")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").symlink_to(secret)

        snap = collect_from_dir(repo)

        assert snap.readme is None
        # Refused, not silently absent — the caller can tell the difference.
        assert any("README.md" in entry and "symlink" in entry for entry in snap.skipped)

    def test_skips_oversized_file_with_reason(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetch_mod, "_MAX_FILE_BYTES", 16)
        (tmp_path / "README.md").write_text("x" * 64)

        snap = collect_from_dir(tmp_path)

        assert snap.readme is None
        assert any("README.md" in entry and "cap" in entry for entry in snap.skipped)

    def test_directory_named_like_a_file_is_not_read(self, tmp_path):
        (tmp_path / "Dockerfile").mkdir()
        snap = collect_from_dir(tmp_path)
        assert snap.dockerfile is None


class TestFetchRepo:
    async def test_validates_url_before_cloning(self, tmp_path):
        with (
            patch.object(fetch_mod, "_clone", new=AsyncMock()) as clone,
            pytest.raises(IntakeError, match="Unsupported URL scheme"),
        ):
            await fetch_repo("file:///etc/passwd", timeout_seconds=5, max_repo_mb=10)
        clone.assert_not_awaited()

    async def test_reads_snapshot_and_cleans_up_clone(self):
        created: list[Path] = []

        async def fake_clone(url: str, dest: Path, *, timeout_seconds: int) -> None:
            dest.mkdir(parents=True)
            (dest / "Dockerfile").write_text("FROM alpine\nEXPOSE 8080\n")
            created.append(dest)

        with patch.object(fetch_mod, "_clone", new=fake_clone):
            snap = await fetch_repo("https://example.com/o/p", timeout_seconds=5, max_repo_mb=100)

        assert snap.dockerfile == "FROM alpine\nEXPOSE 8080\n"
        assert snap.repo_url == "https://example.com/o/p"
        # The temp clone must not outlive the call.
        assert created and not created[0].exists()

    async def test_rejects_oversized_repo_and_still_cleans_up(self):
        created: list[Path] = []

        async def fake_clone(url: str, dest: Path, *, timeout_seconds: int) -> None:
            dest.mkdir(parents=True)
            (dest / "blob.bin").write_bytes(b"0" * (2 * 1024 * 1024))
            created.append(dest)

        with (
            patch.object(fetch_mod, "_clone", new=fake_clone),
            pytest.raises(IntakeError, match="over the 1 MB intake cap"),
        ):
            await fetch_repo("https://example.com/o/p", timeout_seconds=5, max_repo_mb=1)

        assert created and not created[0].exists()


class TestParseDockerfile:
    def test_extracts_base_image_ports_env_and_volumes(self):
        req = parse_dockerfile(
            "# a comment\n"
            "FROM python:3.12-slim\n"
            "ENV APP_PORT=8080 LOG_LEVEL=info\n"
            "EXPOSE 8080 9090/udp\n"
            'VOLUME ["/data"]\n'
        )

        assert req.base_image == "python:3.12-slim"
        assert req.ports == ["8080/tcp", "9090/udp"]
        assert req.env_vars == {"APP_PORT": "8080", "LOG_LEVEL": "info"}
        assert req.volumes == ["/data"]

    def test_joins_continuation_lines(self):
        req = parse_dockerfile("ENV A=1 \\\n    B=2 \\\n    C=3\n")
        assert req.env_vars == {"A": "1", "B": "2", "C": "3"}

    def test_supports_legacy_env_form(self):
        req = parse_dockerfile("ENV GREETING hello there\n")
        assert req.env_vars == {"GREETING": "hello there"}

    def test_handles_quoted_env_value(self):
        req = parse_dockerfile('ENV MOTD="hello world" MODE=prod\n')
        assert req.env_vars == {"MOTD": "hello world", "MODE": "prod"}

    def test_multistage_resolves_final_from_to_real_image(self):
        # The last FROM names a build stage, not an image; the runtime base is
        # the image that stage was built from.
        req = parse_dockerfile(
            'FROM golang:1.22 AS builder\nRUN go build\nFROM builder\nCMD ["/app"]\n'
        )
        assert req.base_image == "golang:1.22"

    def test_multistage_keeps_distinct_final_image(self):
        req = parse_dockerfile("FROM golang:1.22 AS builder\nFROM alpine:3.20\n")
        assert req.base_image == "alpine:3.20"

    def test_space_separated_volume_form(self):
        req = parse_dockerfile("VOLUME /data /config\n")
        assert req.volumes == ["/data", "/config"]

    def test_lowercase_instructions(self):
        req = parse_dockerfile("from alpine:3.20\nexpose 80\n")
        assert req.base_image == "alpine:3.20"
        assert req.ports == ["80/tcp"]

    def test_empty_dockerfile_yields_nothing(self):
        req = parse_dockerfile("# only a comment\n\n")
        assert req.base_image is None
        assert req.ports == []


class TestParseCompose:
    def test_extracts_across_services(self):
        req = parse_compose(
            """
            services:
              app:
                image: ghcr.io/o/app:1.2.3
                ports: ["8080:80"]
                environment:
                  DATABASE_URL: postgres://db/app
                  OPTIONAL_KEY:
                volumes: ["appdata:/var/lib/app"]
                depends_on: [db]
              db:
                image: postgres:16
            """
        )

        assert req.base_image == "ghcr.io/o/app:1.2.3"
        assert req.ports == ["80/tcp"]
        assert req.env_vars == {"DATABASE_URL": "postgres://db/app", "OPTIONAL_KEY": None}
        assert req.volumes == ["/var/lib/app"]
        assert req.depends_on == ["db"]
        assert req.service_names == ["app", "db"]

    def test_port_forms_reduce_to_container_side(self):
        req = parse_compose(
            """
            services:
              app:
                ports:
                  - "80"
                  - "127.0.0.1:8080:8081"
                  - "5353:5353/udp"
                  - target: 9000
                    published: 9999
            """
        )
        assert req.ports == ["80/tcp", "8081/tcp", "5353/udp", "9000/tcp"]

    def test_list_style_environment(self):
        req = parse_compose(
            """
            services:
              app:
                environment:
                  - TZ=UTC
                  - PASSTHROUGH
            """
        )
        assert req.env_vars == {"TZ": "UTC", "PASSTHROUGH": None}

    def test_long_form_volume_uses_target(self):
        req = parse_compose(
            """
            services:
              app:
                volumes:
                  - type: bind
                    source: ./conf
                    target: /etc/app
            """
        )
        assert req.volumes == ["/etc/app"]

    def test_depends_on_mapping_form(self):
        req = parse_compose(
            """
            services:
              app:
                depends_on:
                  db:
                    condition: service_healthy
            """
        )
        assert req.depends_on == ["db"]

    def test_invalid_yaml_warns_rather_than_raising(self):
        req = parse_compose("services: [unclosed\n")
        assert req.warnings
        assert "could not be parsed" in req.warnings[0]

    def test_missing_services_key_warns(self):
        req = parse_compose("version: '3'\n")
        assert any("no `services`" in warning for warning in req.warnings)


class TestParseSnapshot:
    def test_compose_wins_over_dockerfile_on_conflict(self):
        snap = RepoSnapshot(
            repo_url="https://example.com/o/p",
            dockerfile="FROM python:3.12\nENV LOG_LEVEL=debug\nEXPOSE 8080\n",
            compose="services:\n  app:\n    image: ghcr.io/o/app:1\n"
            "    environment:\n      LOG_LEVEL: info\n",
            compose_path="compose.yaml",
        )

        req = parse_snapshot(snap)

        # Compose describes how it actually runs, so its image and value win.
        assert req.base_image == "ghcr.io/o/app:1"
        assert req.env_vars["LOG_LEVEL"] == "info"
        # The Dockerfile's EXPOSE is additive — compose never contradicted it.
        assert "8080/tcp" in req.ports
        assert "compose.yaml" in req.sources
        assert "Dockerfile" in req.sources

    def test_dockerfile_only_repo(self):
        snap = RepoSnapshot(repo_url="x", dockerfile="FROM alpine:3.20\nEXPOSE 80\n")
        req = parse_snapshot(snap)
        assert req.base_image == "alpine:3.20"
        assert req.ports == ["80/tcp"]
        assert req.warnings == []

    def test_empty_repo_warns_instead_of_looking_self_sufficient(self):
        req = parse_snapshot(RepoSnapshot(repo_url="x"))
        assert req.sources == []
        assert any("no Dockerfile or compose file" in warning for warning in req.warnings)

    def test_as_dict_is_json_shaped(self):
        req = parse_snapshot(RepoSnapshot(repo_url="x", dockerfile="FROM alpine\n"))
        payload = req.as_dict()
        assert payload["base_image"] == "alpine"
        assert set(payload) == {
            "base_image",
            "ports",
            "env_vars",
            "volumes",
            "depends_on",
            "service_names",
            "sources",
            "warnings",
        }


# ---------------------------------------------------------------------------
# service-intake-repo MCP tool
# ---------------------------------------------------------------------------


class FakeReasoner:
    def __init__(self, *, enabled=True, result=None, raises=None):
        self.enabled = enabled
        self._result = result
        self._raises = raises
        self.calls: list[dict] = []

    def infer_service_requirements(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._result


def _make_mcp():
    tools: dict = {}

    class _FakeMCP:
        def tool(self, *args, **kwargs):
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

    return _FakeMCP(), tools


def _intake_settings(**overrides):
    base = dict(
        service_deploy_enabled=True,
        service_deploy_confidence_threshold=0.8,
        service_deploy_clone_timeout_seconds=5,
        service_deploy_max_repo_mb=10,
    )
    base.update(overrides)
    return IsolatedSettings(**base)


def _register(monkeypatch, snapshot=None, *, settings=None, reasoner=None, fetch_error=None):
    mcp, tools = _make_mcp()
    settings = settings or _intake_settings()
    reasoner = reasoner or FakeReasoner(enabled=False)

    async def fake_fetch_repo(url, *, timeout_seconds, max_repo_mb):
        if fetch_error is not None:
            raise fetch_error
        return snapshot

    monkeypatch.setattr(intake_tools_mod, "fetch_repo", fake_fetch_repo)
    register_intake_tools(mcp, settings, reasoner)
    return tools["service_intake_repo"], reasoner


class TestServiceIntakeRepoTool:
    async def test_disabled_feature_flag_returns_error(self, monkeypatch):
        tool, _ = _register(
            monkeypatch,
            RepoSnapshot(repo_url="x"),
            settings=_intake_settings(service_deploy_enabled=False),
        )
        result = await tool(repo_url="https://example.com/o/p")
        assert "SERVICE_DEPLOY_ENABLED" in result["error"]

    async def test_fetch_error_becomes_error_dict(self, monkeypatch):
        tool, _ = _register(monkeypatch, fetch_error=IntakeError("Unsupported URL scheme"))
        result = await tool(repo_url="file:///etc/passwd")
        assert result == {"error": "Unsupported URL scheme"}

    async def test_deterministic_requirements_passthrough(self, monkeypatch):
        snapshot = RepoSnapshot(
            repo_url="https://example.com/o/p",
            dockerfile="FROM alpine:3.20\nEXPOSE 80\n",
            compose="services:\n  app:\n    image: ghcr.io/o/app:1\n",
            compose_path="compose.yaml",
            readme="# App",
            skipped=["weird-symlink (symlink, refused)"],
        )
        tool, _ = _register(monkeypatch, snapshot)

        result = await tool(repo_url=snapshot.repo_url)

        assert result["repo_url"] == snapshot.repo_url
        assert result["dockerfile_found"] is True
        assert result["readme_found"] is True
        assert result["compose_path"] == "compose.yaml"
        assert result["requirements"]["base_image"] == "ghcr.io/o/app:1"
        assert result["skipped_files"] == ["weird-symlink (symlink, refused)"]

    async def test_reasoning_disabled_leaves_inference_null(self, monkeypatch):
        snapshot = RepoSnapshot(repo_url="x", dockerfile="FROM alpine\n")
        tool, reasoner = _register(monkeypatch, snapshot, reasoner=FakeReasoner(enabled=False))

        result = await tool(repo_url="x")

        assert result["inference"] is None
        assert "DSPY_ENABLED" in result["inference_rejection_reason"]
        assert reasoner.calls == []  # never called when disabled

    async def test_inference_above_threshold_is_included(self, monkeypatch):
        snapshot = RepoSnapshot(repo_url="x", dockerfile="FROM alpine\n")
        inferred = {
            "service_name": "paperless-ngx",
            "summary": "Document management",
            "category": "app",
            "required_dependencies": ["postgres"],
            "operator_supplied_env_vars": ["SECRET_KEY"],
            "confidence": 0.9,
            "reasoning": "README mentions postgres",
        }
        tool, _ = _register(
            monkeypatch,
            snapshot,
            settings=_intake_settings(service_deploy_confidence_threshold=0.8),
            reasoner=FakeReasoner(enabled=True, result=inferred),
        )

        result = await tool(repo_url="x")

        assert result["inference"] == inferred
        assert "inference_rejection_reason" not in result

    async def test_inference_below_threshold_is_discarded(self, monkeypatch):
        snapshot = RepoSnapshot(repo_url="x", dockerfile="FROM alpine\n")
        low_confidence = {"service_name": "guess", "confidence": 0.3}
        tool, _ = _register(
            monkeypatch,
            snapshot,
            settings=_intake_settings(service_deploy_confidence_threshold=0.8),
            reasoner=FakeReasoner(enabled=True, result=low_confidence),
        )

        result = await tool(repo_url="x")

        # The guess is never returned as if it were fact, matching the
        # write-path modules' no-fallback discipline.
        assert result["inference"] is None
        assert "confidence 0.30 below threshold 0.80" in result["inference_rejection_reason"]

    async def test_inference_none_result_is_reported(self, monkeypatch):
        snapshot = RepoSnapshot(repo_url="x", dockerfile="FROM alpine\n")
        tool, _ = _register(monkeypatch, snapshot, reasoner=FakeReasoner(enabled=True, result=None))
        result = await tool(repo_url="x")
        assert result["inference"] is None
        assert "no result" in result["inference_rejection_reason"]

    async def test_reasoning_exception_does_not_break_intake(self, monkeypatch):
        snapshot = RepoSnapshot(repo_url="x", dockerfile="FROM alpine\n")
        tool, _ = _register(
            monkeypatch,
            snapshot,
            reasoner=FakeReasoner(enabled=True, raises=RuntimeError("LM exploded")),
        )

        result = await tool(repo_url="x")

        assert result["inference"] is None
        assert "reasoning call failed" in result["inference_rejection_reason"]
        # Deterministic facts must still be present despite the failure.
        assert result["requirements"]["base_image"] == "alpine"


async def test_requirement_inference_runs_off_the_event_loop(monkeypatch):
    from conftest import BlockingCall

    reasoner = FakeReasoner(enabled=True)
    reasoner.infer_service_requirements = BlockingCall(None)
    tool, _ = _register(
        monkeypatch,
        RepoSnapshot(repo_url="https://example.com/o/p", dockerfile="FROM python:3.12\n"),
        reasoner=reasoner,
    )
    result = await reasoner.infer_service_requirements.assert_off_loop(
        tool(repo_url="https://example.com/o/p")
    )
    assert result["inference"] is None
