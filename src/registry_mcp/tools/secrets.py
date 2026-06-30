"""MCP tools for git-crypt-based secrets management (Phase C)."""

from __future__ import annotations

import asyncio
import base64
import re
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.config import Settings

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _key_bytes(settings: Settings) -> bytes:
    """Load the git-crypt symmetric key from configured source.

    SECRETS_KEY_PATH (file) takes priority over SECRETS_GIT_CRYPT_KEY (base64 env).
    Raises RuntimeError if neither is set.
    """
    if settings.secrets_key_path:
        p = Path(settings.secrets_key_path)
        if p.exists():
            return p.read_bytes()
        raise RuntimeError(f"SECRETS_KEY_PATH is set but file not found: {p}")
    if settings.secrets_git_crypt_key:
        return base64.b64decode(settings.secrets_git_crypt_key)
    raise RuntimeError(
        "No git-crypt key configured. Set SECRETS_KEY_PATH or SECRETS_GIT_CRYPT_KEY."
    )


def _repo(settings: Settings) -> Path:
    """Return the homelab repo path. Raises RuntimeError if not set or missing."""
    if not settings.secrets_repo_path:
        raise RuntimeError("SECRETS_REPO_PATH is not configured.")
    p = Path(settings.secrets_repo_path)
    if not p.exists():
        raise RuntimeError(f"SECRETS_REPO_PATH does not exist: {p}")
    return p


async def _run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


def _is_locked(repo: Path) -> bool:
    """Return True if the git-crypt repo is currently locked.

    Reads the first bytes of the first encrypted file. Locked files start with
    the git-crypt magic header \x00GITCRYPT\x00.
    """
    gitattributes = repo / ".gitattributes"
    if not gitattributes.exists():
        return False  # git-crypt not initialised — nothing to lock

    # Find any file marked filter=git-crypt in .gitattributes
    for line in gitattributes.read_text().splitlines():
        line = line.strip()
        if "filter=git-crypt" not in line:
            continue
        # line is like: nodes/**/.env filter=git-crypt diff=git-crypt
        pattern = line.split()[0]
        # Try to find a matching file
        for candidate in repo.rglob("*"):
            if candidate.is_file() and _matches_gitattributes_pattern(
                candidate.relative_to(repo), pattern
            ):
                try:
                    header = candidate.read_bytes()[:10]
                    return header.startswith(b"\x00GITCRYPT")
                except OSError:
                    continue
    return False


def _matches_gitattributes_pattern(rel_path: Path, pattern: str) -> bool:
    """Very lightweight glob match for .gitattributes patterns."""
    import fnmatch

    posix = rel_path.as_posix()
    # Handle patterns like **/.env, nodes/**/.env, .env
    if "**" in pattern:
        # Match any depth
        parts = pattern.split("**/")
        suffix = parts[-1]
        return fnmatch.fnmatch(posix, f"*{suffix}") or fnmatch.fnmatch(posix, suffix)
    return fnmatch.fnmatch(posix, pattern)


async def _ensure_unlocked(repo: Path, key_bytes: bytes) -> None:
    """Unlock the repo if currently locked. No-op if already unlocked."""
    if not _is_locked(repo):
        return
    with tempfile.NamedTemporaryFile(delete=False, suffix=".key") as tf:
        tf.write(key_bytes)
        tf.flush()
        key_path = tf.name

    try:
        rc, _, stderr = await _run(["git-crypt", "unlock", key_path], cwd=repo)
        if rc != 0:
            raise RuntimeError(f"git-crypt unlock failed: {stderr.strip()}")
    finally:
        Path(key_path).unlink(missing_ok=True)


def _parse_dotenv(content: str) -> dict[str, str]:
    """Parse KEY=value lines from a .env file. Skips comments and blanks."""
    result: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _serialize_dotenv(data: dict[str, str]) -> str:
    """Write dict back as KEY=value lines."""
    return "".join(f"{k}={v}\n" for k, v in data.items())


def _is_dotenv_content(content: str) -> bool:
    """Heuristic: majority of non-blank, non-comment lines look like KEY=VALUE."""
    lines = [
        ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]
    if not lines:
        return False
    kv_lines = sum(1 for ln in lines if re.match(r"^[A-Z_][A-Z0-9_]*=", ln))
    return kv_lines / len(lines) >= 0.6


