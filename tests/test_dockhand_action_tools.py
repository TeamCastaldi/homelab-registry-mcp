"""The gated Dockhand action tools (ADR-021), driven through the real server:
request -> challenge -> confirm, against a strict POST-only Dockhand fake."""

from __future__ import annotations

import httpx
import pytest
from sqlmodel import Session, create_engine

import registry_mcp.integrations.dockhand.actions as actions
from conftest import IsolatedSettings, tool_payload
from http_fakes import strict_transport
from registry_mcp.models import PendingDockhandAction
from registry_mcp.server import build_server

AUTH = ("Authorization", "Bearer dh_secret")
ACTION_TOOLS = {
    "dockhand_container_action_request",
    "dockhand_stack_action_request",
    "dockhand_action_confirm",
}


async def call(server, name, args):
    return tool_payload(await server.call_tool(name, args))


def answer(challenge: str) -> int:
    x, _plus, y, *_ = challenge.split(" ")
    return int(x) + int(y)


async def tool_names(server) -> set[str]:
    return {tool.name for tool in await server.list_tools()}


def audit_row(tmp_path, request_id):
    """The kept challenge row: the audit trail of what happened to a request."""
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    with Session(engine) as session:
        return session.get(PendingDockhandAction, request_id)


def _settings(tmp_path, **overrides):
    repo = tmp_path / "homelab"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("")
    key = tmp_path / "id_ed25519"
    key.write_text("")
    values = dict(
        registry_db_path=str(tmp_path / "r.db"),
        secrets_repo_path=str(repo),
        ansible_cfg_path=str(cfg),
        ssh_key_path=str(key),
        dockhand_api_url="http://dockhand.test",
        dockhand_token="dh_secret",
        dockhand_allow_write=True,
    )
    values.update(overrides)
    return IsolatedSettings(**values)


@pytest.fixture
def dockhand(monkeypatch):
    """Patch the actions module's client onto a strict POST-only fake, and hand
    back the list of requests that reached it."""
    captured: list[httpx.Request] = []
    routes = {
        "POST /api/containers/abc123/restart?env=2": {"success": True},
        "POST /api/containers/boom/restart?env=2": lambda _r: httpx.Response(500),
        "POST /api/stacks/web/stop?env=3": {"success": True},
        "POST /api/stacks/web/deploy?env=3": {"success": True, "log": "x" * 6000},
    }
    transport = strict_transport(
        routes, captured=captured, allowed_methods=("POST",), auth_header=AUTH
    )
    real = actions.DockhandClient

    def factory(base_url, token, **kwargs):
        kwargs["transport"] = transport
        kwargs["backoff"] = 0
        return real(base_url, token, **kwargs)

    monkeypatch.setattr(actions, "DockhandClient", factory)
    return captured


@pytest.fixture
def server(tmp_path, dockhand):
    return build_server(_settings(tmp_path))


# --- registration -----------------------------------------------------------


async def test_the_action_tools_exist_when_writes_are_enabled(server):
    assert await tool_names(server) >= ACTION_TOOLS


@pytest.mark.parametrize(
    "overrides",
    [
        {"dockhand_allow_write": False},
        {"dockhand_api_url": None},
        {"dockhand_token": None},
    ],
)
async def test_no_action_tool_is_registered_unless_writes_and_dockhand_are_both_on(
    tmp_path, overrides
):
    names = await tool_names(build_server(_settings(tmp_path, **overrides)))
    assert not ACTION_TOOLS & names


async def test_the_read_tools_are_unaffected(server):
    assert "dockhand_list_containers" in await tool_names(server)


async def test_hints_say_what_each_tool_does(server):
    tools = {t.name: t for t in await server.list_tools()}
    for name in ("dockhand_container_action_request", "dockhand_stack_action_request"):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.openWorldHint is False  # only writes our own SQLite
    confirm = tools["dockhand_action_confirm"].annotations
    assert confirm.readOnlyHint is False
    assert confirm.destructiveHint is True
    assert confirm.openWorldHint is True  # it reaches Dockhand


# --- request ----------------------------------------------------------------


async def test_a_request_runs_nothing_and_says_what_it_would_do(server, dockhand):
    result = await call(
        server,
        "dockhand_container_action_request",
        {"environment_id": "2", "container_id": "abc123", "action": "restart"},
    )
    assert result["action"] == "restart container `abc123` in environment 2"
    assert "request_id" in result
    assert "Nothing has run" in result["next_step"]
    assert dockhand == []  # no request reached Dockhand


