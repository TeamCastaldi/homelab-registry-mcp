# ADR-014: Location-Agnostic Service State-Reset Action

| | |
|---|---|
| **Status** | DRAFT |
| **Companion to** | ADR-002 (Client Interfaces), ADR-008 (MCP Tool Organization), ADR-010 (Dockhand Update Webhook) |
| **Org** | github.com/TeamCastaldi |
| **License** | MIT |
| **Date** | 2026 |

---

## 1. Purpose

This record sketches the design for a new class of capability — an instant-execute,
human-confirmed infrastructure action — needed to support an iOS Shortcut that polls
the registry for a live list of running services, builds a selection menu from the
result, and issues a state-reset (restart) command to whichever one the operator
picks, from anywhere, not just the home LAN. Nothing that exists today does this:
every current write surface either opens a PR a human merges (`proposal_*`) or only
ever mutates this server's own SQLite registry (`registry_*`/`hardware-*` writes).
None of them execute a live command against a running container. This is a **sketch
for future work, not an accepted decision** — see §7.

---

## 2. Context

- iOS Shortcuts speak plain HTTP (`Get Contents of URL`, JSON in/out) — no Shortcut
  action initializes an MCP session, so `/mcp` itself cannot be the integration point
  no matter how it's authenticated.
- `/mcp` is LAN-only today and its auth strategy is deferred (CLAUDE.md). The Shortcut
  needs to work off the home network too, so it cannot depend on that endpoint.
- Every write path that exists is either asynchronous-and-reviewed (`proposal_*` opens
  a PR; a human merges it before anything real changes) or scoped to this server's own
  database (`registry_*`/`hardware-*` mutations, the math-gated deletes). None reaches
  out and executes a command against a live service.
- **Upstream APIs are read-only by explicit project rule** — Traefik, Authentik,
  Docker, and Dockhand are never modified. Dockhand's own mutating endpoints
  (`POST /api/containers/check-updates`, `POST /api/stacks`) are explicitly barred
  from ever being exposed as a tool, restart included (CLAUDE.md, ADR-013). A reset
  action cannot be built by relaxing either rule.
- The Docker socket this container mounts is **read-only** in `docker-compose.yml` —
  also a deliberate choice, not an oversight.
- Precedent already exists for executing live commands against fleet hosts *without*
  touching the local Docker socket or any upstream API: `hardware-discover-now`
  (Phase 9b) runs `ansible <host> -m setup` over SSH against `ANSIBLE_CFG_PATH`'s
  inventory, gated by the same startup health check (Git repo / `ansible.cfg` / SSH
  key) that puts every other GitOps write tool into read-only mode when unhealthy.
  `adoption/ssh.py` similarly SSHes into a service's linked `HardwareNode` to inspect
  (never mutate) a live container.
