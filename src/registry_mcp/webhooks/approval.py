"""Approve/Cancel links for Patchmon patch approvals (ADR-020).

Route map (registered only alongside the Patchmon webhook):
  GET  /patch/approve?token=...   confirmation page; changes nothing
  POST /patch/approve             consumes the token, starts the patch
  GET  /patch/cancel?token=...    confirmation page; changes nothing
  POST /patch/cancel              consumes the token, runs nothing

A GET never acts. Mail security scanners and link previewers fetch every URL
in a message before a person sees it, so a one-click GET approval would be
approved by the scanner. The page a GET returns carries a form that POSTs the
token back; only that POST moves the approval off `pending`.

These are the only browser-facing routes on this port. The token is the whole
credential (random, single-use, time-bound, stored hashed), so they sit outside
the /mcp transport-security check and must not sit behind a redirect-based
ForwardAuth either. Every page is sent no-store, no-referrer (the token is in
the URL), and unframeable (the confirm button is a clickjacking target).
"""

from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import parse_qs

from mcp.server.fastmcp import FastMCP
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from registry_mcp.logging import get_logger
from registry_mcp.models import PatchApproval, PatchApprovalStatus
from registry_mcp.patching import (
    ApprovalAction,
    PatchApprovalStore,
    PatchExecutor,
    TokenCheck,
    TokenState,
)
from registry_mcp.providers.notification import NotificationProvider
from registry_mcp.webhooks.common import read_capped

_log = get_logger("webhooks.approval")

APPROVE_PATH = "/patch/approve"
CANCEL_PATH = "/patch/cancel"
PATHS = {ApprovalAction.approve: APPROVE_PATH, ApprovalAction.cancel: CANCEL_PATH}

# A confirmation form posts one ~43-character token.
_MAX_FORM_BYTES = 4096

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
}

_STYLE = (
    "body{font-family:system-ui,sans-serif;background:#f6f8fa;color:#1f2328;margin:0;"
    "padding:16px}main{max-width:560px;margin:10vh auto;background:#fff;border:1px solid "
    "#d0d7de;border-radius:8px;padding:24px}h1{font-size:1.3em;margin-top:0}dl{display:grid;"
    "grid-template-columns:max-content 1fr;gap:4px 12px}dt{color:#57606a}dd{margin:0;"
    "overflow-wrap:anywhere}button{font-size:1em;padding:.6em 1.2em;border:0;border-radius:6px;"
    "color:#fff;cursor:pointer}.approve{background:#2da44e}.cancel{background:#cf222e}"
    ".note{color:#57606a;font-size:.9em}"
)

_RESOLVED_WORDS = {
    PatchApprovalStatus.approved: "approved, and the patch is running",
    PatchApprovalStatus.executed: "approved, and the patch ran",
    PatchApprovalStatus.failed: "approved, but the patch did not succeed",
    PatchApprovalStatus.cancelled: "cancelled",
    PatchApprovalStatus.undelivered: "closed",
}


def format_time(value: datetime | None) -> str:
    """A stored timestamp as UTC text. SQLite hands them back naive; they were
    written as UTC."""
    if value is None:
        return "unknown"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def subject(approval: PatchApproval) -> str:
    """What is being patched, in a few words."""
    return approval.service or approval.event.replace("_", " ")


def describe(approval: PatchApproval) -> list[tuple[str, str]]:
    """The facts a human needs to decide, in display order, omitting blanks."""
    rows = [
        ("Host", approval.target_host),
        ("Service", approval.service),
        ("Current version", approval.current_version),
        ("Target version", approval.target_version),
        ("Event", approval.event),
        ("Severity", approval.severity),
        ("Summary", approval.summary),
    ]
    return [(label, value) for label, value in rows if value]


def _page(
    title: str,
    paragraphs: list[str],
    *,
    status_code: int = 200,
    details: list[tuple[str, str]] | None = None,
    form: str = "",
    background: BackgroundTask | None = None,
) -> HTMLResponse:
    body = [f"<h1>{html.escape(title)}</h1>"]
    body.extend(f"<p>{html.escape(text)}</p>" for text in paragraphs)
    if details:
        body.append("<dl>")
        body.extend(
            f"<dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd>"
            for label, value in details
        )
        body.append("</dl>")
    body.append(form)
    document = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{''.join(body)}</main></body></html>"
    )
    return HTMLResponse(
        document, status_code=status_code, headers=_SECURITY_HEADERS, background=background
    )


def _status_page(found: TokenCheck) -> HTMLResponse:
    """The page for any token that can't act (anymore). Always says nothing ran."""
    approval = found.approval
    if found.state is TokenState.expired and approval is not None:
        return _page(
            "This link has expired",
            [
                f"The request to patch {subject(approval)} on {approval.target_host} expired "
                f"at {format_time(approval.expires_at)} without an answer.",
                "No action was taken.",
            ],
            status_code=410,
        )
    if found.state is TokenState.used and approval is not None:
        words = _RESOLVED_WORDS.get(approval.status, approval.status.value)
        return _page(
            "This link was already used",
            [
                f"The request to patch {subject(approval)} on {approval.target_host} was "
                f"already {words} ({format_time(approval.resolved_at)}).",
                "Each link works once. No further action was taken.",
            ],
            status_code=409,
        )
    return _page(
        "This link isn't valid",
        [
            "It may have been cut off or changed by your email client.",
            "No action was taken.",
        ],
        status_code=404,
    )


