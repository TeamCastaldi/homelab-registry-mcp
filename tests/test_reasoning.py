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
from registry_mcp.dspy.signatures import ResolveServiceIdentity
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


# --- MCP tool: summarize events is gated on the reasoning layer ------------


async def test_summarize_events_tool_disabled(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = (await server.call_tool("authentik_summarize_events", {"slug": "vaultwarden"}))[1]
    assert "error" in result and "DSPY_ENABLED" in result["error"]
