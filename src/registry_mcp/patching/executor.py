"""Runs an approved Patchmon patch with the operator's Ansible playbook.

Reached only after a human confirmed an Approve link (`webhooks/approval.py`);
nothing here is called from the webhook itself.

`PATCHMON_ANSIBLE_PLAYBOOK` runs against the one inventory host the alert names,
with the same `ANSIBLE_CFG_PATH` / `SSH_KEY_PATH` / `SSH_DEFAULT_USER` that
`hardware-discover-now` uses. It is the only execution path. PatchMon's own
`POST /api/v1/patching/trigger` accepts nothing but a logged-in user's
short-lived session token, so calling it would mean holding a PatchMon user's
password (ADR-020's 2026-09-27 amendment).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from registry_mcp.logging import get_logger
from registry_mcp.models import PatchApproval, PatchApprovalStatus

_log = get_logger("patching.executor")

# Bounds on what a run's output contributes to the stored detail and the
# result email: the tail is where ansible-playbook puts its PLAY RECAP.
_DETAIL_CHARS = 2000
_LIST_HOSTS_TIMEOUT_SECONDS = 60
_LIST_HOSTS_RE = re.compile(r"^\s*hosts \((\d+)\):\s*$")


@dataclass(frozen=True)
class ExecutionResult:
    status: PatchApprovalStatus  # executed or failed
    executed_via: str | None  # "ansible", or None when nothing ran
    detail: str


def extra_vars(approval: PatchApproval) -> dict[str, str]:
    """What the playbook receives. Every value passed the intake schema's
    character allowlist, so none can carry a `{{ }}` template."""
    return {
        "patchmon_approval_id": approval.id,
        "patchmon_event": approval.event,
        "patchmon_target_host": approval.target_host,
        "patchmon_host_id": approval.patchmon_host_id or "",
        "patchmon_service": approval.service or "",
        "patchmon_current_version": approval.current_version or "",
        "patchmon_target_version": approval.target_version or "",
    }


def parse_list_hosts(stdout: str) -> list[str]:
    """The host names under `ansible --list-hosts`'s `hosts (N):` header."""
    lines = stdout.splitlines()
    for index, line in enumerate(lines):
        match = _LIST_HOSTS_RE.match(line)
        if match:
            count = int(match.group(1))
            return [name.strip() for name in lines[index + 1 : index + 1 + count] if name.strip()]
    return []


def _tail(*outputs: str) -> str:
    text = "\n".join(part.strip() for part in outputs if part and part.strip())
    return text[-_DETAIL_CHARS:]


async def _run(cmd: list[str], env: dict[str, str], *, timeout: float) -> tuple[int, str, str]:
    """Run `cmd` without a shell; kill it if it outlives `timeout`."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


class PatchExecutor:
    """Carries out one approved patch. Never raises: every outcome, including a
    crash, comes back as an `ExecutionResult`."""

    def __init__(
        self,
        *,
        playbook: str | None = None,
        ansible_cfg_path: str | None = None,
        ssh_key_path: str | None = None,
        ssh_user: str = "root",
        ansible_timeout_seconds: float = 1800,
    ) -> None:
        self._playbook = playbook
        self._ansible_cfg_path = ansible_cfg_path
        self._ssh_key_path = ssh_key_path
        self._ssh_user = ssh_user
        self._ansible_timeout = ansible_timeout_seconds

    @property
    def can_execute(self) -> bool:
        """Whether an approval would have a playbook to run. `ANSIBLE_CFG_PATH`
        and `SSH_KEY_PATH` aren't checked here: missing either puts the server in
        read-only mode, which keeps the routes mounted but refuses to act."""
        return bool(self._playbook)

    def plan(self, approval: PatchApproval) -> str:
        """What an approval would run, in words, for the email and the pages."""
        if not self._playbook:
            return "nothing (PATCHMON_ANSIBLE_PLAYBOOK is not set)"
        return f"the Ansible playbook {Path(self._playbook).name} against {approval.target_host}"

    async def execute(self, approval: PatchApproval) -> ExecutionResult:
        try:
            return await self._execute(approval)
        except Exception as exc:  # the caller records the outcome; it must get one
            _log.exception("patch_execution_crashed", approval_id=approval.id)
            return ExecutionResult(
                PatchApprovalStatus.failed, None, f"execution crashed: {type(exc).__name__}"
            )

    async def _execute(self, approval: PatchApproval) -> ExecutionResult:
        if not self._playbook:
            _log.error("patch_execution_no_playbook", approval_id=approval.id)
            return ExecutionResult(
                PatchApprovalStatus.failed,
                None,
                "Nothing ran: PATCHMON_ANSIBLE_PLAYBOOK is not set.",
            )
        status, detail = await self._run_playbook(approval)
        if status is PatchApprovalStatus.executed:
            _log.info("patch_executed", approval_id=approval.id, target_host=approval.target_host)
        else:
            _log.error(
                "patch_ansible_failed",
                approval_id=approval.id,
                target_host=approval.target_host,
                detail=detail,
            )
        return ExecutionResult(status, "ansible", detail)

    async def _run_playbook(self, approval: PatchApproval) -> tuple[PatchApprovalStatus, str]:
        failed = PatchApprovalStatus.failed
        if not (self._ansible_cfg_path and self._ssh_key_path):
            return failed, "The playbook needs ANSIBLE_CFG_PATH and SSH_KEY_PATH."
        host = approval.target_host
        if host.startswith("-"):
            return failed, f"Refusing target host {host!r}: it would read as an option."
        env = {**os.environ, "ANSIBLE_CONFIG": self._ansible_cfg_path, "ANSIBLE_NOCOLOR": "1"}

        # `--limit` takes a pattern, and a name like `all` or a group name is a
        # valid one. Patch only when the name resolves to exactly itself.
        try:
            _, listed_out, listed_err = await _run(
                ["ansible", "--list-hosts", "--", host],
                env,
                timeout=_LIST_HOSTS_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return failed, "The ansible CLI is not installed on this server."
        except TimeoutError:
            return failed, f"Resolving {host!r} in the inventory timed out."
        listed = parse_list_hosts(listed_out)
        if listed != [host]:
            return failed, (
                f"{host!r} does not name exactly one inventory host "
                f"(it matched {len(listed)}). {_tail(listed_err)}".strip()
            )

        cmd = [
            "ansible-playbook",
            "--private-key",
            self._ssh_key_path,
            "-u",
            self._ssh_user,
            "--limit",
            host,
            "-e",
            json.dumps(extra_vars(approval)),
            "--",
            self._playbook,
        ]
        try:
            rc, stdout, stderr = await _run(cmd, env, timeout=self._ansible_timeout)
        except FileNotFoundError:
            return failed, "The ansible-playbook CLI is not installed on this server."
        except TimeoutError:
            return failed, f"ansible-playbook was stopped after {self._ansible_timeout:g}s."
        output = _tail(stdout, stderr)
        if rc == 0:
            return PatchApprovalStatus.executed, f"Ansible playbook succeeded.\n{output}".strip()
        return failed, f"ansible-playbook exited {rc}.\n{output}".strip()
