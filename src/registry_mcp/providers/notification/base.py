"""NotificationProvider protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class NotificationProvider(Protocol):
    """Sends a short alert to the engineer. Pushes are server-to-server."""

    async def send(
        self, title: str, body: str, url: str | None = None, diff: str | None = None
    ) -> None: ...


@dataclass(frozen=True)
class ActionLink:
    """A button in an actionable email: its label, where it goes, its color."""

    label: str
    url: str
    color: str = "#0969da"


class NotificationDeliveryError(Exception):
    """An actionable message could not be sent. Unlike `send()`, whose failures
    are only logged, a caller waiting on a human's answer needs to know."""
