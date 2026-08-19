"""Normalization: scans compose files against the canonical form
(``docs/specs/spec-compose-normal-form.md``) and opens one PR per node with
any safe formatting fixes. Off by default (``NORMALIZATION_ENABLED``); kept
out of ``proposal/`` so a normalization PR can never bundle a security fix.
"""

from registry_mcp.normalization.engine import NormalizationEngine, schedule_seconds
from registry_mcp.normalization.generator import NormalizationGenerator, NormalizationResult

__all__ = [
    "NormalizationEngine",
    "NormalizationGenerator",
    "NormalizationResult",
    "schedule_seconds",
]
