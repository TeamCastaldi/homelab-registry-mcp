"""Tests for notification providers and their factory."""

import smtplib

import httpx

from conftest import IsolatedSettings
from registry_mcp.providers.notification import (
    NtfyNotificationProvider,
    NullNotificationProvider,
    SmtpNotificationProvider,
    build_notification_provider,
)


async def test_null_provider_is_noop():
    # Should not raise and should accept the full signature.
    await NullNotificationProvider().send("title", "body", "https://x", diff="diff")


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
    await provider.send("Hello", "World", "https://pr.test/1", diff="ignored for push")
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


class _FakeSMTP:
    """Stands in for smtplib.SMTP: records what would have been sent."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_args = None
        self.sent_message = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self, context=None):
        self.starttls_called = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.sent_message = message


async def test_smtp_sends_html_and_plain_with_diff_and_buttons(monkeypatch):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    provider = SmtpNotificationProvider(
        "smtp.test", 587, "bot@test", "you@test", username="user", password="pass"
    )
    await provider.send("Hello", "World", "https://pr.test/1", diff="line1\nline2")

    sent_smtp = _FakeSMTP.instances[0]
    assert sent_smtp.starttls_called is True
    assert sent_smtp.login_args == ("user", "pass")

    message = sent_smtp.sent_message
    assert message["Subject"] == "Hello"
    assert message["From"] == "bot@test"
    assert message["To"] == "you@test"

    plain = message.get_body(preferencelist=("plain",)).get_content()
    html_body = message.get_body(preferencelist=("html",)).get_content()
    assert "World" in plain
    assert "line1" in plain
    assert "line1" in html_body
    assert "Approve" in html_body
    assert "Request Changes" in html_body
    assert "https://pr.test/1/files" in html_body


async def test_smtp_swallows_failure(monkeypatch):
    class _FailingSMTP(_FakeSMTP):
        def __enter__(self):
            raise smtplib.SMTPException("boom")

    monkeypatch.setattr(smtplib, "SMTP", _FailingSMTP)
    provider = SmtpNotificationProvider("smtp.test", 587, "bot@test", "you@test")
    # A failed send must never raise.
    await provider.send("Hello", "World")


def test_factory_builds_smtp_when_configured():
    settings = IsolatedSettings(
        notification_provider="smtp",
        notification_smtp_host="smtp.test",
        notification_from_email="bot@test",
        notification_to_email="you@test",
    )
    assert isinstance(build_notification_provider(settings), SmtpNotificationProvider)


def test_factory_falls_back_to_null_when_smtp_incomplete():
    settings = IsolatedSettings(notification_provider="smtp", notification_smtp_host="smtp.test")
    assert isinstance(build_notification_provider(settings), NullNotificationProvider)
