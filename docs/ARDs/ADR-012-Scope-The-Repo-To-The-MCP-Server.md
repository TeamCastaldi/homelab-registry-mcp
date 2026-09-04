# ADR-012: Scope This Repo to the MCP Server

| | |
|---|---|
| **Status** | Accepted |
| **Supersedes** | ADR-003 (Out-of-Box Experience Design) in full |
| **Amends** | ADR-001 §5 (Out-of-Box Experience) and the provisioning duties it assigns; `docs/plans/updated-phases.md` Phase 1 |
| **Date** | 2026-09-04 |

## Context

ADR-001 §5 described an out-of-box experience that took an operator "from a fresh
Debian install to a running, configured lab registry," assuming "nothing else — Docker,
Ansible, SSH keys, and the homelab repo are all created by the OOBE." ADR-003 designed
that experience in detail. Neither was ever built as specified: the OOBE CLI stayed
unimplemented, and what shipped instead was a set of shell scripts standing in for it —
`install.sh`, `bootstrap.sh`, fifteen per-phase scripts, `setup-homelab-repo.sh`,
`setup-ansible-inventory.sh`, and `reset-node.sh`, plus `vagrant/` fixtures and a
dedicated GitHub Actions workflow to test them.

That surface was substantial and, unlike the server, could not be verified by the test
suite. It carried its own two-tier validation strategy, its own documentation
(`scripts/README.md`, and roughly half of `docs/SETUP.md`), and a class of failure the
MCP server never encounters — netplan versus ifupdown detection, static-IP handover,
LXC container detection, `sg docker` group-membership timing, SSH key distribution.

The cost surfaced concretely. `docker-compose.yml` gained an external `traefik` network
(commit `857e4b4`) once ADR-006/ADR-007 co-located Traefik and moved its deployment into
the operator's private repo. Nothing in `scripts/` created that network, so
`install.sh` died at step 7 on exactly the greenfield path it existed to serve. The
break went unnoticed because the validating workflow only triggered on `scripts/**`
changes, and no PR touched that path for weeks. A provisioning surface that isn't
exercised by the normal test suite decays silently between the releases that touch it.

The operator this repo was written for has since migrated these scripts into their own
homelab config repo, where they sit next to the inventory, `ansible.cfg`, and compose
files they operate on.

## Decision

**This repository ships an MCP server. It does not provision hosts, create repositories,
or manage an operator's infrastructure lifecycle.**

1. **Remove `scripts/` entirely** — the installer, the OS bootstrap, the per-phase
   scripts, the shared prompt/`.env` helpers, the homelab-repo and Ansible-inventory
   bootstrappers, and the node reset script.

2. **Remove `vagrant/`** — `slow-loop/` existed only to exercise the installer against
   real systemd and real network-interface ownership; `workload-node/` went with it.

3. **Remove `.github/workflows/install-validation.yml`** — its entire subject was
   `install.sh`.

4. **Keep the deploy action.** `ansible/roles/docker-stack-deploy`,
   `ansible/playbooks/deploy.yml`, and the reusable `.github/workflows/deploy.yml` stay.
   These are not provisioning: they are the mechanism that turns a merged proposal PR
   into a running change, which is the whole point of the write path. An operator's
   private repo calls the reusable workflow from a thin caller and supplies only config.
   The boundary is *deploying a change the server proposed* (ours) versus *preparing a
   machine to run anything at all* (theirs).

5. **`docs/SETUP.md` becomes a container-deploy guide** — prerequisites, compose, `.env`,
   connecting a client, pointing at your homelab repo, hardware discovery, Dockhand. What
   the server needs from the operator's repo is stated as a contract (which paths, which
   env vars), not as a procedure for building one.

## Consequences

### Positive

- The repo's test surface and its shipped surface are the same thing again. Everything
  here is covered by `pytest`, `ruff`, and `ansible-lint`; nothing depends on a class of
  bug only a real Debian VM can reproduce.
- ~4,200 lines removed, 29 files, one CI workflow, and one whole category of
  documentation — before counting the ~6,300 lines ADR-011's withdrawals took.
- The greenfield-install break is resolved honestly. The failing check retired because
  its subject no longer exists here, and the missing `traefik` network is now a stated
  prerequisite in `README.md` and a troubleshooting entry in `docs/SETUP.md`, rather than
  a silent assumption inside a script.

### Negative

- **There is no one-line install any more.** `curl -fsSL .../install.sh | bash` is gone.
  A new operator provisions their own host and creates their own config repo before the
  container is useful. For a project that wants adopters, this is a real cost — it is
  accepted because a broken one-liner is worse than an honest two-step, and because the
  one-liner had in fact been broken.
- **ADR-001 §5's OOBE vision has no home.** It is not merely deferred here; it is out of
  scope. If it is ever built, it belongs in a separate tool that drives this server's MCP
  tools, not in this repository.
- Operators who used `reset-node.sh` between test rounds lose it. It was a
  development convenience, not a product feature, but it was genuinely useful.

### Neutral

- No functional change to the server. `src/` and `tests/` never imported or shelled out
  to anything in `scripts/`; the only reference was a docstring in
  `hardware/ansible_facts.py` citing `setup-ansible-inventory.sh` for a
  `host_key_checking` rationale, now pointing at the operator's own `ANSIBLE_CFG_PATH`.
- `hardware-discover-now` is unaffected. It still shells out to `ansible -m setup`
  against `ANSIBLE_CFG_PATH`; only the script that used to *generate* that inventory is
  gone. Consuming the operator's inventory is in scope; creating it is not.

## Alternatives considered

**Keep the scripts and fix the `traefik` network bug.** A one-line
`docker network create` in `07-start-server.sh` would have made CI green. Rejected
because it treats the symptom: the bug existed for weeks undetected precisely because
this surface is invisible to the normal test suite, and that property does not change by
fixing one instance of it.

**Keep the scripts but stop testing them.** Deleting only `install-validation.yml` would
have removed the failing check at no apparent cost. Rejected as the worst option —
shipping an installer with no validation at all, in a repo whose CI otherwise gates
everything.

**Move the scripts to a sibling public repo.** A `homelab-control-plane-installer`
alongside this one would preserve the one-liner for adopters. Rejected for now as
maintaining two repos for a surface with one known user; nothing here prevents that
later, and the deleted code remains in this repo's history.

## Open items

1. **The public adoption story is thinner.** `README.md` and `docs/SETUP.md` now assume
   an existing Docker host. If adoption becomes a goal, the sibling-repo option above is
   the path back, and it should have its own record.
2. **ADR-001 §5 and ADR-003 remain readable but non-binding.** Both are kept per
   `docs/ARDs/README.md`; ADR-003 carries a superseded-by header, and a reader of
   ADR-001 §5 should treat its OOBE duties as withdrawn by this record.
3. **`docs/plans/updated-phases.md` Phase 1 is now historical.** It describes building
   the installation pipeline this record removes. The plan file is a historical
   artifact and is not rewritten.
