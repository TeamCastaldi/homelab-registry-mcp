"""Tests for the WUD webhook listener (ADR-005): auth gating and proposal creation.

The route is mounted via FastMCP's `custom_route` on the same Starlette app as the
MCP session, so it's exercised end-to-end with an ASGI client rather than by calling
the handler function directly.
"""

import httpx

from conftest import IsolatedSettings
from registry_mcp.models import Service
from registry_mcp.registry import RegistryStore
from registry_mcp.server import build_server

WEBHOOK_PATH = "/webhooks/wud"
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
        wud_webhook_enabled=True,
        wud_webhook_secret=SECRET,
        secrets_repo_path=str(repo),
        ansible_cfg_path=str(ansible_cfg),
        ssh_key_path=str(ssh_key),
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
    )
    base.update(overrides)
    return IsolatedSettings(**base)


def _payload(name="plex", image="lscr.io/linuxserver/plex", current="1.32.0", new="1.32.1"):
    return {
        "container": {
            "name": name,
            "image": {"name": image, "tag": {"value": current}},
        },
        "updateKind": {"remoteValue": new},
    }


async def _post(server, path=WEBHOOK_PATH, headers=None, **kwargs):
    transport = httpx.ASGITransport(app=server.streamable_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(path, headers=headers, **kwargs)


async def test_disabled_returns_404(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"), wud_webhook_enabled=False)
    server = build_server(settings)
    resp = await _post(server, json=_payload())
    assert resp.status_code == 404


async def test_missing_secret_returns_403(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"), wud_webhook_secret=None)
    server = build_server(settings)
    resp = await _post(server, json=_payload(), headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 403


async def test_wrong_secret_returns_403(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    server = build_server(settings)
    resp = await _post(server, json=_payload(), headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


async def test_unknown_container_is_skipped_not_errored(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    server = build_server(settings)
    resp = await _post(
        server,
        json=_payload(name="ghost"),
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert resp.status_code == 200
    assert resp.json()["skipped"] == "no matching service"


async def test_missing_image_or_tag_returns_400(tmp_path):
    settings = _healthy_settings(tmp_path, str(tmp_path / "r.db"))
    server = build_server(settings)

    resp = await _post(
        server,
        json=_payload(image=""),
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert resp.status_code == 400

    resp = await _post(
        server,
        json=_payload(new=""),
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert resp.status_code == 400


async def test_known_container_reaches_proposal_engine(tmp_path):
    db_path = str(tmp_path / "r.db")
    store = RegistryStore(db_path)
    store.create_service(
        Service(name="plex", display_name="Plex", host="workload-01", auth_mode_conflict=False)
    )

    settings = _healthy_settings(tmp_path, db_path)
    server = build_server(settings)
    resp = await _post(server, json=_payload(), headers={"Authorization": f"Bearer {SECRET}"})

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
