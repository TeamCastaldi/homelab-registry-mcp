"""DSPy signatures for the Phase 7 reasoning layer.

Importing this module imports the top-level ``dspy`` package, so it is loaded
lazily by :class:`registry_mcp.dspy.reasoner.Reasoner` only when the reasoning
layer is enabled.

These signatures are faithful to ``docs/dspy-evaluation-registry-mcp.md`` and
``docs/agentic-design-intent.md``. ``ResolveServiceIdentity`` is scoped to the
detection layer's actual need — confirming a fuzzy match against existing
registry entries *after* deterministic matching has failed — rather than the
broader identity-plus-metadata bundle sketched in the evaluation note; metadata
inference is owned by ``InferServiceMetadata``.
"""

from __future__ import annotations

import dspy


class ResolveServiceIdentity(dspy.Signature):
    """Decide whether a newly discovered service candidate refers to the same
    logical service as one already in the registry.

    This runs only after deterministic matching (exact name, Traefik router,
    shared URL host) has already failed, so the spellings differ across
    sources. Reason from the evidence: a Docker name, a Traefik host, and an
    Authentik slug may be different spellings of one service. Claim a match only
    when the candidate clearly describes the same service as an existing entry.
    When unsure, return an empty ``matched_name`` — a wrong merge is worse than
    a duplicate that a later pass can reconcile."""

    candidate: dict = dspy.InputField(desc="The unmatched discovered service candidate")
    existing_services: list = dspy.InputField(
        desc=(
            "Current registry entries available for matching; each a dict with "
            "name/display_name/urls/host/traefik_router/authentik_app_slug/category/auth_mode"
        )
    )

    matched_name: str = dspy.OutputField(
        desc="The `name` of the existing service this candidate matches, or empty string if new"
    )
    confidence: float = dspy.OutputField(desc="0.0 to 1.0 confidence in the match")
    reasoning: str = dspy.OutputField(desc="Why this is or is not the same service")


class InferServiceMetadata(dspy.Signature):
    """Infer curated metadata for a newly discovered service from the available
    Traefik routing context. Used only when creating a brand-new registry entry
    that no deterministic rule could enrich (e.g. a Traefik-only discovery with
    no Docker labels and no Authentik application)."""

    router_rule: str = dspy.InputField(desc="Traefik router rule string, e.g. Host(`plex.lan`)")
    middlewares: list[str] = dspy.InputField(desc="Middleware names attached to this router")
    service_name: str = dspy.InputField(desc="Short service name derived from the router")

    display_name: str = dspy.OutputField(desc="Human-friendly display name")
    category: str = dspy.OutputField(desc="one of: infra app media monitoring security other")
    auth_mode: str = dspy.OutputField(
        desc="one of: none forward_auth oauth2_proxy basic internal unknown"
    )
    notes: str = dspy.OutputField(desc="One-sentence description inferred from context")
    confidence: float = dspy.OutputField(desc="0.0 to 1.0 confidence in the inference")


class GenerateRemediationPatch(dspy.Signature):
    """Given a service record, its conflict details, and the current file
    content, generate the minimal correct change to resolve an
    auth_mode_conflict. Normalize incidental formatting as part of the same
    operation.

    Output the COMPLETE modified file content, never a diff — Git computes the
    diff. Change only what is necessary to resolve the finding; preserve every
    other line, comment, and value verbatim. If you are not confident the patch
    is correct and complete, say so with a low confidence score rather than
    guessing.

    IMPORTANT: Never include real credentials, tokens, or secrets in the
    patch output. Use descriptive placeholders for any secret values.
    Prefer reusing existing shared middlewares over adding new containers
    when a suitable middleware already exists in the Traefik config."""

    service: dict = dspy.InputField(desc="Full service registry record")
    finding_type: str = dspy.InputField(desc="Type of conflict to remediate")
    current_file: str = dspy.InputField(desc="Current file content verbatim")
    file_path: str = dspy.InputField(desc="Path of the file being modified")
    apply_mode: str = dspy.InputField(desc="How the change will be applied after merge")
    existing_middlewares: str = dspy.InputField(
        desc="Contents of the Traefik dynamic middleware config file "
        "(middleware.yml). Empty string if unavailable.",
        default="",
    )

    patch: str = dspy.OutputField(
        desc=(
            "Complete corrected file content. "
            "If an existing shared middleware (e.g. authentik-auth@file) "
            "already covers this service's auth requirement, prefer adding "
            "a middleware label to the existing router over adding a new "
            "sidecar container. "
            "Only add a new outpost sidecar if no suitable shared middleware "
            "exists. "
            "CRITICAL: Never include real credentials, tokens, passwords, or "
            "API keys in the patch. Any environment variable that holds a "
            "secret MUST use a placeholder value in the format "
            "<replace-with-X> where X describes what the value should be "
            "(e.g. AUTHENTIK_TOKEN: <replace-with-outpost-token>). "
            "The human reviewer will supply real values before merging."
        )
    )
    commit_message: str = dspy.OutputField(desc="Conventional commit message")
    pr_title: str = dspy.OutputField(desc="Pull request title")
    pr_body: str = dspy.OutputField(desc="Pull request description in markdown")
    confidence: float = dspy.OutputField(desc="0.0 to 1.0")
    reasoning: str = dspy.OutputField(desc="Why this patch resolves the finding")


class ApplyReviewFeedback(dspy.Signature):
    """Given the current content of a file on an open remediation PR and a
    human reviewer's comment requesting a change, produce the revised file.

    Output the COMPLETE revised file content, never a diff — Git computes the
    diff. Change only what the feedback asks for; preserve every other line,
    comment, and value verbatim. If the feedback is unclear, out of scope for
    this file, or you are not confident the revision is correct and complete,
    say so with a low confidence score rather than guessing.

    IMPORTANT: Never include real credentials, tokens, or secrets in the
    revised file. Use descriptive placeholders for any secret values."""

    file_path: str = dspy.InputField(desc="Path of the file being revised")
    current_file: str = dspy.InputField(
        desc="Current content of the file on the PR branch, verbatim"
    )
    feedback: str = dspy.InputField(desc="The human reviewer's PR comment")

    revised_file: str = dspy.OutputField(
        desc=(
            "Complete revised file content addressing the feedback. "
            "CRITICAL: Never include real credentials, tokens, passwords, or "
            "API keys. Any environment variable that holds a secret MUST use "
            "a placeholder value in the format <replace-with-X>."
        )
    )
    commit_message: str = dspy.OutputField(desc="Conventional commit message for this revision")
    confidence: float = dspy.OutputField(desc="0.0 to 1.0 confidence the revision is correct")
    reasoning: str = dspy.OutputField(desc="Why this revision addresses the feedback")


class SummarizeAccessAudit(dspy.Signature):
    """Summarize Authentik access events for one application into a structured,
    pre-reasoned report, so the client receives a synthesis rather than raw
    JSON event objects."""

    application_slug: str = dspy.InputField()
    events: list = dspy.InputField(desc="Raw Authentik event objects, newest first")
    time_window_hours: int = dspy.InputField()

    summary: str = dspy.OutputField(desc="Plain-English summary of access patterns")
    anomalies: list[str] = dspy.OutputField(desc="Unusual access events worth flagging")
    unique_users: int = dspy.OutputField(desc="Distinct users seen in the window")
    failed_auth_count: int = dspy.OutputField(desc="Count of failed authentication events")
    risk_level: str = dspy.OutputField(desc="one of: low medium high")