async def test_a_redeploy_request_warns_that_every_container_is_recreated(server, dockhand):
    result = await call(
        server,
        "dockhand_stack_action_request",
        {"environment_id": "3", "stack_name": "web", "action": "redeploy", "pull": True},
    )
    assert "recreating every container" in result["action"]
    assert "after pulling its images" in result["action"]
    assert "Every container in this stack will be recreated" in result["warning"]
    assert "images pulled first" in result["warning"]
    assert dockhand == []


async def test_pull_is_not_stored_for_actions_other_than_redeploy(server, tmp_path):
    result = await call(
        server,
        "dockhand_stack_action_request",
        {"environment_id": "3", "stack_name": "web", "action": "stop", "pull": True},
    )
    assert audit_row(tmp_path, result["request_id"]).pull is False
    assert "stopped" in result["warning"]
    redeploy = await call(
        server,
        "dockhand_stack_action_request",
        {"environment_id": "3", "stack_name": "web", "action": "redeploy", "pull": True},
    )
    assert audit_row(tmp_path, redeploy["request_id"]).pull is True


@pytest.mark.parametrize(
    ("tool", "args", "fragment"),
    [
        (
            "dockhand_container_action_request",
            {"environment_id": "2", "container_id": "abc", "action": "delete"},
            "unsupported container action",
        ),
        (
            "dockhand_container_action_request",
            {"environment_id": "2", "container_id": "../x", "action": "restart"},
            "invalid container id",
        ),
        (
            "dockhand_container_action_request",
            {"environment_id": "two", "container_id": "abc", "action": "restart"},
            "environment_id",
        ),
        (
            "dockhand_stack_action_request",
            {"environment_id": "2", "stack_name": "web", "action": "down"},
            "unsupported stack action",
        ),
        (
            "dockhand_stack_action_request",
            {"environment_id": "2", "stack_name": "a/b", "action": "restart"},
            "invalid stack name",
        ),
    ],
)
async def test_a_bad_request_is_an_error_and_stores_nothing(server, dockhand, tool, args, fragment):
    result = await call(server, tool, args)
    assert fragment in result["error"]
    assert "request_id" not in result
    assert dockhand == []


# --- confirm ----------------------------------------------------------------


async def test_confirm_runs_the_stored_container_action_once(server, dockhand, tmp_path):
    requested = await call(
        server,
        "dockhand_container_action_request",
        {"environment_id": "2", "container_id": "abc123", "action": "restart"},
    )
    result = await call(
        server,
        "dockhand_action_confirm",
        {"request_id": requested["request_id"], "answer": answer(requested["challenge"])},
    )
    assert result["executed"] is True
    assert result["status_code"] == 200
    assert result["action"] == "restart container `abc123` in environment 2"
    row = audit_row(tmp_path, requested["request_id"])
    assert (row.status.value, row.result) == ("executed", "HTTP 200")
    (request,) = dockhand
    assert (request.method, request.url.path, request.url.query) == (
        "POST",
        "/api/containers/abc123/restart",
        b"env=2",
    )


async def test_confirm_runs_a_stack_redeploy_with_the_stored_flags(server, dockhand):
    requested = await call(
        server,
        "dockhand_stack_action_request",
        {"environment_id": "3", "stack_name": "web", "action": "redeploy", "pull": True},
    )
    result = await call(
        server,
        "dockhand_action_confirm",
        {"request_id": requested["request_id"], "answer": answer(requested["challenge"])},
    )
    assert result["executed"] is True
    (request,) = dockhand
    assert request.url.path == "/api/stacks/web/deploy"
    assert request.read() == b'{"pull":true,"build":false,"forceRecreate":true}'


async def test_a_long_response_is_bounded(server):
    requested = await call(
        server,
        "dockhand_stack_action_request",
        {"environment_id": "3", "stack_name": "web", "action": "redeploy"},
    )
    result = await call(
        server,
        "dockhand_action_confirm",
        {"request_id": requested["request_id"], "answer": answer(requested["challenge"])},
    )
    assert result["response"]["truncated"] is True
    assert len(result["response"]["preview"]) == 4000