def _read_only_page() -> HTMLResponse:
    return _page(
        "The server is in read-only mode",
        [
            "Its startup health check failed, so it will not run patches. No action was taken.",
            "The link still works until it expires, once the server restarts healthy.",
        ],
        status_code=503,
    )


def _confirm_page(
    approval: PatchApproval, action: ApprovalAction, token: str, plan: str
) -> HTMLResponse:
    approve = action is ApprovalAction.approve
    # No `action` attribute: the form posts back to this same URL, whatever
    # prefix a reverse proxy serves it under.
    form = (
        "<form method='post'>"
        f"<input type='hidden' name='token' value='{html.escape(token, quote=True)}'>"
        f"<button type='submit' class='{'approve' if approve else 'cancel'}'>"
        f"{'Approve patch' if approve else 'Cancel request'}</button></form>"
    )
    if approve:
        paragraphs = [f"Confirming runs the patch now, using {plan}."]
    else:
        paragraphs = ["Confirming closes this request without running anything."]
    paragraphs.append(f"This request expires at {format_time(approval.expires_at)}.")
    return _page(
        f"{'Approve' if approve else 'Cancel'} patch for {subject(approval)} "
        f"on {approval.target_host}?",
        paragraphs,
        details=describe(approval),
        form=form,
    )


async def _form_token(request: Request) -> str:
    """The token from the posted form, or from the query string when the body
    has none (the form posts back to the URL it was served from)."""
    raw = await read_capped(request, _MAX_FORM_BYTES)
    if raw:
        fields = parse_qs(raw.decode("utf-8", errors="replace"), max_num_fields=10)
        if fields.get("token"):
            return fields["token"][0].strip()
    return request.query_params.get("token", "").strip()


def register_approval_routes(
    mcp: FastMCP,
    approvals: PatchApprovalStore,
    notifier: NotificationProvider,
    executor: PatchExecutor,
    *,
    read_only: bool,
) -> None:
    """Mount the Approve and Cancel routes. Called only by the Patchmon registrar,
    after its own fail-closed checks passed."""

    async def execute_and_report(approval: PatchApproval) -> None:
        result = await executor.execute(approval)
        try:
            approvals.record_outcome(
                approval.id, result.status, executed_via=result.executed_via, detail=result.detail
            )
        except Exception:
            # Still send the result email: the operator hears about it either way.
            _log.exception("patchmon_approval_outcome_not_recorded", approval_id=approval.id)
        ok = result.status is PatchApprovalStatus.executed
        lines = [
            f"Host: {approval.target_host}",
            *(f"{label}: {value}" for label, value in describe(approval)[1:]),
            f"Executed via: {result.executed_via or 'nothing (the playbook never started)'}",
            "",
            result.detail,
        ]
        await notifier.send(
            f"Patch {'completed' if ok else 'FAILED'}: {subject(approval)} on "
            f"{approval.target_host}",
            "\n".join(lines),
        )

    async def handle(request: Request, action: ApprovalAction) -> Response:
        try:
            if request.method in ("GET", "HEAD"):
                token = request.query_params.get("token", "").strip()
                found = approvals.check(token, action)
                if found.state is not TokenState.valid or found.approval is None:
                    return _status_page(found)
                if read_only:
                    return _read_only_page()
                return _confirm_page(found.approval, action, token, executor.plan(found.approval))

            if read_only:
                return _read_only_page()
            found = approvals.consume(await _form_token(request), action)
            if found.state is not TokenState.valid or found.approval is None:
                _log.info(
                    "patchmon_approval_link_refused",
                    action=action.value,
                    state=found.state.value,
                    approval_id=found.approval.id if found.approval else None,
                )
                return _status_page(found)

            approval = found.approval
            if action is ApprovalAction.cancel:
                _log.info(
                    "patchmon_approval_cancelled",
                    approval_id=approval.id,
                    target_host=approval.target_host,
                )
                return _page(
                    "Request cancelled",
                    [
                        f"The patch for {subject(approval)} on {approval.target_host} "
                        "will not run. No action was taken."
                    ],
                    details=describe(approval),
                )

            plan = executor.plan(approval)
            _log.info(
                "patchmon_approval_approved",
                approval_id=approval.id,
                target_host=approval.target_host,
                plan=plan,
            )
            return _page(
                "Patch approved",
                [
                    f"The patch for {subject(approval)} on {approval.target_host} has started, "
                    f"using {plan}.",
                    "You'll get an email with the result when it finishes.",
                ],
                details=describe(approval),
                # Runs after the page is sent: a playbook can take minutes, and
                # a proxy would time the browser out long before it finished.
                background=BackgroundTask(execute_and_report, approval),
            )
        except Exception:
            _log.exception("patchmon_approval_route_failed", action=action.value)
            return _page(
                "Something went wrong",
                ["The server hit an error handling this link. No action was taken."],
                status_code=500,
            )

    @mcp.custom_route(APPROVE_PATH, methods=["GET", "POST"])
    async def approve_patch(request: Request) -> Response:
        return await handle(request, ApprovalAction.approve)

    @mcp.custom_route(CANCEL_PATH, methods=["GET", "POST"])
    async def cancel_patch(request: Request) -> Response:
        return await handle(request, ApprovalAction.cancel)
