"""Tests for notification providers and their factory."""

import httpx

from conftest import IsolatedSettings
from registry_mcp.providers.notification import (
    NtfyNotificationProvider,
    NullNotificationProvider,
    build_notification_provider,
)


async def test_null_provider_is_noop():
    # Should not raise and should accept the full signature.
    await NullNotificationProvider().send("title", "body", "https://x")


async def test_ntfy_posts_to_topic_with_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["title"] = request.headers.get("Title")
        seen["click"] = request.headers.get("Click")
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "1"})

    provider = NtfyNotificationProvider(
        "https://ntfy.test", "homelab", token="sekret", transport=httpx.MockTransport(handler)
    )
    await provider.send("Hello", "World", "https://pr.test/1")
    assert seen["url"] == "https://ntfy.test/homelab"
    assert seen["title"] == "Hello"
    assert seen["click"] == "https://pr.test/1"
    assert seen["auth"] == "Bearer sekret"
    assert seen["body"] == "World"


async def test_ntfy_swallows_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    provider = NtfyNotificationProvider(
        "https://ntfy.test", "homelab", transport=httpx.MockTransport(handler)
    )
    # A failed push must never raise.
    await provider.send("Hello", "World")


def test_factory_defaults_to_null():
    assert isinstance(build_notification_provider(IsolatedSettings()), NullNotificationProvider)


def test_factory_builds_ntfy_when_configured():
    settings = IsolatedSettings(notification_provider="ntfy", notification_url="https://ntfy.test")
    assert isinstance(build_notification_provider(settings), NtfyNotificationProvider)


def test_factory_falls_back_to_null_when_url_missing():
    settings = IsolatedSettings(notification_provider="ntfy")
    assert isinstance(build_notification_provider(settings), NullNotificationProvider)
