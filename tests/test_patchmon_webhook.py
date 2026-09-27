"""Tests for the Patchmon webhook and its Approve/Cancel links (ADR-020).

Exercised end-to-end through the ASGI app, like the Dockhand webhook tests:
registration gating is part of what's under test, and only a real request
through the app proves an unmounted route 404s. The approval email goes through
the real `SmtpNotificationProvider` into a fake `smtplib.SMTP`, and the links a
test follows are read out of that email, the way a person would get them.
"""

import asyncio
import base64
import functools
import hashlib
import hmac
import html
import json
import re
import smtplib
from datetime import timedelta

import httpx
import pytest
from sqlmodel import Session, select

import registry_mcp.patching.executor as executor_module
import registry_mcp.server as server_module
import registry_mcp.webhooks.patchmon as patchmon_module
from conftest import IsolatedSettings
from http_fakes import strict_transport
from registry_mcp.integrations.patchmon import PatchmonClient
from registry_mcp.models import PatchApproval, PatchApprovalStatus
from registry_mcp.models.service import utcnow
from registry_mcp.patching import PatchApprovalStore
from registry_mcp.server import build_server

WEBHOOK_PATH = "/webhooks/patchmon"
SECRET = "patchmon-signing-secret"
BASE_URL = "https://registry.test"
HOST_ID = "0b5f2a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
# The PatchMon Integration API, off in `_settings` unless a test passes these.
API = dict(
    patchmon_api_url="https://patchmon.test",
    patchmon_api_key="patchmon_ae_key",
    patchmon_api_secret="api-secret",
)
API_AUTH = ("Authorization", "Basic " + base64.b64encode(b"patchmon_ae_key:api-secret").decode())
INFO_ROUTE = f"GET /api/v1/api/hosts/{HOST_ID}/info"


# --- fixtures and helpers ---


