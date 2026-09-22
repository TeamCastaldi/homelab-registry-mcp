"""Tests for the service-deploy-generate-compose MCP tool (conversational
deploy Phase 2).

Intake and generation are both faked: `run_intake` is monkeypatched so no
repo is cloned, and a fake generator returns canned drafts.
"""

from __future__ import annotations

import pytest

import registry_mcp.tools.service_deploy as service_deploy_tools_mod
from conftest import IsolatedSettings
from registry_mcp.service_deploy import ComposeDraft
from registry_mcp.tools import register_service_deploy_tools

INTAKE = {
    "repo_url": "https://github.com/o/Paperless-NGX.git",
    "dockerfile_found": True,
    "readme_found": True,
    "compose_path": None,
    "requirements": {"ports": ["8000/tcp"], "env_vars": {"SECRET_KEY": None}},
    "skipped_files": [],
    "inference": None,
}

DRAFT = ComposeDraft(
    ok=True,
    confidence=0.9,
    compose="services:\n  app:\n    image: ghcr.io/o/app:1.2.3\n",
    findings=[{"rule_id": "R-003", "path": "compose.yaml", "service": "app", "detail": "x"}],
    skipped_rules=["N-006"],
    reasoning="ok",
)


class FakeReasoner:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled


class FakeGenerator:
    def __init__(self, draft: ComposeDraft = DRAFT) -> None:
        self._draft = draft
        self.calls: list[dict] = []

    async def generate(self, **kwargs) -> ComposeDraft:
        self.calls.append(kwargs)
        return self._draft


def _make_mcp():
    tools: dict = {}

    class _FakeMCP:
        def tool(self, *args, **kwargs):
            def decorator(fn):
                tools[kwargs["name"]] = fn
                return fn

            return decorator

    return _FakeMCP(), tools


def _register(monkeypatch, *, intake=INTAKE, enabled=True, reasoner_enabled=True, draft=DRAFT):
    intake_calls: list[str] = []

    async def fake_run_intake(settings, reasoner, repo_url):
        intake_calls.append(repo_url)
        return intake

    monkeypatch.setattr(service_deploy_tools_mod, "run_intake", fake_run_intake)
    mcp, tools = _make_mcp()
    generator = FakeGenerator(draft)
    register_service_deploy_tools(
        mcp,
        IsolatedSettings(service_deploy_enabled=enabled),
        FakeReasoner(enabled=reasoner_enabled),
        generator,
    )
    return tools["service-deploy-generate-compose"], generator, intake_calls


# --- gates ---------------------------------------------------------------------


async def test_disabled_feature_flag_returns_error(monkeypatch):
    tool, generator, intake_calls = _register(monkeypatch, enabled=False)
    result = await tool(repo_url="https://github.com/o/p")
    assert "SERVICE_DEPLOY_ENABLED" in result["error"]
    assert intake_calls == [] and generator.calls == []


async def test_reasoning_disabled_returns_error_before_cloning(monkeypatch):
    tool, generator, intake_calls = _register(monkeypatch, reasoner_enabled=False)
    result = await tool(repo_url="https://github.com/o/p")
    assert "DSPY_ENABLED" in result["error"]
    assert intake_calls == [] and generator.calls == []


async def test_intake_error_passes_through(monkeypatch):
    tool, generator, _ = _register(monkeypatch, intake={"error": "Unsupported URL scheme"})
    result = await tool(repo_url="file:///etc/passwd")
    assert result == {"error": "Unsupported URL scheme"}
    assert generator.calls == []


# --- service name resolution ----------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "inference", "expected"),
    [
        ("  mine  ", {"service_name": "inferred"}, "mine"),
        ("", {"service_name": "inferred"}, "inferred"),
        ("", None, "paperless-ngx"),  # repo segment, .git stripped, lowercased
    ],
)
async def test_service_name_resolution_order(monkeypatch, requested, inference, expected):
    tool, generator, _ = _register(monkeypatch, intake={**INTAKE, "inference": inference})
    result = await tool(repo_url=INTAKE["repo_url"], service_name=requested)
    assert result["service_name"] == expected
    assert generator.calls[0]["service_name"] == expected


async def test_invalid_service_name_is_rejected_before_generation(monkeypatch):
    tool, generator, _ = _register(monkeypatch)
    result = await tool(repo_url=INTAKE["repo_url"], service_name="../etc")
    assert "not a valid lowercase name" in result["error"]
    assert result["intake"] == INTAKE
    assert generator.calls == []


# --- draft passthrough ----------------------------------------------------------


async def test_accepted_draft_passes_through(monkeypatch):
    tool, generator, _ = _register(monkeypatch)
    result = await tool(repo_url=INTAKE["repo_url"], target_node="heimdall")

    assert result["ok"] is True
    assert result["confidence"] == 0.9
    assert result["compose"] == DRAFT.compose
    assert result["findings"] == DRAFT.findings
    assert result["skipped_rules"] == ["N-006"]
    assert result["rejection_reason"] is None
    assert result["target_node"] == "heimdall"
    assert result["intake"] == INTAKE
    assert generator.calls[0]["target_node"] == "heimdall"
    assert generator.calls[0]["intake"] == {
        "repo_url": INTAKE["repo_url"],
        "detected": INTAKE["requirements"],
        "inference": None,
    }


async def test_rejected_draft_passes_through_without_compose(monkeypatch):
    rejected = ComposeDraft(ok=False, confidence=0.4, rejection_reason="confidence too low")
    tool, _, _ = _register(monkeypatch, draft=rejected)
    result = await tool(repo_url=INTAKE["repo_url"])

    assert result["ok"] is False
    assert result["compose"] is None
    assert result["rejection_reason"] == "confidence too low"
    assert result["target_node"] is None


# --- registration -----------------------------------------------------------------


def test_build_server_registers_tool(server):
    tools = {tool.name for tool in server._tool_manager.list_tools()}
    assert "service-deploy-generate-compose" in tools
