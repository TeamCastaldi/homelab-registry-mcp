"""The chat feature's actual security boundary: an explicit tool allowlist,
MCP-tool-schema -> Ollama-tool-schema translation, dispatch, and result
normalization.

Everything here operates purely through `mcp.list_tools()`/`mcp.call_tool()`
— no store/engine reference, so chat can never see or do anything an
ordinary MCP client couldn't already (see `registry_mcp.chat.__init__`).
Auth on `/chat` (see `registry_mcp.chat.auth`) gates who can reach this
module; this module gates what they can reach through it.

The allowlist is closed-world by explicit tool **name**, never a prefix or
pattern: a prefix match would silently admit any future tool whose name
happens to fit (e.g. a hypothetical `traefik_set_router`), and a name check
fails closed on anything unrecognized. `DENY_ALWAYS` wins over both READ and
WRITE unconditionally — see the per-tool comments below for why each one is
there. This is the one place in the module worth being conservative in favor
of correctness over convenience.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.config import Settings

# --- Allowlist ---------------------------------------------------------------
#
# Partitioned by hand against every `@mcp.tool` registration in `tools/` and
# `integrations/*/tools.py` (70 tools total as of this writing) — see the
# per-tool comments below rather than re-deriving this from a naming
# convention, since several of these are exceptions to the obvious pattern
# (e.g. `authentik_summarize_events` reads nothing sensitive but still isn't
# a "read" tool for chat purposes; `secrets_decrypt` reads plaintext secrets
# and is therefore *not* safe despite its read-only shape).

READ_TOOLS: frozenset[str] = frozenset(
    {
        # server.py — always safe, no upstream config required
        "health",
        "system_health_check",
        # tools/registry.py
        "registry_get_service",
        "registry_list_services",
        # tools/linking.py
        "service_get_full_context",
        # tools/hardware.py
        "hardware-get-node",
        "hardware-list-nodes",
        "hardware-node-services",
        "hardware-list-unconfirmed",
        "hardware-list-stale",
        "hardware-capacity-summary",
        "hardware-discovery-status",
        # tools/events.py
        "events_list_discoveries",
        "events_list_changes",
        "events_get_for_service",
        # tools/discovery.py
        "discovery_status",
        "discovery_list_stale",
        # tools/proposal.py
        "proposal_list_open",
        "proposal_get",
        # integrations/komodo/tools.py
        "komodo_health",
        "komodo_list_stacks",
        "komodo_get_stack",
        "komodo_list_services",
        "komodo_get_service",
        "komodo_list_updates",
        "komodo_get_logs",
        # integrations/traefik/tools.py
        "traefik_get_overview",
        "traefik_get_entrypoints",
        "traefik_list_routers",
        "traefik_get_router",
        "traefik_list_services",
        "traefik_list_middlewares",
        "traefik_list_tls_certificates",
        # integrations/authentik/tools.py
        "authentik_list_applications",
        "authentik_get_application",
        "authentik_list_providers",
        "authentik_list_outposts",
        "authentik_get_outpost_status",
        "authentik_list_policies",
        "authentik_search_events",
        "authentik_list_users",
        "authentik_list_groups",
    }
)

WRITE_TOOLS: frozenset[str] = frozenset(
    {
        # tools/registry.py — create/update only; delete is in DENY_ALWAYS
        "registry_add_service",
        "registry_update_service",
        # tools/linking.py
        "service_link_authentik",
        # tools/hardware.py — create/update/link only; delete is in DENY_ALWAYS
        "hardware-add-node",
        "hardware-update-node",
        "hardware-link-service",
        # tools/discovery.py — re-scans configured sources; idempotent, no
        # config accepted from the model (unlike discovery_connect_*, denied
        # below)
        "discovery_run_now",
        # tools/proposal.py — opens/cancels/re-verifies a PR; never merges
        # (the PR + human-merge gate is unchanged), still subject to the
        # server's own read_only startup check regardless of this allowlist
        "proposal_create",
        "proposal_cancel",
        "proposal_verify",
        # tools/proposal.py — opens formatting-only PRs (never a security
        # fix — always a separate PR/label); same read_only gate as above
        "proposal_normalize",
    }
)

# Hard-denied at every setting, including CHAT_ALLOW_WRITE=true. Never sent
# to the model, never dispatched even if the model names one anyway.
DENY_ALWAYS: frozenset[str] = frozenset(
    {
        # tools/secrets.py — returns/handles plaintext secret material; a
        # chat turn is exactly the kind of place a secret must never surface
        "secrets_status",
        "secrets_encrypt",
        "secrets_decrypt",
        "secrets_add",
        "secrets_rotate",
        "secrets_list_keys",
        # tools/adoption.py — SSHes into a live node and captures its
        # secrets into an AdoptionDraft; needs a human's keep/rotate
        # decision, not a chat turn
        "proposal_adopt_service",
        "proposal_adopt_service_finalize",
        "proposal_adopt_service_cancel",
        "proposal_adopt_service_get",
        # tools/registry.py, tools/hardware.py — hard deletes; destructive
        # and not reversible from the chat UI. The *_confirm tools are the
        # ones that actually perform the delete once the math challenge is
        # answered, so they're denied right alongside the request step.
        "registry_delete_service",
        "registry_delete_service_confirm",
        "hardware-delete-node",
        "hardware-delete-node-confirm",
        # tools/hardware.py — shells out to `ansible ... -m setup`, a
        # minutes-long blocking operation FastMCP would run inline on the
        # event loop (see routes.py for why sync tools already do this)
        "hardware-discover-now",
        # tools/discovery.py — accepts a URL and (for Authentik) a bearer
        # token as free-form arguments; those are exactly the kind of
        # values that must never be model-supplied
        "discovery_connect_traefik",
        "discovery_connect_authentik",
        # integrations/authentik/tools.py — invokes the DSPy reasoning
        # layer (a second, metered LLM call) per invocation; cost control,
        # not a security boundary — reconsider once usage is understood
        "authentik_summarize_events",
    }
)

# Per-tool hints appended to the description sent to the model, for the few
# tools whose MCP docstring doesn't make a sharp edge obvious enough for a
# 14B model to reliably avoid on the first try.
_TOOL_HINTS: dict[str, str] = {
    "hardware-node-services": (
        " Takes the node's UUID only — it does not resolve a hostname. Call "
        "hardware-get-node first to look up the id from a hostname."
    ),
}


def allowed_tool_names(settings: Settings, *, read_only: bool) -> frozenset[str]:
    """Resolve the effective allowlist for the current settings.

    `read_only` (the server's startup health-check result — see
    `health.check_health`) forces writes off regardless of
    `CHAT_ALLOW_WRITE`, matching every other write path in this server.
    `CHAT_TOOL_DENY` is restrictive-only: it can shrink the set further but
    can never re-admit a name already in `DENY_ALWAYS`.
    """
    allowed = set(READ_TOOLS)
    if settings.chat_allow_write and not read_only:
        allowed |= WRITE_TOOLS
    extra_deny = {name.strip() for name in settings.chat_tool_deny.split(",") if name.strip()}
    allowed -= DENY_ALWAYS
    allowed -= extra_deny
    return frozenset(allowed)


async def to_ollama_tools(mcp: FastMCP, allowed: frozenset[str]) -> list[dict[str, Any]]:
    """Build Ollama's `tools` payload from the MCP tools present in `allowed`.

    Filters against the live tool list rather than trusting the allowlist
    names to exist — a name in `allowed` that isn't currently registered
    (e.g. a future rename) is silently skipped rather than offered.
    """
    specs: list[dict[str, Any]] = []
    for tool in await mcp.list_tools():
        if tool.name not in allowed:
            continue
        description = (tool.description or "").strip()
        hint = _TOOL_HINTS.get(tool.name)
        if hint:
            description = f"{description}{hint}"
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": description,
                    "parameters": tool.inputSchema,
                },
            }
        )
    return specs


def normalize(name: str, raw: Any) -> dict[str, Any]:
    """Collapse `call_tool`'s three possible return shapes, and this repo's
    two response-wrapping conventions, into one consistent envelope.

    `call_tool` (see `mcp.server.fastmcp.utilities.func_metadata.
    FuncMetadata.convert_result`) returns one of:
      - a `CallToolResult` (only if the tool function itself constructs one
        — none in this repo do)
      - a bare `list[ContentBlock]` when the tool has no return-type
        annotation (`output_schema is None`) — none in this repo lack one
      - `(unstructured_content, structured_content)`, a 2-tuple, whenever an
        annotation is present — every tool in this repo, today

    On top of that, tools whose return annotation isn't already a mapping
    (e.g. `list[dict[str, Any]]`) get `wrap_output`-wrapped by FastMCP into
    `{"result": [...]}` before reaching here — every `events_*` tool, every
    `hardware-*` list tool, and `registry_list_services`. This function
    undoes that wrapping too, so callers always see the tool's actual
    payload shape (a list stays a list) rather than this SDK-internal
    artifact. Existing in-band `{"error": "..."}` results (every
    unconfigured-upstream or failed-call path in this codebase) are lifted
    to the `ok: False` envelope.
    """
    if isinstance(raw, tuple) and len(raw) == 2:
        payload = raw[1]
    elif isinstance(raw, list):
        # No output schema: a bare content-block list, not this tool's
        # actual payload. Join any text blocks so there's still something
        # to show rather than silently dropping the result.
        texts = [getattr(block, "text", None) for block in raw]
        payload = "\n".join(t for t in texts if t) or None
    else:
        structured = getattr(raw, "structuredContent", None)
        payload = structured if structured is not None else raw

    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]

    if isinstance(payload, dict) and "error" in payload:
        return {"ok": False, "tool": name, "error": payload["error"]}

    return {"ok": True, "tool": name, "data": payload}


def _truncate(envelope: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Cap the serialized size of a normalized result before it re-enters the
    model's context. `service_get_full_context` alone can embed 20 change
    events plus a full Traefik router blob — large enough to blow a small
    context window on its own if left unbounded.
    """
    if not envelope.get("ok"):
        return envelope
    serialized = json.dumps(envelope["data"], default=str)
    if len(serialized) <= max_chars:
        return envelope
    truncated = serialized[:max_chars]
    return {
        **envelope,
        "data": truncated,
        "truncated": True,
        "note": f"truncated to {max_chars} of {len(serialized)} characters",
    }


async def dispatch(
    mcp: FastMCP,
    name: str,
    arguments: dict[str, Any] | None,
    allowed: frozenset[str],
    *,
    max_result_chars: int,
) -> dict[str, Any]:
    """Call an MCP tool on the model's behalf, enforcing `allowed` first.

    A name outside `allowed` is rejected here even if it was somehow sent to
    the model (e.g. a stale allowlist snapshot) — this check, not the
    tools-list filter in `to_ollama_tools`, is the actual boundary. `name in
    DENY_ALWAYS` is checked independently of `allowed` too, so a caller that
    builds its allowed set some other way than `allowed_tool_names()` still
    can't dispatch a hard-denied tool.
    """
    if name not in allowed or name in DENY_ALWAYS:
        return {"ok": False, "tool": name, "error": f"tool {name!r} is not available in chat"}
    try:
        raw = await mcp.call_tool(name, arguments or {})
    except Exception as exc:  # a tool error must never crash the chat loop
        return {"ok": False, "tool": name, "error": f"{name} failed: {exc}"}
    return _truncate(normalize(name, raw), max_result_chars)