class FakeSMTP:
    """Stands in for smtplib.SMTP: records every message sent through it."""

    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        if FakeSMTP.fail:
            raise smtplib.SMTPConnectError(421, "relay unavailable")
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self, context=None):
        pass

    def login(self, username, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.fail = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


class FakeAnsible:
    """The `ansible`/`ansible-playbook` CLIs at `executor._run`; see test_patching.py."""

    def __init__(self, hosts=("pi-01",), rc=0):
        self.hosts = list(hosts)
        self.rc = rc
        self.calls = []

    async def __call__(self, cmd, env, *, timeout):
        self.calls.append(cmd)
        if cmd[0] == "ansible":
            matched = [cmd[3]] if cmd[3] in self.hosts else []
            return 0, f"  hosts ({len(matched)}):\n" + "".join(f"    {h}\n" for h in matched), ""
        return self.rc, "PLAY RECAP\npi-01 : ok=3 changed=1 failed=0", ""

    @property
    def playbook_runs(self):
        return [cmd for cmd in self.calls if cmd[0] == "ansible-playbook"]


@pytest.fixture
def ansible(monkeypatch):
    fake = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", fake)
    return fake


class PatchmonApi:
    """PatchMon's scoped Integration API: GET only, HTTP Basic required."""

    def __init__(self):
        self.routes = {
            INFO_ROUTE: {"id": HOST_ID, "hostname": "pi-01", "friendly_name": "Living Room Pi"}
        }
        self.requests: list[httpx.Request] = []

    def paths(self):
        return [request.url.path for request in self.requests]


@pytest.fixture
def patchmon_api(monkeypatch):
    api = PatchmonApi()
    transport = strict_transport(api.routes, captured=api.requests, auth_header=API_AUTH)
    monkeypatch.setattr(
        server_module,
        "build_patchmon_client",
        functools.partial(server_module.build_patchmon_client, transport=transport),
    )
    return api


def _settings(tmp_path, **overrides):
    """Healthy (not read-only) with every Patchmon prerequisite set, so each
    test can knock out exactly the one it's about."""
    repo = tmp_path / "homelab"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    ansible_cfg = tmp_path / "ansible.cfg"
    ansible_cfg.write_text("")
    ssh_key = tmp_path / "id_ed25519"
    ssh_key.write_text("")
    base = dict(
        registry_db_path=str(tmp_path / "r.db"),
        secrets_repo_path=str(repo),
        ansible_cfg_path=str(ansible_cfg),
        ssh_key_path=str(ssh_key),
        patchmon_webhook_enabled=True,
        patchmon_webhook_secret=SECRET,
        patchmon_approval_base_url=BASE_URL,
        patchmon_ansible_playbook=str(tmp_path / "patch.yml"),
        notification_provider="smtp",
        notification_smtp_host="smtp.test",
        notification_from_email="registry@test",
        notification_to_email="operator@test",
    )
    base.update(overrides)
    return IsolatedSettings(**base)


def _payload(**overrides):
    """The spec's flat patch alert."""
    body = {
        "event": "patch_available",
        "service": "authentik",
        "target_host": "pi-01",
        "current_version": "2024.2.0",
        "target_version": "2024.2.1",
    }
    body.update(overrides)
    return body


def _native_payload(host_name="pi-01", event_type="host_security_updates_exceeded"):
    """PatchMon's own generic webhook body (notification_worker.go)."""
    return {
        "event_type": event_type,
        "severity": "warning",
        "title": f"Host {host_name} has 5 security updates pending",
        "message": f'Host "{host_name}" has 5 pending security updates, exceeding threshold of 0.',
        "reference": {"type": "host", "id": HOST_ID},
        "metadata": {"host_id": HOST_ID, "host_name": host_name, "count": 5, "threshold": 0},
        "text": "*Host:* pi-01",
    }


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _client(server):
    transport = httpx.ASGITransport(app=server.streamable_http_app())
    return httpx.AsyncClient(transport=transport, base_url="http://registry.test")


async def _deliver(client, payload, *, signature=None, headers=None, path=WEBHOOK_PATH):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    sent = {"content-type": "application/json"}
    if signature is not False:
        sent["X-PatchMon-Signature"] = signature or _sign(raw)
    sent.update(headers or {})
    return await client.post(path, content=raw, headers=sent)


def _links(message):
    """The Approve/Cancel URLs from the email's plain-text part."""
    plain = message.get_body(preferencelist=("plain",)).get_content()
    found = dict(re.findall(r"^(Approve|Cancel): (\S+)$", plain, re.MULTILINE))
    return found["Approve"], found["Cancel"]


def _path(url):
    """A link as a path the ASGI client can request (it's rooted at BASE_URL)."""
    assert url.startswith(BASE_URL + "/patch/")
    return url[len(BASE_URL) :]


def _token(url):
    return httpx.URL(url).params["token"]


def _approvals(settings):
    return PatchApprovalStore(server_module.RegistryStore(settings.registry_db_path).engine)


def _only_approval(settings):
    store = _approvals(settings)
    with Session(store.engine) as session:
        rows = session.exec(select(PatchApproval)).all()
    assert len(rows) == 1
    return rows[0]


async def _intake(client, **payload):
    response = await _deliver(client, _payload(**payload))
    assert response.status_code == 202, response.text
    return _links(FakeSMTP.sent[-1])


# --- registration gating: fail closed ---


@pytest.mark.parametrize(
    "overrides",
    [
        {"patchmon_webhook_enabled": False},
        {"patchmon_webhook_secret": None},
        {"patchmon_webhook_secret": "   "},
        {"patchmon_approval_base_url": None},
        {"patchmon_approval_base_url": "registry.test"},  # no scheme
        {"notification_provider": "ntfy", "notification_url": "https://ntfy.test"},
        {"notification_provider": "smtp", "notification_to_email": None},
        {"patchmon_ansible_playbook": None},
    ],
    ids=[
        "disabled",
        "no-secret",
        "blank-secret",
        "no-base-url",
        "base-url-without-scheme",
        "ntfy-not-email",
        "smtp-incomplete",
        "nothing-to-execute",
    ],
)
async def test_every_route_is_unmounted_unless_fully_configured(tmp_path, overrides):
    server = build_server(_settings(tmp_path, **overrides))
    async with _client(server) as client:
        webhook = await _deliver(client, _payload())
        approve = await client.get("/patch/approve", params={"token": "x"})
        cancel = await client.post("/patch/cancel", data={"token": "x"})

    assert (webhook.status_code, approve.status_code, cancel.status_code) == (404, 404, 404)
    assert FakeSMTP.sent == []


async def test_custom_path_is_honored(tmp_path):
    server = build_server(_settings(tmp_path, patchmon_webhook_path="/hooks/pm"))
    async with _client(server) as client:
        stale = await _deliver(client, _payload())
        moved = await _deliver(client, _payload(), path="/hooks/pm")
    assert stale.status_code == 404
    assert moved.status_code == 202


async def test_read_only_server_refuses_the_webhook(tmp_path):
    settings = _settings(tmp_path, ansible_cfg_path=None)  # health check fails
    async with _client(build_server(settings)) as client:
        response = await _deliver(client, _payload())
    assert response.status_code == 403
    assert "read-only" in response.json()["error"]
    assert FakeSMTP.sent == []


# --- HMAC signature: 401 before anything reads the payload ---


async def test_missing_signature_is_401_and_nothing_happens(tmp_path):
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        response = await _deliver(client, _payload(), signature=False)

    assert response.status_code == 401
    assert "X-PatchMon-Signature" in response.headers["www-authenticate"]
    assert FakeSMTP.sent == []
    assert (
        _approvals(settings).find_pending(
            event="patch_available",
            target_host="pi-01",
            service="authentik",
            target_version="2024.2.1",
        )
        is None
    )


async def test_signature_under_the_wrong_secret_is_401(tmp_path):
    async with _client(build_server(_settings(tmp_path))) as client:
        raw = json.dumps(_payload()).encode()
        response = await _deliver(client, raw, signature=_sign(raw, "not-the-secret"))
    assert response.status_code == 401
    assert FakeSMTP.sent == []


async def test_signature_of_a_different_body_is_401(tmp_path):
    """HMAC covers the exact bytes: a tampered body fails even with a real signature."""
    async with _client(build_server(_settings(tmp_path))) as client:
        signed = json.dumps(_payload()).encode()
        tampered = json.dumps(_payload(target_host="pi-02")).encode()
        response = await _deliver(client, tampered, signature=_sign(signed))
    assert response.status_code == 401


async def test_bad_signature_is_401_even_when_the_body_is_not_json(tmp_path):
    """Checked before parsing: an unsigned caller learns nothing about the parser."""
    async with _client(build_server(_settings(tmp_path))) as client:
        response = await _deliver(client, b"{not json", signature="sha256=00")
    assert response.status_code == 401


async def test_bare_and_uppercase_hex_signatures_are_accepted(tmp_path):
    raw = json.dumps(_payload()).encode()
    digest = _sign(raw).removeprefix("sha256=")
    async with _client(build_server(_settings(tmp_path))) as client:
        bare = await _deliver(client, raw, signature=digest.upper())
    assert bare.status_code == 202


async def test_non_ascii_signature_is_401_not_500(tmp_path):
    async with _client(build_server(_settings(tmp_path))) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=json.dumps(_payload()).encode(),
            headers={b"content-type": b"application/json", b"X-PatchMon-Signature": b"s\xe9c"},
        )
    assert response.status_code == 401


