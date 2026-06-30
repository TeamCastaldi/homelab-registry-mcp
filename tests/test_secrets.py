"""Tests for secrets_* MCP tools (Phase C — git-crypt integration)."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from registry_mcp.config import Settings
from registry_mcp.tools.secrets import (
    _detect_format,
    _is_dotenv_content,
    _key_bytes,
    _parse_dotenv,
    _serialize_dotenv,
    register_secrets_tools,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _settings(**kwargs) -> Settings:
    """Build a Settings instance with secrets enabled and sensible defaults."""
    defaults = dict(
        secrets_enabled=True,
        secrets_repo_path=None,
        secrets_key_path=None,
        secrets_git_crypt_key=None,
    )
    defaults.update(kwargs)
    return Settings.model_construct(**defaults)


def _make_mcp() -> tuple[object, dict]:
    """Return (mcp_mock, tools_dict) where tools_dict maps name→fn after registration."""
    tools: dict = {}

    class _FakeMCP:
        def tool(self):
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

    return _FakeMCP(), tools  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# _key_bytes
# ---------------------------------------------------------------------------


class TestKeyBytes:
    def test_file_path_takes_priority(self, tmp_path: Path) -> None:
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"filekey")
        settings = _settings(
            secrets_key_path=str(key_file),
            secrets_git_crypt_key=base64.b64encode(b"envkey").decode(),
        )
        assert _key_bytes(settings) == b"filekey"

    def test_env_var_fallback(self) -> None:
        settings = _settings(
            secrets_key_path=None,
            secrets_git_crypt_key=base64.b64encode(b"envkey").decode(),
        )
        assert _key_bytes(settings) == b"envkey"

    def test_raises_when_neither_set(self) -> None:
        settings = _settings(secrets_key_path=None, secrets_git_crypt_key=None)
        with pytest.raises(RuntimeError, match="No git-crypt key configured"):
            _key_bytes(settings)

    def test_raises_when_key_file_missing(self, tmp_path: Path) -> None:
        settings = _settings(secrets_key_path=str(tmp_path / "missing.key"))
        with pytest.raises(RuntimeError, match="not found"):
            _key_bytes(settings)


# ---------------------------------------------------------------------------
# _parse_dotenv / _serialize_dotenv / _detect_format
# ---------------------------------------------------------------------------


class TestDotenvHelpers:
    def test_parse_skips_comments_and_blanks(self) -> None:
        content = "# comment\nFOO=bar\n\nBAZ=qux\n"
        assert _parse_dotenv(content) == {"FOO": "bar", "BAZ": "qux"}

    def test_serialize_round_trip(self) -> None:
        data = {"A": "1", "B": "2"}
        assert _parse_dotenv(_serialize_dotenv(data)) == data

    def test_detect_format_dotenv_suffix(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        result = _detect_format(p, "KEY=val\n")
        assert isinstance(result, dict)
        assert result == {"KEY": "val"}

    def test_detect_format_raw_for_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        yaml_content = "version: '3'\nservices:\n  app:\n    image: nginx\n"
        result = _detect_format(p, yaml_content)
        assert isinstance(result, str)
        assert result == yaml_content

    def test_is_dotenv_content_true(self) -> None:
        assert _is_dotenv_content("FOO=1\nBAR=2\nBAZ=3\n")

    def test_is_dotenv_content_false_for_yaml(self) -> None:
        assert not _is_dotenv_content("version: '3'\nservices:\n  app:\n    image: nginx\n")


# ---------------------------------------------------------------------------
# secrets_status
# ---------------------------------------------------------------------------


class TestSecretsStatus:
    async def test_disabled_returns_error(self) -> None:
        settings = _settings(secrets_enabled=False)
        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        result = await tools["secrets_status"]()
        assert "error" in result
        assert "SECRETS_ENABLED" in result["error"]

    async def test_no_repo_path_returns_error(self) -> None:
        settings = _settings(secrets_repo_path=None)
        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        result = await tools["secrets_status"]()
        assert "error" in result

    async def test_locked_repo(self, tmp_path: Path) -> None:
        # Create a fake .gitattributes and a locked (magic-header) .env
        gitattributes = tmp_path / ".gitattributes"
        gitattributes.write_text("nodes/**/.env filter=git-crypt diff=git-crypt\n")
        env_file = tmp_path / "nodes" / "host" / ".env"
        env_file.parent.mkdir(parents=True)
        env_file.write_bytes(b"\x00GITCRYPT\x00" + b"\x00" * 50)

        settings = _settings(secrets_repo_path=str(tmp_path))

        git_crypt_output = "    encrypted: nodes/host/.env\nnot encrypted: .gitattributes\n"
        with patch(
            "registry_mcp.tools.secrets._run",
            new=AsyncMock(return_value=(0, git_crypt_output, "")),
        ):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_status"]()

        assert result["locked"] is True
        assert "nodes/host/.env" in result["encrypted_files"]
        assert ".gitattributes" in result["unencrypted_files"]

    async def test_unlocked_repo(self, tmp_path: Path) -> None:
        gitattributes = tmp_path / ".gitattributes"
        gitattributes.write_text("**/.env filter=git-crypt diff=git-crypt\n")
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")  # plaintext = unlocked

        settings = _settings(secrets_repo_path=str(tmp_path))

        git_crypt_output = "    encrypted: .env\nnot encrypted: .gitattributes\n"
        with patch(
            "registry_mcp.tools.secrets._run",
            new=AsyncMock(return_value=(0, git_crypt_output, "")),
        ):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_status"]()

        assert result["locked"] is False


# ---------------------------------------------------------------------------
# secrets_encrypt
# ---------------------------------------------------------------------------


class TestSecretsEncrypt:
    async def test_adds_entry_to_gitattributes(self, tmp_path: Path) -> None:
        settings = _settings(secrets_repo_path=str(tmp_path))
        with patch(
            "registry_mcp.tools.secrets._run",
            new=AsyncMock(return_value=(0, "", "")),
        ):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_encrypt"]("nodes/host/app/.env")

        assert result["gitattributes_updated"] is True
        assert "nodes/host/app/.env filter=git-crypt" in (tmp_path / ".gitattributes").read_text()

    async def test_idempotent_if_already_present(self, tmp_path: Path) -> None:
        entry = "nodes/host/app/.env filter=git-crypt diff=git-crypt\n"
        (tmp_path / ".gitattributes").write_text(entry)
        settings = _settings(secrets_repo_path=str(tmp_path))

        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        result = await tools["secrets_encrypt"]("nodes/host/app/.env")

        assert result["gitattributes_updated"] is False

    async def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        settings = _settings(secrets_repo_path=str(tmp_path))
        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        result = await tools["secrets_encrypt"]("../outside/.env")
        assert "error" in result
        assert "traversal" in result["error"].lower()

    async def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        settings = _settings(secrets_repo_path=str(tmp_path))
        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        result = await tools["secrets_encrypt"]("/etc/passwd")
        assert "error" in result
        assert "absolute" in result["error"].lower()


# ---------------------------------------------------------------------------
# secrets_decrypt
# ---------------------------------------------------------------------------


class TestSecretsDecrypt:
    async def test_dotenv_returns_parsed_dict(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n")
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")

        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))

        with patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_decrypt"](".env")

        assert result["content"] == {"FOO": "bar", "BAZ": "qux"}

    async def test_non_dotenv_returns_raw_string(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("version: '3'\nservices:\n  app:\n    image: nginx\n")
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")

        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))

        with patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_decrypt"]("config.yaml")

        assert isinstance(result["content"], str)

    async def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")
        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))

        with patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_decrypt"]("nonexistent.env")

        assert "error" in result

    async def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")
        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))

        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        result = await tools["secrets_decrypt"]("/etc/passwd")
        assert "error" in result
        assert "absolute" in result["error"].lower()


# ---------------------------------------------------------------------------
# secrets_add
# ---------------------------------------------------------------------------


class TestSecretsAdd:
    async def test_adds_new_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=value\n")
        gitattributes = tmp_path / ".gitattributes"
        gitattributes.write_text(".env filter=git-crypt diff=git-crypt\n")
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")

        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))

        with (
            patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()),
            patch(
                "registry_mcp.tools.secrets._run",
                new=AsyncMock(return_value=(0, "", "")),
            ),
        ):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_add"]("NEW_KEY", "new_value", ".env")

        assert result["staged"] is True
        content = env_file.read_text()
        assert "EXISTING=value" in content
        assert "NEW_KEY=new_value" in content

    async def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")
        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))

        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        result = await tools["secrets_add"]("KEY", "val", "/root/.ssh/authorized_keys")
        assert "error" in result
        assert "absolute" in result["error"].lower()

    async def test_overwrites_existing_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\n")
        gitattributes = tmp_path / ".gitattributes"
        gitattributes.write_text(".env filter=git-crypt diff=git-crypt\n")
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")

        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))

        with (
            patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()),
            patch(
                "registry_mcp.tools.secrets._run",
                new=AsyncMock(return_value=(0, "", "")),
            ),
        ):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            await tools["secrets_add"]("FOO", "new", ".env")

        data = _parse_dotenv(env_file.read_text())
        assert data["FOO"] == "new"


# ---------------------------------------------------------------------------
# secrets_list_keys
# ---------------------------------------------------------------------------


class TestSecretsListKeys:
    async def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")
        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))

        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        result = await tools["secrets_list_keys"]("/etc/shadow")
        assert "error" in result
        assert "absolute" in result["error"].lower()

    async def test_returns_keys_without_values(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET_A=hunter2\nSECRET_B=password123\n")
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")

        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))

        with patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_list_keys"](".env")

        assert result["keys"] == ["SECRET_A", "SECRET_B"]
        assert "hunter2" not in str(result)
        assert "password123" not in str(result)