- The delete gate (`deletion/store.py`'s `DeletionGateStore`) is the project's existing
  pattern for "irreversible, must be confirmed by a human": request → single-digit
  math challenge → confirm within a TTL; a wrong answer or expired/already-resolved
  challenge invalidates it outright, no retries.

---

## 3. Decision (sketch)

### 3.1 Execute over SSH/Ansible against the linked `HardwareNode` — never the local Docker socket, never Dockhand

Ruled out:

- **Docker socket write.** Would mean re-mounting the socket read-write and reversing
  "upstream APIs are read-only." That turns this container into a single point that
  can mutate *every* container on its host, proportionate to nothing about "restart
  one service."
- **A Dockhand mutating call.** CLAUDE.md and ADR-013 already settled this: no
  Dockhand endpoint that triggers an update or mutates a stack is ever exposed as a
  tool. A restart action is exactly that kind of call.

Chosen: SSH into the specific `HardwareNode` the target service is already linked to
(`hardware-link-service`) and run one scoped command against just that host — an
ad-hoc `docker restart <container>` or an Ansible task using
`community.docker.docker_container: restarted`. This reuses the exact control-plane
primitives `hardware-discover-now` already established (`ANSIBLE_CFG_PATH`,
`SSH_KEY_PATH`, the shared health check) instead of inventing a second execution
mechanism. A service with no linked node cannot be reset — the first call returns
that reason rather than guessing a host.

### 3.2 A new, stricter gate: math-confirm, off by default, no LLM in the loop

Modeled on `DeletionGateStore`, not on the proposal engine — a restart is instant and
unreviewed, so its gate has to be the project's *strongest*, not its weakest:

- New env var `SERVICE_RESET_ENABLED` (default `false`) — same off-by-default posture
  as `ADOPTION_ENABLED`/`NORMALIZATION_ENABLED`.
- `service_reset_request(service_id)` returns the same `x + y = ?` single-digit
  challenge shape `registry_delete_service` does, backed by a new pending-action row
  (its own table, or an `action_type` column added to the existing `PendingDeletion`
  table — an implementation detail left open, see §6) and its own
  `SERVICE_RESET_CHALLENGE_TTL_MINUTES`.
- `service_reset_confirm(challenge_id, answer)` executes the SSH/Ansible restart only
  on a correct answer inside the TTL, then records a new `ActionEvent` (who/what/when/
  outcome: `ok` / `unreachable` / `ansible_error`) — a new event type rather than
  overloading `ChangeEvent`, since this logs a live command's outcome, not a database
  field change.
- Wrong answer, expired challenge, or an already-resolved one invalidate it outright —
  same no-retry rule the delete gate uses.
- No DSPy anywhere in this path. Deterministic in, deterministic out — the same "no
  LLM calls in the execution layer" rule `reconcile.py` already follows.
- The gate logic and the SSH executor live in one small module (e.g.
  `actions/reset.py`) called from *two* entry points — an MCP tool pair for
  agent-driven use, and the HTTP routes in §3.3 for the Shortcut — the same
  one-engine-many-callers shape `ProposalEngine`/`_open_proposal` already has between
  `tools/proposal.py` and `webhooks/dockhand.py`.

### 3.3 A dedicated HTTP surface, not `/mcp`

Two or three new `FastMCP.custom_route`s, following the exact fail-closed pattern
`webhooks/dockhand.py` (ADR-010) already established, rather than a third auth model:

- `GET /actions/services` — services eligible for reset (i.e. carrying a linked
  `HardwareNode`), shaped for a Shortcut's "Choose from List":
  `[{"id", "display_name", "host", "node"}, ...]`. Read-only, but still behind the
  bearer check below — an unauthenticated live list of what's running is itself
  information disclosure.
- `POST /actions/services/{id}/reset` and `POST /actions/services/{id}/reset/confirm`
  — the two-call gate from §3.2, shaped for two sequential Shortcut steps: show the
  returned challenge to the human holding the phone as a confirmation prompt, then
  submit what they typed.
- Same auth model as `/webhooks/dockhand`: a shared secret via
  `Authorization: Bearer <secret>`, compared with `hmac.compare_digest`. If
  `SERVICE_RESET_ENABLED=true` with no `SERVICE_RESET_SECRET` set, the routes stay
  **unmounted**, not mounted-and-rejecting — same fail-closed-at-registration rule.
- `/mcp` itself is untouched by any of this — the Shortcut never speaks MCP, and a
  problem on this new surface can't become an MCP session takeover.

### 3.4 "Location-agnostic" reachability is the operator's Traefik decision, not this repo's

Per CLAUDE.md, this repo "ships the MCP server, not a node provisioner." Exposing
`/actions/*` to the internet while `/mcp` stays LAN-only is a Traefik routing choice
on the operator's own node — a second router matching a `PathPrefix`, its own
hostname or middleware, its own TLS. This record's contract ends at "the routes
exist, are fail-closed, and are authenticated"; the companion checklist (§7) walks
the reachability decision without making it.

---

## 4. Consequences

### 4.1 Positive

- Reuses every control-plane primitive the project already has — SSH key, Ansible
  inventory, health check, math-gate shape, fail-closed `custom_route` pattern, one
  engine shared by multiple callers. No new execution mechanism, no new auth
  mechanism, no new audit-log shape.
- Leaves "upstream APIs are read-only" and "no Dockhand mutation" completely intact —
  this reaches a live container without touching either.
- `/mcp` is unaffected. A leaked secret or a bug on `/actions/*` cannot reach the MCP
  session surface.

### 4.2 Negative / accepted tradeoffs

- A restart is immediate and stateful — unlike a PR there is no review window before
  it happens. The math challenge is the only brake, and CLAUDE.md is explicit that
  challenge is "not a security boundary," just friction against a mistake. That makes
  the bearer secret the *real* security boundary here — more load-bearing than it is
  for the Dockhand webhook, which only ever creates a PR, never executes anything.
- Making `/actions/*` internet-reachable (to satisfy "location-agnostic") is a
  materially larger attack surface than the LAN-only `/mcp`, even authenticated: a
  leaked or brute-forced secret becomes an internet-reachable "restart any linked
  service" primitive. Worth pairing with Traefik IP allow-listing (weak, since a
  phone's carrier/Wi-Fi IP isn't stable) or a short-lived per-request token minted by
  some separate mechanism, rather than relying on one static long-lived secret alone
  — not decided here, see Open Question 4.
