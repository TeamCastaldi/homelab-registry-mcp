"""Startup health checks for the GitOps/Ansible write-path prerequisites.

Evaluates whether this node has been provisioned as a control plane (a cloned
homelab Git repo, an `ansible.cfg`, and an SSH key) — independent of whether
the write-path env vars (`GIT_*`, `SECRETS_*`) happen to be set. A misconfigured
or partially-bootstrapped node degrades to read-only rather than exposing
GitOps tools that would fail or behave unexpectedly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from registry_mcp.config import Settings


@dataclass(frozen=True)
class HealthCheckResult:
    """Outcome of a single prerequisite check."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HealthReport:
    """Aggregate result of all startup health checks."""

    checks: list[HealthCheckResult]

    @property
    def healthy(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


def _check_git_repo(settings: Settings) -> HealthCheckResult:
    if not settings.secrets_repo_path:
        return HealthCheckResult("git_repo", False, "SECRETS_REPO_PATH is not configured")
    repo = Path(settings.secrets_repo_path)
    if not repo.is_dir():
        return HealthCheckResult("git_repo", False, f"{repo} does not exist")
    if not (repo / ".git").exists():
        return HealthCheckResult("git_repo", False, f"{repo} is not a git repository")
    return HealthCheckResult("git_repo", True, str(repo))


def _check_ansible_cfg(settings: Settings) -> HealthCheckResult:
    if not settings.ansible_cfg_path:
        return HealthCheckResult("ansible_cfg", False, "ANSIBLE_CFG_PATH is not configured")
    path = Path(settings.ansible_cfg_path)
    if not path.is_file():
        return HealthCheckResult("ansible_cfg", False, f"{path} not found")
    return HealthCheckResult("ansible_cfg", True, str(path))


def _check_ssh_key(settings: Settings) -> HealthCheckResult:
    if not settings.ssh_key_path:
        return HealthCheckResult("ssh_key", False, "SSH_KEY_PATH is not configured")
    path = Path(settings.ssh_key_path)
    if not path.is_file():
        return HealthCheckResult("ssh_key", False, f"{path} not found")
    return HealthCheckResult("ssh_key", True, str(path))


def check_health(settings: Settings) -> HealthReport:
    """Run all startup health checks against the current configuration."""
    return HealthReport(
        checks=[
            _check_git_repo(settings),
            _check_ansible_cfg(settings),
            _check_ssh_key(settings),
        ]
    )