# --- request validation (signed requests) ---


async def test_oversized_body_is_413(tmp_path):
    settings = _settings(tmp_path, patchmon_webhook_max_body_bytes=64)
    async with _client(build_server(settings)) as client:
        response = await _deliver(client, _payload(service="x" * 200))
    assert response.status_code == 413


async def test_wrong_content_type_is_400(tmp_path):
    async with _client(build_server(_settings(tmp_path))) as client:
        response = await _deliver(client, _payload(), headers={"content-type": "text/plain"})
    assert response.status_code == 400


async def test_invalid_json_is_400(tmp_path):
    async with _client(build_server(_settings(tmp_path))) as client:
        response = await _deliver(client, b"{not json")
    assert response.status_code == 400


async def test_unrecognized_shape_is_422_with_serializable_detail(tmp_path):
    async with _client(build_server(_settings(tmp_path))) as client:
        response = await _deliver(client, {"totally": "unrelated"})
    assert response.status_code == 422
    assert all({"loc", "msg", "type"} == set(item) for item in response.json()["detail"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_host": "all,pi-02"},  # a pattern, not a host
        {"target_host": "-e@evil.yml"},  # an option
        {"target_host": "pi 01"},
        {"target_version": "{{ lookup('pipe', 'id') }}"},  # a Jinja template
        {"service": "auth;rm"},
    ],
)
async def test_values_that_could_reach_ansible_unsafely_are_422(tmp_path, overrides):
    async with _client(build_server(_settings(tmp_path))) as client:
        response = await _deliver(client, _payload(**overrides))
    assert response.status_code == 422
    assert FakeSMTP.sent == []


