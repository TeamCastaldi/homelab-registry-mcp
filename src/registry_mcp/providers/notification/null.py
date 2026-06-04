"""No-op notification provider, used when NOTIFICATION_PROVIDER=none."""

from __future__ import annotations

from registry_mcp.logging import get_logger

_log = get_logger("providers.notification")


class NullNotificationProvider:
    """Discards notifications (still logs them at debug for traceability)."""

    async def send(self, title: str, body: str, url: str | None = None) -> None:
        _log.debug("notification_suppressed", title=title, url=url)
