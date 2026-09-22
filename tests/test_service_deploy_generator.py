"""Tests for service_deploy.ComposeGenerator (conversational deploy Phase 2).

No LLM and no network: a fake reasoner returns canned module outputs and a
fake Git provider stands in for the homelab-repo conventions read.
"""

from __future__ import annotations

import yaml

from registry_mcp.normalization.generator import CANONICAL_FORM_SUMMARY
from registry_mcp.service_deploy import ComposeGenerator
from registry_mcp.service_deploy.generator import REQUIRED_RULES_SUMMARY

GOOD = """\
services:
  app:
    restart: unless-stopped
    container_name: app
    image: ghcr.io/o/app:1.2.3
    labels:
      - "traefik.enable=true"
    networks:
      - ${PROXY_NETWORK:-proxy-net}
networks:
  ${PROXY_NETWORK:-proxy-net}:
    external: true
"""


class FakeReasoner:
    def __init__(self, result: dict | None) -> None:
        self._result = result
        self.calls: list[dict] = []

    def generate_service_compose(self, **kwargs) -> dict | None:
        self.calls.append(kwargs)
        return self._result


class FakeGit:
    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self._content = content
        self._error = error
        self.reads: list[tuple[str, str, str]] = []

    async def read_file(self, repo: str, path: str, ref: str) -> str:
        self.reads.append((repo, path, ref))
        if self._error is not None:
            raise self._error
        return self._content


def _raw(compose: str = GOOD, confidence: float = 0.9, reasoning: str = "ok") -> dict:
    return {"compose_yaml": compose, "confidence": confidence, "reasoning": reasoning}


async def _generate(result: dict | None, *, service_name: str = "app", **gen_kwargs):
    reasoner = FakeReasoner(result)
    draft = await ComposeGenerator(reasoner, threshold=0.8, **gen_kwargs).generate(
        intake={"ports": ["8000/tcp"]}, service_name=service_name, target_node="heimdall"
    )
    return draft, reasoner


# --- rejections: no fallback, ever ------------------------------------------


async def test_rejects_when_reasoning_unavailable():
    draft, _ = await _generate(None)
    assert draft.ok is False
    assert "unavailable" in draft.rejection_reason
    assert draft.compose == ""


async def test_rejects_below_threshold():
    draft, _ = await _generate(_raw(confidence=0.5))
    assert draft.ok is False
    assert draft.confidence == 0.5
    assert "below threshold" in draft.rejection_reason
    assert draft.compose == ""


async def test_rejects_empty_compose():
    draft, _ = await _generate(_raw(compose="   \n"))
    assert draft.ok is False
    assert "empty" in draft.rejection_reason


async def test_rejects_invalid_yaml():
    draft, _ = await _generate(_raw(compose="services:\n  app: [unclosed\n"))
    assert draft.ok is False
    assert "not valid YAML" in draft.rejection_reason


async def test_rejects_non_compose_document():
    draft, _ = await _generate(_raw(compose="just: a mapping\n"))
    assert draft.ok is False
    assert "services:" in draft.rejection_reason


async def test_rejects_when_requested_service_key_is_missing():
    draft, _ = await _generate(_raw(), service_name="paperless")
    assert draft.ok is False
    assert "'paperless'" in draft.rejection_reason


# --- success path ------------------------------------------------------------


async def test_success_is_canonicalized_by_the_formatter():
    draft, reasoner = await _generate(_raw())

    assert draft.ok is True
    assert draft.confidence == 0.9
    assert draft.reasoning == "ok"
    service = yaml.safe_load(draft.compose)["services"]["app"]
    # N-006 key order, N-007 labels list -> mapping.
    assert list(service)[:3] == ["image", "container_name", "restart"]
    assert service["labels"] == {"traefik.enable": "true"}
    assert draft.compose.endswith("\n") and not draft.compose.endswith("\n\n")
    assert draft.skipped_rules == []
    assert draft.findings == []
    assert reasoner.calls[0]["service_name"] == "app"
    assert reasoner.calls[0]["target_node"] == "heimdall"
    assert reasoner.calls[0]["intake"] == {"ports": ["8000/tcp"]}


async def test_tier2_findings_are_reported_not_blocking():
    compose = "services:\n  app:\n    image: ghcr.io/o/app:latest\n    container_name: app\n"
    draft, _ = await _generate(_raw(compose=compose))

    assert draft.ok is True
    rule_ids = {f["rule_id"] for f in draft.findings}
    assert rule_ids == {"R-001", "R-003"}  # :latest tag, no restart policy


async def test_formatter_skipped_rules_are_reported():
    # A comment on a key the reorder would move makes N-006 unsafe to apply.
    compose = (
        "services:\n"
        "  app:\n"
        "    restart: unless-stopped  # policy\n"
        "    image: ghcr.io/o/app:1.2.3\n"
        "    container_name: app\n"
    )
    draft, _ = await _generate(_raw(compose=compose))

    assert draft.ok is True
    assert "N-006" in draft.skipped_rules
    assert "# policy" in draft.compose


async def test_credentials_are_scrubbed_from_draft_and_reasoning():
    leaked = "abcdefghijklmnopqrstuvwxyz123456"
    compose = (
        "services:\n"
        "  app:\n"
        "    image: ghcr.io/o/app:1.2.3\n"
        "    container_name: app\n"
        "    restart: unless-stopped\n"
        "    environment:\n"
        f"      SECRET_KEY: {leaked}\n"
    )
    draft, _ = await _generate(_raw(compose=compose, reasoning=f"used TOKEN={leaked}"))

    assert draft.ok is True
    assert leaked not in draft.compose
    assert leaked not in draft.reasoning
    assert "<replace-with-credential>" in draft.compose


# --- conventions context -------------------------------------------------------


async def test_conventions_baseline_only_without_git():
    _, reasoner = await _generate(_raw())
    conventions = reasoner.calls[0]["homelab_conventions"]
    assert CANONICAL_FORM_SUMMARY in conventions
    assert REQUIRED_RULES_SUMMARY in conventions
    assert "Homelab compose spec" not in conventions


async def test_conventions_append_homelab_spec_when_git_configured():
    git = FakeGit(content="# homelab spec body")
    _, reasoner = await _generate(
        _raw(), git=git, repo="o/homelab", base="main", conventions_path="docs/spec/compose.yaml"
    )

    assert git.reads == [("o/homelab", "docs/spec/compose.yaml", "main")]
    conventions = reasoner.calls[0]["homelab_conventions"]
    assert CANONICAL_FORM_SUMMARY in conventions
    assert "# homelab spec body" in conventions
    assert "the rules above win" in conventions


async def test_conventions_fetch_failure_never_blocks_generation():
    git = FakeGit(error=RuntimeError("404"))
    draft, reasoner = await _generate(_raw(), git=git, repo="o/homelab")

    assert draft.ok is True
    assert "Homelab compose spec" not in reasoner.calls[0]["homelab_conventions"]
