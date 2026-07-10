"""NotificationProvider protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class NotificationProvider(Protocol):
    """Sends a short alert to the engineer. Pushes are server-to-server."""

    async def send(
        self, title: str, body: str, url: str | None = None, diff: str | None = None
    ) -> None: ...
