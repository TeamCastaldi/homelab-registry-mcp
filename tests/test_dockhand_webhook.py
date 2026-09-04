"""Tests for the Dockhand webhook listener (ADR-010): gating, validation, dispatch.

The route is mounted via FastMCP's `custom_route` on the same Starlette app as
the MCP session, so it's exercised end-to-end with an ASGI client rather than by
calling the handler function directly — the registration gating is the point, and
only a real request through the app proves an unmounted route 404s.
"""

import httpx
import structlog.testing

from conftest import IsolatedSettings
from registry_mcp.models import Service
from registry_mcp.registry import RegistryStore
from registry_mcp.server import build_server

WEBHOOK_PATH = "/webhooks/dockhand"
SECRET = "test-secret"


def _healthy_settings(tmp_path, db_path, **overrides):
    """Startup health checks all pass (read_only=False) so the webhook route's
    own gates (enabled flag, secret) are what's under test, not the read-only
    gate — same pattern as test_proposal_tools.py's `_healthy_server`."""
    repo = tmp_path / "homelab"
    (repo / ".git").mkdir(parents=True)
    ansible_cfg = tmp_path / "ansible.cfg"
    ansible_cfg.write_text("")
    ssh_key = tmp_path / "id_ed25519"
    ssh_key.write_text("")
    base = dict(
        registry_db_path=db_path,
        dockhand_webhook_enabled=True,
        dockhand_webhook_secret=SECRET,
        secrets_repo_path=str(repo),
        ansible_cfg_path=str(ansible_cfg),
        ssh_key_path=str(ssh_key),
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
    )
    base.update(overrides)
    return IsolatedSettings(**base)


def _payload(
    container="plex",
    event="update_available",
    current="lscr.io/linuxserver/plex:1.32.0",
    latest="lscr.io/linuxserver/plex:1.32.1",
):
    return {
        "event": event,
        "container": container,
        "current_image": current,
        "latest_image": latest,
        "server": "workload-01",
    }


def _auth(token=SECRET):
    return {"Authorization": f"Bearer {token}"}


