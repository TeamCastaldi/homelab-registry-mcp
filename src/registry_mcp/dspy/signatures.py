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


class InferServiceRequirements(dspy.Signature):
    """Given a foreign repo's README and the facts deterministic parsing
    already extracted from its Dockerfile/compose file, fill in only what
    those files did not state outright.

    `detected` is ground truth — it came from the repo's own files. Do not
    contradict it, restate it, or "correct" it. Your job is the gap: what the
    prose implies that no instruction declares. Chiefly, which backing
    services this needs (a database, a cache, a message broker) and which of
    the detected environment variables an operator must supply a real value
    for, as opposed to ones that already carry a working default.

    An env var is operator-supplied when it is a credential, an API key, an
    external endpoint, or anything the README tells a user to set. A var with
    a sensible default in the Dockerfile usually is not.

    If the README is missing, uninformative, or you are otherwise unsure, say
    so with a low confidence score and return empty fields rather than
    guessing — an unfilled field costs a follow-up question, while an invented
    dependency sends a generated compose file after a service that was never
    needed."""

    repo_url: str = dspy.InputField(desc="Source repository the files came from")
    readme: str = dspy.InputField(
        desc="README content verbatim; empty string when the repo has none", default=""
    )
    detected: dict = dspy.InputField(
        desc=(
            "Facts already extracted deterministically from the repo's own files: "
            "base_image/ports/env_vars/volumes/depends_on/service_names"
        )
    )

    service_name: str = dspy.OutputField(
        desc="Short lowercase name for this service, suitable as a directory name "
        "(e.g. 'paperless-ngx'); empty string if unclear"
    )
    summary: str = dspy.OutputField(desc="One-sentence description of what this service does")
    category: str = dspy.OutputField(desc="one of: infra app media monitoring security other")
    required_dependencies: list[str] = dspy.OutputField(
        desc="Backing services this needs that `detected` does not already list "
        "(e.g. ['postgres', 'redis']); empty list when none or unsure"
    )
    operator_supplied_env_vars: list[str] = dspy.OutputField(
        desc="Names drawn from `detected.env_vars` that an operator must supply a "
        "real value for; empty list when none or unsure. Never invent a name that "
        "is not in `detected.env_vars` or named by the README."
    )
    confidence: float = dspy.OutputField(desc="0.0 to 1.0")
    reasoning: str = dspy.OutputField(desc="What in the README supports each inference")


class GenerateServiceCompose(dspy.Signature):
    """Draft a brand-new Docker Compose file for a service this homelab has
    never run, from facts already extracted from that service's source repo.

    `intake` is ground truth: its ports, env vars, volumes, and dependencies
    came from the repo's own files (plus any confidence-gated inference
    already folded in). Do not contradict it, and do not invent services,
    ports, or variables it does not support.

    Follow `homelab_conventions` for shape and style. Beyond that, every
    generated file must:
    - use `service_name` as the main service's key, and set every service's
      `container_name` equal to its own service key;
    - use a published image with a pinned version tag for every service —
      never `:latest`, never a `build:` key. A Dockerfile's FROM line is a
      build base, not the image to deploy; if no published image for this
      service can be identified from the intake, say so with a low
      confidence score rather than inventing an image name;
    - set a `restart:` policy on every service;
    - reference every secret or operator-supplied environment value as a
      `${VAR_NAME}` interpolation, never a literal — the operator supplies
      those values later, outside your context;
    - include each backing service the intake requires (a database, a cache)
      as its own service in the same file, under the same rules;
    - prefer reaching a web-facing service through the external reverse-proxy
      network `${PROXY_NETWORK:-swarm-net}` over publishing a host port; any
      host port mapping that is genuinely needed carries a `# temporary`
      comment.

    Output the COMPLETE file, never a fragment. If you are not confident the
    file is correct and runnable, say so with a low confidence score rather
    than guessing — a rejected draft costs a follow-up question, while a
    confidently wrong one reaches a pull request.

    IMPORTANT: Never include real credentials, tokens, or secrets."""

    intake: dict = dspy.InputField(
        desc=(
            "Requirements from repo intake: detected facts (base_image/ports/env_vars/"
            "volumes/depends_on/service_names) plus any accepted inference "
            "(summary/required_dependencies/operator_supplied_env_vars)"
        )
    )
    homelab_conventions: str = dspy.InputField(
        desc="This homelab's compose conventions: canonical shape rules, plus the "
        "homelab repo's own compose spec when available"
    )
    service_name: str = dspy.InputField(desc="Main service key, e.g. 'paperless-ngx'")
    target_node: str = dspy.InputField(
        desc="Node this stack will deploy to; empty string if not yet chosen", default=""
    )

    compose_yaml: str = dspy.OutputField(
        desc=(
            "Complete compose.yaml content. CRITICAL: every secret or operator-supplied "
            "value is a ${VAR_NAME} interpolation, never a literal credential."
        )
    )
    confidence: float = dspy.OutputField(desc="0.0 to 1.0")
    reasoning: str = dspy.OutputField(
        desc="Which intake facts drove each choice, and anything left unresolved"
    )


