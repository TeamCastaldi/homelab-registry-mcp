"""Configuration report: what's set, what a feature that's on still needs, and
environment keys that look meant for this server but match no setting.

A setting that's missing or misspelled fails quietly: `Settings` ignores keys
it doesn't know, and a half-configured feature just stays off. This report
makes both visible, along with settings that do nothing where they are: set to
their default, set to an empty value, or set for a feature that's off. It only
ever names settings and never includes a value. It's served by the
`config_status` tool and printed by the `registry-mcp-config-check` command.
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
_GIT_CRYPT_KEY = "secrets_key_path|secrets_git_crypt_key"


@dataclass(frozen=True)
class _Feature:
    """A feature, when it counts as on, the settings it then needs, and the
    other settings that only matter while it's on.

    An entry in `needs` is a setting name, or `a|b` when either will do.
    """

    name: str
    on: Callable[[Settings], bool]
    needs: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()

    def settings(self) -> set[str]:
        """Every setting this feature reads, named in `needs` or `uses`."""
        return {name for need in self.needs for name in need.split("|")} | set(self.uses)


# Mirrors the gates in the code: a feature listed here as needing a setting
# stays off, or fails every call, without it. A setting listed under more than
# one feature is reported as unused only when all of them are off.
_FEATURES = (
    _Feature(
        "Traefik discovery",
        lambda s: bool(s.traefik_api_url),
        uses=("traefik_timeout_seconds", "traefik_retries", "discovery_traefik_interval_seconds"),
    ),
    _Feature(
        "Authentik",
        lambda s: bool(s.authentik_api_url or s.authentik_token),
        ("authentik_api_url", "authentik_token"),
        (
            "authentik_timeout_seconds",
            "authentik_retries",
            "discovery_authentik_interval_seconds",
        ),
    ),
    _Feature(
        "Dockhand",
        lambda s: bool(s.dockhand_api_url or s.dockhand_token),
        ("dockhand_api_url", "dockhand_token"),
        ("dockhand_timeout_seconds", "dockhand_retries", "discovery_dockhand_interval_seconds"),
    ),
    _Feature(
        "Docker discovery",
        lambda s: bool(s.docker_base_url),
        uses=("discovery_docker_interval_seconds",),
    ),
    _Feature(
        "documentation-mcp",
        lambda s: bool(s.docs_mcp_url or s.docs_mcp_token),
        ("docs_mcp_url", "docs_mcp_token"),
        ("docs_mcp_timeout_seconds",),
    ),
    _Feature(
        "Reasoning layer (DSPy)",
        lambda s: s.dspy_enabled,
        uses=(
            "dspy_model",
            "dspy_api_key",
            "dspy_confidence_threshold",
            "dspy_max_tokens",
            "dspy_patch_max_tokens",
            "dspy_compiled_path",
        ),
    ),
    _Feature(
        "Git write path",
        lambda s: any(getattr(s, name) for name in _GIT),
        _GIT,
        (
            "git_provider",
            "git_base_branch",
            "apply_mode",
            "proposal_dry_run",
            "proposal_stale_days",
            "proposal_confidence_threshold",
            "proposal_label",
            "proposal_compose_path_template",
        ),
    ),
    _Feature(
        "ntfy notifications",
        lambda s: s.notification_provider == "ntfy",
        ("notification_url",),
        ("notification_topic", "notification_token"),
    ),
    _Feature(
        "SMTP notifications",
        lambda s: s.notification_provider == "smtp",
        ("notification_smtp_host", "notification_from_email", "notification_to_email"),
        (
            "notification_smtp_port",
            "notification_smtp_username",
            "notification_smtp_password",
            "notification_smtp_use_tls",
        ),
    ),
    _Feature("Proposal auto-create", lambda s: s.proposal_auto_create, (*_GIT, "dspy_enabled")),
    _Feature(
        "PR comment polling",
        lambda s: s.proposal_comment_poll_enabled,
        (*_GIT, "dspy_enabled", "proposal_comment_allowed_users"),
        ("proposal_comment_poll_interval_seconds",),
    ),
    _Feature(
        "Normalization",
        lambda s: s.normalization_enabled,
        _GIT,
        (
            "normalization_schedule",
            "normalization_path_glob",
            "normalization_max_files_per_pr",
            "normalization_dry_run",
            "normalization_rename_misnamed",
            "normalization_label",
        ),
    ),
    _Feature(
        "Brownfield adoption",
        lambda s: s.adoption_enabled,
        (*_GIT, "secrets_repo_path", _GIT_CRYPT_KEY, "ssh_key_path", "dspy_enabled"),
        ("ssh_default_user", "adoption_draft_ttl_minutes"),
    ),
    _Feature(
        "Repo intake",
        lambda s: s.service_deploy_enabled,
        uses=(
            "service_deploy_confidence_threshold",
            "service_deploy_clone_timeout_seconds",
            "service_deploy_max_repo_mb",
        ),
    ),
    _Feature(
        "Compose generation",
        lambda s: s.service_deploy_enabled,
        ("dspy_enabled",),
        ("service_deploy_conventions_path",),
    ),
    _Feature(
        "git-crypt secrets",
        lambda s: bool(s.secrets_enabled and s.secrets_repo_path),
        (_GIT_CRYPT_KEY,),
        ("secrets_allow_decrypt",),
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
        ("infisical_secret_path", "infisical_recursive_scan", "infisical_max_folders"),
    ),
    _Feature(
        "Dockhand webhook",
        lambda s: s.dockhand_webhook_enabled,
        ("dockhand_webhook_secret", *_GIT),
        (
            "dockhand_webhook_path",
            "dockhand_webhook_max_body_bytes",
            "dockhand_webhook_vulnerability_enabled",
            "dockhand_webhook_vulnerability_min_severity",
            "dockhand_webhook_log_raw_payload",
        ),
    ),
    _Feature(
        "Hardware discovery",
        lambda s: bool(s.ansible_cfg_path or s.ssh_key_path),
        ("ansible_cfg_path", "ssh_key_path"),
        ("ssh_default_user",),
    ),
    _Feature(
        "Ansible inventory sync",
        lambda s: bool(s.ansible_inventory_path),
        uses=("ansible_inventory_write_challenge_ttl_minutes",),
    ),
)

# Settings some feature lists that still matter when every such feature is off:
# the startup health check reads the three paths whatever is on, and
# DSPY_ENABLED switches on the reasoning layer by itself. They're never reported
# as set for a feature that's off.
_READ_REGARDLESS = frozenset(
    {"secrets_repo_path", "ansible_cfg_path", "ssh_key_path", "dspy_enabled"}
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
    hosts = settings.mcp_allowed_hosts
    if settings.mcp_transport != "stdio":
        if hosts == Settings.model_fields["mcp_allowed_hosts"].default:
            warnings.append(
                "MCP_ALLOWED_HOSTS is the loopback-only default: a client that reaches /mcp "
                "through Traefik or the LAN gets HTTP 421"
            )
        elif not any(host.strip() for host in hosts.split(",")):
            warnings.append(
                "MCP_ALLOWED_HOSTS is set but names no host: every client that reaches /mcp "
                "gets HTTP 421"
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


def _for_features_off(
    settings: Settings, features_on: list[str], skip: set[str]
) -> list[dict[str, Any]]:
    """Settings given explicitly that only features that are off read."""
    readers: dict[str, list[str]] = {}
    for feature in _FEATURES:
        for name in sorted(feature.settings() - _READ_REGARDLESS):
            readers.setdefault(name, []).append(feature.name)
    on = set(features_on)
    unused = []
    for name in sorted(settings.model_fields_set):
        features = readers.get(name)
        if features and _env_name(name) not in skip and not on.intersection(features):
            unused.append({"key": _env_name(name), "features_off": features})
    return unused


def _is_empty(value: object) -> bool:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return isinstance(value, str) and not value.strip()


def _empty(settings: Settings, skip: set[str]) -> tuple[list[str], list[str]]:
    """Settings given explicitly as an empty value: those with no default of
    their own, which read as unset, and problems for those whose default the
    empty value replaces."""
    unset, problems = [], []
    for name in sorted(settings.model_fields_set):
        field = Settings.model_fields.get(name)
        # _warnings covers MCP_ALLOWED_HOSTS, with what an empty list does.
        if field is None or name == "mcp_allowed_hosts" or _env_name(name) in skip:
            continue
        if not _is_empty(getattr(settings, name, None)):
            continue
        if field.default:
            problems.append(f"{_env_name(name)} is set to an empty value, replacing its default")
        else:
            unset.append(_env_name(name))
    return unset, problems


def build_report(settings: Settings, environ: Mapping[str, str]) -> dict[str, Any]:
    """The report for `settings`, loaded from `environ`. Names only, never values.

    A setting that does nothing is listed once, under the first of: set to its
    default, set for a feature that's off, set to an empty value.
    """
    problems: list[str] = []
    features_on: list[str] = []
    for feature in _FEATURES:
        if not feature.on(settings):
            continue
        features_on.append(feature.name)
        if missing := _missing(settings, feature.needs):
            problems.append(f"{feature.name} is on but missing {', '.join(missing)}")
    same_as_default = _same_as_default(settings)
    for_features_off = _for_features_off(settings, features_on, set(same_as_default))
    empty, empty_problems = _empty(
        settings, {*same_as_default, *(entry["key"] for entry in for_features_off)}
    )
    problems.extend(empty_problems)
    problems.extend(_warnings(settings, environ))
    unknown = _unknown_keys(environ)
    return {
        "ok": not problems and not unknown,
        "problems": problems,
        "unknown_keys": unknown,
        "features_on": features_on,
        "set": sorted(_env_name(name) for name in settings.model_fields_set),
        "same_as_default": same_as_default,
        "for_features_off": for_features_off,
        "empty": empty,
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
    section(
        "Set for a feature that's off (unused until it's on)",
        [
            f"{entry['key']}: {_and(entry['features_off'])} "
            f"{'is' if len(entry['features_off']) == 1 else 'are'} off"
            for entry in report["for_features_off"]
        ],
    )
    section("Set to an empty value, which reads as unset (safe to remove)", report["empty"])
    lines.append("OK" if report["ok"] else "Needs attention")
    return "\n".join(lines)


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def main() -> None:
    """`registry-mcp-config-check`: print the report; exit 1 if anything needs attention."""
    settings = get_settings()
    report = build_report(settings, process_environ(settings))
    print(render_text(report))
    sys.exit(0 if report["ok"] else 1)
