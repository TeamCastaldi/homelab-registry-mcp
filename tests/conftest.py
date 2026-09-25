"""Shared test fixtures."""

import asyncio
import threading

import pytest
from mcp.types import CallToolResult
from pydantic_settings import SettingsConfigDict

from registry_mcp.config import Settings
from registry_mcp.hardware import HardwareStore
from registry_mcp.registry import RegistryStore
from registry_mcp.server import build_server


class IsolatedSettings(Settings):
    """`Settings` made fully hermetic for tests: only values passed explicitly to
    the constructor (plus field defaults) apply. Setting ``env_file=None`` alone is
    not enough — libraries imported by the suite (litellm via ``import dspy``) call
    ``load_dotenv()``, which copies a stray repo ``.env`` into ``os.environ``. Once
    there, pydantic-settings' env source would override the isolated values even
    with the dotenv file source disabled. Dropping the env/dotenv/secrets sources
    entirely guarantees a test that passes no ``TRAEFIK_API_URL`` (etc.) gets an
    unconfigured client regardless of the local ``.env`` or process environment."""

    model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # Honor only constructor kwargs; ignore env vars, .env, and secrets files.
        return (init_settings,)


def tool_payload(result):
    """The structured payload of a `FastMCP.call_tool()` result: a success is a
    `(content, structured)` tuple, a reported error a `CallToolResult` with
    `isError` set and the tool's `{"error": ...}` dict as structured content."""
    if isinstance(result, CallToolResult):
        assert result.isError, "a CallToolResult here is always a reported error"
        return result.structuredContent
    return result[1]


class BlockingCall:
    """A stand-in for a blocking LLM round-trip: blocks its (worker) thread until
    released, so a test can observe the event loop while the call is in flight.
    Assign it in place of a reasoner method."""

    def __init__(self, result=None):
        self.result = result
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, *args, **kwargs):
        self.entered.set()
        self.release.wait(5)
        return self.result

    async def wait_entered(self, task: asyncio.Task) -> None:
        """Yield to the loop until the call is in flight; fail if `task` finished
        first — which is what a call made on the event loop itself looks like."""
        for _ in range(500):
            if self.entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert self.entered.is_set(), "the blocking call was never made"
        assert not task.done(), "the blocking call ran on the event loop"

    async def assert_off_loop(self, coro):
        task = asyncio.create_task(coro)
        await self.wait_entered(task)
        self.release.set()
        return await task


@pytest.fixture
def settings(tmp_path):
    return IsolatedSettings(registry_db_path=str(tmp_path / "registry.db"))


@pytest.fixture
def store(settings):
    return RegistryStore(settings.registry_db_path)


@pytest.fixture
def hardware_store(store):
    return HardwareStore(store.engine)


@pytest.fixture
def server(settings):
    return build_server(settings)