# --- intake: alert -> pending approval + email ---


async def test_patch_alert_emails_single_use_approve_and_cancel_links(tmp_path):
    settings = _settings(tmp_path, patchmon_approval_ttl_minutes=45)
    async with _client(build_server(settings)) as client:
        response = await _deliver(client, _payload())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["execution"] == "the Ansible playbook patch.yml against pi-01"

    assert len(FakeSMTP.sent) == 1
    message = FakeSMTP.sent[0]
    assert message["To"] == "operator@test"
    assert message["Subject"] == "Approve patch: authentik on pi-01"
    approve, cancel = _links(message)
    assert approve.startswith(f"{BASE_URL}/patch/approve?token=")
    assert cancel.startswith(f"{BASE_URL}/patch/cancel?token=")

    plain = message.get_body(preferencelist=("plain",)).get_content()
    html_body = message.get_body(preferencelist=("html",)).get_content()
    assert "2024.2.0" in plain and "2024.2.1" in plain
    assert "(45 minutes)" in plain
    assert "nothing runs until you confirm" in plain
    assert f'href="{html.escape(approve, quote=True)}"' in html_body

    approval = _only_approval(settings)
    assert approval.status == PatchApprovalStatus.pending
    assert approval.id == body["approval_id"]
    remaining = approval.expires_at - utcnow().replace(tzinfo=None)
    assert timedelta(minutes=44) < remaining <= timedelta(minutes=45)
    # The row can't be replayed as a click: only hashes are stored.
    assert _token(approve) not in (approval.approve_token_hash, approval.cancel_token_hash)


async def test_an_older_senders_callback_url_is_accepted_and_ignored(tmp_path, ansible):
    """ADR-020 once called PatchMon back at this URL. A sender still including it
    must not be refused, and nothing may ever be sent to it."""
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        response = await _deliver(client, _payload(patchmon_callback_url="file:///etc/passwd"))
        approve, _ = _links(FakeSMTP.sent[-1])
        await client.post(_path(approve), data={"token": _token(approve)})

    assert response.status_code == 202
    assert _only_approval(settings).status == PatchApprovalStatus.executed
    assert [cmd[0] for cmd in ansible.calls] == ["ansible", "ansible-playbook"]


async def test_native_patchmon_alert_becomes_an_approval_keyed_by_host_id(tmp_path):
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        response = await _deliver(client, _native_payload())

    assert response.status_code == 202
    approval = _only_approval(settings)
    assert approval.target_host == "pi-01"
    assert approval.patchmon_host_id == HOST_ID
    assert approval.event == "host_security_updates_exceeded"
    assert FakeSMTP.sent[0]["Subject"] == "Approve patch: host security updates exceeded on pi-01"


