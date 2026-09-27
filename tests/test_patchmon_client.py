"""Tests for the read-only PatchMon Integration API client (ADR-020, amended).

The fake is `strict_transport`: GET only (anything else is a 405), and a 401
unless the exact HTTP Basic header for the configured Token Key and Secret is
sent, the way PatchMon's `ApiAuth` middleware answers.
"""

import base64

import httpx
import pytest

from conftest import IsolatedSettings
from http_fakes import strict_transport
from registry_mcp.integrations.patchmon import (
    PatchmonClient,
    PatchmonError,
    build_patchmon_client,
)

BASE = "https://patchmon.test"
KEY = "patchmon_ae_0123456789"
SECRET = "token-secret-value"
HOST_ID = "0b5f2a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
HOSTS = f"/api/v1/api/hosts/{HOST_ID}"
BASIC = "Basic " + base64.b64encode(f"{KEY}:{SECRET}".encode()).decode()

INFO = {
    "id": HOST_ID,
    "machine_id": "abc",
    "friendly_name": "Living Room Pi",
    "hostname": "pi-01",
    "ip": "192.168.1.10",
    "os_type": "Debian",
    "os_version": "12",
    "agent_version": "2.0.3",
    "host_groups": [],
}
SYSTEM = {
    "id": HOST_ID,
    "kernel_version": "6.1.0-25-arm64",
    "installed_kernel_version": "6.1.0-26-arm64",
    "needs_reboot": True,
    "reboot_reason": "Kernel update pending",
}
PACKAGES = {
    "host": {"id": HOST_ID, "hostname": "pi-01", "friendly_name": "Living Room Pi"},
    "packages": [
        {
            "name": "openssl",
            "current_version": "3.0.13-1",
            "available_version": "3.0.14-1",
            "needs_update": True,
            "is_security_update": True,
        }
    ],
    "total": 1,
}
REPORTS = {
    "host_id": HOST_ID,
    "reports": [{"status": "success", "date": "2026-09-27T10:30:00Z", "security_updates": 1}],
    "total": 1,
}
ROUTES = {
    f"GET {HOSTS}/info": INFO,
    f"GET {HOSTS}/system": SYSTEM,
    f"GET {HOSTS}/packages?updates_only=true": PACKAGES,
    f"GET {HOSTS}/packages": {**PACKAGES, "total": 1},
    f"GET {HOSTS}/package_reports?limit=1": REPORTS,
}


def _client(routes=None, *, captured=None, base=BASE, secret=SECRET, retries=2):
    transport = strict_transport(
        ROUTES if routes is None else routes,
        captured=captured,
        auth_header=("Authorization", BASIC),
    )
    return PatchmonClient(base, KEY, secret, retries=retries, backoff=0, transport=transport)


async def test_each_read_reaches_its_endpoint_with_basic_auth():
    captured = []
    client = _client(captured=captured)

    assert (await client.get_host_info(HOST_ID))["hostname"] == "pi-01"
    assert (await client.get_host_system(HOST_ID))["needs_reboot"] is True
    assert [p["name"] for p in await client.list_host_packages(HOST_ID)] == ["openssl"]
    assert len(await client.list_host_packages(HOST_ID, updates_only=False)) == 1
    assert (await client.list_package_reports(HOST_ID))[0]["status"] == "success"

    # Read-only: the strict fake would have 405'd anything else.
    assert {request.method for request in captured} == {"GET"}
    assert {request.url.host for request in captured} == {"patchmon.test"}


async def test_the_updates_only_filter_actually_reaches_the_request():
    """Without it PatchMon returns every installed package, hundreds of them."""
    routes = {f"GET {HOSTS}/packages?updates_only=true": PACKAGES}
    assert len(await _client(routes).list_host_packages(HOST_ID)) == 1
    with pytest.raises(PatchmonError, match="404"):
        await _client(routes).list_host_packages(HOST_ID, updates_only=False)


