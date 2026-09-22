"""Tests for the Phase 7 reasoning layer (DSPy enrichment modules).

The Reasoner's confidence gates and output coercion are tested with plain
stand-in predictors (no LLM). One end-to-end test wires a real DSPy
ChainOfThought module against DSPy's DummyLM to prove the signatures parse.
"""

from types import SimpleNamespace

import dspy
from dspy.utils.dummies import DummyLM

from conftest import IsolatedSettings
from registry_mcp.dspy import build_reasoner
from registry_mcp.dspy.signatures import (
    InferServiceRequirements,
    NormalizeConfigFile,
    ResolveServiceIdentity,
)
from registry_mcp.models import AuthMode, Category
from registry_mcp.server import build_server


def _enabled_reasoner(**overrides):
    """A reasoner marked enabled+configured so we can inject fake predictors."""
    reasoner = build_reasoner(IsolatedSettings(dspy_enabled=True, **overrides))
    reasoner._configured = True  # skip real LM/dspy configuration
    return reasoner


# --- disabled layer: everything degrades gracefully -----------------------


def test_disabled_reasoner_short_circuits():
    reasoner = build_reasoner(IsolatedSettings(dspy_enabled=False))
    assert reasoner.enabled is False
    assert reasoner.resolve_identity({"name": "x"}, [{"name": "y"}]) is None
    assert reasoner.infer_metadata(router_rule="", middlewares=[], service_name="x") is None
    summary = reasoner.summarize_access(slug="x", events=[], hours=24)
    assert "error" in summary and "DSPY_ENABLED" in summary["error"]
    assert (
        reasoner.infer_service_requirements(repo_url="https://x/y", readme="", detected={}) is None
    )


# --- ResolveServiceIdentity gate ------------------------------------------


def test_resolve_identity_match_above_threshold():
    reasoner = _enabled_reasoner()
    reasoner._resolve = lambda **kw: SimpleNamespace(
        matched_name="vault", confidence=0.95, reasoning=""
    )
    assert reasoner.resolve_identity({"name": "vw"}, [{"name": "vault"}]) == "vault"


def test_resolve_identity_below_threshold_returns_none():
    reasoner = _enabled_reasoner()
    reasoner._resolve = lambda **kw: SimpleNamespace(
        matched_name="vault", confidence=0.5, reasoning=""
    )
    assert reasoner.resolve_identity({"name": "vw"}, [{"name": "vault"}]) is None


def test_resolve_identity_rejects_name_not_in_candidates():
    # A confident match against a service that is not in the existing set is a
    # hallucination and must be rejected.
    reasoner = _enabled_reasoner()
    reasoner._resolve = lambda **kw: SimpleNamespace(
        matched_name="ghost", confidence=0.99, reasoning=""
    )
    assert reasoner.resolve_identity({"name": "vw"}, [{"name": "vault"}]) is None


def test_resolve_identity_no_existing_skips_call():
    reasoner = _enabled_reasoner()
    reasoner._resolve = lambda **kw: (_ for _ in ()).throw(AssertionError("should not be called"))
    assert reasoner.resolve_identity({"name": "vw"}, []) is None


# --- InferServiceMetadata gate + coercion ---------------------------------


def test_infer_metadata_coerces_enums():
    reasoner = _enabled_reasoner()
    reasoner._infer = lambda **kw: SimpleNamespace(
        display_name="Plex Media Server",
        category="media",
        auth_mode="forward_auth",
        notes="Streams movies",
        confidence=0.9,
    )
    out = reasoner.infer_metadata(
        router_rule="Host(`plex.lan`)", middlewares=["authentik"], service_name="plex"
    )
    assert out["category"] == Category.media
    assert out["auth_mode"] == AuthMode.forward_auth
    assert out["display_name"] == "Plex Media Server"
    assert out["notes"] == "Streams movies"


def test_infer_metadata_drops_invalid_enums_and_blanks():
    reasoner = _enabled_reasoner()
    reasoner._infer = lambda **kw: SimpleNamespace(
        display_name="", category="bogus", auth_mode="nope", notes="", confidence=0.9
    )
    assert reasoner.infer_metadata(router_rule="", middlewares=[], service_name="x") is None


def test_infer_metadata_below_threshold_returns_none():
    reasoner = _enabled_reasoner()
    reasoner._infer = lambda **kw: SimpleNamespace(
        display_name="Plex", category="media", auth_mode="none", notes="", confidence=0.3
    )
    assert reasoner.infer_metadata(router_rule="", middlewares=[], service_name="x") is None


# --- SummarizeAccessAudit structuring -------------------------------------


def test_summarize_access_structures_output():
    reasoner = _enabled_reasoner()
    reasoner._summarize = lambda **kw: SimpleNamespace(
        summary="3 users, no failures",
        anomalies=["off-hours login"],
        unique_users="3",
        failed_auth_count="0",
        risk_level="low",
    )
    out = reasoner.summarize_access(slug="vaultwarden", events=[{"a": 1}, {"b": 2}], hours=24)
    assert out["summary"] == "3 users, no failures"
    assert out["anomalies"] == ["off-hours login"]
    assert out["unique_users"] == 3
    assert out["failed_auth_count"] == 0
    assert out["risk_level"] == "low"
    assert out["event_count"] == 2
    assert out["application_slug"] == "vaultwarden"


