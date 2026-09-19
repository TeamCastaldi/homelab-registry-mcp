# Ansible

Deployment automation shipped by `homelab-registry-mcp` (Phase 4 — GitOps CD,
`docs/plans/updated-phases.md`). This directory holds the *action*, not the
*config*: it deploys compose stacks but contains no operator-specific
hostnames, IPs, or inventory. Each operator's own private homelab repo holds
its inventory, `ansible.cfg`, and `nodes/<node>/<service>/compose.yaml` files;
that repo's GitHub Actions workflow calls the reusable
`.github/workflows/deploy.yml` in *this* repo to pull in the role below.

- **`roles/docker-stack-deploy/`** — deploys one `nodes/<node>/<service>/compose.yaml`
  on one workload node (git pull + `docker compose pull && up -d`). See its
  README for the required variables.
- **`playbooks/deploy.yml`** — thin playbook wrapping the role, parameterized
  by `target_node` / `target_service`.
- **`roles/plumbing-check/`** — confirms Ansible plumbing works end-to-end
  against a node by exercising the five most common ad-hoc operations
  (ping, setup, command, copy, service) as ordinary tasks. See its README
  and `docs/SOPs/SOP-004-Verify-Ansible-Control-Plane-Plumbing.md`.
- **`playbooks/verify-plumbing.yml`** — thin playbook wrapping that role,
  parameterized by `target_node`.

See `.github/workflows/deploy.yml` for the reusable CD workflow and
`CLAUDE.md` for the snippet an operator pastes into their private repo.
