# ARD-004: Upstream Version Detection and Update Proposals

**Status:** Proposed  
**Date:** 2026  
**Deciders:** the maintainer  

---

## Context

The registry currently tracks *actual state* — what images are running on each node,
discovered via Docker. It does not know what image version is *intended* (the tag
committed in the homelab repo) or what version is *available* (the latest published
tag upstream).

The gap means the registry cannot answer: "Is Immich out of date?" or "Is what's
running what I intended to deploy?"

The Watchtower application (containrrr/watchtower) solves the "available vs running"
part of this problem by polling upstream registries and restarting containers
automatically. However, automatic restarts without a human gate are incompatible with
the registry's agentic design principles (see `agentic-design-intent.md`). The
correct registry behavior is to detect and propose, not detect and act.

This ADR documents the decision to build upstream version detection into the registry
as a new discovery source, feeding the existing proposal engine rather than acting
autonomously.

---

## Decision

Implement upstream version detection as a new `DiscoverySource` that:

1. Reads intended image references from `compose.yaml` files in the homelab repo
   (GitHub, private) — this is the source of truth for *intended* state
2. Queries upstream registry APIs (ghcr.io, Docker Hub, etc.) for the latest
   available tag matching the service's tag pattern
3. Compares intended vs available versions
4. When a newer version is detected, opens a PR via the existing proposal engine
   bumping the image tag in the relevant `compose.yaml`
5. Fires a notification via the existing notification provider (Ntfy)

This gives the registry three-way visibility:

| Layer | Source | Already exists? |
|-------|--------|----------------|
| Intended state | homelab repo `compose.yaml` | No — new read path |
| Actual state | Docker discovery | ✅ Yes |
| Available state | Upstream registry API | No — new feature |

Drift between any two layers is a detectable, proposable condition.

---

## Options Considered

### Option A — Use running container image tag as upstream reference
- No new configuration required
- Registry already sees image tags via Docker discovery
- **Rejected**: running tag may have drifted from intended tag; using it as the
  reference conflates actual and intended state

### Option B — Read from homelab repo compose files ✅ Selected
- `compose.yaml` is the canonical source of truth for intended state
- Enables three-way drift detection (intended / actual / available)
- Requires registry to have read access to the homelab repo
- Most accurate, most powerful long-term

### Option C — Explicit per-service configuration
- Maximum flexibility, handles forks and variant tags cleanly
- High friction: manual setup required per service
- **Deferred**: worth revisiting as an override mechanism on top of Option B

### Option D — Hybrid (container default, overridable)
- Option A as default, Option C for exceptions
- Lower friction than B, less accurate
- **Superseded by B**: since homelab repo read access is appropriate and achievable,
  defaulting to the running container is unnecessary

---

## Design Notes

### Homelab repo read access
The proposal engine already has write access to a Git provider. Read access to the
homelab repo is a natural extension — same GitHub token, additional `contents: read`
scope on the homelab repo. No new credential type required.

### Tag interpretation is a DSPy problem
Upstream projects do not have consistent tagging conventions:
- Semver: `1.2.3` → straightforward
- Linuxserver: `1.32.8.1865-ls237` → requires pattern reasoning
- Date-based (Plex): `1.41.0.8994-f2c27da0b` → requires reasoning
- Latest-only: no version signal available

Rule-based tag comparison will fail on real-world tags. A DSPy module
(`ResolveLatestTag` or similar) should interpret tag lists and determine which
represents the newest release. This follows the existing pattern: ambiguous input
belongs in the reasoning layer, not the detection layer.

### Notification content
The notification fired on version detection should include:
- Service name
- Current (intended) version
- Available version
- Link to upstream release notes (if discoverable)
- Link to the opened PR

The "approve/decline/discuss" action button pattern in notifications is explicitly
deferred — it requires a webhook receiver or PR state polling loop that represents
new architectural territory. Document separately when ready to scope.

### Relationship to Watchtower (app)
Watchtower (app) solves the same detection problem but acts autonomously (restarts
containers without a human gate). Users who have adopted the registry and CD pipeline
do not need Watchtower — the registry proposal engine provides a strictly superior
workflow with an audit trail and rollback path. Watchtower is the solution *before*
adopting the registry; the proposal engine is the solution *after*.

This distinction should be surfaced in the post-OOBE recommendations layer (to be
scoped separately).

---

## Consequences

**What becomes possible:**
- Registry can answer "is X out of date?" for any service with a compose file in the
  homelab repo
- Version bumps follow the same PR review workflow as all other proposed changes
- Three-way drift detection: intended vs actual vs available
- Homelab repo becomes a first-class input to the registry alongside live
  infrastructure sources

**What becomes harder:**
- Registry now has a dependency on the homelab repo structure and conventions
- Tag interpretation quality depends on DSPy module quality; edge cases (non-standard
  tags, private images with no public upstream) need graceful handling

**What to revisit later:**
- Per-service overrides (Option C) for forks, variant tags, or pinned services that
  should never auto-propose
- "Approve/decline/discuss" action buttons in notifications
- Handling services where the upstream is a private registry with no public API
- Post-OOBE recommendations layer surfacing the Watchtower → registry graduation path

---

## Action Items

- [ ] Scope homelab repo read access: confirm GitHub token permissions needed
- [ ] Design `HomelabrepoDiscoverySource` interface and compose file parsing logic
- [ ] Design `UpstreamRegistrySource` for ghcr.io and Docker Hub API polling
- [ ] Design `ResolveLatestTag` DSPy module with confidence gate
- [ ] Define new proposal type: `IMAGE_UPDATE` (distinct from security and
      normalization proposals)
- [ ] Extend notification payload to include release notes link and PR link
- [ ] Write ADR for post-OOBE recommendations layer (separate scope)
- [ ] Write ADR for notification action buttons / PR approval via notification
      (separate scope, deferred)
