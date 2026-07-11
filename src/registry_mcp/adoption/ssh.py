"""SSH-based inspection of live Docker containers on remote homelab nodes
(Phase 7 brownfield adoption).

Shells out to the `ssh`/`docker` binaries via subprocess, the same pattern
`registry_mcp.gitcrypt.run` uses for git-crypt — no new SSH client dependency.
Reuses the control-plane's existing `SSH_KEY_PATH` (the same key Ansible uses
to reach workload nodes), rather than inventing a second trust relationship.

Nothing here writes anything, locally or remotely — it only reads. The write
path (encrypting and committing a sanitized `.env`) lives in
`registry_mcp.gitcrypt` and is driven by `tools/adoption.py`.
"""

from __future__ import annotations

import asyncio
import json


class SSHError(RuntimeError):
    """Raised when a remote command fails."""


async def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


def _ssh_base(key_path: str, user: str, host: str) -> list[str]:
    return [
        "ssh",
        "-i",
        key_path,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        f"{user}@{host}",
    ]


async def inspect_container(*, key_path: str, user: str, host: str, container: str) -> dict:
    """Return the parsed `docker inspect` output for one container on a remote host."""
    rc, out, err = await _run([*_ssh_base(key_path, user, host), "docker", "inspect", container])
    if rc != 0:
        raise SSHError(f"docker inspect {container!r} on {host} failed: {err.strip()}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise SSHError(f"docker inspect {container!r} on {host} returned invalid JSON") from exc
    if not data:
        raise SSHError(f"docker inspect returned no data for {container!r} on {host}")
    return data[0]


async def read_remote_file(*, key_path: str, user: str, host: str, path: str) -> str:
    """cat a file on the remote host. Raises SSHError if it can't be read."""
    rc, out, err = await _run([*_ssh_base(key_path, user, host), "cat", path])
    if rc != 0:
        raise SSHError(f"reading {path!r} on {host} failed: {err.strip()}")
    return out


async def try_read_remote_file(*, key_path: str, user: str, host: str, path: str) -> str | None:
    """Best-effort read; returns None instead of raising (e.g. a sibling .env
    that may not exist for this legacy service)."""
    try:
        return await read_remote_file(key_path=key_path, user=user, host=host, path=path)
    except SSHError:
        return None


def env_dict_from_inspect(inspect_data: dict) -> dict[str, str]:
    """Flatten `docker inspect`'s `Config.Env` (["KEY=value", ...]) into a dict.

    Skips malformed entries with no `=` rather than treating them as a key
    mapped to an empty string.
    """
    env: dict[str, str] = {}
    for entry in (inspect_data.get("Config") or {}).get("Env") or []:
        key, sep, value = str(entry).partition("=")
        if sep and key:
            env[key] = value
    return env


def labels_from_inspect(inspect_data: dict) -> dict[str, str]:
    return dict((inspect_data.get("Config") or {}).get("Labels") or {})


def compose_paths_from_labels(labels: dict[str, str]) -> tuple[list[str], str | None]:
    """Extract the compose config file path(s) and working dir from the
    labels Docker Compose stamps onto every container it creates."""
    config_files_raw = labels.get("com.docker.compose.project.config_files", "")
    config_files = [p for p in config_files_raw.split(",") if p]
    working_dir = labels.get("com.docker.compose.project.working_dir")
    return config_files, working_dir
