"""A stricter alternative to a path-only `httpx.MockTransport` fake.

`tests/README.md`'s own rule is that a fake must be no more forgiving than the
real service it stands in for. The integration fakes that routed on
`request.url.path` alone (Dockhand, Traefik, Authentik) satisfied that rule
for the happy path but not for the invariants those services actually
enforce: a read-only client must never send anything but GET, a query-string
filter must actually reach the request, and an auth header must actually be
sent. `docs/plans/2026-09-test-suite-audit.md`'s Tier 1 names the exact gaps
this closes (K1-K3, U1, U3, R1) — a mutation that broke one of those used to
pass the whole suite because the fake matched on path alone.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

RouteBody = dict[str, Any] | list[Any] | Callable[[httpx.Request], httpx.Response]


def strict_transport(
    routes: dict[str, RouteBody],
    *,
    captured: list[httpx.Request] | None = None,
    allowed_methods: tuple[str, ...] = ("GET",),
    auth_header: tuple[str, str] | None = None,
) -> httpx.MockTransport:
    """Build an `httpx.MockTransport` that fails closed on anything a real
    read-only upstream would also reject.

    `routes` keys are `"METHOD /path"`, with an optional `?query` suffix
    (params sorted by key, `&`-joined) when the endpoint's query string is
    part of what's under test — a route with no `?query` matches only a
    request with no query string, so a filter that's supposed to reach the
    request has to be asserted for explicitly rather than matching by
    accident. A route value is a JSON-able body for a 200, or a callable
    taking the request and returning the full `httpx.Response` for anything
    else (a non-200, a header-dependent body, ...).

    A request whose method isn't in `allowed_methods` (default: GET only,
    the read-only invariant every upstream integration in this repo relies
    on) gets a 405 rather than being routed at all. When `auth_header` is
    given as `(name, expected_value)`, a request missing it or sending the
    wrong value gets a 401 instead of being routed — so a client that quietly
    stops sending its token is caught here, not silently served a 200.

    Any request with no matching route gets a 404, same as the path-only
    fakes this replaces — never a fallback to the closest match.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        if request.method not in allowed_methods:
            return httpx.Response(405, json={"detail": f"method {request.method} not allowed"})
        if auth_header is not None:
            name, expected = auth_header
            if request.headers.get(name) != expected:
                return httpx.Response(401, json={"detail": "missing or wrong auth header"})
        query = "&".join(f"{k}={v}" for k, v in sorted(request.url.params.multi_items()))
        key = f"{request.method} {request.url.path}"
        if query:
            key = f"{key}?{query}"
        body = routes.get(key)
        if body is None:
            return httpx.Response(404, json={"detail": f"no route for {key}"})
        if callable(body):
            return body(request)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)
