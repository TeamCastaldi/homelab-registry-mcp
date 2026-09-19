# Ansible planned rollout: plumbing verification, inventory sync, and a basics playbook

| | |
|---|---|
| Status | Draft |
| Date | 2026-09-19 |
| Companion | [ADR-001](../ARDs/ADR-001-Homelab-Control-Plane.md), [ADR-012](../ARDs/ADR-012-Scope-The-Repo-To-The-MCP-Server.md), ADR-015 (drafted in Phase 3 of this plan) |

## Phase 1: Correct documentation drift and record open growth areas

**Goal**: Fix the dangling references and mislabeling the capability scan surfaced, so the paper trail is accurate before new work is built on top of it.

**Decisions captured**:
- Scan finding: CLAUDE.md's "Deferred" line labels "multi-node Ansible bootstrap" as "(Phase E)", but [ADR-001](../ARDs/ADR-001-Homelab-Control-Plane.md) §11's actual Phase E ("Ansible Deploy Role") is complete — the withdrawn capability maps to the withdrawn OOBE, closest to lettered Phase G.
- Scan finding: ADR-001 §12 cites `docs/plans/project-plan-registry-mcp.md` and `docs/plans/plan-ansibleSetup.md`, neither of which exists in `docs/plans/`.
- Scan finding: `ansible/roles/docker-stack-deploy/README.md` cites `docs/plans/phase-4-cd.md`, which doesn't exist — the content lives in `docs/plans/updated-phases.md`'s "Phase 4: Automated Deployment Pipeline (GitOps CD)" section.
- Q9 answer: "Stay exactly what it accepts today. But note somewhere what features could be added later." (`hardware-discover-now`'s fact-gathering scope).

**Tasks**:
- [x] Fix CLAUDE.md's Deferred line to attribute multi-node Ansible bootstrap to the withdrawn OOBE Phase G instead of the completed Phase E
- [x] Replace the dangling `docs/plans/project-plan-registry-mcp.md` and `docs/plans/plan-ansibleSetup.md` references in ADR-001 §12 with a note that they were never written
- [x] Fix the dangling `docs/plans/phase-4-cd.md` reference in the docker-stack-deploy role README to point at `docs/plans/updated-phases.md`
- [x] Add a forward-looking note to `hardware-discover-now`'s docstring listing possible future fact-gathering scope

**Verification**:

```bash
grep -rn "plan-ansibleSetup\|project-plan-registry-mcp\|phase-4-cd" docs ansible || echo "no dangling references remain"
```

Read CLAUDE.md's Deferred line and confirm it now cites Phase G, not Phase E.

## Phase 2: Verify the Ansible plumbing against control-plane and heimdall

**Goal**: Produce a concrete, runnable procedure confirming `ANSIBLE_CFG_PATH`/`SSH_KEY_PATH`-driven execution actually works end-to-end against control-plane and heimdall, so real gaps are visible before new capability is built on top of the plumbing.

**Decisions captured**:
- Q1 answer: "Neither. Currently the goal is to get ansible fully configured and get the pbasics see to run playbooks."
- Q1 follow-up answer: "This effort needs to confirm the plumbing works. Highlight what doesn't and make a plan to address. Once it is working, develop the basics (a playbook and all it's possible pieces)."
- Q2 answer: "control-plane and heimdall."
- Q10 answer: "Out of scope for now" (declared-vs-actual inventory drift detection).

**Tasks**:
- [x] Add `docs/SOPs/SOP-004-Verify-Ansible-Control-Plane-Plumbing.md` covering `ansible --version`, `ansible-config dump`, `ansible <host> -m ping`, and a `--check --diff` dry run of `deploy.yml` against control-plane and heimdall
- [x] Document the `ANSIBLE_CFG_PATH` / `SSH_KEY_PATH` / `SECRETS_REPO_PATH` three-way health-check coupling in the new SOP, so an operator understands why `hardware-discover-now` needs all three health checks even though it only directly uses two of them

**Verification**:

Hand `SOP-004` to the operator and run its steps against control-plane and heimdall; confirm every step's expected result matches. This step is inherently manual — it requires the operator's real hardware, which this session cannot reach.

## Phase 3: Add a math-gated Ansible inventory-sync tool

**Goal**: Give the MCP server a tool that syncs an already-registered `HardwareNode` into the operator's real Ansible inventory file, gated by the same math-confirm pattern the delete tools use, and record the ADR-012 exception this requires.

**Decisions captured**:
- Q3 clarifying answer: "Include it" (in response to "Is 'the mcp gains the inventory-writing tooling' an option?").
- Q4 answer: "Math gate".
- Q13 answer: "Homelab secrets are housed in infisical. Integration from the mcp to infisical has not happened yet." — followed by "Yes, out of scope for now" confirming Infisical integration is not part of this rollout.
- Q16 answer: "No" (no new off-by-default env var for this capability, a deliberate departure from the project's standing convention).
- Confirmed design detail: `HardwareNode` already carries `hostname`, `ip_address`, `ansible_host`, and `ansible_groups` (`src/registry_mcp/models/hardware.py`), so the tool syncs an existing node row rather than inventing a parallel registration flow.

**Tasks**:
- [x] Draft `docs/ARDs/ADR-015-Ansible-Inventory-Sync-Tool.md` amending ADR-012 to permit a math-gated Ansible inventory-sync tool scoped to already-registered hardware nodes
- [x] Add `ANSIBLE_INVENTORY_PATH` setting for the inventory-sync tool's write target
- [x] Add `PendingInventoryWrite` model and `InventoryGateStore` math-confirm gate for inventory writes
- [x] Add an inventory YAML writer that upserts one host entry without disturbing the rest of the file
- [x] Add `ansible-inventory-sync-node` and `ansible-inventory-sync-node-confirm` MCP tools
- [x] Register the new inventory tools in `server.py`
- [x] Add unit tests for the inventory writer and the math-confirm gate

**Verification**:

```bash
uv run pytest tests/test_inventory_gate.py tests/test_inventory_writer.py -v
uv run ruff check .
```

Call `ansible-inventory-sync-node` against a registered test node, solve the returned challenge with `ansible-inventory-sync-node-confirm`, and confirm the target inventory file gains exactly one new/updated host block with the rest of the file unchanged.

## Phase 4: Add the plumbing-check Ansible role

**Goal**: Build a new role/playbook exercising the five most common Ansible ad-hoc operations, giving the project a reusable way to confirm a node's plumbing works beyond the single-purpose `docker-stack-deploy` role.

**Decisions captured**:
- Q5 answer: "Use the 5 most common commands for now." — interpreted as `ping` (connectivity), `setup` (facts), `command`/`shell` (e.g. `uptime`), `copy`/`template` (file delivery), and `service` (status check).
- Q6 answer: "I don't know" — resolved as: `.github/workflows/deploy.yml` stays unchanged; the new role is invoked manually via its own playbook, not wired into CD.
- Q8 answer: "No, keep become: false default".
- Q15 answer: "Agreed, go with that" — rollback relies on (a) Ansible idempotency and re-run, (b) `backup: true` on file-writing tasks, (c) `service` tasks scoped to status-check only, never restart/stop, (d) `--check --diff` as the dry-run gate before any real-node run. No snapshot/restore mechanism.
- Q16 answer: "No" (no new off-by-default env var for this capability either).

**Tasks**:
- [x] Add `ansible/roles/plumbing-check` role scaffold with README and `defaults/main.yml`
- [x] Add `ping` and `setup` tasks to the plumbing-check role
- [x] Add a `command`/`shell` diagnostic task (`uptime`) to the plumbing-check role
- [x] Add a `copy`/`template` file-delivery task with `backup: true` to the plumbing-check role
- [x] Add a `service` status-check task to the plumbing-check role, read-only — never restart or stop
- [x] Add `ansible/playbooks/verify-plumbing.yml` wrapping the plumbing-check role

**Verification**:

```bash
uv run ansible-lint ansible/
ansible-playbook ansible/playbooks/verify-plumbing.yml --syntax-check
```

## Phase 5: Add Molecule coverage for both roles

**Goal**: Close the zero-test-coverage gap the capability scan found — neither `docker-stack-deploy` nor the new plumbing-check role has anything beyond `ansible-lint` exercising it today.

**Decisions captured**:
- Q11 answer: "Required — molecule for the new role".
- Q11 follow-up answer: "Yes, include docker-stack-deploy too".

**Tasks**:
- [x] Add Molecule scaffold (`molecule/default`) for the plumbing-check role using the Docker driver
- [x] Add Molecule scaffold (`molecule/default`) for the docker-stack-deploy role using the Docker driver
- [x] Add molecule and its Docker driver to the project's dev dependencies

**Verification**:

```bash
(cd ansible/roles/plumbing-check && ANSIBLE_ROLES_PATH=$(pwd)/../../../ansible/roles uv run --project ../../.. molecule test)
(cd ansible/roles/docker-stack-deploy && ANSIBLE_ROLES_PATH=$(pwd)/../../../ansible/roles uv run --project ../../.. molecule test)
```

Corrected from the original draft's `molecule test -- <role-path>`, which is not
valid Molecule CLI syntax — `molecule test` has no positional role-path
argument; it discovers `molecule/<scenario>/molecule.yml` relative to the
current directory, so the scenario's own role directory must be the CWD.

**Note on what was actually verified**: `molecule` alone (`ansible-core` +
`molecule` + `molecule-plugins[docker]`) is not sufficient — the docker
driver's own destroy/create playbooks use `community.docker.docker_container`,
which plain `ansible-core` does not bundle. Adding the full `ansible` PyPI
package as a dev dependency fixed this (bundles `community.docker` and
`ansible.posix` without touching Ansible Galaxy), confirmed without disturbing
the project's pinned `ansible-core>=2.21.1`. With that fix, `uv run molecule
test` for both roles progresses cleanly through dependency → cleanup → destroy
→ **syntax: Executed: Successful** → create, failing only at the container
image pull — which this sandbox's network blocks (Docker Hub returns
`Forbidden`), not a defect in the scenario or dependency wiring. The
create → converge → verify → destroy cycle itself could not be exercised
end-to-end in this session; run it for real (CI or a networked dev machine)
before trusting the converge/verify logic, not just the config and syntax.

## Phase 6: Wire the new checks into CI

**Goal**: Make the idempotency check and Molecule coverage from Phases 4-5 run automatically, without changing the existing lint ruleset.

**Decisions captured**:
- Q7 answer: "Yes, add --check --diff in CI".
- Q12 answer: "Keep the existing config as-is" (`.ansible-lint`'s single `role-name` skip).

**Tasks**:
- [x] Add a `--check --diff` verification step to CI for the plumbing-check and docker-stack-deploy roles
- [x] Add Molecule test execution to CI for both roles
- [ ] Confirm `.ansible-lint`'s existing `role-name` skip still covers the new role without further changes

**Verification**:

CI is green on this branch, including the new `--check --diff` and Molecule steps; `uv run ansible-lint ansible/` passes locally with no ruleset changes.

## Open questions

| # | Question | Status |
|---|---|---|
| 1 | Should Infisical replace or supplement git-crypt for secrets this project manages? | Open — deferred, needs its own ADR and provider protocol |
| 2 | Should the inventory-sync tool support ini-format inventories, or stay YAML-only? | Open — YAML-only for this rollout |
| 3 | Should `hardware-discover-now`'s fact scope grow to include installed package versions or live container inventory? | Open — deferred, noted in Phase 1's docstring update for a future phase |
| 4 | What is the `SSH_KEY_PATH` rotation procedure? | Open — separate concern, not designed in this plan |

## Execution discipline

> Every task checkbox in this plan is implemented and committed
> individually — one [Conventional Commit](https://www.conventionalcommits.org/)
> per checked box, in the order listed, before the next box is started. A
> box is only checked once its commit exists on the branch. Commit types
> (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`) are scoped to the
> affected area, e.g. `feat(ansible): add molecule scaffold for
> docker-stack-deploy`. A phase is not done until every one of its task
> commits exists and its own Verification step passes.
