"""Patchmon webhook receiver (ADR-020), registered via `@mcp.custom_route`.

Route map:
  POST /webhooks/patchmon    (path configurable via PATCHMON_WEBHOOK_PATH)
  plus the Approve/Cancel links in `webhooks/approval.py`

Turns a verified PatchMon patch alert into a pending `PatchApproval` and an
email carrying single-use, time-bound Approve and Cancel links. The webhook
itself never runs anything: execution starts only when a human opens the
Approve link and confirms the page it shows.

Conventions, in the order a request meets them:

* **Fail closed at registration.** Disabled, or enabled without a signing
  secret, an approval base URL, an SMTP provider, or any execution path,
  leaves every route unmounted (a real 404). Approval links are bearer
  credentials, so they go by email only, never ntfy, whose topics can be
  readable by anyone who knows the name.
* **HMAC before anything else reads the body.** PatchMon signs the exact bytes
  it sends: `X-PatchMon-Signature: sha256=<hex HMAC-SHA256(secret, body)>`. A
  missing or wrong signature is a 401, checked with `hmac.compare_digest`
  before the body is parsed. Only the size cap comes first, so an unsigned
  sender can't make this server buffer an unbounded body to hash.
* **An unactionable alert answers 200**, like the Dockhand webhook: an event
  type not in PATCHMON_WEBHOOK_EVENTS, a host name that isn't a plain inventory
  name, or an alert that already has a pending approval. A non-2xx makes
  PatchMon retry. The one deliberate exception is an approval email that
  couldn't be sent (502): a retry then gets a fresh approval and another try.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from urllib.parse import urlencode, urlsplit

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from registry_mcp.config import Settings, reveal
from registry_mcp.logging import get_logger
from registry_mcp.models import PatchApproval, PatchApprovalStatus
from registry_mcp.patching import ApprovalAction, PatchApprovalStore, PatchExecutor
from registry_mcp.providers.notification import (
    ActionLink,
    NotificationDeliveryError,
    NotificationProvider,
    SmtpNotificationProvider,
)
from registry_mcp.webhooks.approval import (
    PATHS,
    describe,
    format_time,
    register_approval_routes,
    subject,
)
from registry_mcp.webhooks.common import declared_too_large, read_capped, validation_detail
from registry_mcp.webhooks.patchmon_schemas import (
    IgnoredAlert,
    PatchmonNativeAlert,
    PatchmonWebhookSchema,
)

_log = get_logger("webhooks.patchmon")

SIGNATURE_HEADER = "X-PatchMon-Signature"
_SIGNATURE_PREFIX = "sha256="


def signature_matches(header: str, body: bytes, key: bytes) -> bool:
    """Whether `header` is the HMAC-SHA256 of `body` under `key`.

    Accepts PatchMon's `sha256=<hex>` form and a bare hex digest, either case.
    Compared as bytes: `compare_digest` rejects a non-ASCII str outright, and a
    stray non-ASCII byte in a header should be a 401, not a 500.
    """
    provided = header.strip()
    if provided[: len(_SIGNATURE_PREFIX)].lower() == _SIGNATURE_PREFIX:
        provided = provided[len(_SIGNATURE_PREFIX) :]
    provided = provided.strip().lower()
    if not provided:
        return False
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("ascii"))


def _parse(payload: Any) -> tuple[BaseModel | None, list[dict[str, str]]]:
    """Validate `payload` as either accepted shape. Errors from both come back
    together so a 422 says why neither fit."""
    if not isinstance(payload, dict):
        return None, [{"loc": "", "msg": "expected a JSON object", "type": "type_error"}]
    errors: list[dict[str, str]] = []
    for model in (PatchmonWebhookSchema, PatchmonNativeAlert):
        try:
            return model.model_validate(payload), []
        except ValidationError as exc:
            errors.extend(validation_detail(exc))
    return None, errors


def _approval_base_url(value: str | None) -> str | None:
    """PATCHMON_APPROVAL_BASE_URL without a trailing slash, or None if unusable."""
    if not value or not value.strip():
        return None
    base = value.strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
        return None
    return base


def _events(value: str) -> frozenset[str]:
    return frozenset(item.strip().lower() for item in value.split(",") if item.strip())


def register_patchmon_routes(
    mcp: FastMCP,
    settings: Settings,
    approvals: PatchApprovalStore,
    notifier: NotificationProvider,
    executor: PatchExecutor,
    *,
    read_only: bool,
) -> bool:
    """Register the Patchmon webhook and its approval links, or nothing at all.

    Returns whether the routes were mounted. Every refusal is logged at error
    level with the setting that caused it; none of them leaves a route open.
    """
    if not settings.patchmon_webhook_enabled:
        return False

    def refuse(detail: str) -> bool:
        _log.error("patchmon_webhook_disabled", detail=f"{detail}; no routes were registered.")
        return False

    # `.strip()`: a whitespace-only secret is an unset one.
    secret = (reveal(settings.patchmon_webhook_secret) or "").strip()
    if not secret:
        return refuse("PATCHMON_WEBHOOK_ENABLED=true but PATCHMON_WEBHOOK_SECRET is not set")
    base_url = _approval_base_url(settings.patchmon_approval_base_url)
    if base_url is None:
        return refuse(
            "PATCHMON_APPROVAL_BASE_URL must be the http(s) URL the approval emails link to"
        )
    if not isinstance(notifier, SmtpNotificationProvider):
        return refuse(
            "approval links are sent by email only: set NOTIFICATION_PROVIDER=smtp with "
            "NOTIFICATION_SMTP_HOST, NOTIFICATION_FROM_EMAIL, and NOTIFICATION_TO_EMAIL"
        )
    if not executor.can_execute:
        return refuse(
            "an approval would have nothing to run: set PATCHMON_CALLBACK_URL, "
            "PATCHMON_ANSIBLE_PLAYBOOK, or both"
        )
    if urlsplit(base_url).scheme != "https":
        _log.warning(
            "patchmon_approval_links_not_https",
            detail="Approval links are bearer credentials; over plain http they can be read "
            "by anything on the path between the browser and this server.",
        )

    key = secret.encode("utf-8")
    max_body = settings.patchmon_webhook_max_body_bytes
    ttl_minutes = settings.patchmon_approval_ttl_minutes
    events = _events(settings.patchmon_webhook_events)
    # Narrowed above; the name keeps the type visible below.
    mailer: SmtpNotificationProvider = notifier

    def link(action: ApprovalAction, token: str) -> str:
        return f"{base_url}{PATHS[action]}?{urlencode({'token': token})}"

    @mcp.custom_route(settings.patchmon_webhook_path, methods=["POST"])
    async def patchmon_webhook(request: Request) -> Response:
        try:
            if read_only:
                return JSONResponse(
                    {"error": "server is in read-only mode (startup health check failed)"},
                    status_code=403,
                )
            if declared_too_large(request, max_body):
                return JSONResponse({"error": "payload too large"}, status_code=413)
            raw = await read_capped(request, max_body)
            if raw is None:
                return JSONResponse({"error": "payload too large"}, status_code=413)

            if not signature_matches(request.headers.get(SIGNATURE_HEADER, ""), raw, key):
                _log.warning(
                    "patchmon_webhook_bad_signature",
                    signature_present=bool(request.headers.get(SIGNATURE_HEADER)),
                )
                return JSONResponse(
                    {"error": "invalid or missing signature"},
                    status_code=401,
                    headers={"WWW-Authenticate": f'HMAC-SHA256 header="{SIGNATURE_HEADER}"'},
                )

            content_type = request.headers.get("content-type", "").split(";")[0].strip()
            if content_type != "application/json":
                return JSONResponse({"error": "expected application/json"}, status_code=400)
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return JSONResponse({"error": "invalid JSON body"}, status_code=400)

            parsed, errors = _parse(payload)
            if parsed is None:
                return JSONResponse(
                    {"error": "payload validation failed", "detail": errors}, status_code=422
                )
            event = parsed.event if isinstance(parsed, PatchmonWebhookSchema) else parsed.event_type
            if event not in events:
                return JSONResponse(
                    {"ignored": f"event {event!r} is not in PATCHMON_WEBHOOK_EVENTS"}
                )
            alert = parsed.normalize()
            if isinstance(alert, IgnoredAlert):
                _log.info("patchmon_alert_ignored", patchmon_event=event, reason=alert.reason)
                return JSONResponse({"ignored": alert.reason})

            existing = approvals.find_pending(
                event=alert.event,
                target_host=alert.target_host,
                service=alert.service,
                target_version=alert.target_version,
            )
            if existing is not None:
                return JSONResponse(
                    {
                        "skipped": "an approval for this alert is already pending",
                        "approval_id": existing.id,
                        "expires_at": format_time(existing.expires_at),
                    }
                )

            issued = approvals.create(
                PatchApproval(
                    event=alert.event,
                    target_host=alert.target_host,
                    service=alert.service,
                    current_version=alert.current_version,
                    target_version=alert.target_version,
                    severity=alert.severity,
                    patchmon_host_id=alert.patchmon_host_id,
                    summary=alert.summary,
                    payload_callback_url=alert.callback_url,
                ),
                ttl_minutes,
            )
            approval = issued.approval
            plan = executor.plan(approval)
            expires = format_time(approval.expires_at)
            body = "\n".join(
                [
                    "PatchMon reported a patch that is waiting for your decision.",
                    "",
                    *(f"{label}: {value}" for label, value in describe(approval)),
                    f"If approved, runs: {plan}",
                ]
            )
            footer = (
                f"Each link works once and expires at {expires} ({ttl_minutes} minutes). "
                "Opening a link shows a confirmation page; nothing runs until you confirm "
                "there. If you didn't expect this email, cancel it."
            )
            try:
                await mailer.send_actionable(
                    f"Approve patch: {subject(approval)} on {approval.target_host}",
                    body,
                    [
                        ActionLink(
                            "Approve",
                            link(ApprovalAction.approve, issued.approve_token),
                            "#2da44e",
                        ),
                        ActionLink(
                            "Cancel", link(ApprovalAction.cancel, issued.cancel_token), "#cf222e"
                        ),
                    ],
                    footer=footer,
                )
            except NotificationDeliveryError as exc:
                approvals.record_outcome(
                    approval.id, PatchApprovalStatus.undelivered, detail=str(exc)
                )
                _log.error(
                    "patchmon_approval_email_failed", approval_id=approval.id, error=str(exc)
                )
                return JSONResponse(
                    {"error": "approval email could not be sent", "approval_id": approval.id},
                    status_code=502,
                )

            _log.info(
                "patchmon_approval_requested",
                approval_id=approval.id,
                patchmon_event=approval.event,
                target_host=approval.target_host,
                service=approval.service,
                target_version=approval.target_version,
                expires_at=expires,
            )
            return JSONResponse(
                {
                    "status": "pending_approval",
                    "approval_id": approval.id,
                    "expires_at": expires,
                    "execution": plan,
                },
                status_code=202,
            )
        except Exception:
            # Never a traceback to the caller; a structured 500 PatchMon can log.
            _log.exception("patchmon_webhook_failed")
            return JSONResponse({"error": "internal error"}, status_code=500)

    register_approval_routes(mcp, approvals, notifier, executor, read_only=read_only)
    _log.info(
        "patchmon_webhook_registered",
        path=settings.patchmon_webhook_path,
        events=sorted(events),
        ttl_minutes=ttl_minutes,
        callback_configured=bool(settings.patchmon_callback_url),
        ansible_fallback_configured=bool(settings.patchmon_ansible_playbook),
    )
    return True
