"""Runs an approved Patchmon patch: PatchMon's own trigger API first, Ansible second.

Reached only after a human confirmed an Approve link (`webhooks/approval.py`);
nothing here is called from the webhook itself.

1. **PatchMon callback.** POST the approval to the trigger URL. The configured
   `PATCHMON_CALLBACK_URL` is the trust boundary: a payload's own
   `patchmon_callback_url` is used only on that URL's origin, so a delivery can
   never point this server's bearer token at another host. The body carries
   PatchMon's `host_id` + `patch_type`, the two fields its
   `POST /api/v1/patching/trigger` reads, alongside the rest of the alert.
2. **Ansible fallback.** When the callback is unset, unreachable, or answers
   anything but 2xx, run the operator's `PATCHMON_ANSIBLE_PLAYBOOK` against the
   one inventory host the alert names, with the same `ANSIBLE_CFG_PATH` /
   `SSH_KEY_PATH` / `SSH_DEFAULT_USER` that `hardware-discover-now` uses. Every
   fallback is logged as `patch_execution_fallback_ansible` with its reason.

A callback that times out may still have started a run on PatchMon's side, so
the fallback playbook should be safe to run on a host that is already patching
(package managers hold a lock, so the second run fails rather than interleaves).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from registry_mcp.logging import get_logger
from registry_mcp.models import PatchApproval, PatchApprovalStatus

_log = get_logger("patching.executor")

# Bounds on what a run's output contributes to the stored detail and the
# result email: the tail is where ansible-playbook puts its PLAY RECAP.
_DETAIL_CHARS = 2000
_LIST_HOSTS_TIMEOUT_SECONDS = 60
_LIST_HOSTS_RE = re.compile(r"^\s*hosts \((\d+)\):\s*$")
# Status codes that mean "this endpoint doesn't do that", as opposed to a
# failure of an endpoint that does.
_UNSUPPORTED = {404, 405, 501}


@dataclass(frozen=True)
class ExecutionResult:
    status: PatchApprovalStatus  # executed or failed
    executed_via: str | None  # "patchmon", "ansible", or None when nothing ran
    detail: str
    fallback_reason: str | None = None


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.scheme, parts.hostname.lower(), port or (443 if parts.scheme == "https" else 80)


def _host_of(url: str) -> str:
    """Scheme and host only, for logs: a URL's path or query can carry a token."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"


def resolve_callback_url(
    configured: str | None, from_payload: str | None
) -> tuple[str | None, str]:
    """The URL an approval POSTs to (or None), and why.

    A payload URL on the configured URL's origin wins, since PatchMon may name
    a per-alert path; any other payload URL is ignored in favor of the
    configured one.
    """
    if not configured or _origin(configured) is None:
        return None, "PATCHMON_CALLBACK_URL is not set"
    if from_payload:
        if _origin(from_payload) == _origin(configured):
            return from_payload, "the payload's callback URL"
        return configured, (
            "PATCHMON_CALLBACK_URL (the payload's callback URL is on another origin "
            "and was ignored)"
        )
    return configured, "PATCHMON_CALLBACK_URL"


def callback_body(approval: PatchApproval) -> dict[str, str | None]:
    return {
        "approval_id": approval.id,
        "event": approval.event,
        # PatchMon's trigger API reads these two and ignores the rest.
        "host_id": approval.patchmon_host_id,
        "patch_type": "patch_all",
        "target_host": approval.target_host,
        "service": approval.service,
        "current_version": approval.current_version,
        "target_version": approval.target_version,
    }


