"""Tests for the chat tool allowlist, bridge, and result normalization
(registry_mcp.chat.bridge) — the actual security boundary for chat."""

from unittest.mock import AsyncMock

import pytest

from conftest import IsolatedSettings
from registry_mcp.chat.bridge import (
    DENY_ALWAYS,
    READ_TOOLS,
    WRITE_TOOLS,
    allowed_tool_names,
    dispatch,
    normalize,
    to_ollama_tools,
)


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


# --- allowed_tool_names --------------------------------------------------------


def test_read_write_deny_partitions_are_disjoint():
    assert frozenset() == READ_TOOLS & WRITE_TOOLS
    assert frozenset() == READ_TOOLS & DENY_ALWAYS
    assert frozenset() == WRITE_TOOLS & DENY_ALWAYS


def test_default_settings_are_read_only():
    settings = IsolatedSettings(registry_db_path=":memory:")
    allowed = allowed_tool_names(settings, read_only=False)
    assert allowed == READ_TOOLS
    for name in WRITE_TOOLS | DENY_ALWAYS:
        assert name not in allowed


def test_chat_allow_write_adds_write_tools():
    settings = IsolatedSettings(registry_db_path=":memory:", chat_allow_write=True)
    allowed = allowed_tool_names(settings, read_only=False)
    assert allowed >= WRITE_TOOLS
    assert allowed >= READ_TOOLS


def test_server_read_only_mode_forces_writes_off_even_if_chat_allow_write_true():
    settings = IsolatedSettings(registry_db_path=":memory:", chat_allow_write=True)
    allowed = allowed_tool_names(settings, read_only=True)
    assert allowed == READ_TOOLS
    for name in WRITE_TOOLS:
        assert name not in allowed


@pytest.mark.parametrize("name", sorted(DENY_ALWAYS))
def test_deny_always_tool_never_allowed(name):
    settings = IsolatedSettings(registry_db_path=":memory:", chat_allow_write=True)
    assert name not in allowed_tool_names(settings, read_only=False)


def test_secrets_decrypt_never_allowed_despite_read_only_shape():
    # secrets_decrypt LOOKS like a read tool (no mutation, "read a file") but
    # returns plaintext secret material — it must never be reachable from chat.
    settings = IsolatedSettings(registry_db_path=":memory:", chat_allow_write=True)
    assert "secrets_decrypt" not in allowed_tool_names(settings, read_only=False)


def test_chat_tool_deny_is_restrictive_only():
    settings = IsolatedSettings(
        registry_db_path=":memory:",
        chat_allow_write=True,
        chat_tool_deny="registry_add_service, secrets_decrypt",
    )
    allowed = allowed_tool_names(settings, read_only=False)
    # Shrinks the write set...
    assert "registry_add_service" not in allowed
    # ...but cannot re-admit a hard-denied tool by naming it (it was never
    # going to be in `allowed` regardless).
    assert "secrets_decrypt" not in allowed
    # Unaffected tools stay.
    assert "registry_update_service" in allowed


# --- to_ollama_tools -----------------------------------------------------------


async def test_to_ollama_tools_filters_to_allowed(server):
    specs = await to_ollama_tools(server, frozenset({"registry_list_services"}))
    assert [s["function"]["name"] for s in specs] == ["registry_list_services"]
    assert specs[0]["type"] == "function"
    assert "parameters" in specs[0]["function"]


async def test_to_ollama_tools_never_includes_deny_always_tools(server):
    # Even if a caller mistakenly includes a hard-denied name in `allowed`,
    # it must still not silently appear if it isn't registered — but here we
    # confirm the intended path: DENY_ALWAYS names are simply never passed.
    specs = await to_ollama_tools(server, READ_TOOLS)
    names = {s["function"]["name"] for s in specs}
    assert names.isdisjoint(DENY_ALWAYS)


async def test_to_ollama_tools_appends_hint_for_hardware_node_services(server):
    specs = await to_ollama_tools(server, frozenset({"hardware-node-services"}))
    assert "does not resolve a hostname" in specs[0]["function"]["description"]