async def test_event_outside_the_allowlist_is_acknowledged_not_mailed(tmp_path):
    async with _client(build_server(_settings(tmp_path))) as client:
        response = await _deliver(client, _native_payload(event_type="host_down"))
    assert response.status_code == 200
    assert "not in PATCHMON_WEBHOOK_EVENTS" in response.json()["ignored"]
    assert FakeSMTP.sent == []


async def test_friendly_host_name_is_never_guessed_into_an_inventory_name(tmp_path):
    async with _client(build_server(_settings(tmp_path))) as client:
        response = await _deliver(client, _native_payload(host_name="Living Room Pi"))
    assert response.status_code == 200
    assert "not a plain inventory host name" in response.json()["ignored"]
    assert FakeSMTP.sent == []


# --- naming the host by PatchMon's record for its id ---


async def test_a_friendly_name_is_resolved_through_patchmons_record_for_the_host_id(
    tmp_path, patchmon_api, ansible
):
    settings = _settings(tmp_path, **API)
    async with _client(build_server(settings)) as client:
        response = await _deliver(client, _native_payload(host_name="Living Room Pi"))
        approve, _ = _links(FakeSMTP.sent[-1])
        await client.post(_path(approve), data={"token": _token(approve)})

    assert response.status_code == 202
    assert f"/api/v1/api/hosts/{HOST_ID}/info" in patchmon_api.paths()
    approval = _only_approval(settings)
    assert approval.target_host == "pi-01"
    assert approval.patchmon_host_id == HOST_ID
    assert "Living Room Pi" in approval.summary
    assert FakeSMTP.sent[0]["Subject"].endswith(" on pi-01")
    cmd = ansible.playbook_runs[0]
    assert cmd[cmd.index("--limit") + 1] == "pi-01"


async def test_a_plain_host_name_is_used_as_sent_and_never_looked_up(tmp_path, patchmon_api):
    """PatchMon's hostname for the id is pi-01; the alert says pi-02. An alert
    that already names an inventory host must keep naming it."""
    settings = _settings(tmp_path, **API)
    async with _client(build_server(settings)) as client:
        response = await _deliver(client, _native_payload(host_name="pi-02"))

    assert response.status_code == 202
    assert _only_approval(settings).target_host == "pi-02"
    assert not any(path.endswith("/info") for path in patchmon_api.paths())


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (lambda request: httpx.Response(404, json={"error": "Host not found"}), "failed"),
        (lambda request: httpx.Response(500, json={"error": "boom"}), "failed"),
        ({"id": HOST_ID, "hostname": ""}, "no hostname on record"),
        ({"id": HOST_ID, "hostname": "Living Room Pi"}, "is not one either"),
        ({"id": HOST_ID, "hostname": "all,pi-02"}, "is not one either"),
        (
            {"id": "11111111-2222-4333-8444-555555555555", "hostname": "pi-01"},
            "a host other than",
        ),
    ],
    ids=["unknown-host", "server-error", "no-hostname", "friendly", "pattern", "other-host"],
)
async def test_a_lookup_that_cannot_name_the_host_exactly_is_acknowledged_not_mailed(
    tmp_path, patchmon_api, answer, reason
):
    patchmon_api.routes[INFO_ROUTE] = answer
    settings = _settings(tmp_path, **API)
    async with _client(build_server(settings)) as client:
        response = await _deliver(client, _native_payload(host_name="Living Room Pi"))

    assert response.status_code == 200
    ignored = response.json()["ignored"]
    assert "not a plain inventory host name" in ignored
    assert reason in ignored
    assert FakeSMTP.sent == []


