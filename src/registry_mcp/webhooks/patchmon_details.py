"""What PatchMon last saw on a host, as lines for the approval email.

An approval otherwise asks a person to patch a host without saying what's
pending. With the PatchMon API configured and a host id on the alert, the email
lists the pending packages (security updates first), whether a reboot is
needed and why, the running and installed kernel, and the host's latest agent
report, so how fresh the list is can be judged.

Best effort, and never in the way of the question itself: the three reads run
together under one time budget, a read that fails says so in its own line, and
the email goes out regardless.

Everything here came from PatchMon's database, which its agents write, so it is
untrusted text. Each value is flattened to one line (control characters, line
breaks, and bidirectional overrides become spaces) and clipped, so nothing can
forge a line of the email, such as a second "Approve:" link in its plain-text
part. The HTML part is escaped by the SMTP provider.
"""

from __future__ import annotations

import asyncio
from typing import Any

from registry_mcp.integrations.patchmon import PatchmonClient, PatchmonError
from registry_mcp.logging import get_logger
from registry_mcp.webhooks.common import one_line as _one_line

_log = get_logger("webhooks.patchmon_details")

MAX_PACKAGES_LISTED = 20
_MAX_VALUE_CHARS = 120


def one_line(value: object, limit: int = _MAX_VALUE_CHARS) -> str:
    return _one_line(value, limit)


def _failure(exc: BaseException) -> str:
    return one_line(exc) if isinstance(exc, PatchmonError) else type(exc).__name__


def _report_line(result: list[dict[str, Any]] | BaseException) -> str:
    if isinstance(result, BaseException):
        return f"Last agent report: unavailable ({_failure(result)})"
    if not result:
        return "Last agent report: none on record"
    latest = result[0]
    line = f"Last agent report: {one_line(latest.get('date')) or 'unknown time'}"
    status = one_line(latest.get("status"))
    return f"{line} ({status})" if status else line


def _system_lines(result: dict[str, Any] | BaseException) -> list[str]:
    if isinstance(result, BaseException):
        return [f"Reboot and kernel: unavailable ({_failure(result)})"]
    if result.get("needs_reboot") is True:
        reason = one_line(result.get("reboot_reason"))
        lines = [f"Reboot needed: yes ({reason})" if reason else "Reboot needed: yes"]
    else:
        lines = ["Reboot needed: no"]
    running = one_line(result.get("kernel_version"))
    installed = one_line(result.get("installed_kernel_version"))
    if running and installed and running != installed:
        lines.append(f"Kernel: running {running}, installed {installed}")
    elif running or installed:
        lines.append(f"Kernel: {running or installed}")
    return lines


def _package_lines(result: list[dict[str, Any]] | BaseException) -> list[str]:
    if isinstance(result, BaseException):
        return [f"Pending updates: unavailable ({_failure(result)})"]
    if not result:
        return ["Pending updates: none"]
    ordered = sorted(
        result,
        key=lambda item: (item.get("is_security_update") is not True, one_line(item.get("name"))),
    )
    security = sum(1 for item in ordered if item.get("is_security_update") is True)
    lines = [f"Pending updates: {len(ordered)} ({security} security)"]
    for item in ordered[:MAX_PACKAGES_LISTED]:
        tag = "[security] " if item.get("is_security_update") is True else ""
        current = one_line(item.get("current_version"), 60) or "?"
        available = one_line(item.get("available_version"), 60) or "?"
        lines.append(f"  - {tag}{one_line(item.get('name'), 80)} {current} -> {available}")
    if len(ordered) > MAX_PACKAGES_LISTED:
        lines.append(f"  - ...and {len(ordered) - MAX_PACKAGES_LISTED} more")
    return lines


async def host_details(patchmon: PatchmonClient, host_id: str, *, budget: float) -> list[str]:
    """The email's PatchMon section for `host_id`. Never raises."""
    heading = "What PatchMon last saw on this host:"
    try:
        system, packages, reports = await asyncio.wait_for(
            asyncio.gather(
                patchmon.get_host_system(host_id),
                patchmon.list_host_packages(host_id),
                patchmon.list_package_reports(host_id, limit=1),
                return_exceptions=True,
            ),
            budget,
        )
    except TimeoutError:
        _log.warning("patchmon_details_unavailable", host_id=host_id, error="timed out")
        return [heading, "Unavailable: PatchMon didn't answer in time."]
    for result in (system, packages, reports):
        if isinstance(result, BaseException):
            _log.warning("patchmon_details_unavailable", host_id=host_id, error=_failure(result))
    return [
        heading,
        _report_line(reports),
        *_system_lines(system),
        *_package_lines(packages),
        "This is PatchMon's list as of that report; the playbook decides what it installs.",
    ]