async def _post(server, path=WEBHOOK_PATH, headers=None, **kwargs):
    transport = httpx.ASGITransport(app=server.streamable_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(path, headers=headers, **kwargs)


# --- registration gating ---


async def test_disabled_returns_404(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"), dockhand_webhook_enabled=False)
    resp = await _post(build_server(settings), json=_payload(), headers=_auth())
    assert resp.status_code == 404


async def test_enabled_without_secret_is_never_mounted(tmp_path):
    """Fail closed: a missing secret leaves no endpoint at all, not an open one."""
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"), dockhand_webhook_secret=None)
    resp = await _post(build_server(settings), json=_payload(), headers=_auth())
    assert resp.status_code == 404


async def test_custom_path_is_honored(tmp_path):
    settings = _healthy_settings(
        tmp_path, str(tmp_path / "r.db"), dockhand_webhook_path="/api/v1/webhooks/dockhand"
    )
    server = build_server(settings)
    stale = await _post(server, path=WEBHOOK_PATH, json=_payload(), headers=_auth())
    assert stale.status_code == 404
    resp = await _post(server, path="/api/v1/webhooks/dockhand", json=_payload(), headers=_auth())
    assert resp.status_code == 200


# --- auth ---


async def test_missing_token_returns_403(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(build_server(settings), json=_payload())
    assert resp.status_code == 403


async def test_wrong_token_returns_403(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(build_server(settings), json=_payload(), headers=_auth("wrong"))
    assert resp.status_code == 403


async def test_x_dockhand_token_header_accepted(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(
        build_server(settings),
        json=_payload(container="ghost"),
        headers={"X-Dockhand-Token": SECRET},
    )
    assert resp.status_code == 200


async def test_read_only_returns_403(tmp_path):
    """No ansible.cfg/ssh key => startup health check fails => read-only."""
    settings = IsolatedSettings(
        registry_db_path=str(tmp_path / "r.db"),
        dockhand_webhook_enabled=True,
        dockhand_webhook_secret=SECRET,
    )
    resp = await _post(build_server(settings), json=_payload(), headers=_auth())
    assert resp.status_code == 403
    assert "read-only" in resp.json()["error"]


# --- request validation ---


async def test_wrong_content_type_returns_400(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(
        build_server(settings),
        content=b"event=update",
        headers={**_auth(), "content-type": "text/plain"},
    )
    assert resp.status_code == 400


async def test_invalid_json_returns_400(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(
        build_server(settings),
        content=b"{not json",
        headers={**_auth(), "content-type": "application/json"},
    )
    assert resp.status_code == 400


async def test_oversized_body_returns_413(tmp_path):
    settings = _healthy_settings(
        tmp_path, str(tmp_path / "r.db"), dockhand_webhook_max_body_bytes=64
    )
    resp = await _post(
        build_server(settings),
        json={"event": "update_available", "container": "x" * 500},
        headers=_auth(),
    )
    assert resp.status_code == 413


async def test_unrecognized_shape_returns_422_with_serializable_detail(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(build_server(settings), json={"totally": "unrelated"}, headers=_auth())

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "payload validation failed"
    # Must be plain JSON — a raw ValidationError.errors() ctx would 500 here.
    assert all({"loc", "msg", "type"} == set(item) for item in body["detail"])


async def test_non_object_json_returns_422(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(build_server(settings), json=["not", "an", "object"], headers=_auth())
    assert resp.status_code == 422


# --- unactionable alerts are acknowledged, never retried ---


async def test_container_state_event_is_ignored_with_200(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(
        build_server(settings), json=_payload(event="container_started"), headers=_auth()
    )
    assert resp.status_code == 200
    assert "ignored" in resp.json()


async def test_digest_only_generic_payload_is_ignored_not_proposed(tmp_path):
    """Dockhand's documented generic body names no version — acknowledge, don't guess."""
    db_path = str(tmp_path / "r.db")
    store = RegistryStore(db_path)
    store.create_service(Service(name="c1", display_name="C1", host="workload-01"))

    settings = _healthy_settings(tmp_path, db_path)
    resp = await _post(
        build_server(settings),
        json={
            "title": "Container updated: c1",
            "message": "image=sha256:new old_image=sha256:old",
            "agent": "Dockhand",
        },
        headers=_auth(),
    )

    assert resp.status_code == 200
    assert "digest-only" in resp.json()["ignored"]


async def test_unknown_container_is_skipped_not_errored(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(build_server(settings), json=_payload(container="ghost"), headers=_auth())

    assert resp.status_code == 200
    assert resp.json()["skipped"] == "no matching service"


async def test_vulnerability_disabled_is_ignored(tmp_path):
    settings = _healthy_settings(
        tmp_path, str(tmp_path / "r.db"), dockhand_webhook_vulnerability_enabled=False
    )
    resp = await _post(
        build_server(settings),
        json={
            "event": "vulnerability_found",
            "container": "plex",
            "severity": "critical",
            "current_image": "plex:1.0",
        },
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert "vulnerability alerts disabled" in resp.json()["ignored"]


# --- dispatch ---


async def test_known_container_reaches_proposal_engine(tmp_path):
    db_path = str(tmp_path / "r.db")
    store = RegistryStore(db_path)
    store.create_service(
        Service(name="plex", display_name="Plex", host="workload-01", auth_mode_conflict=False)
    )

    settings = _healthy_settings(tmp_path, db_path)
    resp = await _post(build_server(settings), json=_payload(), headers=_auth())

    assert resp.status_code == 200
    body = resp.json()
    # git_base_url/git_token/git_repo are all set (see _healthy_settings), so
    # engine.configured is True and this reaches a real GitProvider.read_file
    # call against a fake host — it fails downstream (not "write path not
    # configured"), which is what matters here: the image_update finding
    # reached the engine rather than being skipped for an unmatched container.
    assert "skipped" not in body
    assert "error" in body
    assert "write path not configured" not in body["error"]


async def test_non_bearer_authorization_falls_through_to_token_header(tmp_path):
    """A stray `Basic ...` must not shadow a valid X-Dockhand-Token."""
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(
        build_server(settings),
        json=_payload(container="ghost"),
        headers={"Authorization": "Basic dXNlcjpwYXNz", "X-Dockhand-Token": SECRET},
    )
    assert resp.status_code == 200


async def test_non_ascii_token_is_unauthorized_not_a_500(tmp_path):
    """Starlette decodes headers as latin-1, so a raw non-ASCII byte arrives as a
    non-ASCII str — which `compare_digest` on str rejects with TypeError. That
    must be a 403, not an unhandled 500. Sent as bytes because httpx (correctly)
    refuses to encode a non-ASCII str header itself."""
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(
        build_server(settings),
        json=_payload(),
        headers={b"X-Dockhand-Token": b"s\xe9cret"},
    )
    assert resp.status_code == 403


async def test_token_with_surrounding_whitespace_is_accepted(tmp_path):
    """A secret pasted into a config UI easily picks up a trailing newline."""
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(
        build_server(settings),
        json=_payload(container="ghost"),
        headers={"X-Dockhand-Token": f"  {SECRET}\t"},
    )
    assert resp.status_code == 200


async def test_bearer_token_with_trailing_whitespace_is_accepted(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    resp = await _post(
        build_server(settings),
        json=_payload(container="ghost"),
        headers={"Authorization": f"Bearer {SECRET}  "},
    )
    assert resp.status_code == 200


async def test_whitespace_only_secret_is_treated_as_unset(tmp_path):
    """Fail closed: it must not register a route nothing could authorize."""
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"), dockhand_webhook_secret="   ")
    resp = await _post(build_server(settings), json=_payload(), headers=_auth())
    assert resp.status_code == 404


async def test_vulnerability_without_image_is_ignored_not_errored(tmp_path):
    db_path = str(tmp_path / "r.db")
    store = RegistryStore(db_path)
    store.create_service(Service(name="plex", display_name="Plex", host="workload-01"))

    settings = _healthy_settings(tmp_path, db_path)
    resp = await _post(
        build_server(settings),
        json={"event": "vulnerability_found", "container": "plex", "severity": "critical"},
        headers=_auth(),
    )

    assert resp.status_code == 200
    assert "no image reference" in resp.json()["ignored"]


# --- Apprise json:// delivery shape (what production actually sends) ---


def _apprise_payload(title="Container updated: plex", message="", type_="info"):
    """The body Apprise's json:// posts — Dockhand's webhook channel takes
    Apprise-style URLs, so this shape, not the structured one, is what arrives."""
    return {"version": "1.0", "title": title, "message": message, "type": type_}


async def test_apprise_json_payload_shape_is_accepted(tmp_path):
    """version/type are unknown to both models; title/message still parse.

    The structured model must fail first (no event/container) and the generic
    one must take it, with `extra="ignore"` dropping version/type.
    """
    db_path = str(tmp_path / "r.db")
    store = RegistryStore(db_path)
    store.create_service(Service(name="plex", display_name="Plex", host="workload-01"))

    settings = _healthy_settings(tmp_path, db_path)
    resp = await _post(
        build_server(settings),
        json=_apprise_payload(message="image=sha256:new old_image=sha256:old"),
        headers=_auth(),
    )

    assert resp.status_code == 200
    assert "digest-only" in resp.json()["ignored"]


async def test_apprise_payload_with_tags_reaches_the_engine(tmp_path):
    db_path = str(tmp_path / "r.db")
    store = RegistryStore(db_path)
    store.create_service(Service(name="plex", display_name="Plex", host="workload-01"))

    settings = _healthy_settings(tmp_path, db_path)
    resp = await _post(
        build_server(settings),
        json=_apprise_payload(
            message=(
                "image=lscr.io/linuxserver/plex:1.32.1 old_image=lscr.io/linuxserver/plex:1.32.0"
            )
        ),
        headers=_auth(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "ignored" not in body and "skipped" not in body


# --- raw payload logging (DOCKHAND_WEBHOOK_LOG_RAW_PAYLOAD) ---


def _raw_lines(logs):
    return [entry for entry in logs if entry.get("event") == "dockhand_webhook_raw_payload"]


async def test_raw_payload_logged_when_enabled(tmp_path):
    settings = _healthy_settings(
        tmp_path, str(tmp_path / "r.db"), dockhand_webhook_log_raw_payload=True
    )
    server = build_server(settings)

    with structlog.testing.capture_logs() as logs:
        await _post(server, json=_apprise_payload(message="hello"), headers=_auth())

    entries = _raw_lines(logs)
    assert len(entries) == 1
    assert "hello" in entries[0]["body"]
    assert entries[0]["content_type"] == "application/json"


async def test_raw_payload_not_logged_by_default(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    server = build_server(settings)

    with structlog.testing.capture_logs() as logs:
        await _post(server, json=_apprise_payload(), headers=_auth())

    assert _raw_lines(logs) == []


async def test_raw_payload_is_truncated(tmp_path):
    settings = _healthy_settings(
        tmp_path,
        str(tmp_path / "r.db"),
        dockhand_webhook_log_raw_payload=True,
        dockhand_webhook_max_body_bytes=1_000_000,
    )
    server = build_server(settings)

    with structlog.testing.capture_logs() as logs:
        await _post(server, json=_apprise_payload(message="x" * 5000), headers=_auth())

    assert len(_raw_lines(logs)[0]["body"]) == 2000


async def test_raw_payload_logged_even_when_content_type_is_wrong(tmp_path):
    """The whole point: a bad content type 400s, and without this the operator
    would have nothing to look at."""
    settings = _healthy_settings(
        tmp_path, str(tmp_path / "r.db"), dockhand_webhook_log_raw_payload=True
    )
    server = build_server(settings)

    with structlog.testing.capture_logs() as logs:
        resp = await _post(
            server,
            content=b'{"title":"hi"}',
            headers={**_auth(), "content-type": "text/plain"},
        )

    assert resp.status_code == 400
    entries = _raw_lines(logs)
    assert len(entries) == 1
    assert entries[0]["content_type"] == "text/plain"


async def test_raw_payload_not_logged_for_unauthorized_caller(tmp_path):
    """Logging sits after auth so an unauthenticated caller can never write to it."""
    settings = _healthy_settings(
        tmp_path, str(tmp_path / "r.db"), dockhand_webhook_log_raw_payload=True
    )
    server = build_server(settings)

    with structlog.testing.capture_logs() as logs:
        resp = await _post(server, json=_apprise_payload(), headers=_auth("wrong"))

    assert resp.status_code == 403
    assert _raw_lines(logs) == []
