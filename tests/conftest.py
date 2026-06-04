"""Shared test fixtures."""

import pytest
from pydantic_settings import SettingsConfigDict

from registry_mcp.config import Settings
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


@pytest.fixture
def settings(tmp_path):
    return IsolatedSettings(registry_db_path=str(tmp_path / "registry.db"))


@pytest.fixture
def store(settings):
    return RegistryStore(settings.registry_db_path)


@pytest.fixture
def server(settings):
    return build_server(settings)