async def test_a_wrong_answer_runs_nothing_and_kills_the_challenge(server, dockhand):
    requested = await call(
        server,
        "dockhand_container_action_request",
        {"environment_id": "2", "container_id": "abc123", "action": "restart"},
    )
    wrong = await call(
        server,
        "dockhand_action_confirm",
        {"request_id": requested["request_id"], "answer": answer(requested["challenge"]) + 1},
    )
    assert "incorrect" in wrong["error"]
    retry = await call(
        server,
        "dockhand_action_confirm",
        {"request_id": requested["request_id"], "answer": answer(requested["challenge"])},
    )
    assert "wrong_answer" in retry["error"]
    assert dockhand == []


async def test_a_challenge_runs_the_action_only_once(server, dockhand):
    requested = await call(
        server,
        "dockhand_container_action_request",
        {"environment_id": "2", "container_id": "abc123", "action": "restart"},
    )
    args = {"request_id": requested["request_id"], "answer": answer(requested["challenge"])}
    assert (await call(server, "dockhand_action_confirm", args))["executed"] is True
    again = await call(server, "dockhand_action_confirm", args)
    assert "error" in again
    assert len(dockhand) == 1


async def test_confirm_takes_no_target_so_nothing_can_be_swapped_in(server):
    tool = next(t for t in await server.list_tools() if t.name == "dockhand_action_confirm")
    assert set(tool.inputSchema["properties"]) == {"request_id", "answer"}


async def test_a_dockhand_failure_is_reported_once_and_recorded(server, dockhand, tmp_path):
    requested = await call(
        server,
        "dockhand_container_action_request",
        {"environment_id": "2", "container_id": "boom", "action": "restart"},
    )
    result = await call(
        server,
        "dockhand_action_confirm",
        {"request_id": requested["request_id"], "answer": answer(requested["challenge"])},
    )
    assert "500" in result["error"]
    assert result["request_id"] == requested["request_id"]
    assert len(dockhand) == 1  # a failed write is not retried
    row = audit_row(tmp_path, requested["request_id"])
    assert row.status.value == "failed"
    assert "500" in row.result


async def test_an_unknown_challenge_is_an_error(server):
    result = await call(server, "dockhand_action_confirm", {"request_id": "nope", "answer": 3})
    assert "no Dockhand action challenge" in result["error"]


async def test_the_token_never_appears_in_a_result(server):
    requested = await call(
        server,
        "dockhand_container_action_request",
        {"environment_id": "2", "container_id": "boom", "action": "restart"},
    )
    result = await call(
        server,
        "dockhand_action_confirm",
        {"request_id": requested["request_id"], "answer": answer(requested["challenge"])},
    )
    assert "dh_secret" not in str(result) + str(requested)


# --- read-only mode ---------------------------------------------------------


@pytest.fixture
def unhealthy_server(tmp_path, dockhand):
    """Dockhand and writes configured, but the startup health check fails."""
    return build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            dockhand_api_url="http://dockhand.test",
            dockhand_token="dh_secret",
            dockhand_allow_write=True,
        )
    )


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (
            "dockhand_container_action_request",
            {"environment_id": "2", "container_id": "abc", "action": "restart"},
        ),
        (
            "dockhand_stack_action_request",
            {"environment_id": "2", "stack_name": "web", "action": "restart"},
        ),
        ("dockhand_action_confirm", {"request_id": "x", "answer": 1}),
    ],
)
async def test_read_only_mode_refuses_every_action_tool(unhealthy_server, dockhand, tool, args):
    result = await call(unhealthy_server, tool, args)
    assert "read-only mode" in result["error"]
    assert dockhand == []


async def test_read_only_mode_does_not_run_a_challenge_issued_before_it(
    tmp_path, dockhand, monkeypatch
):
    healthy = build_server(_settings(tmp_path))
    requested = await call(
        healthy,
        "dockhand_container_action_request",
        {"environment_id": "2", "container_id": "abc123", "action": "restart"},
    )
    degraded = build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            dockhand_api_url="http://dockhand.test",
            dockhand_token="dh_secret",
            dockhand_allow_write=True,
        )
    )
    result = await call(
        degraded,
        "dockhand_action_confirm",
        {"request_id": requested["request_id"], "answer": answer(requested["challenge"])},
    )
    assert "read-only mode" in result["error"]
    assert dockhand == []
