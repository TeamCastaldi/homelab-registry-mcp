"""Dockhand webhook receiver (ADR-010), registered via `@mcp.custom_route`.

Route map:
  POST /webhooks/dockhand    (path configurable via DOCKHAND_WEBHOOK_PATH)

Turns a Dockhand update or vulnerability alert into a staged `image_update` /
`vulnerability_scan` proposal through the existing proposal engine. Nothing
here mutates the registry or touches a container: the pull request and a human
merge remain the only path to a change, exactly as for every other write in
this server.

This revives the detection path ADR-006 removed with WUD, using push
notifications rather than ADR-004's unimplemented polling source — Dockhand
supplies the exact target tag, so the `ResolveLatestTag` reasoning gate that
design needed does not arise here.

Two response conventions worth knowing before editing:

* **Fail closed at registration.** Disabled, or enabled with no shared secret,
  leaves the route unmounted entirely (a real 404), rather than mounted and
  rejecting at request time. Same posture as `chat/routes.py`.
* **An unactionable alert answers 200.** A non-2xx makes Dockhand retry, so an
  unknown container or a non-update event is acknowledged with `{"skipped":
  ...}` / `{"ignored": ...}`. Only a *malformed* payload or a failed auth check
  earns a non-2xx, because those are worth retrying or fixing.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from registry_mcp.config import Settings
from registry_mcp.logging import get_logger
from registry_mcp.proposal import ProposalEngine
from registry_mcp.registry import RegistryStore
from registry_mcp.webhooks.schemas import (
    AlertKind,
    DockhandGenericAlert,
    DockhandStructuredAlert,
    NormalizedAlert,
)

_log = get_logger("webhooks.dockhand")


def _validation_detail(exc: ValidationError) -> list[dict[str, str]]:
    """Project a ValidationError into JSON-serializable detail.

    `ValidationError.errors()` can carry a `ctx` holding the original exception
    object, which `JSONResponse` cannot encode — serializing it raw would turn
    the 422 path into a 500.
    """
    detail = []
    for err in exc.errors():
        detail.append(
            {
                "loc": ".".join(str(part) for part in err.get("loc", ())),
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return detail


def _parse_alert(payload: Any, *, min_severity: str) -> tuple[NormalizedAlert | None, list[dict]]:
    """Validate `payload` as either Dockhand shape and normalize it.

    Returns `(alert, errors)`. The structured shape is tried first because it
    carries strictly more information; the generic one is the documented stock
    body. Errors from both are returned together so a 422 says why neither fit.
    """
    if not isinstance(payload, dict):
        return None, [{"loc": "", "msg": "expected a JSON object", "type": "type_error"}]

    errors: list[dict] = []
    for model in (DockhandStructuredAlert, DockhandGenericAlert):
        try:
            parsed = model.model_validate(payload)
        except ValidationError as exc:
            errors.extend(_validation_detail(exc))
            continue
        return parsed.normalize(min_severity=min_severity), []
    return None, errors


def register_webhook_routes(
    mcp: FastMCP,
    settings: Settings,
    store: RegistryStore,
    engine: ProposalEngine,
    *,
    read_only: bool,
) -> None:
    """Register the Dockhand webhook route, or do nothing when it isn't usable.

    "Usable" means `DOCKHAND_WEBHOOK_ENABLED=true` *and* a shared secret is
    configured. Enabled without a secret is a misconfiguration — logged and left
    unregistered, never an unauthenticated endpoint.
    """
    if not settings.dockhand_webhook_enabled:
        return

    secret = settings.dockhand_webhook_secret
    if not secret:
        _log.error(
            "dockhand_webhook_disabled_no_secret",
            detail=(
                "DOCKHAND_WEBHOOK_ENABLED=true but DOCKHAND_WEBHOOK_SECRET is not set; "
                "the webhook route will not be registered."
            ),
        )
        return

    max_body = settings.dockhand_webhook_max_body_bytes
    min_severity = settings.dockhand_webhook_vulnerability_min_severity
    vuln_enabled = settings.dockhand_webhook_vulnerability_enabled

    expected = secret.encode("utf-8")

    def _authorized(request: Request) -> bool:
        # Only a bearer-shaped Authorization counts; anything else (a stray
        # `Basic ...`) falls through to the token header rather than being
        # compared verbatim and blocking it.
        header = request.headers.get("authorization", "")
        provided = header[len("bearer ") :] if header.lower().startswith("bearer ") else ""
        if not provided:
            provided = request.headers.get("x-dockhand-token", "")
        if not provided:
            return False
        # Compare as bytes: compare_digest rejects non-ASCII str outright, and a
        # header with a stray non-ASCII byte should be unauthorized, not a 500.
        return hmac.compare_digest(provided.encode("utf-8"), expected)

    @mcp.custom_route(settings.dockhand_webhook_path, methods=["POST"])
    async def dockhand_webhook(request: Request) -> Response:
        try:
            if read_only:
                return JSONResponse(
                    {"error": "server is in read-only mode (startup health check failed)"},
                    status_code=403,
                )

            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_body:
                return JSONResponse({"error": "payload too large"}, status_code=413)

            if not _authorized(request):
                return JSONResponse({"error": "unauthorized"}, status_code=403)

            content_type = request.headers.get("content-type", "").split(";")[0].strip()
            if content_type != "application/json":
                return JSONResponse({"error": "expected application/json"}, status_code=400)

            raw = await request.body()
            # Re-checked against the real body: Content-Length is a claim, and a
            # chunked request may not send one at all.
            if len(raw) > max_body:
                return JSONResponse({"error": "payload too large"}, status_code=413)
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return JSONResponse({"error": "invalid JSON body"}, status_code=400)

            alert, errors = _parse_alert(payload, min_severity=min_severity)
            if alert is None:
                return JSONResponse(
                    {"error": "payload validation failed", "detail": errors}, status_code=422
                )

            if alert.kind is AlertKind.ignored:
                _log.info("dockhand_alert_ignored", container=alert.container, reason=alert.reason)
                return JSONResponse({"ignored": alert.reason, "container": alert.container})

            if alert.kind is AlertKind.vulnerability and not vuln_enabled:
                return JSONResponse(
                    {
                        "ignored": "vulnerability alerts disabled "
                        "(DOCKHAND_WEBHOOK_VULNERABILITY_ENABLED=false)",
                        "container": alert.container,
                    }
                )

            service = store.get_service(alert.container)
            if service is None:
                _log.info("dockhand_alert_unmatched", container=alert.container)
                return JSONResponse(
                    {"skipped": "no matching service", "container": alert.container}
                )

            _log.info(
                "dockhand_alert_received",
                container=alert.container,
                service_id=service.id,
                kind=alert.kind.value,
                image=alert.image,
                current_tag=alert.current_tag,
                new_tag=alert.new_tag,
                severity=alert.severity,
                cve_ids=alert.cve_ids,
            )

            if alert.kind is AlertKind.vulnerability:
                result = await engine.create_for_vulnerability(
                    service.id,
                    image=alert.image,
                    current_tag=alert.current_tag,
                    fixed_tag=alert.new_tag,
                    severity=alert.severity or "",
                    cve_ids=alert.cve_ids,
                )
            else:
                result = await engine.create_for_image_update(
                    service.id,
                    image=alert.image,
                    current_tag=alert.current_tag,
                    new_tag=alert.new_tag,
                )
            return JSONResponse(result)
        except Exception:
            # An inbound endpoint must never leak a traceback to its caller, and
            # a 500 body Dockhand can log beats a bare ASGI error page.
            _log.exception("dockhand_webhook_failed")
            return JSONResponse({"error": "internal error"}, status_code=500)

    _log.info(
        "dockhand_webhook_registered",
        path=settings.dockhand_webhook_path,
        vulnerability_enabled=vuln_enabled,
        min_severity=min_severity,
    )