def _detect_format(path: Path, content: str) -> dict[str, str] | str:
    """Return parsed dict for .env files, raw string for everything else."""
    if path.suffix == ".env" or _is_dotenv_content(content):
        return _parse_dotenv(content)
    return content


def _guard(settings: Settings) -> dict[str, Any] | None:
    """Return an error dict if secrets tools are disabled, else None."""
    if not settings.secrets_enabled:
        return {"error": "Secrets tools are disabled. Set SECRETS_ENABLED=true to enable."}
    return None


def _check_path(repo: Path, path: str) -> Path:
    """Return the resolved target path inside repo, or raise ValueError.

    Blocks absolute paths (pathlib discards the base when joined with an
    absolute right-hand side), dotdot traversal, and symlink escapes.
    """
    p = Path(path)
    if p.is_absolute():
        raise ValueError("Absolute paths are not allowed.")
    if ".." in p.parts:
        raise ValueError("Path traversal is not allowed.")
    target = (repo / path).resolve()
    if not target.is_relative_to(repo.resolve()):
        raise ValueError("Path must be within the repository.")
    return target


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_secrets_tools(mcp: FastMCP, settings: Settings) -> None:
    """Register the six secrets_* MCP tools."""

    @mcp.tool()
    async def secrets_status() -> dict[str, Any]:
        """Show git-crypt encrypted files and current lock state of the homelab repo."""
        if err := _guard(settings):
            return err
        try:
            repo = _repo(settings)
        except RuntimeError as exc:
            return {"error": str(exc)}

        rc, stdout, stderr = await _run(["git-crypt", "status"], cwd=repo)
        if rc != 0:
            return {"error": f"git-crypt status failed: {stderr.strip()}"}

        encrypted: list[str] = []
        unencrypted: list[str] = []
        for line in stdout.splitlines():
            if line.startswith("    encrypted:"):
                encrypted.append(line.replace("    encrypted:", "").strip())
            elif line.startswith("not encrypted:"):
                unencrypted.append(line.replace("not encrypted:", "").strip())

        locked = _is_locked(repo)
        return {"locked": locked, "encrypted_files": encrypted, "unencrypted_files": unencrypted}

    @mcp.tool()
    async def secrets_encrypt(path: str) -> dict[str, Any]:
        """Add a file to .gitattributes so git-crypt encrypts it.

        `path` must be relative to the homelab repo root (e.g. 'nodes/heimdall/app/.env').
        The file is encrypted on the next git push. Existing unencrypted history is not
        rewritten — only future commits are encrypted.
        """
        if err := _guard(settings):
            return err
        try:
            repo = _repo(settings)
        except RuntimeError as exc:
            return {"error": str(exc)}

        try:
            _check_path(repo, path)
        except ValueError as exc:
            return {"error": str(exc)}

        gitattributes = repo / ".gitattributes"
        current = gitattributes.read_text() if gitattributes.exists() else ""

        entry = f"{path} filter=git-crypt diff=git-crypt"
        if entry in current:
            return {"encrypted": path, "gitattributes_updated": False, "note": "Already present."}

        updated = current.rstrip("\n") + ("\n" if current else "") + entry + "\n"
        gitattributes.write_text(updated)

        rc, _, stderr = await _run(["git", "add", ".gitattributes"], cwd=repo)
        if rc != 0:
            return {"error": f"git add failed: {stderr.strip()}"}

        rc, _, stderr = await _run(["git", "commit", "-m", f"chore: encrypt {path}"], cwd=repo)
        if rc != 0:
            return {"error": f"git commit failed: {stderr.strip()}"}

        return {"encrypted": path, "gitattributes_updated": True}

    @mcp.tool()
    async def secrets_decrypt(path: str) -> dict[str, Any]:
        """Read an encrypted .env file without writing plaintext to disk.

        Returns a parsed key/value dict for .env files; raw string for other formats.
        The repo is unlocked in-place to read the file — no separate plaintext copy
        is written. Values are returned to the AI context only.
        """
        if err := _guard(settings):
            return err
        try:
            repo = _repo(settings)
            key = _key_bytes(settings)
        except RuntimeError as exc:
            return {"error": str(exc)}

        try:
            target = _check_path(repo, path)
        except ValueError as exc:
            return {"error": str(exc)}

        if not target.exists():
            return {"error": f"File not found: {path}"}

        try:
            await _ensure_unlocked(repo, key)
        except RuntimeError as exc:
            return {"error": str(exc)}

        content = target.read_text()
        return {"path": path, "content": _detect_format(target, content)}

    @mcp.tool()
    async def secrets_add(key: str, value: str, path: str) -> dict[str, Any]:
        """Add or update a key in an encrypted .env file.

        If the file does not yet exist it is created. If the path is not already
        in .gitattributes it is added automatically. Changes are staged (git add)
        but NOT committed — the operator controls commits.
        """
        if err := _guard(settings):
            return err
        try:
            repo = _repo(settings)
            key_bytes = _key_bytes(settings)
        except RuntimeError as exc:
            return {"error": str(exc)}

        try:
            target = _check_path(repo, path)
        except ValueError as exc:
            return {"error": str(exc)}

        # Ensure the file is tracked by git-crypt
        gitattributes = repo / ".gitattributes"
        current_attrs = gitattributes.read_text() if gitattributes.exists() else ""
        entry = f"{path} filter=git-crypt diff=git-crypt"
        if entry not in current_attrs:
            result = await secrets_encrypt(path)  # type: ignore[name-defined]
            if "error" in result:
                return result

        try:
            await _ensure_unlocked(repo, key_bytes)
        except RuntimeError as exc:
            return {"error": str(exc)}

        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text() if target.exists() else ""
        data = _parse_dotenv(existing)
        data[key] = value
        target.write_text(_serialize_dotenv(data))

        rc, _, stderr = await _run(["git", "add", path], cwd=repo)
        if rc != 0:
            return {"error": f"git add failed: {stderr.strip()}"}

        return {"path": path, "key": key, "staged": True}

    @mcp.tool()
    async def secrets_rotate(path: str) -> dict[str, Any]:
        """Re-encrypt the homelab repo with a new git-crypt key.

        Exports a new key to <SECRETS_KEY_PATH>.new (or /tmp/git-crypt-new.key if
        SECRETS_KEY_PATH is unset). Historical commits remain accessible via the old
        key — true history rewrite is out of scope. Store the new key in your password
        manager and discard the old one.

        `path` is not used for filtering — rotation affects the entire repo. It is
        accepted for API symmetry and future per-file rotation support.
        """
        if err := _guard(settings):
            return err
        try:
            repo = _repo(settings)
            key_bytes = _key_bytes(settings)
        except RuntimeError as exc:
            return {"error": str(exc)}

        try:
            await _ensure_unlocked(repo, key_bytes)
        except RuntimeError as exc:
            return {"error": str(exc)}

        # Determine where to export the new key
        if settings.secrets_key_path:
            new_key_path = settings.secrets_key_path + ".new"
        else:
            new_key_path = "/tmp/git-crypt-new.key"

        # Re-init generates a fresh key in .git/git-crypt/
        rc, _, stderr = await _run(["git-crypt", "init"], cwd=repo)
        if rc != 0:
            return {"error": f"git-crypt init failed: {stderr.strip()}"}

        rc, _, stderr = await _run(["git-crypt", "export-key", new_key_path], cwd=repo)
        if rc != 0:
            return {"error": f"git-crypt export-key failed: {stderr.strip()}"}

        # Lock and re-unlock with new key so the working tree reflects new encryption
        await _run(["git-crypt", "lock"], cwd=repo)
        rc, _, stderr = await _run(["git-crypt", "unlock", new_key_path], cwd=repo)
        if rc != 0:
            return {"error": f"Re-unlock with new key failed: {stderr.strip()}"}

        return {
            "rotated": True,
            "new_key_path": new_key_path,
            "warning": (
                "Old key still decrypts historical commits. "
                "Store the new key in your password manager and discard the old key."
            ),
        }

    @mcp.tool()
    async def secrets_list_keys(path: str) -> dict[str, Any]:
        """List the keys present in an encrypted .env file without revealing their values."""
        if err := _guard(settings):
            return err
        try:
            repo = _repo(settings)
            key_bytes = _key_bytes(settings)
        except RuntimeError as exc:
            return {"error": str(exc)}

        try:
            target = _check_path(repo, path)
        except ValueError as exc:
            return {"error": str(exc)}

        if not target.exists():
            return {"error": f"File not found: {path}"}

        try:
            await _ensure_unlocked(repo, key_bytes)
        except RuntimeError as exc:
            return {"error": str(exc)}

        content = target.read_text()
        data = _parse_dotenv(content)
        return {"path": path, "keys": list(data.keys())}