async def test_a_wrong_secret_is_reported_without_echoing_it_or_retrying():
    captured = []
    with pytest.raises(PatchmonError) as raised:
        await _client(captured=captured, secret="wrong-secret").get_host_info(HOST_ID)

    message = str(raised.value)
    assert "401" in message and "Token Key or Token Secret" in message
    assert "wrong-secret" not in message and KEY not in message
    assert len(captured) == 1


@pytest.mark.parametrize(
    "host_id",
    ["../../settings", f"{HOST_ID}/../..", f"{HOST_ID}?x=1", "pi-01", "", HOST_ID + "0"],
)
async def test_anything_but_a_uuid_never_reaches_a_url_path(host_id):
    captured = []
    with pytest.raises(PatchmonError, match="UUID"):
        await _client(captured=captured).get_host_info(host_id)
    assert captured == []


async def test_a_server_error_is_retried_then_succeeds():
    attempts = []

    def flaky(request):
        attempts.append(request)
        return httpx.Response(503) if len(attempts) == 1 else httpx.Response(200, json=INFO)

    info = await _client({f"GET {HOSTS}/info": flaky}).get_host_info(HOST_ID)

    assert info["hostname"] == "pi-01"
    assert len(attempts) == 2


async def test_retries_run_out_on_a_server_that_stays_down():
    attempts = []

    def down(request):
        attempts.append(request)
        return httpx.Response(502)

    with pytest.raises(PatchmonError, match="failed"):
        await _client({f"GET {HOSTS}/info": down}, retries=3).get_host_info(HOST_ID)
    assert len(attempts) == 3


async def test_a_redirect_is_never_followed_with_the_credentials():
    captured = []
    routes = {
        f"GET {HOSTS}/info": lambda request: httpx.Response(
            302, headers={"Location": "https://elsewhere.test/collect"}
        )
    }
    with pytest.raises(PatchmonError, match="302"):
        await _client(routes, captured=captured).get_host_info(HOST_ID)
    assert [request.url.host for request in captured] == ["patchmon.test"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>login</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_a_body_that_is_not_a_json_object_is_an_error(response):
    with pytest.raises(PatchmonError, match="info"):
        await _client({f"GET {HOSTS}/info": lambda request: response}).get_host_info(HOST_ID)


async def test_packages_without_a_list_is_an_error_not_an_empty_list():
    """An empty list would read as "nothing pending" in the approval email."""
    routes = {f"GET {HOSTS}/packages?updates_only=true": {"error": "Host not found"}}
    with pytest.raises(PatchmonError, match="packages"):
        await _client(routes).list_host_packages(HOST_ID)


@pytest.mark.parametrize(
    "base",
    [
        "https://patchmon.test",
        "https://patchmon.test/",
        "https://patchmon.test/api/v1/api/hosts/",  # the dynamic-inventory plugin's api_url
        "https://patchmon.test/api/v1",
    ],
)
async def test_the_instance_root_or_a_pasted_endpoint_url_both_work(base):
    assert (await _client(base=base).get_host_info(HOST_ID))["id"] == HOST_ID


def _settings(**overrides):
    base = dict(
        patchmon_api_url=BASE,
        patchmon_api_key=KEY,
        patchmon_api_secret=SECRET,
        patchmon_api_timeout_seconds=3,
    )
    base.update(overrides)
    return IsolatedSettings(**base)


async def test_the_client_is_built_from_settings():
    transport = strict_transport(ROUTES, auth_header=("Authorization", BASIC))
    client = build_patchmon_client(_settings(), transport=transport)
    assert (await client.get_host_info(HOST_ID))["hostname"] == "pi-01"


@pytest.mark.parametrize(
    "overrides",
    [
        {"patchmon_api_url": None},
        {"patchmon_api_url": "  "},
        {"patchmon_api_key": None},
        {"patchmon_api_secret": None},
        {"patchmon_api_secret": "   "},
    ],
    ids=["no-url", "blank-url", "no-key", "no-secret", "blank-secret"],
)
def test_no_client_without_all_three_settings(overrides):
    assert build_patchmon_client(_settings(**overrides)) is None
