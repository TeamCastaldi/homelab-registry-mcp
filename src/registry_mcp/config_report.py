"""Configuration report: what's set, what a feature that's on still needs, and
environment keys that look meant for this server but match no setting.

A setting that's missing or misspelled fails quietly: `Settings` ignores keys
it doesn't know, and a half-configured feature just stays off. This report
makes both visible. It only ever names settings and never includes a value.
It's served by the `config_status` tool and printed by the
`registry-mcp-config-check` command.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from registry_mcp.config import Settings, get_settings

# How close an unknown key must be to a setting's name to count as a likely
# typo. One-character slips score above 0.9 (MCP_ALLOWED_HOST 0.97,
# NOTIFICATION_SMTP_PASS 0.92), while other tools' own variables score below
# it (GITHUB_TOKEN 0.86 against GIT_TOKEN, ANSIBLE_INVENTORY 0.87).
_TYPO_CUTOFF = 0.9

# Setting families of features that have since been removed. A key with one
# of these prefixes does nothing any more.
_RETIRED_PREFIXES = {
    "KOMODO_": "the Komodo integration was removed (ADR-011)",
    "CHAT_": "the /chat interface was removed (ADR-011)",
    "WUD_": "the WUD webhook was removed (ADR-006)",
}

# Settings that exist but that nothing reads yet.
_NO_EFFECT = {"infisical_allow_write": "reserved for a future write phase"}

_GIT = ("git_base_url", "git_token", "git_repo")


@dataclass(frozen=True)
class _Feature:
    """A feature, when it counts as on, and the settings it then needs.

    An entry in `needs` is a setting name, or `a|b` when either will do.
    """

    name: str
    on: Callable[[Settings], bool]
    needs: tuple[str, ...] = ()


# Mirrors the gates in the code: a feature listed here as needing a setting
# stays off, or fails every call, without it.
_FEATURES = (
    _Feature("Traefik discovery", lambda s: bool(s.traefik_api_url)),
    _Feature(
        "Authentik",
        lambda s: bool(s.authentik_api_url or s.authentik_token),
        ("authentik_api_url", "authentik_token"),
    ),
    _Feature(
        "Dockhand",
        lambda s: bool(s.dockhand_api_url or s.dockhand_token),
        ("dockhand_api_url", "dockhand_token"),
    ),
    _Feature("Docker discovery", lambda s: bool(s.docker_base_url)),
    _Feature(
        "documentation-mcp",
        lambda s: bool(s.docs_mcp_url or s.docs_mcp_token),
        ("docs_mcp_url", "docs_mcp_token"),
    ),
    _Feature("Reasoning layer (DSPy)", lambda s: s.dspy_enabled),
    _Feature("Git write path", lambda s: any(getattr(s, name) for name in _GIT), _GIT),
    _Feature(
        "ntfy notifications",
        lambda s: s.notification_provider == "ntfy",
        ("notification_url",),
    ),
    _Feature(
        "SMTP notifications",
        lambda s: s.notification_provider == "smtp",
        ("notification_smtp_host", "notification_from_email", "notification_to_email"),
    ),
    _Feature("Proposal auto-create", lambda s: s.proposal_auto_create, (*_GIT, "dspy_enabled")),
    _Feature(
        "PR comment polling",
        lambda s: s.proposal_comment_poll_enabled,
        (*_GIT, "dspy_enabled", "proposal_comment_allowed_users"),
    ),
    _Feature("Normalization", lambda s: s.normalization_enabled, _GIT),
    _Feature(
        "Brownfield adoption",
        lambda s: s.adoption_enabled,
        (*_GIT, "secrets_repo_path", "ssh_key_path", "dspy_enabled"),
    ),
    _Feature("Repo intake", lambda s: s.service_deploy_enabled),
    _Feature("Compose generation", lambda s: s.service_deploy_enabled, ("dspy_enabled",)),
    _Feature(
        "git-crypt secrets",
        lambda s: bool(s.secrets_enabled and s.secrets_repo_path),
        ("secrets_key_path|secrets_git_crypt_key",),
    ),
    _Feature(
        "Infisical",
        lambda s: s.infisical_enabled,
        (
            "infisical_base_url",
            "infisical_client_id",
            "infisical_client_secret",
            "infisical_project_id",
            "infisical_environment",
        ),
    ),
    _Feature(
        "Dockhand webhook",
        lambda s: s.dockhand_webhook_enabled,
        ("dockhand_webhook_secret", *_GIT),
    ),
    _Feature(
        "Hardware discovery",
        lambda s: bool(s.ansible_cfg_path or s.ssh_key_path),
        ("ansible_cfg_path", "ssh_key_path"),
    ),
    _Feature("Ansible inventory sync", lambda s: bool(s.ansible_inventory_path)),
)


def _env_name(setting: str) -> str:
    return setting.upper()


def _missing(settings: Settings, needs: tuple[str, ...]) -> list[str]:
    """The entries of `needs` that aren't set, as env names (`A or B` for either)."""
    missing = []
    for need in needs:
        options = need.split("|")
        if not any(getattr(settings, option) for option in options):
            missing.append(" or ".join(_env_name(option) for option in options))
    return missing


