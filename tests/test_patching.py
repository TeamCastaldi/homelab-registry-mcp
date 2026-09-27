"""Tests for `patching/`: the approval store's one-way token gate and the
executor's PatchMon-first, Ansible-fallback execution (ADR-020)."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
import structlog.testing
from sqlmodel import Session

import registry_mcp.patching.executor as executor_module
from http_fakes import strict_transport
from registry_mcp.models import PatchApproval, PatchApprovalStatus
from registry_mcp.models.service import utcnow
from registry_mcp.patching import ApprovalAction, PatchApprovalStore, PatchExecutor, TokenState
from registry_mcp.patching.executor import parse_list_hosts, resolve_callback_url
from registry_mcp.patching.store import hash_token

CALLBACK = "https://patchmon.test/api/v1/patching/trigger"
HOST_ID = "0b5f2a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"


@pytest.fixture
def approvals(store):
    return PatchApprovalStore(store.engine)


def _approval(**overrides):
    fields = dict(
        event="patch_available",
        target_host="pi-01",
        service="authentik",
        current_version="2024.2.0",
        target_version="2024.2.1",
    )
    fields.update(overrides)
    return PatchApproval(**fields)


def _expire(approvals, approval_id):
    with Session(approvals.engine) as session:
        row = session.get(PatchApproval, approval_id)
        row.expires_at = utcnow() - timedelta(minutes=1)
        session.add(row)
        session.commit()


# --- store: tokens ---


def test_create_stores_only_hashes_of_two_distinct_tokens(approvals):
    issued = approvals.create(_approval(), ttl_minutes=60)
    row = approvals.get(issued.approval.id)

    assert issued.approve_token != issued.cancel_token
    assert row.approve_token_hash == hash_token(issued.approve_token)
    assert row.cancel_token_hash == hash_token(issued.cancel_token)
    assert issued.approve_token not in (row.approve_token_hash, row.cancel_token_hash)
    assert row.status == PatchApprovalStatus.pending


def test_check_is_scoped_to_the_links_action(approvals):
    """A cancel token must not work as an approval, or the reverse."""
    issued = approvals.create(_approval(), ttl_minutes=60)

    assert approvals.check(issued.approve_token, ApprovalAction.approve).state is TokenState.valid
    assert approvals.check(issued.cancel_token, ApprovalAction.approve).state is TokenState.invalid
    assert approvals.check(issued.approve_token, ApprovalAction.cancel).state is TokenState.invalid


def test_check_changes_nothing(approvals):
    issued = approvals.create(_approval(), ttl_minutes=60)
    for _ in range(3):
        approvals.check(issued.approve_token, ApprovalAction.approve)
    assert approvals.get(issued.approval.id).status == PatchApprovalStatus.pending


def test_unknown_empty_and_oversized_tokens_are_invalid(approvals):
    approvals.create(_approval(), ttl_minutes=60)
    for token in ("nope", "", "x" * 5000):
        assert approvals.check(token, ApprovalAction.approve).state is TokenState.invalid


def test_consume_succeeds_once_then_reports_used(approvals):
    issued = approvals.create(_approval(), ttl_minutes=60)

    first = approvals.consume(issued.approve_token, ApprovalAction.approve)
    second = approvals.consume(issued.approve_token, ApprovalAction.approve)

    assert first.state is TokenState.valid
    assert first.approval.status == PatchApprovalStatus.approved
    assert second.state is TokenState.used


def test_cancel_consumes_the_approve_link_too(approvals):
    issued = approvals.create(_approval(), ttl_minutes=60)

    assert approvals.consume(issued.cancel_token, ApprovalAction.cancel).state is TokenState.valid
    after = approvals.consume(issued.approve_token, ApprovalAction.approve)

    assert after.state is TokenState.used
    assert approvals.get(issued.approval.id).status == PatchApprovalStatus.cancelled


def test_consume_loses_a_race_to_a_concurrent_click(approvals, monkeypatch):
    """The conditional UPDATE is what makes a click single-use: if another
    request resolved the row between this one's check and its write, the
    write must match nothing."""
    issued = approvals.create(_approval(), ttl_minutes=60)
    stale = approvals.check(issued.approve_token, ApprovalAction.approve)
    approvals.consume(issued.cancel_token, ApprovalAction.cancel)  # the other click
    monkeypatch.setattr(approvals, "check", lambda token, action: stale)

    result = approvals.consume(issued.approve_token, ApprovalAction.approve)

    assert result.state is TokenState.used
    assert approvals.get(issued.approval.id).status == PatchApprovalStatus.cancelled


def test_expired_token_is_reported_and_consume_marks_it(approvals):
    issued = approvals.create(_approval(), ttl_minutes=60)
    _expire(approvals, issued.approval.id)

    assert approvals.check(issued.approve_token, ApprovalAction.approve).state is TokenState.expired
    assert approvals.get(issued.approval.id).status == PatchApprovalStatus.pending
    result = approvals.consume(issued.approve_token, ApprovalAction.approve)

    assert result.state is TokenState.expired
    assert approvals.get(issued.approval.id).status == PatchApprovalStatus.expired


def test_ttl_running_out_between_check_and_write_is_expired_not_approved(approvals, monkeypatch):
    issued = approvals.create(_approval(), ttl_minutes=60)
    stale = approvals.check(issued.approve_token, ApprovalAction.approve)
    _expire(approvals, issued.approval.id)
    monkeypatch.setattr(approvals, "check", lambda token, action: stale)

    result = approvals.consume(issued.approve_token, ApprovalAction.approve)

    assert result.state is TokenState.expired
    assert approvals.get(issued.approval.id).status == PatchApprovalStatus.expired


def test_find_pending_matches_missing_fields_to_missing_fields(approvals):
    issued = approvals.create(_approval(service=None, target_version=None), ttl_minutes=60)

    found = approvals.find_pending(
        event="patch_available", target_host="pi-01", service=None, target_version=None
    )
    other = approvals.find_pending(
        event="patch_available", target_host="pi-01", service="authentik", target_version=None
    )

    assert found is not None and found.id == issued.approval.id
    assert other is None


def test_find_pending_ignores_expired_and_resolved(approvals):
    expired = approvals.create(_approval(), ttl_minutes=60)
    _expire(approvals, expired.approval.id)
    resolved = approvals.create(_approval(), ttl_minutes=60)
    approvals.consume(resolved.cancel_token, ApprovalAction.cancel)

    assert (
        approvals.find_pending(
            event="patch_available",
            target_host="pi-01",
            service="authentik",
            target_version="2024.2.1",
        )
        is None
    )


def test_purge_expired_marks_only_past_ttl_pending_rows(approvals):
    old = approvals.create(_approval(), ttl_minutes=60)
    fresh = approvals.create(_approval(target_host="pi-02"), ttl_minutes=60)
    _expire(approvals, old.approval.id)

    assert approvals.purge_expired() == 1
    assert approvals.get(old.approval.id).status == PatchApprovalStatus.expired
    assert approvals.get(fresh.approval.id).status == PatchApprovalStatus.pending


# --- executor: callback URL trust ---


def test_payload_callback_on_the_configured_origin_is_used():
    url, _ = resolve_callback_url(CALLBACK, "https://patchmon.test:443/api/v1/other")
    assert url == "https://patchmon.test:443/api/v1/other"


@pytest.mark.parametrize(
    "payload_url",
    [
        "https://evil.test/api/v1/patching/trigger",
        "http://patchmon.test/api/v1/patching/trigger",  # scheme is part of the origin
        "https://patchmon.test:8443/api/v1/patching/trigger",
        "https://patchmon.test.evil.test/x",
    ],
)
def test_payload_callback_on_another_origin_falls_back_to_the_configured_url(payload_url):
    url, why = resolve_callback_url(CALLBACK, payload_url)
    assert url == CALLBACK
    assert "ignored" in why


def test_payload_callback_is_never_used_without_a_configured_one():
    url, _ = resolve_callback_url(None, "https://patchmon.test/api/v1/patching/trigger")
    assert url is None


# --- executor: fakes ---


class FakeAnsible:
    """Stands in for the `ansible` and `ansible-playbook` CLIs at `executor._run`.

    `--list-hosts` answers the way the real CLI does: the `hosts (N):` header
    and one indented name per line, with an unmatched pattern listing zero
    hosts (and a warning on stderr) rather than failing.
    """

    def __init__(self, hosts=("pi-01", "pi-02"), groups=None, rc=0, out="", err=""):
        self.hosts = list(hosts)
        self.groups = {"all": self.hosts, **(groups or {})}
        self.rc, self.out, self.err = rc, out, err
        self.calls = []

    def _resolve(self, pattern):
        if pattern in self.groups:
            return self.groups[pattern]
        return [pattern] if pattern in self.hosts else []

    async def __call__(self, cmd, env, *, timeout):
        self.calls.append({"cmd": cmd, "env": env, "timeout": timeout})
        if cmd[0] == "ansible":
            assert cmd[1:3] == ["--list-hosts", "--"], cmd
            matched = self._resolve(cmd[3])
            out = f"  hosts ({len(matched)}):\n" + "".join(f"    {h}\n" for h in matched)
            err = "" if matched else f"[WARNING]: Could not match supplied host pattern: {cmd[3]}"
            return 0, out, err
        if cmd[0] == "ansible-playbook":
            return self.rc, self.out, self.err
        raise FileNotFoundError(cmd[0])

    @property
    def playbook_calls(self):
        return [call for call in self.calls if call["cmd"][0] == "ansible-playbook"]


def _trigger(status=200, captured=None):
    return strict_transport(
        {"POST /api/v1/patching/trigger": lambda request: httpx.Response(status, json={})},
        captured=captured,
        allowed_methods=("POST",),
        auth_header=("Authorization", "Bearer pm-token"),
    )


def _executor(tmp_path, *, callback=CALLBACK, playbook=True, transport=None, **overrides):
    fields = dict(
        callback_url=callback,
        api_token="pm-token",
        playbook=str(tmp_path / "patch.yml") if playbook else None,
        ansible_cfg_path=str(tmp_path / "ansible.cfg"),
        ssh_key_path=str(tmp_path / "id_ed25519"),
        ssh_user="ansible",
        ansible_timeout_seconds=900,
        transport=transport or _trigger(),
    )
    fields.update(overrides)
    return PatchExecutor(**fields)


def _approved(**overrides):
    return _approval(id="appr-1", patchmon_host_id=HOST_ID, **overrides)


def _events(logs, name):
    return [entry for entry in logs if entry.get("event") == name]


# --- executor: PatchMon first ---


async def test_accepted_callback_runs_no_playbook(tmp_path, monkeypatch):
    ansible = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", ansible)
    captured = []

    result = await _executor(tmp_path, transport=_trigger(captured=captured)).execute(_approved())

    assert result.status is PatchApprovalStatus.executed
    assert result.executed_via == "patchmon"
    assert ansible.calls == []
    assert len(captured) == 1
    body = json.loads(captured[0].content)
    # PatchMon's POST /api/v1/patching/trigger reads host_id and patch_type.
    assert body["host_id"] == HOST_ID
    assert body["patch_type"] == "patch_all"
    assert body["approval_id"] == "appr-1"
    assert body["target_version"] == "2024.2.1"


async def test_callback_sends_the_bearer_token(tmp_path, monkeypatch):
    """The strict fake 401s without it — which must then fall back, not pass."""
    ansible = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", ansible)

    result = await _executor(tmp_path, api_token=None).execute(_approved())

    assert result.executed_via == "ansible"
    assert "HTTP 401" in result.fallback_reason


@pytest.mark.parametrize(
    ("status", "reason"), [(404, "not supported"), (501, "not supported"), (500, "HTTP 500")]
)
async def test_callback_error_status_falls_back_to_ansible(tmp_path, monkeypatch, status, reason):
    ansible = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", ansible)

    result = await _executor(tmp_path, transport=_trigger(status)).execute(_approved())

    assert result.status is PatchApprovalStatus.executed
    assert result.executed_via == "ansible"
    assert reason in result.fallback_reason
    assert len(ansible.playbook_calls) == 1


async def test_unreachable_callback_logs_and_falls_back(tmp_path, monkeypatch):
    ansible = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", ansible)

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    with structlog.testing.capture_logs() as logs:
        result = await _executor(tmp_path, transport=httpx.MockTransport(refuse)).execute(
            _approved()
        )

    assert result.executed_via == "ansible"
    assert "unreachable" in result.fallback_reason
    failed = _events(logs, "patchmon_callback_failed")
    fallback = _events(logs, "patch_execution_fallback_ansible")
    assert len(failed) == 1 and failed[0]["log_level"] == "warning"
    assert len(fallback) == 1 and fallback[0]["log_level"] == "warning"
    assert "unreachable" in fallback[0]["reason"]
    assert fallback[0]["approval_id"] == "appr-1"


async def test_redirect_is_not_followed(tmp_path, monkeypatch):
    """Following it would carry the bearer token to wherever it points."""
    ansible = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", ansible)
    captured = []
    transport = strict_transport(
        {
            "POST /api/v1/patching/trigger": lambda request: httpx.Response(
                307, headers={"Location": "https://elsewhere.test/steal"}
            )
        },
        captured=captured,
        allowed_methods=("POST",),
    )

    result = await _executor(tmp_path, transport=transport).execute(_approved())

    assert [str(request.url.host) for request in captured] == ["patchmon.test"]
    assert result.executed_via == "ansible"


async def test_payload_callback_on_another_origin_never_receives_the_request(tmp_path, monkeypatch):
    monkeypatch.setattr(executor_module, "_run", FakeAnsible())
    captured = []
    approval = _approved(payload_callback_url="https://evil.test/api/v1/patching/trigger")

    with structlog.testing.capture_logs() as logs:
        result = await _executor(tmp_path, transport=_trigger(captured=captured)).execute(approval)

    assert [request.url.host for request in captured] == ["patchmon.test"]
    assert result.executed_via == "patchmon"
    assert len(_events(logs, "patchmon_callback_url_rejected")) == 1


async def test_unconfigured_callback_goes_straight_to_ansible(tmp_path, monkeypatch):
    ansible = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", ansible)
    captured = []

    with structlog.testing.capture_logs() as logs:
        result = await _executor(
            tmp_path, callback=None, transport=_trigger(captured=captured)
        ).execute(_approved(payload_callback_url=CALLBACK))

    assert captured == []  # a payload URL alone is never trusted
    assert result.executed_via == "ansible"
    assert "not configured" in result.fallback_reason
    assert len(_events(logs, "patch_execution_fallback_ansible")) == 1


async def test_failed_callback_without_a_playbook_fails_and_says_so(tmp_path, monkeypatch):
    ansible = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", ansible)

    with structlog.testing.capture_logs() as logs:
        result = await _executor(tmp_path, playbook=False, transport=_trigger(500)).execute(
            _approved()
        )

    assert result.status is PatchApprovalStatus.failed
    assert result.executed_via is None
    assert "PATCHMON_ANSIBLE_PLAYBOOK" in result.detail
    assert ansible.calls == []
    assert len(_events(logs, "patch_execution_no_fallback")) == 1


# --- executor: the Ansible fallback ---


async def test_playbook_runs_against_exactly_the_alerts_host(tmp_path, monkeypatch):
    ansible = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", ansible)

    result = await _executor(tmp_path, callback=None).execute(_approved())

    assert result.status is PatchApprovalStatus.executed
    call = ansible.playbook_calls[0]
    cmd = call["cmd"]
    assert cmd[cmd.index("--limit") + 1] == "pi-01"
    assert cmd[cmd.index("--private-key") + 1] == str(tmp_path / "id_ed25519")
    assert cmd[cmd.index("-u") + 1] == "ansible"
    assert cmd[-2:] == ["--", str(tmp_path / "patch.yml")]
    assert call["env"]["ANSIBLE_CONFIG"] == str(tmp_path / "ansible.cfg")
    assert call["timeout"] == 900
    extra = json.loads(cmd[cmd.index("-e") + 1])
    assert extra == {
        "patchmon_approval_id": "appr-1",
        "patchmon_event": "patch_available",
        "patchmon_target_host": "pi-01",
        "patchmon_host_id": HOST_ID,
        "patchmon_service": "authentik",
        "patchmon_current_version": "2024.2.0",
        "patchmon_target_version": "2024.2.1",
    }


@pytest.mark.parametrize("host", ["all", "workers", "ghost"])
async def test_a_name_that_is_not_exactly_one_host_is_never_patched(tmp_path, monkeypatch, host):
    """`--limit all` or a group name would patch many hosts; an unknown name,
    none (reported as a failure, not a quiet success)."""
    ansible = FakeAnsible(hosts=("pi-01", "pi-02"), groups={"workers": ["pi-01", "pi-02"]})
    monkeypatch.setattr(executor_module, "_run", ansible)

    result = await _executor(tmp_path, callback=None).execute(_approved(target_host=host))

    assert result.status is PatchApprovalStatus.failed
    assert "exactly one inventory host" in result.detail
    assert ansible.playbook_calls == []


async def test_nonzero_playbook_exit_is_a_failure_with_its_output(tmp_path, monkeypatch):
    ansible = FakeAnsible(rc=2, out="PLAY RECAP\npi-01 : ok=1 failed=1", err="boom")
    monkeypatch.setattr(executor_module, "_run", ansible)

    result = await _executor(tmp_path, callback=None).execute(_approved())

    assert result.status is PatchApprovalStatus.failed
    assert result.executed_via == "ansible"
    assert "exited 2" in result.detail
    assert "failed=1" in result.detail


async def test_playbook_timeout_is_a_failure(tmp_path, monkeypatch):
    ansible = FakeAnsible()

    async def fake(cmd, env, *, timeout):
        if cmd[0] == "ansible-playbook":
            raise TimeoutError
        return await ansible(cmd, env, timeout=timeout)

    monkeypatch.setattr(executor_module, "_run", fake)

    result = await _executor(tmp_path, callback=None).execute(_approved())

    assert result.status is PatchApprovalStatus.failed
    assert "stopped after 900s" in result.detail


async def test_missing_ansible_cli_is_a_failure_not_a_crash(tmp_path, monkeypatch):
    async def missing(cmd, env, *, timeout):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(executor_module, "_run", missing)

    result = await _executor(tmp_path, callback=None).execute(_approved())

    assert result.status is PatchApprovalStatus.failed
    assert "not installed" in result.detail


async def test_fallback_needs_the_control_plane_paths(tmp_path, monkeypatch):
    ansible = FakeAnsible()
    monkeypatch.setattr(executor_module, "_run", ansible)

    result = await _executor(tmp_path, callback=None, ansible_cfg_path=None).execute(_approved())

    assert result.status is PatchApprovalStatus.failed
    assert "ANSIBLE_CFG_PATH" in result.detail
    assert ansible.calls == []


async def test_execute_never_raises(tmp_path, monkeypatch):
    async def explode(cmd, env, *, timeout):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(executor_module, "_run", explode)

    result = await _executor(tmp_path, callback=None).execute(_approved())

    assert result.status is PatchApprovalStatus.failed
    assert "RuntimeError" in result.detail


async def test_run_kills_a_command_that_outlives_its_timeout():
    """The real subprocess runner, not a fake: a hung playbook must not hold
    the approval (and its worker) forever."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(TimeoutError):
        await executor_module._run(["sleep", "5"], {}, timeout=0.2)
    assert loop.time() - started < 3


def test_parse_list_hosts_reads_the_real_cli_format():
    stdout = "  hosts (2):\n    pi-01\n    pi-02\n"
    assert parse_list_hosts(stdout) == ["pi-01", "pi-02"]
    assert parse_list_hosts("  hosts (0):\n") == []
    assert parse_list_hosts("ERROR! something else") == []


def test_can_execute_needs_a_callback_or_a_playbook(tmp_path):
    assert _executor(tmp_path, playbook=False).can_execute
    assert _executor(tmp_path, callback=None).can_execute
    assert not _executor(tmp_path, callback=None, playbook=False).can_execute
