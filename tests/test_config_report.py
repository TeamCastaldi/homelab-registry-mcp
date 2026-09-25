"""The configuration report: names what's missing or mistyped, never a value."""

import json

import pytest

from conftest import IsolatedSettings, tool_payload
from registry_mcp import config_report
from registry_mcp.config_report import build_report
from registry_mcp.server import build_server

ALLOWED = {"mcp_allowed_hosts": "registry-mcp.example.com"}


def report(environ=None, **settings):
    return build_report(IsolatedSettings(**{**ALLOWED, **settings}), environ or {})


def test_a_feature_that_is_on_names_the_settings_it_still_needs():
    result = report(infisical_enabled=True, infisical_base_url="https://i.lan")
    assert "Infisical" in result["features_on"]
    assert result["problems"] == [
        "Infisical is on but missing INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, "
        "INFISICAL_PROJECT_ID, INFISICAL_ENVIRONMENT"
    ]
    assert result["ok"] is False


def test_either_of_two_settings_satisfies_an_or_requirement():
    missing = report(secrets_repo_path="/opt/homelab")
    assert missing["problems"] == [
        "git-crypt secrets is on but missing SECRETS_KEY_PATH or SECRETS_GIT_CRYPT_KEY"
    ]
    assert report(secrets_repo_path="/opt/homelab", secrets_git_crypt_key="a2V5")["ok"]


def test_a_complete_configuration_is_ok():
    result = report(
        traefik_api_url="http://traefik.lan:8080",
        authentik_api_url="https://auth.lan/api/v3",
        authentik_token="t",
        git_base_url="https://git.lan",
        git_token="t",
        git_repo="owner/homelab",
        normalization_enabled=True,
    )
    assert result["ok"] is True
    assert result["problems"] == []
    assert result["features_on"] == [
        "Traefik discovery",
        "Authentik",
        "Git write path",
        "Normalization",
    ]


def test_a_feature_needing_the_reasoning_layer_reports_it_off():
    result = report(
        git_base_url="https://git.lan", git_token="t", git_repo="o/r", adoption_enabled=True
    )
    assert result["problems"] == [
        "Brownfield adoption is on but missing SECRETS_REPO_PATH, "
        "SECRETS_KEY_PATH or SECRETS_GIT_CRYPT_KEY, SSH_KEY_PATH, DSPY_ENABLED"
    ]


def test_loopback_only_allowed_hosts_is_flagged_on_http_but_not_stdio():
    http = build_report(IsolatedSettings(), {})
    assert any(
        p.startswith("MCP_ALLOWED_HOSTS is the loopback-only default") for p in http["problems"]
    )
    stdio = build_report(IsolatedSettings(mcp_transport="stdio"), {})
    assert not [p for p in stdio["problems"] if "MCP_ALLOWED_HOSTS" in p]


@pytest.mark.parametrize(
    ("environ", "settings", "flagged"),
    [
        ({}, {}, True),
        ({"ANTHROPIC_API_KEY": "x"}, {}, False),
        ({}, {"dspy_api_key": "x"}, False),
        ({}, {"dspy_model": "openai/gpt-5"}, False),
    ],
)
def test_reasoning_layer_without_an_api_key_is_flagged(environ, settings, flagged):
    result = report(environ, dspy_enabled=True, **settings)
    assert any("DSPY_API_KEY" in p for p in result["problems"]) is flagged


def test_a_setting_with_no_effect_is_flagged_when_set():
    assert (
        "INFISICAL_ALLOW_WRITE is set but has no effect"
        in report(infisical_allow_write=True)["problems"][0]
    )


def test_unknown_keys_name_typos_and_retired_settings():
    result = report({"MCP_ALLOWED_HOST": "", "INFISICAL_CLIENTID": "", "KOMODO_API_URL": ""})
    assert result["unknown_keys"] == [
        {"key": "INFISICAL_CLIENTID", "did_you_mean": "INFISICAL_CLIENT_ID"},
        {
            "key": "KOMODO_API_URL",
            "note": "does nothing: the Komodo integration was removed (ADR-011)",
        },
        {"key": "MCP_ALLOWED_HOST", "did_you_mean": "MCP_ALLOWED_HOSTS"},
    ]
    assert result["ok"] is False


@pytest.mark.parametrize(
    "key",
    [
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "ANSIBLE_INVENTORY",
        "ANSIBLE_CONFIG",
        "GIT_SSL_CAINFO",
        "DOCKER_HOST",
        "SSH_AUTH_SOCK",
        "ANTHROPIC_API_KEY",
        "REGISTRY_MCP_VERSION",
        "PATH",
    ],
)
def test_other_tools_variables_are_not_reported(key):
    assert report({key: ""})["unknown_keys"] == []


def test_settings_set_to_their_default_are_listed_but_secrets_never_are():
    result = report(mcp_port=8765, log_level="DEBUG", git_token="t")
    assert result["same_as_default"] == ["MCP_PORT"]
    assert "GIT_TOKEN" in result["set"]


