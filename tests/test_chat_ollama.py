"""Tests for the Ollama chat client (registry_mcp.chat.ollama)."""

import json

import httpx
import pytest

from registry_mcp.chat.ollama import OllamaClient, OllamaError


def _ndjson(*chunks: dict) -> bytes:
    return "\n".join(json.dumps(c) for c in chunks).encode() + b"\n"


def _transport(handler):
    return httpx.MockTransport(handler)


# --- chat_stream ------------------------------------------------------------


async def test_chat_stream_yields_content_and_done_chunks():
    body = _ndjson(
        {"model": "qwen3:14b", "message": {"role": "assistant", "content": "The"}, "done": False},
        {"model": "qwen3:14b", "message": {"role": "assistant", "content": " sky"}, "done": False},
        {
            "model": "qwen3:14b",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "eval_count": 12,
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3:14b"
        assert payload["stream"] is True
        return httpx.Response(200, content=body)

    client = OllamaClient(
        "http://ollama", model="qwen3:14b", transport=_transport(handler), backoff=0
    )
    chunks = [c async for c in client.chat_stream([{"role": "user", "content": "hi"}])]

    assert [c["message"]["content"] for c in chunks] == ["The", " sky", ""]
    assert chunks[-1]["done"] is True
    assert chunks[-1]["eval_count"] == 12


async def test_chat_stream_accumulates_tool_calls():
    body = _ndjson(
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "Tokyo"}}}
                ],
            },
            "done": False,
        },
        {"message": {"role": "assistant", "content": ""}, "done": True},
    )
    client = OllamaClient(
        "http://ollama",
        model="m",
        transport=_transport(lambda r: httpx.Response(200, content=body)),
        backoff=0,
    )
    chunks = [c async for c in client.chat_stream([{"role": "user", "content": "weather?"}])]
    calls = chunks[0]["message"]["tool_calls"]
    assert calls[0]["function"]["name"] == "get_weather"
    assert calls[0]["function"]["arguments"] == {"city": "Tokyo"}


async def test_chat_stream_passes_tools_think_and_options():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_ndjson({"message": {"content": ""}, "done": True}))

    client = OllamaClient("http://ollama", model="m", transport=_transport(handler), backoff=0)
    tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
    async for _ in client.chat_stream(
        [{"role": "user", "content": "hi"}],
        tools=tools,
        think=True,
        options={"num_ctx": 4096},
        keep_alive="30m",
    ):
        pass

    assert seen["tools"] == tools
    assert seen["think"] is True
    assert seen["options"] == {"num_ctx": 4096}
    assert seen["keep_alive"] == "30m"


async def test_chat_stream_omits_optional_fields_by_default():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_ndjson({"message": {"content": ""}, "done": True}))

    client = OllamaClient("http://ollama", model="m", transport=_transport(handler), backoff=0)
    async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
        pass

    assert "tools" not in seen
    assert "think" not in seen
    assert "options" not in seen
    assert "keep_alive" not in seen


async def test_chat_stream_4xx_fails_fast_no_retry():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(400, content=b"bad request")

    client = OllamaClient(
        "http://ollama", model="m", transport=_transport(handler), retries=3, backoff=0
    )
    with pytest.raises(OllamaError):
        async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass
    assert calls["n"] == 1


async def test_chat_stream_5xx_retries_then_succeeds():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=_ndjson({"message": {"content": "ok"}, "done": True}))

    client = OllamaClient(
        "http://ollama", model="m", transport=_transport(handler), retries=3, backoff=0
    )
    chunks = [c async for c in client.chat_stream([{"role": "user", "content": "hi"}])]
    assert calls["n"] == 3
    assert chunks[-1]["message"]["content"] == "ok"


async def test_chat_stream_5xx_exhausts_retries_and_raises():
    def handler(_request):
        return httpx.Response(503)

    client = OllamaClient(
        "http://ollama", model="m", transport=_transport(handler), retries=2, backoff=0
    )
    with pytest.raises(OllamaError):
        async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass


async def test_chat_stream_malformed_json_raises_without_retry():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(200, content=b"not json\n")

    client = OllamaClient(
        "http://ollama", model="m", transport=_transport(handler), retries=3, backoff=0
    )
    with pytest.raises(OllamaError):
        async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass
    # Malformed content is fatal, not a transient transport failure — never retried.
    assert calls["n"] == 1


async def test_chat_stream_skips_blank_lines():
    body = (
        b'{"message": {"content": "a"}, "done": false}\n'
        b"\n"
        b'{"message": {"content": ""}, "done": true}\n'
    )
    client = OllamaClient(
        "http://ollama",
        model="m",
        transport=_transport(lambda r: httpx.Response(200, content=body)),
        backoff=0,
    )
    chunks = [c async for c in client.chat_stream([{"role": "user", "content": "hi"}])]
    assert len(chunks) == 2


# --- list_models --------------------------------------------------------------


async def test_list_models_parses_tags():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200, json={"models": [{"name": "qwen3:14b"}, {"name": "llama3.2:3b"}]}
        )

    client = OllamaClient("http://ollama", model="m", transport=_transport(handler), backoff=0)
    assert await client.list_models() == ["qwen3:14b", "llama3.2:3b"]


async def test_list_models_4xx_fails_fast():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404)

    client = OllamaClient(
        "http://ollama", model="m", transport=_transport(handler), retries=3, backoff=0
    )
    with pytest.raises(OllamaError):
        await client.list_models()
    assert calls["n"] == 1


async def test_list_models_non_json_2xx_body_raises_ollama_error():
    # A 2xx with a non-JSON body must surface as a controlled OllamaError —
    # otherwise a transient upstream hiccup becomes an unhandled 500 out of
    # /chat/api/health, which calls list_models() directly.
    def handler(_request):
        return httpx.Response(200, content=b"not json")

    client = OllamaClient(
        "http://ollama", model="m", transport=_transport(handler), retries=1, backoff=0
    )
    with pytest.raises(OllamaError):
        await client.list_models()


async def test_list_models_5xx_retries():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(502)
        return httpx.Response(200, json={"models": []})

    client = OllamaClient(
        "http://ollama", model="m", transport=_transport(handler), retries=3, backoff=0
    )
    assert await client.list_models() == []
    assert calls["n"] == 2


def test_base_url_trailing_slash_stripped():
    client = OllamaClient("http://ollama:11434/", model="m")
    assert client._base == "http://ollama:11434"


def test_retries_clamped_to_at_least_one():
    client = OllamaClient("http://ollama", model="m", retries=0)
    assert client._retries == 1
