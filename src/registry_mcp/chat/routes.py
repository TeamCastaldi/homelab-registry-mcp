"""HTTP surface for the chat UI, registered on the FastMCP server via
`@mcp.custom_route`. `register_chat_routes()` is called once from
`build_server()` (see `server.py`), in the same "pure, cheap, called once at
startup" spot every other tool registrar occupies — so it does no network or
filesystem I/O itself: OIDC discovery and the persona overlay are both read
lazily, on first use.

Route map:
  GET  /chat                  the page (redirects to login if no session)
  GET  /chat/auth/login       OIDC redirect, or renders the password form
  GET  /chat/auth/callback    OIDC code exchange
  POST /chat/auth/password    password-mode sign-in
  POST /chat/auth/logout      clears the session cookie
  GET  /chat/api/health       Ollama reachability + configured model
  POST /chat/api/send         the SSE chat stream

See `registry_mcp.chat.__init__` for why this module (and everything else
under `chat/`) never receives a store/engine reference directly, and
ADR-009 for the auth design rationale and its explicit limits — most
importantly that this login gates the chat UI only, not `/mcp`, which stays
unauthenticated exactly as it is today.
"""

from __future__ import annotations

import html
import json
import secrets
from collections.abc import AsyncIterator
from importlib import resources

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from registry_mcp.chat import auth, bridge
from registry_mcp.chat.agent import format_sse, run_chat
from registry_mcp.chat.ollama import OllamaClient, OllamaError
from registry_mcp.chat.session import (
    Identity,
    issue_flow,
    issue_session,
    resolve_secret,
    session_needs_refresh,
    verify_flow,
    verify_session,
)
from registry_mcp.config import Settings
from registry_mcp.logging import get_logger

_log = get_logger("chat.routes")

_SESSION_COOKIE = "rmcp_chat_session"
_FLOW_COOKIE = "rmcp_chat_flow"
_FLOW_TTL_SECONDS = 300

_index_html_cache: str | None = None


def _load_index_html() -> str:
    global _index_html_cache
    if _index_html_cache is None:
        _index_html_cache = (
            resources.files("registry_mcp.chat.static")
            .joinpath("index.html")
            .read_text(encoding="utf-8")
        )
    return _index_html_cache


def _client_ip(request: Request) -> str:
    """Best-effort identity for the login rate limiter.

    `request.client.host` is the direct TCP peer — in a real deployment
    that's Traefik, not the browser, which would collapse every user behind
    the proxy into one shared rate-limit bucket. Prefer the standard
    `X-Forwarded-For` header (which Traefik sets) when present. This is
    still just a speed bump, not a hard boundary — see
    `LoginAttemptLimiter`'s own docstring — so a forged header from a
    client connecting directly to :8765 (bypassing Traefik, which this
    deployment already tolerates on the LAN) only defeats the rate limiter,
    nothing more.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _safe_next_path(raw: str | None) -> str:
    """Only ever redirect somewhere under /chat — never trust a caller-
    supplied `next` as an absolute or scheme-relative URL (open redirect)."""
    if not raw or not raw.startswith("/chat") or raw.startswith("//"):
        return "/chat"
    return raw


def _password_form_html(next_path: str, *, error: str | None = None) -> str:
    error_html = f'<p class="err">{html.escape(error)}</p>' if error else ""
    safe_next = html.escape(next_path, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in — Homelab Chat</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 320px; margin: 18vh auto;
    padding: 0 1rem; }}
  input {{ width: 100%; padding: 0.6rem; margin: 0.5rem 0; box-sizing: border-box; }}
  button {{ width: 100%; padding: 0.6rem; cursor: pointer; }}
  .err {{ color: #b91c1c; font-size: 0.9rem; }}
</style></head>
<body>
<h2>Sign in</h2>
{error_html}
<form method="post" action="/chat/auth/password">
<input type="hidden" name="next" value="{safe_next}">
<input type="password" name="password" placeholder="Password" autofocus required>
<button type="submit">Sign in</button>
</form>
</body></html>"""


