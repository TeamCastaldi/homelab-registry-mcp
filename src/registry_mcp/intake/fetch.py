"""Shallow-fetch a foreign source repo and read back the files intake cares about.

Shells out to the `git` binary via subprocess, the same pattern
`registry_mcp.gitcrypt.run` and `adoption/ssh.py` already use — no new Git
library dependency, and no reuse of `providers/git/`, which is bound to the
operator's own `GIT_REPO` and authenticates with a token that must never be
offered to a stranger's host.

Everything here is read-only and leaves nothing behind: the clone lands in a
temp dir, the interesting files are read into memory, and the directory is
removed before returning.

The caller-supplied URL is the trust boundary. `check_repo_url` rejects every
scheme that would turn a clone into something other than an HTTPS fetch —
`ext::` runs an arbitrary command, `file://` reads this node's disk, and
`ssh://`/scp-style would spend the control-plane SSH key on a stranger's host.
A private-range HTTPS host is *allowed*: a self-hosted Gitea is a legitimate
intake target in a homelab, so the scheme is the boundary, not the address.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from registry_mcp.logging import get_logger

_log = get_logger("intake.fetch")

# Only https. http is omitted deliberately rather than overlooked — intake
# pulls code this homelab is about to run, so the transport is authenticated.
_ALLOWED_SCHEMES = frozenset({"https"})

# Checked in preference order; `compose.yaml` is the canonical spelling this
# homelab's own deploy role can see (see N-100 in the normalization spec).
_COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
_DOCKERFILE_NAMES = ("Dockerfile", "dockerfile")
_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")

# A single file is read into memory to hand to the reasoning layer, so it is
# capped well below the whole-repo limit. A README past this is padding.
_MAX_FILE_BYTES = 256 * 1024


class IntakeError(RuntimeError):
    """Raised when a repo cannot be fetched or is not safe to fetch."""


@dataclass
class RepoSnapshot:
    """The files intake read out of a repo, with the clone already discarded."""

    repo_url: str
    dockerfile: str | None = None
    compose: str | None = None
    compose_path: str | None = None
    readme: str | None = None
    # Files that were found but refused (symlink escape, oversized). Surfaced
    # rather than silently dropped so a caller can tell "absent" from "skipped".
    skipped: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.dockerfile or self.compose or self.readme)


def check_repo_url(url: str) -> str:
    """Return the URL unchanged if it is safe to hand to `git clone`, else raise.

    Rejects, in order: empty input, a leading `-` (git would read it as a
    flag), any scheme outside `_ALLOWED_SCHEMES` (which covers `ext::`'s
    arbitrary command execution, `file://`'s local disk read, and
    `ssh://`/scp-style's use of this node's SSH key), and a URL with no host.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise IntakeError("Repository URL is required.")
    if candidate.startswith("-"):
        raise IntakeError("Repository URL may not start with '-'.")

    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    if not scheme:
        # Bare `host:path` is git's scp-like syntax, which resolves over SSH.
        raise IntakeError(
            f"Repository URL must specify a scheme ({'/'.join(sorted(_ALLOWED_SCHEMES))}://)."
        )
    if scheme not in _ALLOWED_SCHEMES:
        raise IntakeError(
            f"Unsupported URL scheme {scheme!r}; "
            f"only {'/'.join(sorted(_ALLOWED_SCHEMES))} is allowed."
        )
    if not parsed.hostname:
        raise IntakeError("Repository URL must include a host.")
    return candidate


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    # Never block on a credential prompt for a private repo, and never offer a
    # stored credential (the operator's GIT_TOKEN among them) to a foreign host.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


async def _clone(url: str, dest: Path, *, timeout_seconds: int) -> None:
    cmd = [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "protocol.ext.allow=never",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        "--quiet",
        "--",
        url,
        str(dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_git_env(),
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise IntakeError(f"Cloning {url} timed out after {timeout_seconds}s.") from None
    if proc.returncode != 0:
        raise IntakeError(f"Cloning {url} failed: {stderr.decode().strip()}")


def _dir_size_bytes(root: Path) -> int:
    """Total size on disk, never following symlinks out of the tree."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def _read_contained(root: Path, name: str) -> tuple[str | None, str | None]:
    """Read `name` from the repo root, or return a reason it was skipped.

    A cloned repo is untrusted content and git happily stores symlinks, so
    `README.md -> /etc/passwd` is a real way to make intake hand this node's
    files back to an MCP client. Same containment rule as `gitcrypt.check_path`:
    refuse the symlink outright, then confirm the resolved path is still inside
    the clone before reading it.
    """
    target = root / name
    # Checked before `exists()`, which follows the link and would report a
    # symlink to a real host file as an ordinary present file.
    if target.is_symlink():
        return None, f"{name} (symlink, refused)"
    if not target.exists() or not target.is_file():
        return None, None
    if not target.resolve().is_relative_to(root.resolve()):
        return None, f"{name} (resolves outside the repository)"
    size = target.stat().st_size
    if size > _MAX_FILE_BYTES:
        return None, f"{name} ({size} bytes exceeds the {_MAX_FILE_BYTES}-byte cap)"
    try:
        return target.read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return None, f"{name} ({exc})"


def _first_present(
    root: Path, names: tuple[str, ...], skipped: list[str]
) -> tuple[str | None, str | None]:
    """Return (content, filename) for the first readable name, recording skips."""
    for name in names:
        content, skip_reason = _read_contained(root, name)
        if skip_reason:
            skipped.append(skip_reason)
            continue
        if content is not None:
            return content, name
    return None, None


def collect_from_dir(root: Path, *, repo_url: str = "") -> RepoSnapshot:
    """Read the intake-relevant files out of an already-present directory.

    Split out from `fetch_repo` so the file-selection rules are testable
    against a fixture directory without cloning anything.
    """
    skipped: list[str] = []
    dockerfile, _ = _first_present(root, _DOCKERFILE_NAMES, skipped)
    compose, compose_path = _first_present(root, _COMPOSE_NAMES, skipped)
    readme, _ = _first_present(root, _README_NAMES, skipped)
    return RepoSnapshot(
        repo_url=repo_url,
        dockerfile=dockerfile,
        compose=compose,
        compose_path=compose_path,
        readme=readme,
        skipped=skipped,
    )


async def fetch_repo(url: str, *, timeout_seconds: int, max_repo_mb: int) -> RepoSnapshot:
    """Shallow-clone `url`, read the intake files, and discard the clone.

    The size cap is enforced after the clone rather than during it — git offers
    no transfer-size limit, so `timeout_seconds` is what actually bounds how
    much can arrive, and the cap is the backstop that refuses to parse (and
    keeps nothing of) a repo that came in too large.
    """
    safe_url = check_repo_url(url)
    tmpdir = Path(tempfile.mkdtemp(prefix="registry-mcp-intake-"))
    dest = tmpdir / "repo"
    try:
        await _clone(safe_url, dest, timeout_seconds=timeout_seconds)
        size_mb = _dir_size_bytes(dest) / (1024 * 1024)
        if size_mb > max_repo_mb:
            raise IntakeError(
                f"Repository is {size_mb:.1f} MB, over the {max_repo_mb} MB intake cap."
            )
        snapshot = collect_from_dir(dest, repo_url=safe_url)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    _log.info(
        "intake_fetched",
        repo_url=safe_url,
        has_dockerfile=snapshot.dockerfile is not None,
        compose_path=snapshot.compose_path,
        has_readme=snapshot.readme is not None,
        skipped=snapshot.skipped,
    )
    return snapshot
