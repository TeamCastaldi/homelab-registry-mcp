"""Tests for the Dockhand payload models and their parsing helpers.

Pure unit tests — no HTTP, no database, no settings. The route-level behavior
these feed is covered separately in `test_dockhand_webhook.py`.
"""

import pytest

from registry_mcp.webhooks.schemas import (
    AlertKind,
    DockhandGenericAlert,
    DockhandStructuredAlert,
    parse_image_ref,
    severity_meets,
)

# --- parse_image_ref ---


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("lscr.io/linuxserver/plex:1.32.0", ("lscr.io/linuxserver/plex", "1.32.0")),
        ("nginx:latest", ("nginx", "latest")),
        ("nginx", ("nginx", "")),
        # The colon here is a registry port, not a tag separator.
        ("ghcr.io:5000/team/app", ("ghcr.io:5000/team/app", "")),
        ("ghcr.io:5000/team/app:2.1", ("ghcr.io:5000/team/app", "2.1")),
        # Digests pin an image but name no version a compose file could carry.
        ("repo/name@sha256:abc123", ("repo/name", "")),
        ("sha256:deadbeef", ("", "")),
        ("", ("", "")),
    ],
)
def test_parse_image_ref(ref, expected):
    assert parse_image_ref(ref) == expected


# --- severity_meets ---


def test_severity_meets_ladder():
    assert severity_meets("critical", "high")
    assert severity_meets("high", "high")
    assert not severity_meets("medium", "high")
    assert not severity_meets("low", "high")
    assert severity_meets("low", "low")
    assert not severity_meets(None, "high")


def test_unknown_severity_surfaces_rather_than_dropping():
    """A label the ladder doesn't know should reach a human, not vanish."""
    assert severity_meets("catastrophic", "high")


# --- DockhandStructuredAlert ---


def test_structured_update_normalizes():
    alert = DockhandStructuredAlert(
        event="update_available",
        container="plex",
        current_image="lscr.io/linuxserver/plex:1.32.0",
        latest_image="lscr.io/linuxserver/plex:1.32.1",
        server="workload-01",
    )
    result = alert.normalize()

    assert result.kind is AlertKind.image_update
    assert result.container == "plex"
    assert result.image == "lscr.io/linuxserver/plex"
    assert result.current_tag == "1.32.0"
    assert result.new_tag == "1.32.1"
    assert result.node == "workload-01"


def test_structured_update_without_target_tag_is_ignored():
    alert = DockhandStructuredAlert(
        event="update_available",
        container="plex",
        current_image="lscr.io/linuxserver/plex:1.32.0",
        latest_image="lscr.io/linuxserver/plex@sha256:abc",
    )
    result = alert.normalize()

    assert result.kind is AlertKind.ignored
    assert "digest-only" in result.reason


def test_structured_vulnerability_with_fix():
    alert = DockhandStructuredAlert(
        event="vulnerability_found",
        container="plex",
        current_image="lscr.io/linuxserver/plex:1.32.0",
        fixed_image="lscr.io/linuxserver/plex:1.32.2",
        severity="critical",
        cve_ids=["CVE-2026-1234"],
    )
    result = alert.normalize()

    assert result.kind is AlertKind.vulnerability
    assert result.new_tag == "1.32.2"
    assert result.cve_ids == ["CVE-2026-1234"]


def test_structured_vulnerability_below_threshold_is_ignored():
    alert = DockhandStructuredAlert(event="vulnerability_scan", container="plex", severity="low")
    result = alert.normalize(min_severity="high")

    assert result.kind is AlertKind.ignored
    assert "below" in result.reason


def test_vulnerability_classification_wins_over_update():
    """'update blocked by vulnerability scan' is a CVE finding, not a bump.

    Carries an image so this exercises classification precedence rather than
    tripping the separate no-image-reference guard.
    """
    alert = DockhandStructuredAlert(
        event="update blocked by vulnerability scan",
        container="plex",
        current_image="lscr.io/linuxserver/plex:1.32.0",
        severity="critical",
    )
    assert alert.normalize().kind is AlertKind.vulnerability


def test_structured_container_state_event_is_ignored():
    alert = DockhandStructuredAlert(event="container_started", container="plex")
    result = alert.normalize()

    assert result.kind is AlertKind.ignored
    assert "not actionable" in result.reason


def test_structured_ignores_unknown_keys():
    alert = DockhandStructuredAlert.model_validate(
        {
            "event": "update_available",
            "container": "plex",
            "latest_image": "plex:2",
            "some_future_field": {"nested": True},
        }
    )
    assert alert.normalize().new_tag == "2"


# --- DockhandGenericAlert ---


def test_generic_documented_payload_is_ignored_not_proposed():
    """The body Dockhand actually documents is digest-only.

    Nothing in `image=sha256:new old_image=sha256:old` names a version, so this
    must acknowledge and stop — inventing a tag here would open a wrong PR.
    """
    alert = DockhandGenericAlert(
        title="Container updated: c1",
        message="image=sha256:new old_image=sha256:old",
        agent="Dockhand",
    )
    result = alert.normalize()

    assert result.kind is AlertKind.ignored
    assert result.container == "c1"
    assert "digest-only" in result.reason


def test_generic_with_tags_normalizes():
    alert = DockhandGenericAlert(
        title="Container updated: plex",
        message=("image=lscr.io/linuxserver/plex:1.32.1 old_image=lscr.io/linuxserver/plex:1.32.0"),
    )
    result = alert.normalize()

    assert result.kind is AlertKind.image_update
    assert result.container == "plex"
    assert result.image == "lscr.io/linuxserver/plex"
    assert result.current_tag == "1.32.0"
    assert result.new_tag == "1.32.1"


def test_generic_bare_ref_fallback():
    """No key=value tokens: fall back to bare refs, newest written first."""
    alert = DockhandGenericAlert(
        title="Container updated: plex", message="plex:1.32.1 replaced plex:1.32.0"
    )
    result = alert.normalize()

    assert result.kind is AlertKind.image_update
    assert result.new_tag == "1.32.1"
    assert result.current_tag == "1.32.0"


def test_generic_vulnerability_collects_cves_and_severity():
    alert = DockhandGenericAlert(
        title="Vulnerability scan: plex",
        message="critical findings CVE-2026-1111 cve-2026-2222 image=plex:1.32.0",
    )
    result = alert.normalize()

    assert result.kind is AlertKind.vulnerability
    assert result.severity == "critical"
    assert result.cve_ids == ["CVE-2026-1111", "CVE-2026-2222"]


def test_generic_unrelated_title_is_ignored():
    alert = DockhandGenericAlert(title="Backup completed", message="all good")
    assert alert.normalize().kind is AlertKind.ignored


def test_structured_vulnerability_without_image_is_ignored():
    """No repository to anchor to — the notification and any patch would be blind."""
    alert = DockhandStructuredAlert(
        event="vulnerability_found", container="plex", severity="critical"
    )
    result = alert.normalize()

    assert result.kind is AlertKind.ignored
    assert "no image reference" in result.reason


def test_generic_vulnerability_without_image_is_ignored():
    alert = DockhandGenericAlert(
        title="Vulnerability scan: plex", message="critical CVE-2026-1111 found"
    )
    result = alert.normalize()

    assert result.kind is AlertKind.ignored
    assert "no image reference" in result.reason