async def test_an_alert_without_a_host_id_is_never_looked_up(tmp_path, patchmon_api):
    payload = _native_payload(host_name="Living Room Pi")
    del payload["reference"]
    del payload["metadata"]["host_id"]
    async with _client(build_server(_settings(tmp_path, **API))) as client:
        response = await _deliver(client, payload)

    assert response.status_code == 200
    assert "not a plain inventory host name" in response.json()["ignored"]
    assert patchmon_api.requests == []


async def test_a_slow_lookup_is_cut_off_inside_patchmons_delivery_timeout(
    tmp_path, patchmon_api, monkeypatch
):
    """PatchMon abandons a delivery after 30s; the lookup must give up first."""

    async def stalls(self, host_id):
        await asyncio.sleep(2)
        return {"id": host_id, "hostname": "pi-01"}

    monkeypatch.setattr(PatchmonClient, "get_host_info", stalls)
    monkeypatch.setattr(patchmon_module, "_LOOKUP_BUDGET_SECONDS", 0.05)
    async with _client(build_server(_settings(tmp_path, **API))) as client:
        response = await _deliver(client, _native_payload(host_name="Living Room Pi"))

    assert response.status_code == 200
    assert "timed out" in response.json()["ignored"]
    assert FakeSMTP.sent == []


async def test_repeat_alert_while_one_is_pending_sends_no_second_email(tmp_path):
    async with _client(build_server(_settings(tmp_path))) as client:
        first = await _deliver(client, _payload())
        again = await _deliver(client, _payload())

    assert first.status_code == 202
    assert again.status_code == 200
    assert again.json()["approval_id"] == first.json()["approval_id"]
    assert len(FakeSMTP.sent) == 1


async def test_undeliverable_email_is_502_and_a_retry_starts_fresh(tmp_path):
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        FakeSMTP.fail = True
        failed = await _deliver(client, _payload())
        FakeSMTP.fail = False
        retried = await _deliver(client, _payload())

    assert failed.status_code == 502
    assert retried.status_code == 202
    first = _approvals(settings).get(failed.json()["approval_id"])
    assert first.status == PatchApprovalStatus.undelivered
    assert retried.json()["approval_id"] != first.id


# --- the links: GET shows, POST acts, once ---


async def test_opening_the_approve_link_only_shows_a_confirmation(tmp_path, ansible):
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        approve, _ = await _intake(client)
        page = await client.get(_path(approve))
        again = await client.get(_path(approve))  # a mail scanner's prefetch, say

    assert page.status_code == again.status_code == 200
    assert "<form method='post'>" in page.text
    assert f"value='{_token(approve)}'" in page.text
    assert "authentik" in page.text and "pi-01" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert _only_approval(settings).status == PatchApprovalStatus.pending
    assert ansible.calls == []


async def test_confirming_approve_runs_the_playbook_and_reports_back(tmp_path, ansible):
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        approve, _ = await _intake(client)
        response = await client.post(_path(approve), data={"token": _token(approve)})

    assert response.status_code == 200
    assert "Patch approved" in response.text
    assert len(ansible.playbook_runs) == 1
    cmd = ansible.playbook_runs[0]
    assert cmd[cmd.index("--limit") + 1] == "pi-01"
    assert cmd[-1] == str(tmp_path / "patch.yml")
    extra = json.loads(cmd[cmd.index("-e") + 1])
    assert extra["patchmon_target_version"] == "2024.2.1"

    approval = _only_approval(settings)
    assert approval.status == PatchApprovalStatus.executed
    assert approval.executed_via == "ansible"
    result_email = FakeSMTP.sent[-1]
    assert result_email["Subject"] == "Patch completed: authentik on pi-01"
    result_plain = result_email.get_body(preferencelist=("plain",)).get_content()
    assert "Executed via: ansible" in result_plain
    assert "PLAY RECAP" in result_plain


