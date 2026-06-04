"""Ntfy notification provider — HTTP POST to a topic."""

from __future__ import annotations

import httpx

from registry_mcp.logging import get_logger

_log = get_logger("providers.notification")


class NtfyNotificationProvider:
    """Publishes to an Ntfy topic. A send failure is logged, never raised — a
    missed notification must not abort a proposal."""

    def __init__(
        self,
        base_url: str,
        topic: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._topic = topic
        self._token = token
        self._timeout = timeout
        self._transport = transport

    async def send(self, title: str, body: str, url: str | None = None) -> None:
        headers = {"Title": title}
        if url:
            # Tapping the notification opens the PR.
            headers["Click"] = url
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base}/{self._topic}",
                    content=body.encode("utf-8"),
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            _log.warning("notification_failed", title=title, error=str(exc))