def register_chat_routes(mcp: FastMCP, settings: Settings, *, read_only: bool) -> None:
    """Register the /chat* routes, or do nothing when chat isn't usable.

    "Usable" means `CHAT_ENABLED=true` *and* at least one auth method
    resolves. `CHAT_ENABLED=true` with neither OIDC nor `CHAT_PASSWORD`
    configured is a startup misconfiguration — logged and left
    unregistered, never an open endpoint.
    """
    if not settings.chat_enabled:
        return

    mode = auth.resolve_mode(settings)
    if mode == "disabled":
        _log.error(
            "chat_disabled_no_auth_configured",
            detail=(
                "CHAT_ENABLED=true but neither CHAT_OIDC_* nor CHAT_PASSWORD is "
                "set; chat routes will not be registered."
            ),
        )
        return

    secret = resolve_secret(settings.chat_session_secret)
    if not settings.chat_session_secret:
        _log.warning(
            "chat_session_secret_ephemeral",
            detail="CHAT_SESSION_SECRET is unset; sessions will not survive a restart.",
        )

    login_limiter = auth.LoginAttemptLimiter()
    allowed_origins = {o.strip() for o in settings.chat_allowed_origins.split(",") if o.strip()}
    active_sends = 0  # single-process, single-loop counter — see send() below

    def _identity(request: Request) -> Identity | None:
        token = request.cookies.get(_SESSION_COOKIE)
        if not token:
            return None
        identity = verify_session(secret, token)
        if identity is None:
            return None
        # Re-checked live rather than trusted from the token: group
        # membership can change after a session was issued. A session that
        # fails this now simply looks logged-out; a fresh OIDC login
        # re-fetches current groups and re-gates at the callback below, so
        # this can't loop — it resolves in at most one extra round trip.
        if identity.amr == "oidc" and not auth.groups_allowed(settings, identity.groups):
            return None
        return identity

    def _set_session_cookie(response: Response, identity: Identity) -> None:
        token = issue_session(secret, identity, ttl_seconds=settings.chat_session_ttl_seconds)
        response.set_cookie(
            _SESSION_COOKIE,
            token,
            max_age=settings.chat_session_ttl_seconds,
            httponly=True,
            samesite="lax",
            secure=settings.chat_cookie_secure,
            path="/chat",
        )

    @mcp.custom_route("/chat", methods=["GET"])
    async def chat_page(request: Request) -> Response:
        identity = _identity(request)
        if identity is None:
            return RedirectResponse(url="/chat/auth/login?next=/chat", status_code=302)

        nonce = secrets.token_urlsafe(16)
        body = _load_index_html().replace("__CSP_NONCE__", nonce)
        response = HTMLResponse(body)
        response.headers["Content-Security-Policy"] = (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        token = request.cookies.get(_SESSION_COOKIE, "")
        if session_needs_refresh(secret, token, ttl_seconds=settings.chat_session_ttl_seconds):
            _set_session_cookie(response, identity)
        return response

    @mcp.custom_route("/chat/auth/login", methods=["GET"])
    async def login(request: Request) -> Response:
        next_path = _safe_next_path(request.query_params.get("next"))

        if mode == "password":
            return HTMLResponse(_password_form_html(next_path))

        # mode == "oidc"
        try:
            endpoints = await auth.discover_oidc(settings.chat_oidc_issuer or "")
        except auth.OidcError as exc:
            _log.warning("chat_oidc_discovery_failed", error=str(exc))
            return Response("Identity provider is unreachable. Try again shortly.", status_code=503)

        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        verifier, challenge = auth.generate_pkce()
        authorize_url = auth.build_authorize_url(
            endpoints, settings, state=state, code_challenge=challenge
        )

        response = RedirectResponse(url=authorize_url, status_code=302)
        flow_token = issue_flow(
            secret,
            state=state,
            verifier=verifier,
            nonce=nonce,
            next_path=next_path,
            ttl_seconds=_FLOW_TTL_SECONDS,
        )
        response.set_cookie(
            _FLOW_COOKIE,
            flow_token,
            max_age=_FLOW_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=settings.chat_cookie_secure,
            path="/chat/auth",
        )
        return response

    @mcp.custom_route("/chat/auth/callback", methods=["GET"])
    async def callback(request: Request) -> Response:
        if mode != "oidc":
            return Response("Not found", status_code=404)

        flow = verify_flow(secret, request.cookies.get(_FLOW_COOKIE, ""))
        if flow is None:
            return Response("Login session expired or invalid. Go back and try again.", 400)

        error = request.query_params.get("error")
        if error:
            return Response(f"Login failed: {html.escape(error)}", status_code=400)

        state = request.query_params.get("state", "")
        if not secrets.compare_digest(state, flow.state):
            return Response("Login state mismatch. Go back and try again.", status_code=400)

        code = request.query_params.get("code")
        if not code:
            return Response("Identity provider did not return a code.", status_code=400)

        try:
            endpoints = await auth.discover_oidc(settings.chat_oidc_issuer or "")
            tokens = await auth.exchange_code(
                endpoints, settings, code=code, code_verifier=flow.verifier
            )
            access_token = tokens["access_token"]
            userinfo = await auth.fetch_userinfo(endpoints, access_token=access_token)
        except (auth.OidcError, KeyError) as exc:
            _log.warning("chat_oidc_callback_failed", error=str(exc))
            return Response("Login failed while contacting the identity provider.", status_code=502)

        identity = auth.identity_from_userinfo(userinfo)
        if not identity.sub:
            return Response("Identity provider did not return a subject.", status_code=502)
        if not auth.groups_allowed(settings, identity.groups):
            return Response("You are not a member of an allowed group.", status_code=403)

        response = RedirectResponse(url=flow.next_path, status_code=302)
        _set_session_cookie(response, identity)
        response.delete_cookie(_FLOW_COOKIE, path="/chat/auth")
        return response

    @mcp.custom_route("/chat/auth/password", methods=["POST"])
    async def password_login(request: Request) -> Response:
        if mode != "password":
            return Response("Not found", status_code=404)

        ip = _client_ip(request)
        if login_limiter.is_blocked(ip):
            return Response("Too many attempts. Try again in a minute.", status_code=429)

        form = await request.form()
        candidate = str(form.get("password", ""))
        next_path = _safe_next_path(str(form.get("next", "")))

        if not auth.check_password(settings, candidate):
            login_limiter.record_failure(ip)
            return HTMLResponse(
                _password_form_html(next_path, error="Incorrect password."), status_code=401
            )

        login_limiter.record_success(ip)
        identity = Identity(sub="static", name="Operator", email=None, groups=(), amr="password")
        response = RedirectResponse(url=next_path, status_code=302)
        _set_session_cookie(response, identity)
        return response

    @mcp.custom_route("/chat/auth/logout", methods=["POST"])
    async def logout(_request: Request) -> Response:
        response = JSONResponse({"ok": True})
        response.delete_cookie(_SESSION_COOKIE, path="/chat")
        return response

    @mcp.custom_route("/chat/api/health", methods=["GET"])
    async def chat_api_health(request: Request) -> Response:
        if _identity(request) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not settings.chat_ollama_url:
            return JSONResponse({"ok": False, "error": "CHAT_OLLAMA_URL is not configured"})
        client = OllamaClient(
            settings.chat_ollama_url, model=settings.chat_ollama_model, timeout=10.0, retries=1
        )
        try:
            models = await client.list_models()
        except OllamaError as exc:
            return JSONResponse({"ok": False, "error": str(exc)})
        return JSONResponse(
            {"ok": True, "models": models, "configured_model": settings.chat_ollama_model}
        )

    @mcp.custom_route("/chat/api/send", methods=["POST"])
    async def send(request: Request) -> Response:
        identity = _identity(request)
        if identity is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "application/json":
            return JSONResponse({"error": "expected application/json"}, status_code=400)

        if allowed_origins:
            origin = request.headers.get("origin")
            if origin and origin not in allowed_origins:
                return JSONResponse({"error": "origin not allowed"}, status_code=403)

        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)

        raw_messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(raw_messages, list):
            return JSONResponse({"error": "'messages' must be a list"}, status_code=400)

        # Server-enforced cap regardless of what the client sends. Dropped,
        # never trusted from the request body:
        #   - "system": the system prompt is always server-derived.
        #   - "tool": a client could otherwise fabricate a fake prior tool
        #     result and feed it back as if the server had produced it,
        #     which carries more authority than an ordinary user/assistant
        #     turn in a tool-calling chat format.
        #   - any "assistant" message carrying tool_calls: without the
        #     "tool" response(s) it called for (just dropped above), it's a
        #     dangling call Ollama never resolved — malformed on resend.
        # The real cost is that tool context doesn't survive past the turn
        # it was produced in; the model just re-calls the tool if it needs
        # that data again, a safe tradeoff given the client-side-history
        # design (see ADR-009).
        history = [
            m
            for m in raw_messages
            if isinstance(m, dict)
            and m.get("role") not in ("system", "tool")
            and not (m.get("role") == "assistant" and m.get("tool_calls"))
        ][-settings.chat_max_history_messages :]

        if not settings.chat_ollama_url:
            return JSONResponse({"error": "CHAT_OLLAMA_URL is not configured"}, status_code=503)

        allowed_tools = bridge.allowed_tool_names(settings, read_only=read_only)

        async def stream() -> AsyncIterator[bytes]:
            nonlocal active_sends
            if active_sends >= settings.chat_max_concurrent:
                yield format_sse(
                    "error",
                    {
                        "kind": "busy",
                        "message": "Another chat request is in progress. Try again shortly.",
                    },
                )
                return
            active_sends += 1
            try:
                async for frame in run_chat(
                    mcp, settings, allowed_tools=allowed_tools, history=history
                ):
                    yield frame
            finally:
                active_sends -= 1

        response = StreamingResponse(stream(), media_type="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Connection"] = "keep-alive"
        return response

    _log.info(
        "chat_routes_registered",
        mode=mode,
        allow_write=settings.chat_allow_write and not read_only,
    )