def test_settings_for_a_feature_that_is_off_are_listed():
    result = report(
        notification_provider="smtp",
        notification_smtp_host="smtp.lan",
        notification_from_email="from@example.com",
        notification_to_email="to@example.com",
        notification_url="https://ntfy.lan",
        notification_token="t",
    )
    assert result["for_features_off"] == [
        {"key": "NOTIFICATION_TOKEN", "features_off": ["ntfy notifications"]},
        {"key": "NOTIFICATION_URL", "features_off": ["ntfy notifications"]},
    ]
    assert result["ok"] is True
    assert "NOTIFICATION_URL: ntfy notifications is off" in config_report.render_text(result)


def test_a_shared_setting_is_unused_only_when_every_feature_reading_it_is_off():
    unused = report(ssh_default_user="admin")["for_features_off"]
    assert unused == [
        {"key": "SSH_DEFAULT_USER", "features_off": ["Brownfield adoption", "Hardware discovery"]}
    ]
    assert "SSH_DEFAULT_USER: Brownfield adoption and Hardware discovery are off" in (
        config_report.render_text(report(ssh_default_user="admin"))
    )
    in_use = report(ssh_default_user="admin", ansible_cfg_path="/a.cfg", ssh_key_path="/k")
    assert in_use["for_features_off"] == []


def test_settings_the_health_check_reads_are_never_unused():
    result = report(secrets_enabled=False, secrets_repo_path="/opt/homelab")
    assert "git-crypt secrets" not in result["features_on"]
    assert result["for_features_off"] == []


def test_every_feature_names_real_settings():
    named = {name for feature in config_report._FEATURES for name in feature.settings()}
    assert named | config_report._READ_REGARDLESS <= set(IsolatedSettings.model_fields)


def test_an_empty_value_with_no_default_reads_as_unset():
    result = report(dspy_enabled=True, dspy_api_key="k", dspy_compiled_path="", dspy_model="a/b")
    assert result["empty"] == ["DSPY_COMPILED_PATH"]
    assert result["ok"] is True
    assert report(dspy_enabled=True, dspy_api_key="  ", dspy_model="a/b")["empty"] == [
        "DSPY_API_KEY"
    ]


def test_an_empty_value_that_replaces_a_default_is_a_problem():
    result = report(
        git_base_url="https://git.lan", git_token="t", git_repo="o/r", git_base_branch=""
    )
    assert result["problems"] == ["GIT_BASE_BRANCH is set to an empty value, replacing its default"]
    assert result["empty"] == []


def test_a_setting_that_does_nothing_is_listed_once():
    result = report(normalization_label="", normalization_schedule="0 7 * * wed,sat")
    assert result["same_as_default"] == ["NORMALIZATION_SCHEDULE"]
    assert result["for_features_off"] == [
        {"key": "NORMALIZATION_LABEL", "features_off": ["Normalization"]}
    ]
    assert result["empty"] == []
    assert result["problems"] == []


@pytest.mark.parametrize("hosts", ["", " ", " , "])
def test_allowed_hosts_naming_no_host_is_flagged_once_and_not_on_stdio(hosts):
    http = build_report(IsolatedSettings(mcp_allowed_hosts=hosts), {})
    assert http["problems"] == [
        "MCP_ALLOWED_HOSTS is set but names no host: every client that reaches /mcp gets HTTP 421"
    ]
    stdio = build_report(IsolatedSettings(mcp_allowed_hosts=hosts, mcp_transport="stdio"), {})
    assert stdio["problems"] == []


def test_the_report_never_contains_a_value():
    values = {
        "authentik_token": "value-authentik-token",
        "authentik_api_url": "https://value-authentik-url.lan",
        "git_token": "value-git-token",
        "infisical_client_secret": "value-infisical-secret",
        "infisical_enabled": True,
        "notification_url": "https://value-ntfy-url.lan",
    }
    rendered = json.dumps(report({"MCP_ALLOWED_HOST": "value-typo-host"}, **values))
    rendered += config_report.render_text(report(**values))
    assert "value-" not in rendered


async def test_config_status_tool_serves_the_report(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = tool_payload(await server.call_tool("config_status", {}))
    assert {
        "ok",
        "problems",
        "unknown_keys",
        "features_on",
        "set",
        "same_as_default",
        "for_features_off",
        "empty",
    } <= set(result)


@pytest.mark.parametrize(("settings", "code"), [({}, 1), (ALLOWED, 0)])
def test_cli_exits_non_zero_when_anything_needs_attention(monkeypatch, capsys, settings, code):
    monkeypatch.setattr(config_report, "get_settings", lambda: IsolatedSettings(**settings))
    monkeypatch.setattr(config_report, "process_environ", lambda _settings: {})
    with pytest.raises(SystemExit) as exited:
        config_report.main()
    assert exited.value.code == code
    assert "setting names only, never values" in capsys.readouterr().out
