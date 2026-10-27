"""Credential settings stay out of anything that prints the settings object."""

import base64

import pytest

from conftest import IsolatedSettings
from registry_mcp.config import Settings, reveal
from registry_mcp.gitcrypt import key_bytes
from registry_mcp.providers.git import build_git_provider
from registry_mcp.providers.notification import build_notification_provider

# Every credential setting, each with a value that is easy to spot if it leaks.
CREDENTIALS = {
    "authentik_token": "leak-authentik-token",
    "dockhand_token": "leak-dockhand-token",
    "docs_mcp_token": "leak-docs-mcp-token",
    "dspy_api_key": "leak-dspy-api-key",
    "git_token": "leak-git-token",
    "notification_token": "leak-ntfy-token",
    "notification_smtp_password": "leak-smtp-password",
    "secrets_git_crypt_key": base64.b64encode(b"leak-git-crypt-key").decode(),
    "infisical_client_secret": "leak-infisical-secret",
    "dockhand_webhook_secret": "leak-webhook-secret",
}


@pytest.fixture
def settings():
    return IsolatedSettings(
        git_base_url="https://git.test",
        git_repo="owner/homelab",
        notification_provider="ntfy",
        notification_url="https://ntfy.test",
        **CREDENTIALS,
    )


@pytest.mark.parametrize("render", [repr, str, lambda s: s.model_dump_json()])
def test_no_credential_appears_when_settings_are_printed(settings, render):
    printed = render(settings)
    assert not [name for name, value in CREDENTIALS.items() if value in printed]


def test_each_consumer_still_gets_the_real_value(settings):
    assert build_git_provider(settings)._token == "leak-git-token"
    assert build_notification_provider(settings)._token == "leak-ntfy-token"
    assert key_bytes(settings) == b"leak-git-crypt-key"


def test_reveal_passes_an_unset_credential_through_as_none(settings):
    assert reveal(IsolatedSettings().git_token) is None
    assert reveal(settings.git_token) == "leak-git-token"


def test_mcp_auth_is_off_until_turned_on(monkeypatch):
    assert IsolatedSettings().mcp_auth_enabled is False
    monkeypatch.setenv("MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_AUTH_AUDIENCE", "registry-mcp-client")
    loaded = Settings(_env_file=None)
    assert loaded.mcp_auth_enabled is True
    assert loaded.mcp_auth_audience == "registry-mcp-client"