def test_summarize_access_handles_module_error():
    reasoner = _enabled_reasoner()

    def _boom(**kw):
        raise RuntimeError("LM down")

    reasoner._summarize = _boom
    out = reasoner.summarize_access(slug="x", events=[], hours=1)
    assert "error" in out and "LM down" in out["error"]


# --- end-to-end with DummyLM (real DSPy module, no network) ---------------


def test_resolve_identity_end_to_end_with_dummy_lm():
    reasoner = build_reasoner(IsolatedSettings(dspy_enabled=True))
    lm = DummyLM(
        [{"reasoning": "same host vault.lan", "matched_name": "vault", "confidence": "0.9"}]
    )
    dspy.configure(lm=lm)
    reasoner._resolve = dspy.ChainOfThought(ResolveServiceIdentity)
    reasoner._configured = True

    result = reasoner.resolve_identity(
        {"name": "vaultwarden", "urls": ["https://vault.lan"]},
        [{"name": "vault", "urls": ["https://vault.lan"]}],
    )
    assert result == "vault"


def test_normalize_config_end_to_end_with_dummy_lm():
    reasoner = build_reasoner(IsolatedSettings(dspy_enabled=True))
    lm = DummyLM(
        [
            {
                "reasoning": "moved image above restart",
                "normalized_file": (
                    "services:\n  plex:\n    image: x:1\n    restart: unless-stopped\n"
                ),
                "commit_message": "style: normalize plex/compose.yaml",
                "confidence": "0.9",
            }
        ]
    )
    dspy.configure(lm=lm)
    reasoner._normalize = dspy.ChainOfThought(NormalizeConfigFile)
    reasoner._patch_lm = lm
    reasoner._configured = True

    result = reasoner.normalize_config(
        current_file="services:\n  plex:\n    restart: unless-stopped\n    image: x:1\n",
        file_path="nodes/pi/plex/compose.yaml",
        violations="N-006",
        canonical_form="per-service keys ordered image, restart, ...",
    )
    assert result["confidence"] == 0.9
    assert "image: x:1" in result["normalized_file"]


# --- MCP tool: summarize events is gated on the reasoning layer ------------


async def test_summarize_events_tool_disabled(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = (await server.call_tool("authentik_summarize_events", {"slug": "vaultwarden"}))[1]
    assert "error" in result and "DSPY_ENABLED" in result["error"]


# --- InferServiceRequirements: raw passthrough, caller owns the gate -------


def test_infer_service_requirements_returns_raw_outputs_with_confidence():
    # Unlike the enrichment modules, this one does not gate internally — the
    # intake tool applies SERVICE_DEPLOY_CONFIDENCE_THRESHOLD, the same split
    # the write-path modules use.
    reasoner = _enabled_reasoner()
    reasoner._infer_requirements = lambda **kw: SimpleNamespace(
        service_name="  paperless-ngx  ",
        summary=" Document management ",
        category="APP",
        required_dependencies=["postgres", "redis"],
        operator_supplied_env_vars=["PAPERLESS_SECRET_KEY"],
        confidence=0.42,
        reasoning="README documents a postgres backend",
    )

    result = reasoner.infer_service_requirements(
        repo_url="https://github.com/o/p", readme="# Paperless", detected={"env_vars": {}}
    )

    # A low score is returned, not swallowed: the caller decides.
    assert result["confidence"] == 0.42
    assert result["service_name"] == "paperless-ngx"
    assert result["summary"] == "Document management"
    assert result["category"] == "app"
    assert result["required_dependencies"] == ["postgres", "redis"]
    assert result["operator_supplied_env_vars"] == ["PAPERLESS_SECRET_KEY"]


def test_infer_service_requirements_tolerates_missing_fields():
    reasoner = _enabled_reasoner()
    reasoner._infer_requirements = lambda **kw: SimpleNamespace(confidence="not-a-number")

    result = reasoner.infer_service_requirements(repo_url="x", readme="", detected={})

    assert result["confidence"] == 0.0
    assert result["required_dependencies"] == []
    assert result["service_name"] == ""


def test_infer_service_requirements_survives_module_error():
    reasoner = _enabled_reasoner()

    def _boom(**kw):
        raise RuntimeError("LM exploded")

    reasoner._infer_requirements = _boom
    assert reasoner.infer_service_requirements(repo_url="x", readme="", detected={}) is None


def test_infer_service_requirements_end_to_end_with_dummy_lm():
    reasoner = build_reasoner(IsolatedSettings(dspy_enabled=True))
    lm = DummyLM(
        [
            {
                "reasoning": "README says it needs postgres",
                "service_name": "paperless-ngx",
                "summary": "Document management system",
                "category": "app",
                "required_dependencies": '["postgres"]',
                "operator_supplied_env_vars": '["PAPERLESS_SECRET_KEY"]',
                "confidence": "0.85",
            }
        ]
    )
    dspy.configure(lm=lm)
    reasoner._infer_requirements = dspy.ChainOfThought(InferServiceRequirements)
    reasoner._configured = True

    result = reasoner.infer_service_requirements(
        repo_url="https://github.com/o/paperless",
        readme="# Paperless\n\nRequires a PostgreSQL database.",
        detected={"env_vars": {"PAPERLESS_SECRET_KEY": None}, "ports": ["8000/tcp"]},
    )

    assert result["confidence"] == 0.85
    assert result["required_dependencies"] == ["postgres"]
    assert result["service_name"] == "paperless-ngx"
