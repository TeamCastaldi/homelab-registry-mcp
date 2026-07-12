"""Live Ansible fact-gather discovery for the hardware node registry (Phase 9b).

Shells out to the `ansible` CLI's `setup` module against the operator's own
inventory (via `ANSIBLE_CONFIG=<ansible.cfg>`, the same file the deploy
workflow uses) — no separate inventory setting to keep in sync. Like every
other discovery source in this project, this only *reads* the target node;
nothing here writes anything remotely. The write path is `HardwareStore`,
driven by the caller (`tools/hardware.py`).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from registry_mcp.models.hardware import DiskType, StorageDisk

# One-line-per-host `ansible -o` output: "<host> | <STATUS> => {json}"
_ONE_LINE_RE = re.compile(r"^(?P<host>\S+)\s*\|\s*(?P<status>[A-Z!]+)\s*=>\s*(?P<json>\{.*\})\s*$")

_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]?B)")
_SIZE_SCALE_GB = {"B": 1e-9, "KB": 1e-6, "MB": 1e-3, "GB": 1.0, "TB": 1e3}

# Provenance fields this module can populate from `setup` facts. Anything a
# human curated on the node (display_name, role, tags, notes, location, ...)
# is deliberately absent — the caller only ever writes these back.
DISCOVERY_FIELDS = {
    "ip_address",
    "mac_address",
    "os",
    "cpu_model",
    "cpu_cores",
    "ram_gb",
    "storage",
}


class AnsibleFactsError(RuntimeError):
    """Raised when the `ansible` command itself could not be run at all."""


async def _run(cmd: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


def parse_setup_output(stdout: str) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Parse `ansible <pattern> -m setup -o` output.

    Returns `(facts_by_host, failures)` — `facts_by_host` maps inventory
    hostname to the `ansible_facts` dict for hosts that reported SUCCESS;
    `failures` maps hostname to an error message for anything else
    (unreachable, permission denied, module error).
    """
    facts_by_host: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for line in stdout.splitlines():
        match = _ONE_LINE_RE.match(line.strip())
        if not match:
            continue
        host, status = match.group("host"), match.group("status")
        try:
            payload = json.loads(match.group("json"))
        except json.JSONDecodeError:
            failures[host] = f"unparseable {status} response"
            continue
        if status == "SUCCESS" and payload.get("ansible_facts"):
            facts_by_host[host] = payload["ansible_facts"]
        else:
            failures[host] = str(payload.get("msg", status))
    return facts_by_host, failures


async def gather_facts(
    *,
    pattern: str,
    ansible_cfg_path: str,
    ssh_key_path: str,
    ssh_user: str = "root",
    connect_timeout_seconds: int = 15,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Run `ansible <pattern> -m setup` and return `(facts_by_host, failures)`."""
    env = {
        **os.environ,
        "ANSIBLE_CONFIG": ansible_cfg_path,
    }
    cmd = [
        "ansible",
        pattern,
        "-m",
        "setup",
        "-o",
        "--private-key",
        ssh_key_path,
        "-u",
        ssh_user,
        "-e",
        "ansible_ssh_common_args=-o StrictHostKeyChecking=accept-new "
        f"-o ConnectTimeout={connect_timeout_seconds}",
    ]
    try:
        rc, stdout, stderr = await _run(cmd, env)
    except FileNotFoundError as exc:
        raise AnsibleFactsError(f"ansible CLI not found: {exc}") from exc
    if not stdout.strip() and stderr.strip():
        raise AnsibleFactsError(f"ansible setup against {pattern!r} failed: {stderr.strip()}")
    facts_by_host, failures = parse_setup_output(stdout)
    if rc != 0 and not facts_by_host and not failures:
        # Nonzero exit with output that didn't match a single per-host line
        # (e.g. an inventory/parsing error printed as plain text) — surface it
        # rather than silently reporting an empty, ostensibly successful pass.
        raise AnsibleFactsError(
            f"ansible setup against {pattern!r} exited {rc}: {(stderr or stdout).strip()}"
        )
    return facts_by_host, failures


def hostname_from_facts(facts: dict[str, Any]) -> str | None:
    return facts.get("ansible_hostname") or facts.get("ansible_fqdn")


def node_fields_from_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Map a narrow subset of `setup` facts onto `HardwareNode` provenance
    fields. Keys absent here are simply omitted, so the caller's update path
    leaves the node's existing value alone rather than clearing it."""
    fields: dict[str, Any] = {}

    default_ipv4 = facts.get("ansible_default_ipv4") or {}
    if default_ipv4.get("address"):
        fields["ip_address"] = default_ipv4["address"]
    if default_ipv4.get("macaddress"):
        fields["mac_address"] = default_ipv4["macaddress"]

    distribution = facts.get("ansible_distribution")
    if distribution:
        version = facts.get("ansible_distribution_version")
        fields["os"] = f"{distribution} {version}".strip() if version else distribution

    cpu_model = _cpu_model(facts.get("ansible_processor"))
    if cpu_model:
        fields["cpu_model"] = cpu_model
    cores = facts.get("ansible_processor_vcpus") or facts.get("ansible_processor_cores")
    if cores:
        fields["cpu_cores"] = int(cores)

    mem_mb = facts.get("ansible_memtotal_mb")
    if mem_mb:
        fields["ram_gb"] = round(mem_mb / 1024, 2)

    disks = _disks_from_devices(facts.get("ansible_devices") or {})
    if disks:
        fields["storage"] = [d.model_dump() for d in disks]

    return fields


def _cpu_model(processor: Any) -> str | None:
    """`ansible_processor` is a flat list mixing index/vendor/model strings
    per physical CPU (e.g. `["0", "GenuineIntel", "Intel(R) Xeon(R) ..."]`) —
    the model string is reliably the longest entry."""
    if not isinstance(processor, list) or not processor:
        return None
    return max((str(p) for p in processor), key=len, default="") or None


def _disks_from_devices(devices: dict[str, Any]) -> list[StorageDisk]:
    disks: list[StorageDisk] = []
    for name, info in devices.items():
        if not isinstance(info, dict) or name.startswith(("loop", "sr", "dm-", "zd", "ram")):
            continue
        size_gb = _size_to_gb(info.get("size"))
        if size_gb is None:
            continue
        disks.append(
            StorageDisk(
                device=f"/dev/{name}",
                model=info.get("model") or None,
                size_gb=size_gb,
                type=_disk_type(name, info),
            )
        )
    return disks


def _size_to_gb(size: str | None) -> float | None:
    """Ansible reports device size as a human string, e.g. `'500.00 GB'`."""
    if not size:
        return None
    match = _SIZE_RE.match(str(size).strip())
    if not match:
        return None
    value = float(match.group(1))
    return round(value * _SIZE_SCALE_GB.get(match.group(2), 1.0), 2)


def _disk_type(name: str, info: dict[str, Any]) -> DiskType:
    if name.startswith("nvme"):
        return DiskType.nvme
    rotational = info.get("rotational")
    if rotational == "0":
        return DiskType.ssd
    if rotational == "1":
        return DiskType.hdd
    return DiskType.unknown
