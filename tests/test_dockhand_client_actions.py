"""The Dockhand client's write path (ADR-021): a closed allowlist of container
and stack actions, each sent once, to a validated path."""

import httpx
import pytest

from http_fakes import strict_transport
from registry_mcp.integrations.dockhand import DockhandClient, DockhandError

AUTH = ("Authorization", "Bearer dh_secret")
OK = {"success": True}


def client(routes=None, captured=None, **kwargs):
    transport = strict_transport(
        routes or {},
        captured=captured,
        allowed_methods=("POST",),
        auth_header=AUTH,
    )
    return DockhandClient("http://d", "dh_secret", transport=transport, backoff=0, **kwargs)


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
async def test_a_container_action_posts_to_its_own_endpoint(action):
    captured: list[httpx.Request] = []
    routes = {f"POST /api/containers/abc123/{action}?env=2": OK}
    result = await client(routes, captured).container_action("2", "abc123", action)
    assert result == {"status_code": 200, "response": OK}
    (request,) = captured
    assert request.headers["Accept"] == "application/json"
    assert request.content == b""


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
async def test_a_stack_action_posts_to_its_own_endpoint(action):
    routes = {f"POST /api/stacks/web/{action}?env=3": OK}
    assert (await client(routes).stack_action("3", "web", action))["status_code"] == 200


@pytest.mark.parametrize(("pull", "expected"), [(False, False), (True, True)])
async def test_redeploy_always_forces_recreate_and_never_builds(pull, expected):
    captured: list[httpx.Request] = []
    routes = {"POST /api/stacks/web/deploy?env=3": OK}
    await client(routes, captured).stack_action("3", "web", "redeploy", pull=pull)
    (request,) = captured
    assert request.headers["Accept"] == "application/json"
    assert request.read() == (
        b'{"pull":%s,"build":false,"forceRecreate":true}' % (b"true" if expected else b"false")
    )


@pytest.mark.parametrize(
    "action", ["delete", "down", "pause", "unpause", "rename", "exec", "update", "redeploy", ""]
)
async def test_a_container_action_outside_the_allowlist_sends_nothing(action):
    captured: list[httpx.Request] = []
    with pytest.raises(DockhandError, match="unsupported container action"):
        await client(captured=captured).container_action("1", "abc", action)
    assert captured == []


@pytest.mark.parametrize(
    "action", ["delete", "down", "pause", "build", "deploy", "restart ", "Restart", ""]
)
async def test_a_stack_action_outside_the_allowlist_sends_nothing(action):
    captured: list[httpx.Request] = []
    with pytest.raises(DockhandError, match="unsupported stack action"):
        await client(captured=captured).stack_action("1", "web", action)
    assert captured == []


BAD_IDS = [
    "",
    "a/b",
    "../x",
    "..",
    ".hidden",
    "-lead",
    "a b",
    "a?x=1",
    "a#b",
    "%2e%2e",
    "a%2fb",
    "a\n",
    "a\nb",
    "x" * 129,
]


@pytest.mark.parametrize("bad", BAD_IDS)
async def test_an_unsafe_container_id_never_reaches_a_url(bad):
    captured: list[httpx.Request] = []
    with pytest.raises(DockhandError, match="invalid container id"):
        await client(captured=captured).container_action("1", bad, "restart")
    assert captured == []


@pytest.mark.parametrize("bad", BAD_IDS)
async def test_an_unsafe_stack_name_never_reaches_a_url(bad):
    captured: list[httpx.Request] = []
    with pytest.raises(DockhandError, match="invalid stack name"):
        await client(captured=captured).stack_action("1", bad, "restart")
    assert captured == []


@pytest.mark.parametrize("bad", ["", "abc", "1;2", "-1", "1 ", "1.0", "1\n", "1" * 10, "1&x=2"])
async def test_an_environment_id_must_be_digits(bad):
    captured: list[httpx.Request] = []
    with pytest.raises(DockhandError, match="environment_id"):
        await client(captured=captured).container_action(bad, "abc", "restart")
    assert captured == []


async def test_a_failed_write_is_sent_once_and_not_retried():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(503)

    dockhand = DockhandClient(
        "http://d", "dh_secret", transport=httpx.MockTransport(handler), retries=3, backoff=0
    )
    with pytest.raises(DockhandError, match="503"):
        await dockhand.container_action("1", "abc", "restart")
    assert calls["n"] == 1


async def test_a_dropped_connection_says_the_outcome_is_unknown_and_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    dockhand = DockhandClient(
        "http://d", "dh_secret", transport=httpx.MockTransport(handler), retries=3, backoff=0
    )
    with pytest.raises(DockhandError, match="may or may not have been applied"):
        await dockhand.stack_action("1", "web", "redeploy")
    assert calls["n"] == 1


async def test_a_redirect_is_not_followed():
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://elsewhere.test/steal"})

    dockhand = DockhandClient("http://d", "dh_secret", transport=httpx.MockTransport(handler))
    with pytest.raises(DockhandError, match="302"):
        await dockhand.container_action("1", "abc", "restart")
    assert len(seen) == 1
    assert "elsewhere.test" not in seen[0]


async def test_a_200_that_reports_failure_is_an_error():
    routes = {
        "POST /api/containers/abc/restart?env=1": {"success": False, "error": "no\nsuch  box"}
    }
    with pytest.raises(DockhandError, match="reported failure.*no such box"):
        await client(routes).container_action("1", "abc", "restart")


async def test_a_missing_or_wrong_token_is_rejected_by_the_fake():
    routes = {"POST /api/containers/abc/restart?env=1": OK}
    transport = strict_transport(routes, allowed_methods=("POST",), auth_header=AUTH)
    wrong = DockhandClient("http://d", "other", transport=transport)
    with pytest.raises(DockhandError, match="401"):
        await wrong.container_action("1", "abc", "restart")


async def test_the_token_does_not_appear_in_an_error():
    dockhand = DockhandClient(
        "http://d", "dh_secret", transport=httpx.MockTransport(lambda _r: httpx.Response(500))
    )
    with pytest.raises(DockhandError) as caught:
        await dockhand.container_action("1", "abc", "restart")
    assert "dh_secret" not in str(caught.value)


async def test_actions_use_their_own_timeout_and_reads_keep_the_read_timeout():
    captured: list[httpx.Request] = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=[] if request.method == "GET" else OK)

    dockhand = DockhandClient(
        "http://d",
        "dh_secret",
        transport=httpx.MockTransport(handler),
        timeout=7.0,
        action_timeout=77.0,
    )
    await dockhand.stack_action("1", "web", "restart")
    await dockhand.list_environments()
    assert captured[0].extensions["timeout"]["read"] == 77.0
    assert captured[1].extensions["timeout"]["read"] == 7.0


def test_the_client_has_no_method_for_any_other_write():
    """ADR-021's exclusions are structural: nothing to call, even by accident."""
    public = {name for name in dir(DockhandClient) if not name.startswith("_")}
    reads = {name for name in public if name.startswith(("list_", "get_"))}
    assert public - reads == {"container_action", "stack_action"}
