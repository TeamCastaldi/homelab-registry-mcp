"""Reasoning layer (Phase 7): DSPy-backed enrichment modules.

See ``docs/agentic-design-intent.md``. This layer *reasons and generates* — it
never writes to the registry and never acts on infrastructure. The detection
layer (``registry/reconcile.py``) and the discovery engine consume the callables
exposed here; they never import dspy directly, which keeps LLM calls out of the
deterministic layers.

The top-level ``dspy`` package is imported lazily inside :class:`Reasoner`, so
importing this package is cheap and side-effect free when the reasoning layer is
disabled (``DSPY_ENABLED=false``, the default).
"""

from registry_mcp.dspy.reasoner import Reasoner, build_reasoner

__all__ = ["Reasoner", "build_reasoner"]