- Only services with a linked `HardwareNode` are reachable at all — a brownfield
  service discovered but never linked needs one `hardware-link-service` call first.

---

## 5. Alternatives considered

| Alternative | Why not |
|---|---|
| Read-write Docker socket, restart via local `docker` CLI in-container | Reverses an explicit "read-only upstream" rule; blast radius is every container on the host, not just linked services |
| Expose a Dockhand restart/update endpoint as a tool | CLAUDE.md/ADR-013 already rule this out categorically |
| Shortcut talks to `/mcp` directly | Shortcuts don't speak MCP's session protocol; `/mcp` is also LAN-only today |
| Route the reset through the proposal engine (open a PR that redeploys the stack) | Defeats the point — a state reset needs to happen now, not after a human merges a PR and CI redeploys |

---

## 6. Open Questions

| # | Question | Status |
|---|---|---|
| 1 | New `PendingAction` table vs. widening `PendingDeletion` with an `action_type` column? | Open — decide at implementation time |
| 2 | Does a restart need a per-service cooldown, to stop a repeated Shortcut tap from crash-looping something? | Open |
| 3 | Should `GET /actions/services` reflect Dockhand's live container state instead of the registry's last-discovery-pass `Service` rows, so "running" is actually live? | Open |
| 4 | IP allow-listing vs. short-lived per-request tokens for the internet-facing case (§4.2) — possibly its own follow-up ADR | Open |
| 5 | Does the MCP tool pair (`service_reset_request`/`_confirm`) get registered by default once this ships, or does it stay behind `SERVICE_RESET_ENABLED` the same as the HTTP routes? | Open — leaning toward the same flag gating both |

---

## 7. Status

**DRAFT — design sketch only.** Nothing in `src/` changes as part of this record. See
[SOP-003](../SOPs/SOP-003-Enable-Service-Reset-Action.md) for the checklist from the
project's current deployed state (no reset capability of any kind exists) to a
confirmed, working write capability, covering both the implementation work this ADR
describes and the operational steps to turn it on and verify it end-to-end.

---

## 8. References

- ADR-002: Client Interfaces
- ADR-008: MCP Tool Organization (Tier system this proposes extending with a Tier 4)
- ADR-010: Dockhand Update Webhook (the `custom_route`, fail-closed-registration,
  bearer-secret pattern this reuses)
- ADR-013: Dockhand Read-Only API Integration (the rule this respects)
- CLAUDE.md — "Upstream APIs are read-only", "Every hard delete is math-gated",
  "No LLM calls in the detection layer"
- `deletion/store.py` (`DeletionGateStore`) — the gate this models
- `hardware/ansible_facts.py`, `adoption/ssh.py` — the SSH/Ansible execution
  precedent this reuses
- `webhooks/dockhand.py` — the `custom_route` + fail-closed-auth precedent this reuses

---

*ADR-014 | github.com/TeamCastaldi | MIT License | 2026*
