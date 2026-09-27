"""SMTP notification provider — templated HTML email per proposal event.

Validated in production against SMTP2GO; any standard SMTP relay with
STARTTLS works. Uses stdlib smtplib/email only — no new dependency — and
runs the blocking send in a thread so it never stalls the event loop.
"""

from __future__ import annotations

import asyncio
import html
import smtplib
import ssl
from collections.abc import Sequence
from email.message import EmailMessage

from registry_mcp.logging import get_logger
from registry_mcp.providers.notification.base import ActionLink, NotificationDeliveryError

_log = get_logger("providers.notification")

# Keep the email body bounded — a runaway patch shouldn't produce a
# multi-megabyte message.
_MAX_DIFF_CHARS = 20_000

_BUTTON_STYLE = (
    "display:inline-block; margin:0.5em 0.5em 0.5em 0; padding:0.6em 1.2em; "
    "border-radius:6px; text-decoration:none; color:#fff;"
)


class SmtpNotificationProvider:
    """Sends a templated HTML email. A send failure is logged, never raised —
    a missed notification must not abort a proposal."""

    def __init__(
        self,
        host: str,
        port: int,
        from_addr: str,
        to_addr: str,
        *,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._from = from_addr
        self._to = to_addr
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._timeout = timeout

    async def send(
        self, title: str, body: str, url: str | None = None, diff: str | None = None
    ) -> None:
        try:
            message = self._build_message(title, body, url, diff)
            await asyncio.to_thread(self._send_sync, message)
        except Exception as exc:  # a missed notification must not abort a proposal
            _log.warning("notification_failed", title=title, error=str(exc))

    def _build_message(
        self, title: str, body: str, url: str | None, diff: str | None
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self._from
        message["To"] = self._to

        plain_parts = [body]
        if diff:
            plain_parts.append(f"\n--- diff ---\n{diff[:_MAX_DIFF_CHARS]}")
        if url:
            plain_parts.append(f"\n{url}")
        message.set_content("\n".join(plain_parts))

        message.add_alternative(self._render_html(title, body, url, diff), subtype="html")
        return message

    def _render_html(self, title: str, body: str, url: str | None, diff: str | None) -> str:
        parts = [
            "<html><body style='font-family: sans-serif;'>",
            f"<h2>{html.escape(title)}</h2>",
            f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>",
        ]
        if diff:
            truncated = diff[:_MAX_DIFF_CHARS]
            note = "" if len(diff) <= _MAX_DIFF_CHARS else "<p><em>(diff truncated)</em></p>"
            parts.append(
                "<pre style='background:#f4f4f4; padding:1em; overflow-x:auto;'>"
                f"{html.escape(truncated)}</pre>{note}"
            )
        if url:
            # GitHub/Gitea have no separate deep link for "click to approve"
            # vs "click to request changes" — both actions live in the same
            # Review dialog on the PR page, so both buttons open it.
            safe_url = html.escape(url.rstrip("/"), quote=True)
            button = _BUTTON_STYLE
            parts.append(
                f'<p><a href="{safe_url}" style="{button} background:#2da44e;">'
                f"Approve &amp; Merge</a>"
                f'<a href="{safe_url}" style="{button} background:#cf222e;">'
                f"Request Changes</a>"
                f'<a href="{safe_url}/files" style="{button} background:#0969da;">'
                f"View Diff</a></p>"
            )
        parts.append("</body></html>")
        return "".join(parts)

    async def send_actionable(
        self,
        title: str,
        body: str,
        actions: Sequence[ActionLink],
        *,
        footer: str | None = None,
    ) -> None:
        """Send an email whose buttons are `actions`, e.g. Approve and Cancel.

        Raises `NotificationDeliveryError` on failure rather than logging it
        away like `send()`: the caller is waiting on the recipient's answer,
        and an email that never arrived means no answer will ever come.
        """
        try:
            message = self._build_actionable(title, body, actions, footer)
            await asyncio.to_thread(self._send_sync, message)
        except Exception as exc:
            raise NotificationDeliveryError(f"{type(exc).__name__}: {exc}") from exc

    def _build_actionable(
        self, title: str, body: str, actions: Sequence[ActionLink], footer: str | None
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self._from
        message["To"] = self._to

        plain = [body, ""]
        plain.extend(f"{action.label}: {action.url}" for action in actions)
        if footer:
            plain.extend(["", footer])
        message.set_content("\n".join(plain))

        buttons = "".join(
            f'<a href="{html.escape(action.url, quote=True)}" '
            f'style="{_BUTTON_STYLE} background:{html.escape(action.color, quote=True)};">'
            f"{html.escape(action.label)}</a>"
            for action in actions
        )
        parts = [
            "<html><body style='font-family: sans-serif;'>",
            f"<h2>{html.escape(title)}</h2>",
            f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>",
            f"<p>{buttons}</p>",
        ]
        if footer:
            parts.append(
                f"<p style='color:#57606a; font-size:0.9em;'>"
                f"{html.escape(footer).replace(chr(10), '<br>')}</p>"
            )
        parts.append("</body></html>")
        message.add_alternative("".join(parts), subtype="html")
        return message

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
            if self._use_tls:
                client.starttls(context=ssl.create_default_context())
            if self._username:
                client.login(self._username, self._password or "")
            client.send_message(message)
