"""Patchmon patch approvals (ADR-020): the pause between an alert and a patch.

`PatchApprovalStore` holds each alert until a human answers its email;
`PatchExecutor` runs an approved one with the operator's Ansible playbook,
against exactly one inventory host. The HTTP surface that drives both
lives in `webhooks/patchmon.py` (intake) and `webhooks/approval.py` (the links).
"""

from registry_mcp.patching.executor import ExecutionResult, PatchExecutor
from registry_mcp.patching.store import (
    ApprovalAction,
    IssuedApproval,
    PatchApprovalStore,
    TokenCheck,
    TokenState,
)

__all__ = [
    "ApprovalAction",
    "ExecutionResult",
    "IssuedApproval",
    "PatchApprovalStore",
    "PatchExecutor",
    "TokenCheck",
    "TokenState",
]
