"""Gated Dockhand container and stack actions as MCP tools (ADR-021).

Three tools: two that only *request* an action and a `dockhand_action_confirm`
that runs it. A request stores exactly what will run and returns an `x + y = ?`
challenge; confirm runs the stored values, never anything supplied at confirm
time. None of the three exists unless `DOCKHAND_ALLOW_WRITE` is set and the
Dockhand API is configured, and all of them refuse while the server is in
read-only mode.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from registry_mcp.config import Settings
from registry_mcp.integrations.dockhand.client import (
    CONTAINER_ACTIONS,
    STACK_ACTIONS,
    DockhandClient,
    DockhandError,
    check_environment,
    check_path_id,
)
from registry_mcp.integrations.dockhand.gate import (
    DockhandActionGateError,
    DockhandActionGateStore,
    describe_action,
)
from registry_mcp.logging.events import get_logger

_log = get_logger("integrations.dockhand.actions")

_READ_ONLY_MODE = (
    "Server is in read-only mode (startup health check failed). "
    "Run system_health_check for details."
)
# A stack deploy can return a long log; the caller needs the outcome, not all of it.
_RESPONSE_MAX_CHARS = 4000


def _bounded(response: Any) -> Any:
    text = json.dumps(response, default=str)
    if len(text) <= _RESPONSE_MAX_CHARS:
        return response
    return {"truncated": True, "preview": text[:_RESPONSE_MAX_CHARS]}


def register_dockhand_action_tools(
    mcp: FastMCP,
    settings: Settings,
    gate: DockhandActionGateStore,
    *,
    read_only: bool,
) -> None:
    """Register the request/confirm tools, only when writes are enabled and
    Dockhand is configured."""
    if not (
        settings.dockhand_allow_write and settings.dockhand_api_url and settings.dockhand_token
    ):
        return

    def _challenge(row, *, warning: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "request_id": row.id,
            "action": describe_action(row),
            "challenge": f"{row.x} + {row.y} = ?",
            "expires_at": row.expires_at.isoformat(),
            "next_step": (
                f"Nothing has run. Show the user the action above, ask them to solve "
                f"{row.x} + {row.y}, then call dockhand_action_confirm("
                f"request_id={row.id!r}, answer=<their answer>)."
            ),
        }
        if warning:
            result["warning"] = warning
        return result

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    def dockhand_container_action_request(
        environment_id: str, container_id: str, action: str
    ) -> dict[str, Any]:
        """Request starting, stopping, or restarting one Dockhand container
        (`action` is start, stop, or restart). Runs nothing yet: returns an
        arithmetic challenge that must be solved and passed to
        dockhand_action_confirm. `environment_id` is a Dockhand environment id
        (see dockhand_list_environments)."""
        if read_only:
            return {"error": _READ_ONLY_MODE}
        try:
            if action not in CONTAINER_ACTIONS:
                raise DockhandError(
                    f"unsupported container action {action!r}; use one of "
                    f"{', '.join(sorted(CONTAINER_ACTIONS))}"
                )
            check_environment(environment_id)
            check_path_id(container_id, "container id")
        except DockhandError as exc:
            return {"error": str(exc)}
        row = gate.request(
            kind="container",
            environment_id=environment_id,
            target=container_id,
            action=action,
            actor="manual:dockhand_container_action_request",
            ttl_minutes=settings.dockhand_action_challenge_ttl_minutes,
        )
        return _challenge(row)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    def dockhand_stack_action_request(
        environment_id: str, stack_name: str, action: str, pull: bool = False
    ) -> dict[str, Any]:
        """Request starting, stopping, restarting, or redeploying a Dockhand
        stack by name (`action` is start, stop, restart, or redeploy). A
        redeploy recreates EVERY container in the stack; `pull=true` also pulls
        the images first, which can change what version runs. Runs nothing yet:
        returns an arithmetic challenge that must be solved and passed to
        dockhand_action_confirm."""
        if read_only:
            return {"error": _READ_ONLY_MODE}
        try:
            if action not in STACK_ACTIONS:
                raise DockhandError(
                    f"unsupported stack action {action!r}; use one of "
                    f"{', '.join(sorted(STACK_ACTIONS))}"
                )
            check_environment(environment_id)
            check_path_id(stack_name, "stack name")
        except DockhandError as exc:
            return {"error": str(exc)}
        row = gate.request(
            kind="stack",
            environment_id=environment_id,
            target=stack_name,
            action=action,
            pull=bool(pull) and action == "redeploy",
            actor="manual:dockhand_stack_action_request",
            ttl_minutes=settings.dockhand_action_challenge_ttl_minutes,
        )
        warning = None
        if action == "redeploy":
            warning = (
                "Every container in this stack will be recreated"
                + (", and its images pulled first" if row.pull else "")
                + ". The stack is unavailable until its containers are back."
            )
        elif action == "stop":
            warning = "Every container in this stack will be stopped."
        return _challenge(row, warning=warning)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def dockhand_action_confirm(request_id: str, answer: int) -> dict[str, Any]:
        """Run a Dockhand action by answering the math challenge from
        dockhand_container_action_request or dockhand_stack_action_request.
        Runs the action stored with the request, once. A wrong or expired
        answer invalidates the challenge: request the action again."""
        if read_only:
            return {"error": _READ_ONLY_MODE}
        try:
            row = gate.confirm(request_id, answer)
        except DockhandActionGateError as exc:
            return {"error": str(exc)}
        what = describe_action(row)
        client = DockhandClient(
            settings.dockhand_api_url,
            settings.dockhand_token.get_secret_value(),
            timeout=settings.dockhand_timeout_seconds,
            retries=settings.dockhand_retries,
            action_timeout=settings.dockhand_action_timeout_seconds,
        )
        try:
            if row.kind == "stack":
                outcome = await client.stack_action(
                    row.environment_id, row.target, row.action, pull=row.pull
                )
            else:
                outcome = await client.container_action(row.environment_id, row.target, row.action)
        except DockhandError as exc:
            gate.finish(row.id, ok=False, summary=str(exc))
            _log.warning("dockhand_action_failed", request_id=row.id, action=what, error=str(exc))
            return {"error": str(exc), "request_id": row.id, "action": what}
        except Exception:
            gate.finish(row.id, ok=False, summary="unexpected error before a result")
            raise
        gate.finish(row.id, ok=True, summary=f"HTTP {outcome['status_code']}")
        _log.info("dockhand_action_executed", request_id=row.id, action=what)
        return {
            "executed": True,
            "request_id": row.id,
            "action": what,
            "status_code": outcome["status_code"],
            "response": _bounded(outcome["response"]),
        }
