"""Shared git-crypt primitives used by both `tools/secrets.py` (Phase C) and the
brownfield adoption flow (Phase 7).

Encryption only happens through a real `git commit` against a working tree with
git-crypt's clean filter configured — the remote Git hosting APIs (GitHub/Gitea
content API) never see the working tree and therefore never invoke the filter.
Anything that needs a secret to land encrypted must go through these local
subprocess helpers against a real clone, never through `GitProvider.commit_file`.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from registry_mcp.config import Settings


async def run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


def key_bytes(settings: Settings) -> bytes:
    """Load the git-crypt symmetric key from configured source.

    SECRETS_KEY_PATH (file) takes priority over SECRETS_GIT_CRYPT_KEY (base64 env).
    Raises RuntimeError if neither is set.
    """
    import base64

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


def repo_path(settings: Settings) -> Path:
    """Return the homelab repo path. Raises RuntimeError if not set or missing."""
    if not settings.secrets_repo_path:
        raise RuntimeError("SECRETS_REPO_PATH is not configured.")
    p = Path(settings.secrets_repo_path)
    if not p.exists():
        raise RuntimeError(f"SECRETS_REPO_PATH does not exist: {p}")
    return p


def _matches_gitattributes_pattern(rel_path: Path, pattern: str) -> bool:
    """Very lightweight glob match for .gitattributes patterns."""
    posix = rel_path.as_posix()
    if "**" in pattern:
        parts = pattern.split("**/")
        suffix = parts[-1]
        return fnmatch.fnmatch(posix, f"*{suffix}") or fnmatch.fnmatch(posix, suffix)
    return fnmatch.fnmatch(posix, pattern)


def is_locked(repo: Path) -> bool:
    """Return True if the git-crypt repo is currently locked.

    Reads the first bytes of the first encrypted file. Locked files start with
    the git-crypt magic header \x00GITCRYPT\x00.
    """
    gitattributes = repo / ".gitattributes"
    if not gitattributes.exists():
        return False  # git-crypt not initialised — nothing to lock

    for line in gitattributes.read_text().splitlines():
        line = line.strip()
        if "filter=git-crypt" not in line:
            continue
        pattern = line.split()[0]
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


async def ensure_unlocked(repo: Path, key: bytes) -> None:
    """Unlock the repo if currently locked. No-op if already unlocked."""
    if not is_locked(repo):
        return
    with tempfile.NamedTemporaryFile(delete=False, suffix=".key") as tf:
        tf.write(key)
        tf.flush()
        key_path = tf.name

    try:
        rc, _, stderr = await run(["git-crypt", "unlock", key_path], cwd=repo)
        if rc != 0:
            raise RuntimeError(f"git-crypt unlock failed: {stderr.strip()}")
    finally:
        Path(key_path).unlink(missing_ok=True)


def check_path(repo: Path, path: str) -> Path:
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


def parse_dotenv(content: str) -> dict[str, str]:
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


def serialize_dotenv(data: dict[str, str]) -> str:
    """Write dict back as KEY=value lines."""
    return "".join(f"{k}={v}\n" for k, v in data.items())


def is_dotenv_content(content: str) -> bool:
    """Heuristic: majority of non-blank, non-comment lines look like KEY=VALUE."""
    lines = [
        ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]
    if not lines:
        return False
    kv_lines = sum(1 for ln in lines if re.match(r"^[A-Z_][A-Z0-9_]*=", ln))
    return kv_lines / len(lines) >= 0.6


def detect_format(path: Path, content: str) -> dict[str, str] | str:
    """Return parsed dict for .env files, raw string for everything else."""
    if path.suffix == ".env" or is_dotenv_content(content):
        return parse_dotenv(content)
    return content


async def ensure_gitattributes_entry(repo: Path, path: str) -> bool:
    """Add `path` to .gitattributes as a git-crypt-filtered file if not already
    present. Returns True if .gitattributes was modified."""
    gitattributes = repo / ".gitattributes"
    current = gitattributes.read_text() if gitattributes.exists() else ""
    entry = f"{path} filter=git-crypt diff=git-crypt"
    if entry in current:
        return False
    updated = current.rstrip("\n") + ("\n" if current else "") + entry + "\n"
    gitattributes.write_text(updated)
    return True


async def git_checkout_branch(repo: Path, branch: str, base: str) -> None:
    """Create `branch` off `base` in the local clone, or resume it if it
    already exists on the remote (e.g. retrying a finalize call after a
    partial failure left the branch pushed but the PR not yet opened)."""
    rc, _, stderr = await run(["git", "fetch", "origin", base], cwd=repo)
    if rc != 0:
        raise RuntimeError(f"git fetch origin {base} failed: {stderr.strip()}")
    await run(["git", "fetch", "origin", branch], cwd=repo)  # best-effort; may not exist yet
    rc, _, _ = await run(["git", "rev-parse", "--verify", f"origin/{branch}"], cwd=repo)
    upstream = f"origin/{branch}" if rc == 0 else f"origin/{base}"
    rc, _, stderr = await run(["git", "checkout", "-B", branch, upstream], cwd=repo)
    if rc != 0:
        raise RuntimeError(f"git checkout -B {branch} failed: {stderr.strip()}")


async def git_commit_paths(repo: Path, paths: list[str], message: str) -> None:
    rc, _, stderr = await run(["git", "add", *paths], cwd=repo)
    if rc != 0:
        raise RuntimeError(f"git add failed: {stderr.strip()}")
    # Nothing staged (e.g. a retried finalize re-writing identical content) —
    # idempotent no-op rather than an error.
    rc, _, _ = await run(["git", "diff", "--cached", "--quiet"], cwd=repo)
    if rc == 0:
        return
    rc, _, stderr = await run(["git", "commit", "-m", message], cwd=repo)
    if rc != 0:
        raise RuntimeError(f"git commit failed: {stderr.strip()}")


async def git_push_branch(repo: Path, branch: str) -> None:
    rc, _, stderr = await run(["git", "push", "-u", "origin", branch], cwd=repo)
    if rc != 0:
        raise RuntimeError(f"git push origin {branch} failed: {stderr.strip()}")
