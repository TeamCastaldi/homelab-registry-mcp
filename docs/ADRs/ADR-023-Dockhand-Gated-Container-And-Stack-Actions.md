# ADR-023: Dockhand Gated Container and Stack Actions

| | |
|---|---|
| **Status** | Accepted and implemented ([#195](https://github.com/TeamCastaldi/homelab-registry-mcp/issues/195)); container actions validated against the live Dockhand on 2026-10-19, stack actions not yet ([#201](https://github.com/TeamCastaldi/homelab-registry-mcp/issues/201)) |
| **Amends** | [ADR-013](ADR-013-Dockhand-Read-Only-API-Integration.md) (Dockhand Read-Only API Integration): lifts its "no mutating endpoint is ever a tool" rule for the actions listed here and no others |
| **Setup** | [SOP-009](../SOPs/SOP-009-Enable-Dockhand-Actions.md) |
| **Related** | [ADR-014](ADR-014-Service-State-Reset-Action-Tool.md) (draft sketch of a human-confirmed live action, same premise); [ADR-015](ADR-015-Ansible-Inventory-Sync-Tool.md) (the math-gate pattern this copies); [ADR-020](ADR-020-Patchmon-Webhook-Email-Approval.md) (the other amendment to the read-only-upstreams rule) |
| **Date** | 2026-10-17 |

## Context

The Dockhand integration is read-only by decision (ADR-013). It can list
containers, stacks, and pending updates, but an MCP client cannot start, stop,
restart, or recreate anything. The operator asked for exactly that: to act on a
service from the conversation where the problem was diagnosed.

Three existing rules point the other way, and this record has to answer each:

- **CLAUDE.md, "Upstream APIs are read-only"**, and ADR-013 name Dockhand's
  mutating endpoints as barred, ADR-014 repeats it ("restart included").
- **ADR-013's own Negative consequences** say a write path "needs its own ADR"
  and, per this server's pattern, "should go through the PR-gated proposal
  engine rather than a direct Dockhand API call."
- **The conversational-deploy plan** resolved Phase 0 as "the CD pipeline,
  never Dockhand automation": new services reach a node by a merged PR, not
  by a Dockhand call.

The operator's decision is that restarting or redeploying something that
already exists is operational control, not a deploy: a PR is the wrong
instrument for "this container is wedged, restart it". The remaining
question is how to allow it without an agent able to act on a host on its own.

### What Dockhand's API offers

Taken from Dockhand's published API reference (manual, "API reference"). The
live instance's own `/api/docs` (`FEAT_API_DOCS=true`) is the authority and
should be checked before the client is relied on (Open items, 1).

| Action | Endpoint | Notes |
|---|---|---|
| Container start / stop / restart | `POST /api/containers/[id]/start`, `/stop`, `/restart` | the manual's own example restarts with no body and no `env` |
| Stack start / stop / restart | `POST /api/stacks/[name]/start`, `/stop`, `/restart?env=<id>` | addressed by **name**, not id |
| Stack redeploy | `POST /api/stacks/[name]/deploy?env=<id>`, body `{"pull": bool, "build": bool, "forceRecreate": bool}` | |
| Auth | `Authorization: Bearer dh_...` on every endpoint, writes included | a bearer token cannot create tokens; failed auth is rate-limited per IP |
| Sync vs job | stack endpoints return `{"jobId": ...}` unless `Accept: application/json` is sent, which makes them run to completion and return the result | long-running |

There is no single-container "recreate" endpoint. `POST
/api/containers/[id]/update` is documented only as "Update container
configuration", with no body shape, so it is not used.

## Decision

Add **gated, off-by-default Dockhand actions**: three tools, a small
persisted challenge record, and a client write path, all behind a new
`DOCKHAND_ALLOW_WRITE` setting.

### The actions (an allowlist, not a pass-through)

| Kind | Actions | Dockhand call |
|---|---|---|
| container | `start`, `stop`, `restart` | `POST /api/containers/{id}/{action}` |
| stack | `start`, `stop`, `restart` | `POST /api/stacks/{name}/{action}` |
| stack | `redeploy` ("recreate") | `POST /api/stacks/{name}/deploy` with `forceRecreate: true`, `build: false`, and `pull` as asked (default `false`) |

"Recreate" means the stack redeploy with `forceRecreate`. It recreates every
container in the stack, and the challenge text says so. A single container is
not recreated on its own, because the API has no documented way to.

The action name is a closed enum validated server-side. There is no tool that
takes an arbitrary path, method, or body.

### Never exposed, whatever the setting

`DELETE /api/containers/[id]`, `DELETE /api/stacks/[name]`, `POST
/api/stacks/[name]/down`, `POST /api/containers/[id]/exec/run`, `POST
/api/batch`, `POST /api/containers/batch-update`, `POST
/api/containers/[id]/update`, `pause`/`unpause`/`rename`, container or stack
creation, `POST /api/containers/check-updates`, and everything under images,
volumes, networks, registries, and backups. The client has no method for any
of them, not even an unused one (ADR-013's rejection of a dormant write seam
stands).

### The gate

Request, then confirm, in the shape of ADR-015 and the delete tools:

1. `dockhand_container_action_request(environment_id, container_id, action)`
   and `dockhand_stack_action_request(environment_id, stack_name, action,
   pull=False)` validate their inputs, store a pending row, and return an
   `x + y = ?` single-digit challenge **plus the exact action in words**
   ("restart container `abc123` in environment `2`").
2. `dockhand_action_confirm(challenge_id, answer)` re-reads the stored row and
   executes **the stored values**, never anything the caller supplies at
   confirm time. A wrong answer, an expired challenge (default 5 minutes,
   `DOCKHAND_ACTION_CHALLENGE_TTL_MINUTES`), or an already-resolved one
   invalidates it with no retry.
3. The challenge is consumed by a conditional update (`status='pending' AND
   expires_at > now`), so two concurrent confirms cannot both run it.

Like the delete gate, this is not a security boundary (the sum is shown in the
challenge itself). It is deliberate friction against an agent, or a wrong id,
acting on a live host without the operator seeing what is about to happen.

The challenge is a **sibling store** (`PendingDockhandAction` +
`DockhandActionGateStore`), not a new entity type in `DeletionGateStore`, for
the reason ADR-015 gave: a delete table should not carry a restart.

### Safety properties

- **Off by default and not registered.** With `DOCKHAND_ALLOW_WRITE=false`, or
  Dockhand unconfigured, none of the three tools exist. The setting is the
  second lock; the math gate is the first.
- **Read-only mode applies.** When the startup health check puts the GitOps
  write tools into read-only mode, these refuse too, as the other live-action
  tools do.
- **Ids are validated before they reach a path.** A container id or stack name
  must match a conservative allowlist (no `/`, `..`, `?`, `#`, whitespace, or
  percent-encoding), and `environment_id` must be an integer string.
- **No retry on a write.** The read path retries on 5xx; a POST that reached
  Dockhand and failed is reported, never repeated, so one confirm can restart
  something at most once.
- **Redirects are not followed** and the token is only sent to the configured
  base URL.
- **Bounded time.** Stack calls send `Accept: application/json` and use
  `DOCKHAND_ACTION_TIMEOUT_SECONDS` (default 120), not the 10s read timeout.
- **An audit trail.** The pending row is kept after it resolves, with its
  outcome (`executed`, `failed`, `expired`, `invalid_answer`) and a short
  result summary, and execution logs `dockhand_action_executed`. The token is
  redacted by the existing log redaction.
- **Failures are `{"error": ...}`**, per the repo's tool convention, never a
  raised exception for an expected failure.

### Token scope

`DOCKHAND_TOKEN` currently needs only read access. Enabling writes means the
same token (or a separate one, if the operator prefers: see Open items, 3) is
used for actions, so the operator should create it under a Dockhand user whose
role allows container and stack control and nothing wider. Dockhand's manual
does not say whether tokens carry their own scope or inherit their owner's
role. The SOP written with this change says to verify that on the first call.

## Consequences

### Positive

- An agent can act on what it just diagnosed, with the operator seeing the
  exact action before it runs.
- Reuses the project's existing human-in-the-loop shape rather than a new kind
  of control.
- The closed action set and the client's lack of any other write method make
  the boundary structural, not just documented.

### Negative / accepted tradeoffs

- This is a real amendment to a stated project rule. A reader of CLAUDE.md or
  ADR-013 alone would not expect this server to execute anything on a host
  through Dockhand. The convention text is updated in the same change set so
  the contradiction is not left standing.
- It bypasses the PR + human merge gate that every other write path here uses.
  The substitute is a per-action challenge, which is weaker than a reviewed
  diff. Accepted because a restart changes no configuration: nothing is left
  behind to review.
- A stack redeploy with `forceRecreate` can take a service down for as long as
  its containers take to come back, and `pull: true` can change what image it
  runs. The challenge names both.
- The math gate stops a casual or mistaken call, not a determined caller.
  Anyone who can reach `/mcp` and enable the flag can read the sum.
- The Dockhand API paths and the container endpoints' response bodies are taken
  from published documentation and have not been exercised against the
  operator's instance. The read client's endpoints were also assumed and are
  partly wrong (#193), which is why the live check comes before reliance.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Route every action through the PR-gated proposal engine (ADR-013's suggestion) | A restart has no file change to put in a PR; the engine would have to invent one. Wrong tool for "restart this wedged container" |
| No gate beyond `DOCKHAND_ALLOW_WRITE` | Leaves one tool call between an agent and a stopped production service. The flag says the capability exists; it does not show a human what is about to run |
| Reuse `DeletionGateStore` with new entity types | Conflates delete semantics with action semantics in a class and table named for deletion (ADR-015 rejected the same) |
| One generic `dockhand_post(path, body)` tool | Turns the token into an arbitrary-write capability; `DELETE`-adjacent endpoints would be one argument away |
| Call `POST /api/containers/[id]/update` for recreate | Body undocumented; unknown side effects (it may change configuration) |
| Container-level recreate via `batch-update` | It is an image update path, not a recreate, and `/api/batch` is excluded as a bulk-delete surface |
| Run the actions over SSH/Ansible instead of Dockhand | Possible (ADR-014's sketch), but a second control plane to maintain when Dockhand already holds the environment connections and permissions |

## Open items

| # | Question | Status |
|---|---|---|
| 1 | Do the endpoints, `env` handling, and response bodies match the live instance's `/api/docs`? | Open. Check before first use; the client's tests pin the documented shape, not the live one |
| 2 | Should the tools refuse to act on Dockhand's own container or this registry's own container? A restart of either drops the connection mid-confirm | Open. Not guarded yet; the SOP warns |
| 3 | Separate write token (`DOCKHAND_WRITE_TOKEN`) so the read token can stay read-only? | Open. Single token for now |
| 4 | Should executed actions also be written as `ChangeEvent`s once a container maps to a registry `Service`? | Open. The gate record is the audit trail for now |

## References

- [ADR-013](ADR-013-Dockhand-Read-Only-API-Integration.md): the read-only integration this amends
- [ADR-014](ADR-014-Service-State-Reset-Action-Tool.md): the earlier, unbuilt sketch of a confirmed live action
- [ADR-015](ADR-015-Ansible-Inventory-Sync-Tool.md): the math-gate pattern and sibling-store reasoning
- `deletion/store.py` (`DeletionGateStore`): the gate this models
- Dockhand manual, "API reference" (containers, stacks, long-running operations)

---

*ADR-023 | github.com/TeamCastaldi/homelab-registry-mcp | MIT License | 2026*
