# SOP: Verify Ansible Control-Plane Plumbing

**Owner:** the maintainer  
**Frequency:** After any change to `ansible.cfg`, the inventory, `ANSIBLE_CFG_PATH`/`SSH_KEY_PATH`, or an SSH key rotation  
**Last Updated:** 2026  
**Status:** Current

---

### Purpose

Confirm that `ANSIBLE_CFG_PATH`/`SSH_KEY_PATH`-driven Ansible execution actually
works end-to-end against `homelab-control-plane` and `heimdall` (substitute your
real inventory hostnames if they differ) — not just that the env vars are set,
but that `ansible`, `ansible-playbook`, and `hardware-discover-now` can all
reach both nodes and do something useful there. Run this before relying on
`docker-stack-deploy`, `hardware-discover-now`, or the inventory-sync tool
against a node you haven't verified yet.

See [ADR-001](../ADRs/ADR-001-Homelab-Control-Plane.md) §4.3 and
[`docs/plans/ansible-planned-rollout.md`](../plans/ansible-planned-rollout.md)
for the design context this SOP verifies.

---

### When to use it

- First time bringing a new node's Ansible plumbing online.
- After rotating `SSH_KEY_PATH` or editing `ansible.cfg`/the inventory file.
- A GitOps deploy or `hardware-discover-now` failed and you need to isolate
  whether the problem is Ansible plumbing or something further downstream
  (the compose file, the target service, GitHub Actions).

---

### Prerequisites

- [ ] `ANSIBLE_CFG_PATH` is set to an absolute path to a real `ansible.cfg` on
      this node
- [ ] `SSH_KEY_PATH` is set to an absolute path to the control-plane's SSH
      private key
- [ ] `SECRETS_REPO_PATH` is also set, even though this SOP never touches
      secrets — Step 4 explains why it's required anyway
- [ ] The inventory file `ansible.cfg` points at contains entries for both
      `homelab-control-plane` and `heimdall`
- [ ] Plain `ssh` (no Ansible involved yet) from the control-plane node to
      itself and to `heimdall` already works with the key at `SSH_KEY_PATH` —
      confirm this before layering Ansible on top, so a failure below is
      Ansible/inventory-specific, not a raw network/key problem

---

### Procedure

#### Step 1: Confirm the Ansible CLI itself is sane

```bash
ansible --version
```

**Expected result:** Reports `ansible [core 2.21.x]` or newer, matching the
`ansible-core>=2.21.1` pin in `pyproject.toml`.  
**If it fails or reports an older/different version:** the `ansible` binary on
`$PATH` inside the running container is not the project's own venv-installed
one. `hardware-discover-now` (`hardware/ansible_facts.py`) shells out to
whatever `ansible` resolves to via subprocess — it does not invoke `uv run`
itself — so a `$PATH` mismatch here is a silent source of divergent behavior
between what you test manually and what the tool actually runs.

---

#### Step 2: Dump the effective Ansible config

```bash
ANSIBLE_CONFIG="$ANSIBLE_CFG_PATH" ansible-config dump --only-changed
```

