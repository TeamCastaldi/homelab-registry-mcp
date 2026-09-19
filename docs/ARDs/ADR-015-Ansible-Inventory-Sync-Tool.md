# ADR-015: Ansible Inventory Sync Tool

| | |
|---|---|
| **Status** | Accepted |
| **Amends** | [ADR-012](ADR-012-Scope-The-Repo-To-The-MCP-Server.md) (Scope The Repo To The MCP Server) |
| **Related** | [ADR-001](ADR-001-Homelab-Control-Plane.md) §5 (the withdrawn OOBE's `oobe_add_node`/`oobe_distribute_ssh_keys`), `deletion/store.py` (the gate this models) |
| **Date** | 2026-09-19 |

## Context

ADR-012 drew this repository's boundary sharply: it "does not provision hosts,
create repositories, or manage an operator's infrastructure lifecycle." That
decision withdrew the full `oobe_*` tool surface, including node registration,
SSH key distribution, and connectivity validation.

`docs/plans/ansible-planned-rollout.md` (Phase 3) asked for a narrower
capability than any of that: syncing a `HardwareNode` row the registry
*already has* — added via `hardware-add-node` or discovered via
`hardware-discover-now` — into the real Ansible inventory file `ansible.cfg`
names, so a node the registry already knows about doesn't also need a
hand-maintained inventory entry kept in sync by hand.

This is deliberately narrower than what ADR-012 withdrew:

- It does not register a new node from scratch — `HardwareNode` already
  carries `hostname`, `ip_address`, `ansible_host`, and `ansible_groups`
  (`src/registry_mcp/models/hardware.py`), populated independently through
  the existing curated (`hardware-add-node`) or discovered
  (`hardware-discover-now`) paths.
- It does not distribute SSH keys.
- It does not validate connectivity (`ansible all -m ping`).
- It does not create or scaffold any repository.

## Decision

Add a math-gated tool pair — `ansible-inventory-sync-node` and
`ansible-inventory-sync-node-confirm` — scoped to a `HardwareNode` that
already exists in the registry.

- The tool takes a `node_id`/`hostname` referencing an existing
  `HardwareNode`, never raw connection parameters. It cannot register a node
  that isn't already known to the registry through an existing path.
- It writes to `ANSIBLE_INVENTORY_PATH`, a new, explicit, absolute-path
  setting — the same convention `ANSIBLE_CFG_PATH`/`SSH_KEY_PATH`/
  `SECRETS_REPO_PATH` already use — rather than parsing `ansible.cfg`'s
  `inventory =` line to infer the target, which would be fragile against any
  `ansible.cfg` that sets it via a relative path, an environment variable, or
  a dynamic inventory script.
- Gated by the same math-confirm pattern `registry_delete_service`/
  `hardware-delete-node` use (`x + y = ?`, a short TTL, no retries on a wrong
  answer) — modeled on `DeletionGateStore`, but implemented as a sibling
  `InventoryGateStore` rather than sharing its table. See Alternatives
  considered for why.
- The writer upserts exactly one host's block in a YAML inventory file,
  leaving every other entry untouched — never a full-file regenerate.
- No new off-by-default env var gates this pair specifically — a deliberate
  departure from this project's usual convention for a new capability. The
  math-confirm gate is treated as sufficient friction here, the same way it
  already is for the delete tools rather than being paired with a flag.
- YAML-format inventories only, for this iteration. ini-format inventory
  support is an open question (§Open items), not built here.

Explicitly **not** in scope, and still withdrawn per ADR-012: registering a
node from scratch, distributing SSH keys, validating connectivity, creating
or scaffolding any repository, and any Infisical secrets integration
(separately deferred — see `docs/plans/ansible-planned-rollout.md`'s Open
questions).

## Consequences

### Positive

- Closes a real gap without reopening the provisioning boundary ADR-012 drew:
  a node the registry already curates no longer needs a second, manually
  kept-in-sync copy of its identity in a separate inventory file.
- Reuses the project's existing math-confirm shape rather than inventing a
  new kind of human-in-the-loop control.
- The narrow input contract (only an existing `node_id`/`hostname`) makes the
  boundary with ADR-012's withdrawn scope structurally enforced, not just
  documented — there is no code path from this tool to registering a node
  that doesn't already exist in `HardwareStore`.

### Negative / accepted tradeoffs

- This is still a real amendment to ADR-012's stated boundary, not a
  reinterpretation of it. A future reader of ADR-012 alone would not expect
  this repository to write to any file outside its own SQLite database;
  this record exists specifically so that expectation is corrected in one
  place rather than left to be discovered by surprise.
- No off-by-default env var means this capability is live as soon as it
  ships, gated only by the math-confirm challenge — weaker friction than
  every other write-path feature in this project, which pairs a challenge
  or a review step with its own flag. Accepted per the rollout plan's
  explicit decision, on the reasoning that the existing math-gate is already
  the same friction control the delete tools rely on alone.
- ini-format inventories are not supported. An operator using one gets a
  clear rejection, not a best-effort or partially-correct write.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Parse `ansible.cfg`'s `inventory =` setting to find the target file automatically | Fragile — that setting can be a relative path, an environment-variable expansion, a directory of multiple files, or a dynamic inventory script; an explicit `ANSIBLE_INVENTORY_PATH` is unambiguous |
| Reuse `DeletionGateStore`/`PendingDeletion` directly, adding an `inventory_write` `DeletionEntityType` | Conflates delete semantics with a write action under a class and table literally named for deletion; a sibling `InventoryGateStore` keeps the concepts separate, matching this project's existing pattern of building a sibling rather than force-fitting an unrelated concern (e.g. normalization staying out of `proposal/`) |
| Regenerate the entire inventory file from every `HardwareNode` row on each sync | Clobbers anything in the file the registry doesn't model — manually added hosts, group-only entries, `group_vars`-style structure — turning a narrow sync into an implicit full-file takeover |
| Gate this tool behind its own off-by-default env var, matching project convention | Considered and explicitly declined in the rollout plan's interview (Q16) — the math-confirm gate is treated as sufficient friction on its own |

## Open items

| # | Question | Status |
|---|---|---|
| 1 | Should the inventory-sync tool support ini-format inventories, or stay YAML-only indefinitely? | Open — YAML-only for this rollout |
| 2 | Should this tool eventually absorb any part of the withdrawn `oobe_add_node` flow, or stay permanently scoped to syncing an already-registered node? | Open — no plan to widen it today |

## References

- [ADR-001](ADR-001-Homelab-Control-Plane.md) §5 — the withdrawn OOBE tool surface this stays narrower than
- [ADR-012](ADR-012-Scope-The-Repo-To-The-MCP-Server.md) — the boundary this amends
- `deletion/store.py` (`DeletionGateStore`) — the gate this models
- `models/hardware.py` (`HardwareNode`) — the existing curated/discovered data this tool syncs from
- [`docs/plans/ansible-planned-rollout.md`](../plans/ansible-planned-rollout.md) — the plan whose interview captured this decision

---

*ADR-015 | github.com/TeamCastaldi/homelab-registry-mcp | MIT License | 2026*
