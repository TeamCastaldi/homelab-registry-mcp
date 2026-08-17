"""The chat request/response loop: turns one posted conversation into a
stream of Server-Sent Events by driving Ollama and, when it asks to, the MCP
tool bridge — round-tripping between the two until the model stops calling
tools or `CHAT_MAX_TOOL_ROUNDS` is hit.

No conversation state is held here or anywhere server-side: the caller
(`registry_mcp.chat.routes`) passes in the client-supplied transcript plus
the new user turn, and the final `done` event hands back only the messages
this turn produced, for the browser to append to its own copy. See
`registry_mcp.chat.__init__` for why this module never touches a
store/engine directly either — everything about the lab comes back through
`bridge.dispatch`, the same MCP call surface any other client uses.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.chat import bridge, context, persona
from registry_mcp.chat.ollama import OllamaClient, OllamaError
from registry_mcp.config import Settings
from registry_mcp.logging import get_logger

_log = get_logger("chat.agent")

_STATS_KEYS = ("eval_count", "eval_duration", "prompt_eval_count", "prompt_eval_duration")


def format_sse(event: str, data: dict[str, Any]) -> bytes:
    """Format one Server-Sent Events frame. Exported so `routes.py` can emit
    a matching frame (e.g. a "busy" error) before `run_chat` ever starts.
    """
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Ollama returns `arguments` as a parsed object; guard for a JSON
    string anyway since not every build/model is consistent about it."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


async def run_chat(
    mcp: FastMCP,
    settings: Settings,
    *,
    allowed_tools: frozenset[str],
    history: list[dict[str, Any]],
) -> AsyncIterator[bytes]:
    """Drive one chat turn end to end, yielding SSE frames as they're ready.

    `history` is the client-supplied prior transcript plus the new user
    turn, already capped to `CHAT_MAX_HISTORY_MESSAGES` and stripped of any
    client-supplied `system` role by the caller — the system prompt here is
    always server-derived, never taken from the request body.
    """
    assert settings.chat_ollama_url, "run_chat requires CHAT_OLLAMA_URL to be configured"
    ollama = OllamaClient(
        settings.chat_ollama_url,
        model=settings.chat_ollama_model,
        timeout=settings.chat_ollama_timeout_seconds,
        retries=settings.chat_ollama_retries,
    )
    tool_specs = await bridge.to_ollama_tools(mcp, allowed_tools)
    system_prompt = persona.build_system_prompt(settings)
    context_pack = await context.build_context_pack(mcp, settings)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context_pack},
        *history,
    ]
    initial_len = len(messages)

    yield format_sse(
        "open",
        {
            "model": settings.chat_ollama_model,
            "tools": len(tool_specs),
            "write": bool(bridge.WRITE_TOOLS & allowed_tools),
        },
    )

    options = {"num_ctx": settings.chat_num_ctx, "temperature": settings.chat_temperature}

    for round_num in range(settings.chat_max_tool_rounds):
        content = ""
        tool_calls: list[dict[str, Any]] = []
        stats: dict[str, Any] = {}
        try:
            async for chunk in ollama.chat_stream(
                messages,
                tools=tool_specs,
                think=settings.chat_think,
                options=options,
                keep_alive=settings.chat_ollama_keep_alive,
            ):
                message = chunk.get("message") or {}
                if thinking := message.get("thinking"):
                    yield format_sse("thinking", {"delta": thinking})
                if delta := message.get("content"):
                    content += delta
                    yield format_sse("token", {"delta": delta})
                if calls := message.get("tool_calls"):
                    tool_calls.extend(calls)
                if chunk.get("done"):
                    stats = {k: chunk[k] for k in _STATS_KEYS if k in chunk}
        except OllamaError as exc:
            _log.warning("chat_ollama_stream_failed", error=str(exc))
            yield format_sse("error", {"kind": "ollama_unreachable", "message": str(exc)})
            return

        assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        messages.append(assistant_message)

        if not tool_calls:
            yield format_sse("done", {"messages": messages[initial_len:], "stats": stats})
            return

        for call in tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name", ""))
            arguments = _parse_tool_arguments(function.get("arguments"))
            call_id = str(call.get("id") or f"r{round_num}-{len(messages)}")

            yield format_sse("tool_call", {"id": call_id, "name": name, "arguments": arguments})
            result = await bridge.dispatch(
                mcp,
                name,
                arguments,
                allowed_tools,
                max_result_chars=settings.chat_tool_result_max_chars,
            )
            yield format_sse(
                "tool_result",
                {
                    "id": call_id,
                    "ok": bool(result.get("ok")),
                    "truncated": bool(result.get("truncated")),
                },
            )
            messages.append(
                {"role": "tool", "tool_name": name, "content": json.dumps(result, default=str)}
            )

    _log.warning("chat_tool_round_limit_hit", max_rounds=settings.chat_max_tool_rounds)
    yield format_sse(
        "error",
        {
            "kind": "tool_round_limit",
            "message": "Reached the maximum number of tool rounds for this turn.",
        },
    )