class GenerateRemediationPatch(dspy.Signature):
    """Given a service record, its finding details, and the current file
    content, generate the minimal correct change to resolve the finding.
    Normalize incidental formatting as part of the same operation.

    Output the COMPLETE modified file content, never a diff — Git computes the
    diff. Change only what is necessary to resolve the finding; preserve every
    other line, comment, and value verbatim. If you are not confident the patch
    is correct and complete, say so with a low confidence score rather than
    guessing.

    For an `image_update` or `vulnerability_scan` finding, `context` gives the
    exact image and current/new tag to bump to — apply that literal change to
    the file's image reference and do not otherwise second-guess the tag.

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
    context: str = dspy.InputField(
        desc="Extra structured context specific to this finding_type "
        "(e.g. current/new image tag for image_update). Empty string if none.",
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


class NormalizeConfigFile(dspy.Signature):
    """Given a Docker Compose file and a list of formatting rules it still
    violates, rewrite it to satisfy exactly those rules.

    A deterministic formatter has already applied every rule it could apply
    safely; ``current_file`` may already be partially normalized. The rules
    listed in ``violations`` are the only ones left — usually because a
    comment stood in the way of a safe automated fix (e.g. reordering keys
    around a comment, or converting a commented list to a mapping).

    Output the COMPLETE file content, never a diff. Change ONLY what is
    needed to satisfy the listed violations: preserve every value, every
    comment (in the same position relative to the line it annotates), and
    every key not mentioned by a violation, verbatim. This must be a pure
    reformatting — the parsed meaning of the file (images, environment
    values, port mappings, volumes, networks) must be identical before and
    after. If you cannot satisfy a violation without touching something the
    canonical form doesn't call for, leave that specific violation
    unresolved rather than guess, and reflect it in a lower confidence score.

    IMPORTANT: Never include real credentials, tokens, or secrets in the
    output. If a value already looks like a hardcoded credential, leave it
    exactly as it was in the input — do not invent a placeholder; that is a
    Tier 2 finding for a human to fix, not something this rewrite handles."""

    current_file: str = dspy.InputField(desc="Current file content verbatim")
    file_path: str = dspy.InputField(desc="Path of the file being normalized")
    violations: str = dspy.InputField(
        desc="Comma-separated rule IDs (e.g. 'N-006, N-009') this file still violates"
    )
    canonical_form: str = dspy.InputField(desc="Summary of the target canonical form's rules")

    normalized_file: str = dspy.OutputField(
        desc="Complete file content with only the listed violations resolved"
    )
    commit_message: str = dspy.OutputField(
        desc="Conventional commit message, e.g. 'style: normalize ...'"
    )
    confidence: float = dspy.OutputField(desc="0.0 to 1.0")
    reasoning: str = dspy.OutputField(desc="Which violations were resolved and how")


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


class DetectHardcodedSecrets(dspy.Signature):
    """Given the raw content of a legacy, hand-written `docker-compose.yml` and
    the environment variables actually running inside its live container,
    produce a sanitized version of the compose file safe to commit to a public
    or shared Git repo.

    Replace every environment value that is a real credential (API token,
    password, private key, session secret, etc.) with a `${VAR_NAME}`
    interpolation reading from a sibling `.env` file, and list each one you
    replaced. Preserve every other line, comment, formatting choice, and
    non-secret value verbatim — this is adoption into GitOps management, not a
    rewrite. Ordinary configuration (ports, image tags, volume paths, feature
    flags) is not a secret and must not be touched.

    Output the COMPLETE sanitized file content, never a diff. If you are not
    confident you have correctly identified which values are secrets, say so
    with a low confidence score rather than guessing — a missed secret leaks
    into Git history, and a false positive breaks the service's config.

    IMPORTANT: Never invent, guess, or fabricate a replacement credential
    value yourself. Your only job here is to identify and interpolate; the
    actual secret values (kept or freshly rotated) are supplied by the
    operator after this step, outside your context."""

    compose_content: str = dspy.InputField(desc="Raw docker-compose.yml content, verbatim")
    container_env: dict = dspy.InputField(
        desc="Environment variables actually running in the live container "
        "(name -> value), from `docker inspect`"
    )
    container_labels: dict = dspy.InputField(desc="Docker labels on the live container")

    sanitized_compose: str = dspy.OutputField(
        desc="Complete compose file with secret values replaced by ${VAR_NAME} "
        "interpolations; every other line preserved verbatim"
    )
    detected_secret_keys: list[str] = dspy.OutputField(
        desc="Names of the environment variables identified as real secrets "
        "and interpolated out of the compose file"
    )
    confidence: float = dspy.OutputField(desc="0.0 to 1.0 confidence in the sanitization")
    reasoning: str = dspy.OutputField(desc="Why these values were flagged as secrets")


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
