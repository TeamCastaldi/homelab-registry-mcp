# ADR-013: Dockhand Read-Only API Integration

| | |
|---|---|
| **Status** | Accepted |
| **Related** | ADR-010 (Dockhand Update Webhook) — covers inbound alerts, not outbound queries |
| **Related** | ADR-011 (Remove Komodo Integration) — the architecturally closest precedent |
| **Date** | 2026-09-09 |

## Context

The operator migrated the homelab's container-management tool from Komodo to
Dockhand (self-hosted, v1.0.46). ADR-010 already covers Dockhand's *outbound
push* — update and CVE alerts turned into staged proposals — but nothing lets
this server *query* Dockhand on demand: no `dockhand_*` tools, no discovery
source. Meanwhile the removed Komodo integration (ADR-011) is the direct
architectural precedent for what to build, and what to avoid.

Dockhand's own docs confirm it exposes a full REST API: Bearer tokens
(`dh_...`, scoped/revocable, role-based), an OpenAPI spec at `/api/docs`
(opt-in via Dockhand's own `FEAT_API_DOCS`), and a resource model of
environments (Docker host connections) → stacks (compose projects) →
containers, plus image-update checks and a vulnerability/CVE dashboard.

## Decision

Add a read-only `DockhandClient` + seven `dockhand_*` tools (environments,
stacks, containers, pending updates, vulnerabilities) + `DockhandDiscoverySource`,
gated on `DOCKHAND_API_URL`/`DOCKHAND_TOKEN`, mirroring the Traefik/Authentik
integration shape exactly. Explicitly do **not** expose Dockhand's two known
write endpoints (`POST /api/containers/check-updates`, `POST /api/stacks`) as
tools — this preserves CLAUDE.md's "Upstream APIs are read-only" rule rather
than creating an exception for it.

Exact endpoint paths beyond the one changelog-confirmed read path
(`GET /api/containers/check-updates`) are REST-conventional guesses, stated
plainly as such in `client.py`'s docstring and pending verification against
the live instance's `/api/docs`.

`DockhandDiscoverySource` feeds discovered containers into the registry as
`SourceType.dockhand` provenance, reconciling by `name` against whatever
`Service` row Docker/Traefik discovery may already have created for the same
container — this is what makes the integration load-bearing.

## Alternatives considered

**A Komodo-style dormant write seam** (an unused `execute()`/`trigger_update()`
method, kept "for later"). Rejected — Komodo shipped exactly this and it
stayed permanently unused; keeping dead write-capable surface around is the
kind of tech debt ADR-011 removed. If a write path is ever wanted, it
deserves its own ADR and its own explicit decision, not a pre-built escape
hatch nobody asked for.

**Tools only, no discovery-source wiring** (a straight port of Komodo's
shape). Rejected — this is precisely why ADR-011 withdrew Komodo: "a
read-only window onto a system the operator can already open directly... it
was never a discovery source... never wrote a row to the registry." Wiring
`DockhandDiscoverySource` into `build_sources()` is what avoids repeating
that outcome.

## Consequences

### Positive

- Full read visibility into Dockhand (environments, stacks, containers,
  pending updates, CVEs) from an MCP client, following the exact tool/
  resource/prompt shape already established by Traefik and Authentik.
- Discovered containers reconcile against existing Docker/Traefik-discovered
  `Service` rows by name, so no duplicate rows.
- Symmetric with ADR-010: together, ADR-010 (push) and ADR-013 (pull) cover
  both directions of Dockhand integration.
- No data migration: `SourceType` is a `StrEnum` stored as a string, so
  adding `dockhand` is additive, the same as ADR-010's `FindingType` additions.

### Negative / accepted tradeoffs

- A future write path (e.g. "trigger an update check via MCP") needs its own
  ADR, and per this server's established pattern should go through the
  PR-gated proposal engine rather than a direct Dockhand API call —
  consistent with every other write path here (Git-only, human-merged).
- Endpoint paths not already confirmed by Dockhand's changelog are unverified
  guesses. A mismatch fails safely (a 404 → `DockhandError` → the tool
  returns `{"error": ...}`), but should be corrected against the live
  `/api/docs` rather than left silently wrong.
- Reconciliation assumes Dockhand's container `name` field is the raw Docker
  container name, matching what Docker/Traefik discovery already use. If a
  live instance's `name` differs (a Dockhand-internal display name, say),
  reconciliation would silently create a duplicate row instead of merging —
  unverified against a live instance.

## Open items

1. Exact `/api/docs` schema unverified — confirm and correct endpoint paths,
   query parameters, and list-response envelope shape (`client.py`'s `_list`
   already unwraps `results`/`data`/`items`/bare-array defensively, but the
   real shape should be pinned down rather than guessed indefinitely).
2. Whether Dockhand's container `name` matches the raw Docker container name
   Docker/Traefik discovery already key on — required for reconciliation to
   merge rather than duplicate.
