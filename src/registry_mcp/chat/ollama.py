"""Async HTTP client for a remote Ollama instance (`/api/chat`, `/api/tags`).

Follows the same shape as the other integration clients in this repo (see
`registry_mcp.integrations.traefik.client.TraefikClient`): a dedicated
`<Name>Error`, a `transport=` test seam, and an exponential-backoff retry
loop. Two deliberate departures from that shared idiom, both because this
client streams a live generation rather than fetching a JSON document:

- `chat_stream()` opens one long-lived `httpx.AsyncClient`/response for the
  whole generation instead of a fresh client per attempt — a streamed
  response can't be reopened mid-body the way a plain GET can.
- Retries only ever apply *before* the first chunk has reached the caller.
  Once content has been yielded, a transport error is fatal (raised, not
  retried) — replaying the request from the top would duplicate tokens the
  caller has already rendered.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class OllamaError(RuntimeError):
    """Raised when the Ollama API cannot be reached or returns an error."""


class OllamaClient:
    """Client for a single Ollama instance's `/api/chat` and `/api/tags`."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        timeout: float = 300.0,
        retries: int = 3,
        backoff: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._retries = max(1, retries)
        self._backoff = backoff
        self._transport = transport

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        think: bool = False,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """POST `/api/chat` with `stream: true` and yield each parsed NDJSON chunk.

        Yields Ollama's raw chunk dicts (`message`, `done`, and on the final
        chunk the `eval_count`/`eval_duration` stats) — untouched, so the
        caller decides how to interpret `message.content` vs
        `message.thinking` vs `message.tool_calls`.
        """
        url = f"{self._base}/api/chat"
        payload: dict[str, Any] = {"model": self._model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        if think:
            payload["think"] = True
        if options:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive

        last_exc: Exception | None = None
        for attempt in range(self._retries):
            started = False
            try:
                async with (
                    httpx.AsyncClient(
                        timeout=self._timeout,
                        transport=self._transport,
                    ) as client,
                    client.stream("POST", url, json=payload) as response,
                ):
                    if response.status_code >= 400:
                        body = (await response.aread())[:200]
                        if response.status_code < 500:
                            raise OllamaError(
                                f"Ollama API returned {response.status_code} for /api/chat: "
                                f"{body!r}"
                            )
                        last_exc = OllamaError(
                            f"Ollama API returned {response.status_code} for /api/chat"
                        )
                    else:
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                chunk = json.loads(line)
                            except json.JSONDecodeError as exc:
                                raise OllamaError(
                                    f"Ollama API returned malformed JSON: {line[:200]!r}"
                                ) from exc
                            started = True
                            yield chunk
                            if chunk.get("done"):
                                return
                        return
            except OllamaError:
                raise
            except httpx.HTTPError as exc:
                if started:
                    # Already streamed content to the caller — a retry here
                    # would replay the whole generation and duplicate tokens.
                    raise OllamaError(
                        f"Ollama stream to /api/chat failed after partial output: {exc}"
                    ) from exc
                last_exc = exc
            if attempt < self._retries - 1:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise OllamaError(f"Ollama API request to /api/chat failed: {last_exc}") from last_exc

    async def list_models(self) -> list[str]:
        """Return the model names Ollama currently reports via `/api/tags`."""
        url = f"{self._base}/api/tags"
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                ) as client:
                    response = await client.get(url)
                response.raise_for_status()
                try:
                    data = response.json()
                except ValueError as exc:
                    # A 2xx with a non-JSON body must surface as a
                    # controlled OllamaError — otherwise a transient upstream
                    # hiccup turns into an unhandled 500 out of
                    # /chat/api/health, which calls this method directly.
                    raise OllamaError(f"Ollama response from {url} was not valid JSON") from exc
                return [m.get("name", "") for m in data.get("models", [])]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise OllamaError(
                        f"Ollama API returned {exc.response.status_code} for /api/tags"
                    ) from exc
                last_exc = exc
            except httpx.HTTPError as exc:
                last_exc = exc
            if attempt < self._retries - 1:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise OllamaError(f"Ollama API request to /api/tags failed: {last_exc}") from last_exc