def _warnings(settings: Settings, environ: Mapping[str, str]) -> list[str]:
    warnings = []
    default_hosts = Settings.model_fields["mcp_allowed_hosts"].default
    if settings.mcp_transport != "stdio" and settings.mcp_allowed_hosts == default_hosts:
        warnings.append(
            "MCP_ALLOWED_HOSTS is the loopback-only default: a client that reaches /mcp "
            "through Traefik or the LAN gets HTTP 421"
        )
    if (
        settings.dspy_enabled
        and not settings.dspy_api_key
        and settings.dspy_model.startswith("anthropic/")
        and "ANTHROPIC_API_KEY" not in environ
    ):
        warnings.append(
            "Reasoning layer (DSPy) is on, but neither DSPY_API_KEY nor ANTHROPIC_API_KEY is set"
        )
    for setting, why in _NO_EFFECT.items():
        if setting in settings.model_fields_set:
            warnings.append(f"{_env_name(setting)} is set but has no effect ({why})")
    return warnings


def _unknown_keys(environ: Mapping[str, str]) -> list[dict[str, str]]:
    """Keys that match no setting but look meant for this server: a retired
    setting, or a near-miss of a real name. Other tools' variables are left out."""
    known = {_env_name(name) for name in Settings.model_fields}
    unknown = []
    for key in sorted({key.upper() for key in environ} - known):
        retired = next(
            (why for prefix, why in _RETIRED_PREFIXES.items() if key.startswith(prefix)), None
        )
        if retired:
            unknown.append({"key": key, "note": f"does nothing: {retired}"})
        elif close := get_close_matches(key, sorted(known), n=1, cutoff=_TYPO_CUTOFF):
            unknown.append({"key": key, "did_you_mean": close[0]})
    return unknown


def _same_as_default(settings: Settings) -> list[str]:
    """Settings given explicitly whose value is the default anyway."""
    redundant = []
    for name in sorted(settings.model_fields_set):
        field = Settings.model_fields.get(name)
        value = getattr(settings, name, None)
        if field is None or isinstance(value, SecretStr):
            continue
        if value == field.default:
            redundant.append(_env_name(name))
    return redundant


def build_report(settings: Settings, environ: Mapping[str, str]) -> dict[str, Any]:
    """The report for `settings`, loaded from `environ`. Names only, never values."""
    problems: list[str] = []
    features_on: list[str] = []
    for feature in _FEATURES:
        if not feature.on(settings):
            continue
        features_on.append(feature.name)
        if missing := _missing(settings, feature.needs):
            problems.append(f"{feature.name} is on but missing {', '.join(missing)}")
    problems.extend(_warnings(settings, environ))
    unknown = _unknown_keys(environ)
    return {
        "ok": not problems and not unknown,
        "problems": problems,
        "unknown_keys": unknown,
        "features_on": features_on,
        "set": sorted(_env_name(name) for name in settings.model_fields_set),
        "same_as_default": _same_as_default(settings),
    }


def process_environ(settings: Settings) -> dict[str, str]:
    """The keys `settings` could have read: the process environment plus the
    dotenv file, when one exists. Only the key names are used."""
    environ = dict(os.environ)
    env_file = settings.model_config.get("env_file")
    if isinstance(env_file, str) and Path(env_file).is_file():
        from dotenv import dotenv_values

        environ.update({key: "" for key in dotenv_values(env_file)})
    return environ


def render_text(report: dict[str, Any]) -> str:
    """The report as plain text, for the command line."""
    lines = ["Configuration report (setting names only, never values)", ""]

    def section(title: str, items: list[str]) -> None:
        lines.append(f"{title} ({len(items)}):")
        if items:
            lines.extend(f"  - {item}" for item in items)
        else:
            lines.append("  none")
        lines.append("")

    section("Problems", report["problems"])
    section(
        "Unrecognized keys",
        [
            f"{entry['key']}: did you mean {entry['did_you_mean']}?"
            if "did_you_mean" in entry
            else f"{entry['key']}: {entry['note']}"
            for entry in report["unknown_keys"]
        ],
    )
    section("Features on", report["features_on"])
    section("Set explicitly", report["set"])
    section("Set to the default anyway (safe to remove)", report["same_as_default"])
    lines.append("OK" if report["ok"] else "Needs attention")
    return "\n".join(lines)


def main() -> None:
    """`registry-mcp-config-check`: print the report; exit 1 if anything needs attention."""
    settings = get_settings()
    report = build_report(settings, process_environ(settings))
    print(render_text(report))
    sys.exit(0 if report["ok"] else 1)
