# docker-stack-deploy

Deploys a single compose stack (`nodes/<node>/<service>/compose.yaml`) on one
workload node: pulls the operator's homelab repo clone up to date on the
target, then runs `docker compose pull && up -d` in that service's directory.

This role ships with `homelab-registry-mcp` (the automation), not with an
operator's private homelab repo (the config). It is invoked by the reusable
`.github/workflows/deploy.yml` in this repo — see that file and
`docs/plans/phase-4-cd.md` for how an operator wires it into their own
private repo.

## Required variables

None of these have defaults in this role — they identify a specific
operator's environment and must come from the caller (`-e` extra-vars) or
the operator's own inventory/group_vars:

| Variable | Meaning |
|---|---|
| `target_node` | Name of the `nodes/<node>/` directory being deployed |
| `target_service` | Name of the `<service>/` directory being deployed |
| `docker_stack_deploy_repo_url` | Git remote of the operator's private homelab repo |
| `docker_stack_deploy_repo_path` | Where that repo is cloned on the workload node |

## Optional variables (see `defaults/main.yml`)

| Variable | Default | Meaning |
|---|---|---|
| `docker_stack_deploy_repo_branch` | `main` | Branch to pull |
| `docker_stack_deploy_become` | `false` | Whether the git/docker tasks need `become` (set `true` if the workload node's Docker socket requires root) |

## Example

```bash
ansible-playbook -i inventory.yml ansible/playbooks/deploy.yml \
  -e target_node=workload-01 \
  -e target_service=paperless \
  -e docker_stack_deploy_repo_url=git@github.com:youruser/homelab.git \
  -e docker_stack_deploy_repo_path=/opt/homelab
```
