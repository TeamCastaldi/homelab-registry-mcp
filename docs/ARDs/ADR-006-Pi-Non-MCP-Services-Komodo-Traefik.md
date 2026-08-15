# ADR-006: Pi Non-MCP Services — Komodo + Traefik (Supersedes ADR-005)

| | |
|---|---|
| **Status** | Superseded by [ADR-007](ADR-007-Komodo-Traefik-Move-To-GitOps.md) (2026-08-15) — kept for historical context. Komodo and Traefik are no longer bundled in this repo's `docker-compose.yml`; they're deployed as ordinary GitOps-managed `nodes/<node>/<service>/compose.yaml` entries instead. Sections 1-2 below (which tool, which node) still describe the operator's standing choice — only *how it's deployed* changed. |
| **Supersedes** | ADR-005-Monitoring-Alerting-Recovery-Ingress-Architecture.md (in full) |
| **Amends** | ADR-001-Homelab-Control-Plane.md §4.1 reference topology (Traefik row), §6.2 (ingress topology) |
| **Date** | 2026-08-10 |

## Context

ADR-005 stood up a modular, single-purpose-tool stack on the Pi: Beszel
(telemetry), Gatus (synthetic uptime), Dozzle (log viewing), WUD (update
detection), Homepage + Glance (dashboards), a docker-socket-proxy, Autorestic
(backups), and a Healthchecks.io heartbeat — nine containers plus
`traefik-kop` for cross-node routing.

Separately, this homelab already runs Komodo (`komo.do`) lab-wide: ADR-003
§"Komodo migration" notes "a couple dozen production services across other
nodes are still managed by Komodo," with an explicit incremental-migration
plan away from it as the Ansible/GitHub-Actions GitOps pattern ADR-001
introduced proves itself per node. That migration was about *how declared
changes get deployed*, not about container visibility — Komodo's UI still
covers logs, container/host telemetry, and update detection for those nodes
in one place, the same ground ADR-005's five-tool split covered for the Pi
alone.

Operator decision: standardize the Pi's own non-MCP services on the same
tool already used everywhere else in the lab, rather than maintaining a
second, Pi-only stack of single-purpose alternatives.

## Decision

The Pi's only non-MCP services going forward are **Komodo** and **Traefik**.

### 1. Komodo (Core + Periphery + Mongo) replaces the ADR-005 observability set

Komodo manages and monitors the Pi's own local containers — logs, host/
container telemetry, and update detection in one UI, replacing Beszel,
Dozzle, WUD, Homepage, and Glance.

> **Not a reversal of ADR-001 §4.1.** ADR-001 replaced Komodo *as the deploy
> engine* — the Ansible + GitHub Actions GitOps pipeline (PR → merge →
> `docker-stack-deploy` role) remains the only path by which
> `homelab-registry-mcp`'s proposal engine lands a change, on the Pi or any
> other node. Komodo here is purely operational visibility and manual
> container ops, the same role it already plays on the other nodes ADR-003
> references — it does not gain authority over MCP-proposed changes.

### 2. Traefik moves onto the Pi and becomes the lab's central ingress

Previously Traefik lived on a workload node (ADR-001's reference topology
table: `workload-01` hosts "Traefik, Authentik, applications"; ADR-005 §4
called this node "Node B" — central Traefik + Redis, with `traefik-kop`
running on the Pi and other nodes to publish routing rules to it). This
decision moves Traefik itself onto the Pi, which now plays the "Node B"
role.

This reverses the specific ADR-001 §6.2 rationale for keeping Traefik off
the control plane ("routing all lab traffic through the Pi would create a
bottleneck and defeat the purpose of a dedicated control plane") — the
operator has accepted that trade-off in exchange for one fewer node to
maintain ingress on.

Cross-node routing keeps the `traefik-kop` + Redis mechanism from ADR-005
§4 unchanged in shape, just re-centered: Redis now runs alongside Traefik
on the Pi; workload nodes' `traefik-kop` instances point at the Pi's Redis
instead of a remote Node B's. `docker-socket-proxy` is dropped — Traefik
reads the local Docker socket directly (read-only), the same pattern
already used in `vagrant/workload-node`'s fixture, and Komodo Periphery
needs read-write socket access regardless (it is Komodo's deploy agent),
so a read-only proxy in front of it would defeat the point.

### 3. Dropped entirely, no replacement planned

Beszel, Gatus, Dozzle, WUD, Homepage, Glance, `docker-socket-proxy`,
Autorestic, and the Healthchecks.io heartbeat are removed from
`docker-compose.yml`, `scripts/install.sh`, and `monitoring/`.

The WUD-webhook-driven `image_update` proposal flow (`/webhooks/wud` route,
`FindingType.image_update`, `ProposalEngine.create_for_image_update`, and
their tests) is removed as dead code along with WUD itself, rather than
left disabled. Re-adding an update-triggered proposal path — via Komodo's
own update detection, or ADR-004's still-unimplemented polling
`UpstreamRegistrySource` — is a decision for a future ADR, not addressed
here.

## Consequences

### Positive

- One container-management tool for the whole lab instead of two (Komodo
  everywhere else, five single-purpose tools on the Pi alone).
- Net container count on the Pi drops (from nine ADR-005 services plus
  `traefik-kop` to five: Mongo, Komodo Core, Komodo Periphery, Traefik,
  Redis).
- No second UI to learn for container visibility on the control plane.

### Negative

- Reverses ADR-001 §6.2's explicit rationale for keeping the control plane
  off the ingress path — a real bottleneck/blast-radius trade-off the
  operator is accepting knowingly, not a resolved objection.
- No disaster-recovery (Autorestic) or out-of-band dead-man's-switch
  (Healthchecks.io) coverage for the Pi until a future decision revisits
  them — Komodo's own backup feature only covers its own database, not the
  registry's SQLite or git-crypt keys.
- No update-triggered proposal path (WUD's role) until a future ADR wires
  one up against Komodo or ADR-004's polling source.

## Open Items

1. TLS/ACME certificate resolver configuration for the now-Pi-hosted
   Traefik instance — not decided here; operator-supplied static/dynamic
   config, same "bring your own certs" posture as the rest of this project.
2. Whether Komodo's own webhook/update-detection output gets wired into
   `homelab-registry-mcp`'s proposal engine as an `image_update`-equivalent
   finding.
3. Autorestic/Healthchecks.io (or replacements) for the Pi, if disaster
   recovery / dead-man's-switch coverage is wanted again later.