async def test_a_used_approve_link_does_nothing_the_second_time(tmp_path, ansible):
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        approve, _ = await _intake(client)
        await client.post(_path(approve), data={"token": _token(approve)})
        reused = await client.post(_path(approve), data={"token": _token(approve)})
        reopened = await client.get(_path(approve))

    assert reused.status_code == reopened.status_code == 409
    assert "already used" in reused.text
    assert "No further action was taken" in reused.text
    assert len(ansible.playbook_runs) == 1


async def test_an_expired_link_shows_a_clean_message_and_runs_nothing(tmp_path, ansible):
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        approve, _ = await _intake(client)
        store = _approvals(settings)
        with Session(store.engine) as session:
            row = session.exec(select(PatchApproval)).one()
            row.expires_at = utcnow() - timedelta(minutes=1)
            session.add(row)
            session.commit()
        opened = await client.get(_path(approve))
        confirmed = await client.post(_path(approve), data={"token": _token(approve)})

    assert opened.status_code == confirmed.status_code == 410
    assert "expired" in confirmed.text
    assert "No action was taken" in confirmed.text
    assert "Traceback" not in confirmed.text
    assert ansible.calls == []
    assert _only_approval(settings).status == PatchApprovalStatus.expired


async def test_cancel_closes_the_request_and_disarms_approve(tmp_path, ansible):
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        approve, cancel = await _intake(client)
        cancelled = await client.post(_path(cancel), data={"token": _token(cancel)})
        late = await client.post(_path(approve), data={"token": _token(approve)})

    assert cancelled.status_code == 200
    assert "Request cancelled" in cancelled.text
    assert late.status_code == 409
    assert "cancelled" in late.text
    assert ansible.calls == []
    assert _only_approval(settings).status == PatchApprovalStatus.cancelled


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_unknown_or_cross_action_token_is_404_and_harmless(tmp_path, ansible, method):
    async with _client(build_server(_settings(tmp_path))) as client:
        _, cancel = await _intake(client)
        # A cancel token presented at the approve route is not an approval.
        cross = await client.request(
            method, "/patch/approve", params={"token": _token(cancel)}, data=None
        )
        unknown = await client.request(method, "/patch/approve", params={"token": "made-up"})

    assert cross.status_code == unknown.status_code == 404
    assert "No action was taken" in unknown.text
    assert ansible.calls == []


async def test_token_in_the_query_string_alone_can_confirm(tmp_path, ansible):
    """The form posts back to the URL it came from, so the token rides along
    in the query even if a client drops the form body."""
    async with _client(build_server(_settings(tmp_path))) as client:
        approve, _ = await _intake(client)
        response = await client.post(_path(approve))
    assert response.status_code == 200
    assert len(ansible.playbook_runs) == 1


# --- execution outcome, end to end ---


async def test_failed_patch_is_recorded_and_reported(tmp_path, monkeypatch):
    failing = FakeAnsible(rc=2)
    monkeypatch.setattr(executor_module, "_run", failing)
    settings = _settings(tmp_path)
    async with _client(build_server(settings)) as client:
        approve, _ = await _intake(client)
        await client.post(_path(approve), data={"token": _token(approve)})

    approval = _only_approval(settings)
    assert approval.status == PatchApprovalStatus.failed
    assert "exited 2" in approval.detail
    assert FakeSMTP.sent[-1]["Subject"] == "Patch FAILED: authentik on pi-01"


async def test_read_only_server_shows_links_but_never_acts(tmp_path, ansible):
    healthy = _settings(tmp_path)
    async with _client(build_server(healthy)) as client:
        approve, _ = await _intake(client)

    read_only = _settings(tmp_path, ansible_cfg_path=None)
    async with _client(build_server(read_only)) as client:
        opened = await client.get(_path(approve))
        confirmed = await client.post(_path(approve), data={"token": _token(approve)})

    assert opened.status_code == confirmed.status_code == 503
    assert "read-only" in confirmed.text
    assert _only_approval(healthy).status == PatchApprovalStatus.pending
    assert ansible.calls == []