async def test_to_ollama_tools_empty_allowed_yields_no_specs(server):
    assert await to_ollama_tools(server, frozenset()) == []


# --- normalize -----------------------------------------------------------------


def test_normalize_unwraps_two_tuple():
    raw = (["content-block-placeholder"], {"id": "svc-1", "name": "plex"})
    result = normalize("registry_get_service", raw)
    assert result == {
        "ok": True,
        "tool": "registry_get_service",
        "data": {"id": "svc-1", "name": "plex"},
    }


def test_normalize_unwraps_result_wrapped_list():
    # FastMCP wraps a bare list return (e.g. events_list_changes) as
    # {"result": [...]} — normalize should undo that so callers see a list.
    raw = (["content-block-placeholder"], {"result": [{"a": 1}, {"a": 2}]})
    result = normalize("events_list_changes", raw)
    assert result["data"] == [{"a": 1}, {"a": 2}]


def test_normalize_lifts_inband_error():
    raw = (["content-block-placeholder"], {"error": "TRAEFIK_API_URL is not configured"})
    result = normalize("traefik_get_overview", raw)
    assert result == {
        "ok": False,
        "tool": "traefik_get_overview",
        "error": "TRAEFIK_API_URL is not configured",
    }


def test_normalize_handles_bare_content_block_list():
    raw = [_FakeTextBlock("hello"), _FakeTextBlock("world")]
    result = normalize("some_tool", raw)
    assert result == {"ok": True, "tool": "some_tool", "data": "hello\nworld"}


def test_normalize_handles_call_tool_result_object():
    class _FakeCallToolResult:
        structuredContent = {"ok": True}

    result = normalize("some_tool", _FakeCallToolResult())
    assert result == {"ok": True, "tool": "some_tool", "data": {"ok": True}}


def test_normalize_preserves_dict_that_only_incidentally_has_error_key_value_none():
    # A dict with an "error" key set to a falsy-but-present value is still an
    # error — presence, not truthiness, is what matters.
    raw = (["x"], {"error": ""})
    result = normalize("t", raw)
    assert result["ok"] is False


# --- dispatch --------------------------------------------------------------


async def test_dispatch_rejects_tool_outside_allowed(server):
    result = await dispatch(server, "secrets_decrypt", {}, frozenset(), max_result_chars=1000)
    assert result["ok"] is False
    assert "not available" in result["error"]


async def test_dispatch_rejects_deny_always_even_if_present_in_allowed(server):
    # Defense in depth: DENY_ALWAYS wins even if a caller's `allowed` set was
    # built some other way and mistakenly includes a hard-denied name.
    bad_allowed = frozenset({"secrets_decrypt"})
    result = await dispatch(server, "secrets_decrypt", {}, bad_allowed, max_result_chars=1000)
    assert result["ok"] is False


async def test_dispatch_calls_allowed_tool_and_normalizes(server):
    result = await dispatch(
        server,
        "registry_list_services",
        {},
        frozenset({"registry_list_services"}),
        max_result_chars=1000,
    )
    assert result["ok"] is True
    assert result["data"] == []


async def test_dispatch_catches_tool_exception(server, monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(server, "call_tool", AsyncMock(side_effect=boom))
    result = await dispatch(
        server,
        "registry_list_services",
        {},
        frozenset({"registry_list_services"}),
        max_result_chars=1000,
    )
    assert result["ok"] is False
    assert "kaboom" in result["error"]


async def test_dispatch_truncates_large_result(server):
    for i in range(5):
        await server.call_tool(
            "registry_add_service", {"name": f"svc-{i}", "display_name": f"Service {i}"}
        )
    result = await dispatch(
        server,
        "registry_list_services",
        {},
        frozenset({"registry_list_services"}),
        max_result_chars=50,
    )
    assert result["ok"] is True
    assert result.get("truncated") is True
    assert len(result["data"]) <= 50