**Expected result:** Shows your inventory path and (per
`hardware/ansible_facts.py`'s own docstring) `host_key_checking = False` —
this project deliberately does not pass `StrictHostKeyChecking` itself, so
your `ansible.cfg` is the only thing disabling the interactive host-key
prompt that would otherwise hang a non-interactive fact-gather.  
**If `host_key_checking` is missing:** add it to `ansible.cfg`, or expect the
first connection to every new host to hang waiting for a `yes/no` prompt that
never arrives non-interactively.

---

#### Step 3: Ping both nodes

```bash
ANSIBLE_CONFIG="$ANSIBLE_CFG_PATH" ansible homelab-control-plane,heimdall -m ping \
  --private-key "$SSH_KEY_PATH" -u "${SSH_DEFAULT_USER:-root}"
```

**Expected result:** `SUCCESS => {"changed": false, "ping": "pong"}` for both
hosts.  
**If `UNREACHABLE!`:** name resolution or network path problem — check the
inventory's `ansible_host`/IP for that entry.  
**If `Permission denied`:** wrong key, wrong user, or the public key isn't
in that node's `authorized_keys`.

---

#### Step 4: Confirm `hardware-discover-now` reaches both nodes

Through your MCP client, call `hardware-discover-now` with
`host="homelab-control-plane,heimdall"`.

**Expected result:** `{"status": "ok", "nodes_created": [...] or "nodes_updated": [...], "failures": {}}`
naming both nodes.  
**If it returns `"Server is in read-only mode"`:** run `system_health_check`.
This tool is gated by **all three** startup health checks —
`SECRETS_REPO_PATH` (git repo), `ANSIBLE_CFG_PATH`, and `SSH_KEY_PATH` — even
though it only directly uses the latter two. `health.py`'s own docstring
describes this as deliberate: a partially-bootstrapped node degrades to
read-only entirely rather than exposing some GitOps tools but not others. So
`hardware-discover-now` failing with a read-only error when `ANSIBLE_CFG_PATH`
and `SSH_KEY_PATH` both look correct usually means `SECRETS_REPO_PATH` is the
actual problem — check that first.

---

#### Step 5: Dry-run a deploy against `heimdall`

```bash
ANSIBLE_CONFIG="$ANSIBLE_CFG_PATH" ANSIBLE_ROLES_PATH="<registry-mcp-checkout>/ansible/roles" \
  ansible-playbook "<registry-mcp-checkout>/ansible/playbooks/deploy.yml" \
  -e target_node=heimdall \
  -e target_service=<an-existing-nodes/heimdall/*-directory> \
  -e docker_stack_deploy_repo_url=<your-homelab-repo-git-url> \
  -e docker_stack_deploy_repo_path=<homelab-repo-clone-path-on-heimdall> \
  --check --diff
```

**Expected result:** The `git` pull task reports what it would change (or
`ok` if already current) under `--check`.  
**Known limitation, not a bug:** the two `docker compose pull`/`up -d` tasks
in `docker-stack-deploy` use `ansible.builtin.command`, which Ansible skips
entirely under `--check` unless a task explicitly opts in with
`check_mode: false`. So this dry run validates the git-pull step for real,
but tells you nothing about whether the compose commands themselves would
succeed — a genuine dry run of those two steps requires a real (non-check)
run. Phase 6 of `docs/plans/ansible-planned-rollout.md` wires `--check --diff`
into CI with this same limitation; it is not something this SOP or that CI
step can close.

**A second, deeper limitation**: running `--check --diff` against a
`docker_stack_deploy_repo_path` that has never been cloned for real always
fails at the "compose file exists" gate, even for a perfectly valid
`target_node`/`target_service` — `--check` mode makes the `git` task report
what it *would* pull without ever writing anything to disk, so the file the
next task looks for genuinely isn't there yet. This isn't specific to
`heimdall`; it's true of any fresh path. To dry-run a deploy meaningfully,
either run once for real first (so the clone exists), or point
`docker_stack_deploy_repo_path` at a path that's already been deployed to
before. CI's idempotency step (Phase 6) seeds a fake, already-deployed local
fixture for exactly this reason, rather than trying to dry-run a from-scratch
deploy.

---

#### Step 6: Verify `ansible-lint` runs correctly

`ansible-lint` is **not** a declared project dependency — `pyproject.toml`
only pins `ansible-core`. Installing `ansible-lint` into the same virtual
environment as the project pulls in a conflicting transitive `ansible-core`
and breaks both. Run it isolated instead, exactly as the `ansible-lint` CI
job does:

```bash
ANSIBLE_ROLES_PATH=ansible/roles uvx --from ansible-lint ansible-lint ansible/roles ansible/playbooks
```

**Expected result:** `Passed: 0 failure(s), 0 warning(s) ...`.  
**If you see `Ansible CLI and python module versions do not match`:** you (or
a prior step) installed `ansible-lint` directly into the project's own venv —
run `uv sync --frozen` to restore it, then use the isolated `uvx` form above
instead of `uv run ansible-lint`.  
**If you see `the role 'docker-stack-deploy' was not found`:** `ANSIBLE_ROLES_PATH`
wasn't set, or you pointed the lint at `ansible/` as a whole instead of
`ansible/roles ansible/playbooks` separately.

---

### Verification

- [ ] `ansible --version` reports `2.21.x`+ from the project's own venv
- [ ] `ansible-config dump` shows the expected inventory path and `host_key_checking = False`
- [ ] `ansible homelab-control-plane,heimdall -m ping` returns `pong` for both
- [ ] `hardware-discover-now` returns `status: ok` for both hosts with an empty `failures` map
- [ ] `ansible-playbook deploy.yml --check --diff` runs the git-pull task cleanly against `heimdall`
- [ ] The isolated `ansible-lint` invocation passes with zero failures

---

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ansible --version` shows an unexpected version | `$PATH` resolves to a different `ansible` than the project's venv | See Step 1 |
| First connection to a host hangs indefinitely | `host_key_checking` not disabled in `ansible.cfg` | See Step 2 |
| `UNREACHABLE!` from `ansible -m ping` | Wrong IP/hostname in inventory, or network path blocked | Confirm with plain `ssh` first (Prerequisites) |
| `hardware-discover-now` returns a read-only error | One of the three health checks is failing, often `SECRETS_REPO_PATH` even when Ansible-specific vars look fine | Run `system_health_check`; see Step 4 |
| `ansible-lint` reports `versions do not match` | `ansible-lint` was installed into the project's own venv | `uv sync --frozen`, then use the isolated `uvx` invocation in Step 6 |
| `ansible-lint` fails with `role ... was not found` | `ANSIBLE_ROLES_PATH` unset, or linting `ansible/` instead of `ansible/roles ansible/playbooks` | See Step 6 |
| `--check --diff` shows no diff for the compose tasks even when you know a change is pending | Expected — `command`-module tasks are skipped under `--check` | See Step 5's known limitation |

---

### Rollback

Nothing to roll back — every step here is read-only or a `--check` dry run.
Following this SOP as written makes no changes to either node.

---

### Notes

- This SOP verifies the plumbing itself, not any specific deploy. Run it
  before troubleshooting a failed `docker-stack-deploy` invocation or
  `hardware-discover-now` pass, to rule out the plumbing before looking
  further downstream.
- All environment variables referenced here are documented in
  [CLAUDE.md](../../CLAUDE.md)'s Environment Variables table.
