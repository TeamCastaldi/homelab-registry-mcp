"""Tests for secrets_* MCP tools (Phase C — git-crypt integration)."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from conftest import IsolatedSettings
from registry_mcp.config import Settings
from registry_mcp.gitcrypt import (
    check_attr_path,
    check_dotenv_entry,
    check_path,
    has_gitattributes_entry,
)
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
    return IsolatedSettings(**defaults)


def _make_mcp() -> tuple[object, dict]:
    """Return (mcp_mock, tools_dict) where tools_dict maps name→fn after registration."""
    tools: dict = {}

    class _FakeMCP:
        def tool(self, *args, **kwargs):
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

        settings = _settings(
            secrets_repo_path=str(tmp_path),
            secrets_key_path=str(key_file),
            secrets_allow_decrypt=True,
        )

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

        settings = _settings(
            secrets_repo_path=str(tmp_path),
            secrets_key_path=str(key_file),
            secrets_allow_decrypt=True,
        )

        with patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_decrypt"]("config.yaml")

        assert isinstance(result["content"], str)

    async def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")
        settings = _settings(
            secrets_repo_path=str(tmp_path),
            secrets_key_path=str(key_file),
            secrets_allow_decrypt=True,
        )

        with patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_decrypt"]("nonexistent.env")

        assert "error" in result

    async def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")
        settings = _settings(
            secrets_repo_path=str(tmp_path),
            secrets_key_path=str(key_file),
            secrets_allow_decrypt=True,
        )

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


# ---------------------------------------------------------------------------
# Path and entry guards (registry_mcp.gitcrypt, shared with adoption)
# ---------------------------------------------------------------------------


class TestGitcryptGuards:
    @pytest.mark.parametrize("path", [".git/config", ".git/hooks/pre-commit", "sub/.GIT/HEAD"])
    def test_check_path_rejects_git_internals(self, tmp_path: Path, path: str) -> None:
        with pytest.raises(ValueError, match=r"\.git/"):
            check_path(tmp_path, path)

    def test_check_path_rejects_a_symlink_into_git(self, tmp_path: Path) -> None:
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        (tmp_path / "hooks").symlink_to(tmp_path / ".git" / "hooks")
        with pytest.raises(ValueError, match=r"\.git/"):
            check_path(tmp_path, "hooks/pre-commit")

    @pytest.mark.parametrize(
        "path",
        [
            "x\n*.env -filter -diff\ny",  # a newline would add a rule turning encryption off
            "my app/.env",
            "nodes/*/.env",
            'a"b/.env',
            "nodes/pi/.gitattributes",
        ],
    )
    def test_check_attr_path_rejects_non_literal_patterns(self, path: str) -> None:
        with pytest.raises(ValueError):
            check_attr_path(path)

    def test_check_attr_path_accepts_a_plain_path(self) -> None:
        check_attr_path("nodes/pi/app/.env")

    def test_entry_match_is_exact_not_substring(self) -> None:
        current = "nodes/pi/app/.env filter=git-crypt diff=git-crypt\n"
        assert has_gitattributes_entry(current, "nodes/pi/app/.env")
        assert not has_gitattributes_entry(current, "app/.env")

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("BAD KEY", "v"),
            ("[core]\n\tfsmonitor", "x"),
            ("1ABC", "v"),
            ("OK", "a\nINJECTED=1"),
            ("OK", "a\rb"),
        ],
    )
    def test_check_dotenv_entry_rejects_bad_keys_and_line_breaks(
        self, key: str, value: str
    ) -> None:
        with pytest.raises(ValueError):
            check_dotenv_entry(key, value)


class TestSecretsToolHardening:
    def _tools(self, tmp_path: Path) -> dict:
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")
        settings = _settings(
            secrets_repo_path=str(tmp_path),
            secrets_key_path=str(key_file),
            secrets_allow_decrypt=True,
        )
        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        return tools

    async def test_encrypt_rejects_gitattributes_injection(self, tmp_path: Path) -> None:
        tools = self._tools(tmp_path)
        run = AsyncMock(return_value=(0, "", ""))
        with patch("registry_mcp.tools.secrets._run", new=run):
            result = await tools["secrets_encrypt"]("x\n*.env -filter -diff\ny")
        assert "error" in result
        assert not (tmp_path / ".gitattributes").exists()
        run.assert_not_awaited()

    async def test_add_gets_its_own_entry_beside_a_longer_listed_path(self, tmp_path: Path) -> None:
        """`app/.env` used to count as covered by `nodes/pi/app/.env` (a substring
        match), so the new file was staged without ever being encrypted."""
        (tmp_path / ".gitattributes").write_text(
            "nodes/pi/app/.env filter=git-crypt diff=git-crypt\n"
        )
        tools = self._tools(tmp_path)
        with (
            patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()),
            patch("registry_mcp.tools.secrets._run", new=AsyncMock(return_value=(0, "", ""))),
        ):
            result = await tools["secrets_add"]("KEY", "value", "app/.env")

        assert result["staged"] is True
        lines = (tmp_path / ".gitattributes").read_text().splitlines()
        assert "app/.env filter=git-crypt diff=git-crypt" in lines

    async def test_add_rejects_a_value_with_a_line_break(self, tmp_path: Path) -> None:
        tools = self._tools(tmp_path)
        with patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()):
            result = await tools["secrets_add"]("KEY", "v\nINJECTED=1", ".env")
        assert "line break" in result["error"]
        assert not (tmp_path / ".env").exists()

    async def test_add_refuses_a_git_hook(self, tmp_path: Path) -> None:
        tools = self._tools(tmp_path)
        result = await tools["secrets_add"]("A", "$(touch /tmp/pwned)", ".git/hooks/pre-commit")
        assert ".git/" in result["error"]

    async def test_decrypt_refuses_git_config(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text(
            '[remote "origin"]\n\turl = https://token@git.example/o/r\n'
        )
        tools = self._tools(tmp_path)
        with patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()):
            result = await tools["secrets_decrypt"](".git/config")
        assert ".git/" in result["error"]
        assert "token" not in str(result)


# ---------------------------------------------------------------------------
# Decrypt policy: opt-in, and never leave a repo decrypted that was locked
# ---------------------------------------------------------------------------


class TestDecryptPolicy:
    def _locked_repo(self, tmp_path: Path) -> Path:
        (tmp_path / ".gitattributes").write_text(".env filter=git-crypt diff=git-crypt\n")
        (tmp_path / ".env").write_bytes(b"\x00GITCRYPT\x00ciphertext")
        (tmp_path / "git-crypt.key").write_bytes(b"fakekey")
        return tmp_path

    def _tools(self, repo: Path, **overrides) -> dict:
        settings = _settings(
            secrets_repo_path=str(repo),
            secrets_key_path=str(repo / "git-crypt.key"),
            **overrides,
        )
        mcp, tools = _make_mcp()
        register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
        return tools

    @staticmethod
    def _fake_unlock(repo: Path) -> AsyncMock:
        async def unlock(_repo, _key):
            (repo / ".env").write_text("FOO=bar\n")

        return AsyncMock(side_effect=unlock)

    async def test_decrypt_is_off_by_default(self, tmp_path: Path) -> None:
        repo = self._locked_repo(tmp_path)
        unlock = self._fake_unlock(repo)
        with patch("registry_mcp.tools.secrets._ensure_unlocked", new=unlock):
            result = await self._tools(repo)["secrets_decrypt"](".env")
        assert "SECRETS_ALLOW_DECRYPT" in result["error"]
        unlock.assert_not_awaited()

    @pytest.mark.parametrize(
        ("tool", "expected"),
        [
            ("secrets_decrypt", {"content": {"FOO": "bar"}}),
            ("secrets_list_keys", {"keys": ["FOO"]}),
        ],
    )
    async def test_relocks_a_repo_it_unlocked(
        self, tmp_path: Path, tool: str, expected: dict
    ) -> None:
        repo = self._locked_repo(tmp_path)
        run = AsyncMock(return_value=(0, "", ""))
        with (
            patch("registry_mcp.tools.secrets._ensure_unlocked", new=self._fake_unlock(repo)),
            patch("registry_mcp.tools.secrets._run", new=run),
        ):
            result = await self._tools(repo, secrets_allow_decrypt=True)[tool](".env")

        for key, value in expected.items():
            assert result[key] == value
        assert "warning" not in result
        run.assert_awaited_once()
        assert run.await_args.args[0] == ["git-crypt", "lock"]

    async def test_leaves_an_already_unlocked_repo_alone(self, tmp_path: Path) -> None:
        repo = self._locked_repo(tmp_path)
        (repo / ".env").write_text("FOO=bar\n")  # the operator already unlocked it
        run = AsyncMock(return_value=(0, "", ""))
        with (
            patch("registry_mcp.tools.secrets._ensure_unlocked", new=AsyncMock()),
            patch("registry_mcp.tools.secrets._run", new=run),
        ):
            result = await self._tools(repo)["secrets_list_keys"](".env")

        assert result["keys"] == ["FOO"]
        run.assert_not_awaited()

    async def test_a_failed_relock_is_reported_as_a_warning(self, tmp_path: Path) -> None:
        repo = self._locked_repo(tmp_path)
        run = AsyncMock(return_value=(1, "", "Working directory not clean"))
        with (
            patch("registry_mcp.tools.secrets._ensure_unlocked", new=self._fake_unlock(repo)),
            patch("registry_mcp.tools.secrets._run", new=run),
        ):
            result = await self._tools(repo, secrets_allow_decrypt=True)["secrets_decrypt"](".env")

        assert result["content"] == {"FOO": "bar"}
        assert "repo left unlocked" in result["warning"]
        assert "Working directory not clean" in result["warning"]


# ---------------------------------------------------------------------------
# secrets_rotate
# ---------------------------------------------------------------------------


class TestSecretsRotate:
    async def test_returns_manual_steps_and_changes_nothing(self, tmp_path: Path) -> None:
        """git-crypt has no rotation command; `git-crypt init` on an initialized
        repo always fails, so the old automation could never succeed."""
        key_file = tmp_path / "git-crypt.key"
        key_file.write_bytes(b"fakekey")
        settings = _settings(secrets_repo_path=str(tmp_path), secrets_key_path=str(key_file))
        run = AsyncMock(return_value=(0, "", ""))
        unlock = AsyncMock()
        with (
            patch("registry_mcp.tools.secrets._run", new=run),
            patch("registry_mcp.tools.secrets._ensure_unlocked", new=unlock),
        ):
            mcp, tools = _make_mcp()
            register_secrets_tools(mcp, settings)  # type: ignore[arg-type]
            result = await tools["secrets_rotate"]("")

        assert "not automated" in result["error"]
        steps = " ".join(result["manual_steps"])
        assert "git add --renormalize ." in steps
        assert "/tmp" not in steps
        run.assert_not_awaited()
        unlock.assert_not_awaited()
