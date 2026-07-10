"""Notification providers and their factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from registry_mcp.providers.notification.base import NotificationProvider
from registry_mcp.providers.notification.ntfy import NtfyNotificationProvider
from registry_mcp.providers.notification.null import NullNotificationProvider
from registry_mcp.providers.notification.smtp import SmtpNotificationProvider

if TYPE_CHECKING:
    from registry_mcp.config import Settings

__all__ = [
    "NotificationProvider",
    "NtfyNotificationProvider",
    "NullNotificationProvider",
    "SmtpNotificationProvider",
    "build_notification_provider",
]


def build_notification_provider(settings: Settings) -> NotificationProvider:
    """Construct the configured notification provider.

    Falls back to the null provider when ntfy/smtp is selected but not fully
    configured, so a half-set environment never crashes the server.
    """
    if settings.notification_provider == "ntfy" and settings.notification_url:
        return NtfyNotificationProvider(
            settings.notification_url,
            settings.notification_topic,
            token=settings.notification_token,
        )
    if (
        settings.notification_provider == "smtp"
        and settings.notification_smtp_host
        and settings.notification_from_email
        and settings.notification_to_email
    ):
        return SmtpNotificationProvider(
            settings.notification_smtp_host,
            settings.notification_smtp_port,
            settings.notification_from_email,
            settings.notification_to_email,
            username=settings.notification_smtp_username,
            password=settings.notification_smtp_password,
            use_tls=settings.notification_smtp_use_tls,
        )
    return NullNotificationProvider()
