"""MCP tools for git-crypt-based secrets management (Phase C)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from registry_mcp.config import Settings
from registry_mcp.gitcrypt import check_attr_path as _check_attr_path
from registry_mcp.gitcrypt import check_dotenv_entry as _check_dotenv_entry
from registry_mcp.gitcrypt import check_path as _check_path
from registry_mcp.gitcrypt import detect_format as _detect_format
from registry_mcp.gitcrypt import ensure_gitattributes_entry as _ensure_gitattributes_entry
from registry_mcp.gitcrypt import ensure_unlocked as _ensure_unlocked
from registry_mcp.gitcrypt import has_gitattributes_entry as _has_gitattributes_entry
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


# git-crypt has no key-rotation command, so secrets_rotate returns these steps
# rather than half-automating a procedure that can leave the repo unreadable.
_MANUAL_ROTATION_STEPS = (
    "In the homelab repo clone, unlock it and make sure `git status` is clean.",
    "Move the current key aside: `mv .git/git-crypt .git/git-crypt.old` "
    "(keep it until the last step passes).",
    "Run `git-crypt init` to generate a new key.",
    "Re-encrypt every protected file with the new key: `git add --renormalize .`, then "
    "`git commit -m 'chore: re-encrypt with a new git-crypt key'` and push.",
    "Run `git-crypt export-key <new-key-file>`, `chmod 400` it, and store it in your "
    "password manager.",
    "Point SECRETS_KEY_PATH or SECRETS_GIT_CRYPT_KEY at the new key (in Infisical, for "
    "Dockhand-deployed stacks) and redeploy.",
    "Verify: a fresh clone unlocks with the new key and `git-crypt status` reports no "
    "unencrypted protected files. Only then delete .git/git-crypt.old.",
    "Commits made before the rotation still decrypt with the old key. If that key may be "
    "compromised, rotate the secrets themselves too.",
)


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
    (encrypt/add) refuse to run regardless of git-crypt configuration, as does
    rotate (which only returns manual steps); status/decrypt/list_keys stay usable.
    """

    def _read_only_error() -> dict[str, Any] | None:
        if read_only:
            return {
                "error": "Server is in read-only mode (startup health check failed). "
                "Run system_health_check for details."
            }
        return None

    async def _read_then_relock(repo: Path, key: bytes, target: Path) -> tuple[str, str | None]:
        """Read `target` from the unlocked working tree, then lock the repo again
        if this call was what unlocked it — plaintext stays on disk only for the
        length of the call. Returns (content, warning); a failed re-lock is a
        warning, since the read itself succeeded. `_ensure_unlocked`'s
        RuntimeError propagates to the caller."""
        was_locked = _is_locked(repo)
        await _ensure_unlocked(repo, key)
        warning = None
        try:
            content = target.read_text()
        finally:
            if was_locked:
                rc, _, stderr = await _run(["git-crypt", "lock"], cwd=repo)
                if rc != 0:
                    warning = f"repo left unlocked: git-crypt lock failed: {stderr.strip()}"
        return content, warning

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
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

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
    )
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
            _check_attr_path(path)
        except ValueError as exc:
            return {"error": str(exc)}

        if not await _ensure_gitattributes_entry(repo, path):
            return {"encrypted": path, "gitattributes_updated": False, "note": "Already present."}

        rc, _, stderr = await _run(["git", "add", ".gitattributes"], cwd=repo)
        if rc != 0:
            return {"error": f"git add failed: {stderr.strip()}"}

        rc, _, stderr = await _run(["git", "commit", "-m", f"chore: encrypt {path}"], cwd=repo)
        if rc != 0:
            return {"error": f"git commit failed: {stderr.strip()}"}

        return {"encrypted": path, "gitattributes_updated": True}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def secrets_decrypt(path: str) -> dict[str, Any]:
        """Return the plaintext of an encrypted file. Off unless SECRETS_ALLOW_DECRYPT=true.

        Returns a parsed key/value dict for .env files; raw string for other formats.
        A locked repo is unlocked in place to read the file and locked again
        afterwards. For key names only, use secrets_list_keys instead.
        """
        if err := _guard(settings):
            return err
        if not settings.secrets_allow_decrypt:
            return {
                "error": "secrets_decrypt is disabled: it returns plaintext secret values to "
                "the MCP client. Set SECRETS_ALLOW_DECRYPT=true to enable it, or use "
                "secrets_list_keys for key names only."
            }
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
            content, warning = await _read_then_relock(repo, key, target)
        except RuntimeError as exc:
            return {"error": str(exc)}

        result: dict[str, Any] = {"path": path, "content": _detect_format(target, content)}
        if warning:
            result["warning"] = warning
        return result

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
    )
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
            _check_attr_path(path)
            _check_dotenv_entry(key, value)
        except ValueError as exc:
            return {"error": str(exc)}

        # Ensure the file is tracked by git-crypt
        gitattributes = repo / ".gitattributes"
        current_attrs = gitattributes.read_text() if gitattributes.exists() else ""
        if not _has_gitattributes_entry(current_attrs, path):
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

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def secrets_rotate(path: str) -> dict[str, Any]:
        """Explain how to rotate the homelab repo's git-crypt key. Changes nothing.

        git-crypt has no key-rotation command, and the automation this tool used
        to attempt could never succeed: `git-crypt init` refuses to run on a repo
        that is already initialized, and it would have exported the new key to a
        predictable /tmp path. Returns the manual procedure instead. `path` is
        unused and kept for API compatibility.
        """
        if err := _read_only_error():
            return err
        if err := _guard(settings):
            return err
        return {
            "error": "secrets_rotate is not automated: git-crypt has no key-rotation "
            "command. Rotate the key by hand with manual_steps.",
            "manual_steps": list(_MANUAL_ROTATION_STEPS),
        }

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def secrets_list_keys(path: str) -> dict[str, Any]:
        """List the keys present in an encrypted .env file without revealing their values.

        A locked repo is unlocked in place to read the file and locked again afterwards.
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

        if not target.exists():
            return {"error": f"File not found: {path}"}

        try:
            content, warning = await _read_then_relock(repo, key_bytes, target)
        except RuntimeError as exc:
            return {"error": str(exc)}

        result: dict[str, Any] = {"path": path, "keys": list(_parse_dotenv(content).keys())}
        if warning:
            result["warning"] = warning
        return result