def extra_vars(approval: PatchApproval) -> dict[str, str]:
    """What the fallback playbook receives. Every value passed the intake
    schema's character allowlist, so none can carry a `{{ }}` template."""
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
    crash in either path, comes back as an `ExecutionResult`."""

    def __init__(
        self,
        *,
        callback_url: str | None = None,
        api_token: str | None = None,
        callback_timeout_seconds: float = 10.0,
        playbook: str | None = None,
        ansible_cfg_path: str | None = None,
        ssh_key_path: str | None = None,
        ssh_user: str = "root",
        ansible_timeout_seconds: float = 1800,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._callback_url = callback_url
        self._api_token = api_token
        self._callback_timeout = callback_timeout_seconds
        self._playbook = playbook
        self._ansible_cfg_path = ansible_cfg_path
        self._ssh_key_path = ssh_key_path
        self._ssh_user = ssh_user
        self._ansible_timeout = ansible_timeout_seconds
        self._transport = transport

    @property
    def can_execute(self) -> bool:
        """Whether any execution path is configured at all."""
        return bool(resolve_callback_url(self._callback_url, None)[0] or self._playbook)

    def plan(self, approval: PatchApproval) -> str:
        """The execution path an approval would take, in words, for the email."""
        url, _ = resolve_callback_url(self._callback_url, approval.payload_callback_url)
        playbook = Path(self._playbook).name if self._playbook else None
        if url and playbook:
            return (
                f"PatchMon trigger at {_host_of(url)}; if that fails, "
                f"the Ansible playbook {playbook} against {approval.target_host}"
            )
        if url:
            return f"PatchMon trigger at {_host_of(url)} (no Ansible fallback configured)"
        if playbook:
            return f"the Ansible playbook {playbook} against {approval.target_host}"
        return "nothing (no execution path is configured)"

    async def execute(self, approval: PatchApproval) -> ExecutionResult:
        try:
            return await self._execute(approval)
        except Exception as exc:  # the caller records the outcome; it must get one
            _log.exception("patch_execution_crashed", approval_id=approval.id)
            return ExecutionResult(
                PatchApprovalStatus.failed, None, f"execution crashed: {type(exc).__name__}"
            )

    async def _execute(self, approval: PatchApproval) -> ExecutionResult:
        url, source = resolve_callback_url(self._callback_url, approval.payload_callback_url)
        if url is None:
            fallback_reason = f"PatchMon callback not configured ({source})"
        else:
            if approval.payload_callback_url and url != approval.payload_callback_url:
                _log.warning(
                    "patchmon_callback_url_rejected",
                    approval_id=approval.id,
                    payload_callback_host=_host_of(approval.payload_callback_url),
                    using=_host_of(url),
                )
            error = await self._callback(url, approval)
            if error is None:
                _log.info(
                    "patch_executed",
                    approval_id=approval.id,
                    target_host=approval.target_host,
                    via="patchmon",
                    callback_host=_host_of(url),
                )
                return ExecutionResult(
                    PatchApprovalStatus.executed,
                    "patchmon",
                    f"PatchMon accepted the trigger ({_host_of(url)}, via {source}).",
                )
            fallback_reason = f"PatchMon callback failed: {error}"
            _log.warning(
                "patchmon_callback_failed",
                approval_id=approval.id,
                target_host=approval.target_host,
                callback_host=_host_of(url),
                error=error,
            )

        if not self._playbook:
            _log.error(
                "patch_execution_no_fallback",
                approval_id=approval.id,
                target_host=approval.target_host,
                reason=fallback_reason,
            )
            return ExecutionResult(
                PatchApprovalStatus.failed,
                None,
                f"{fallback_reason}. No Ansible fallback: PATCHMON_ANSIBLE_PLAYBOOK is not set.",
                fallback_reason,
            )

        _log.warning(
            "patch_execution_fallback_ansible",
            approval_id=approval.id,
            target_host=approval.target_host,
            playbook=self._playbook,
            reason=fallback_reason,
        )
        status, detail = await self._run_playbook(approval)
        if status is PatchApprovalStatus.executed:
            _log.info(
                "patch_executed",
                approval_id=approval.id,
                target_host=approval.target_host,
                via="ansible",
            )
        else:
            _log.error(
                "patch_ansible_failed",
                approval_id=approval.id,
                target_host=approval.target_host,
                detail=detail,
            )
        return ExecutionResult(status, "ansible", f"{fallback_reason}. {detail}", fallback_reason)

    async def _callback(self, url: str, approval: PatchApproval) -> str | None:
        """POST the trigger. None on a 2xx, otherwise why it didn't take."""
        headers = {"Accept": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        try:
            # No redirects: a 3xx is not an accepted trigger, and following one
            # would carry the bearer token somewhere this server wasn't told to.
            async with httpx.AsyncClient(
                timeout=self._callback_timeout,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = await client.post(url, json=callback_body(approval), headers=headers)
        except httpx.HTTPError as exc:
            return f"unreachable ({type(exc).__name__})"
        if response.status_code in _UNSUPPORTED:
            return f"not supported by the endpoint (HTTP {response.status_code})"
        if not response.is_success:
            return f"HTTP {response.status_code}"
        return None

    async def _run_playbook(self, approval: PatchApproval) -> tuple[PatchApprovalStatus, str]:
        failed = PatchApprovalStatus.failed
        if not (self._ansible_cfg_path and self._ssh_key_path):
            return failed, "The Ansible fallback needs ANSIBLE_CFG_PATH and SSH_KEY_PATH."
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
