"""MCP tools for git-crypt-based secrets management (Phase C)."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.config import Settings
from registry_mcp.gitcrypt import check_path as _check_path
from registry_mcp.gitcrypt import detect_format as _detect_format
from registry_mcp.gitcrypt import ensure_unlocked as _ensure_unlocked
from registry_mcp.gitcrypt import is_dotenv_content as _is_dotenv_content  # noqa: F401
from registry_mcp.gitcrypt import is_locked as _is_locked
from registry_mcp.gitcrypt import key_bytes as _key_bytes
from registry_mcp.gitcrypt import parse_dotenv as _parse_dotenv
from registry_mcp.gitcrypt import repo_path as _repo
from registry_mcp.gitcrypt import run as _run
from registry_mcp.gitcrypt import serialize_dotenv as _serialize_dotenv

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
#
# The git-crypt/dotenv primitives above live in `registry_mcp.gitcrypt` so the
# brownfield adoption flow (Phase 7) can reuse the exact same encryption and
# path-safety logic rather than a second, possibly-diverging copy. They are
# re-imported under their historical private names here so the rest of this
# module — and the existing tests, which patch e.g.
# `registry_mcp.tools.secrets._run` — are unaffected.


def _guard(settings: Settings) -> dict[str, Any] | None:
    """Return an error dict if secrets tools are disabled, else None."""
    if not settings.secrets_enabled:
        return {"error": "Secrets tools are disabled. Set SECRETS_ENABLED=true to enable."}
    return None


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_secrets_tools(mcp: FastMCP, settings: Settings, read_only: bool = False) -> None:
    """Register the six secrets_* MCP tools.

    When `read_only` is set (startup health check failed; see
    `system_health_check`), the tools that mutate the homelab repo
    (encrypt/add/rotate) refuse to run regardless of git-crypt configuration;
    status/decrypt/list_keys stay usable.
    """

    def _read_only_error() -> dict[str, Any] | None:
        if read_only:
            return {
                "error": "Server is in read-only mode (startup health check failed). "
                "Run system_health_check for details."
            }
        return None

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

        `path` must be relative to the homelab repo root (e.g. 'nodes/workload-01/app/.env').
        The file is encrypted on the next git push. Existing unencrypted history is not
        rewritten — only future commits are encrypted.
        """
        if err := _read_only_error():
            return err
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
        if err := _read_only_error():
            return err
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
        if err := _read_only_error():
            return err
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
