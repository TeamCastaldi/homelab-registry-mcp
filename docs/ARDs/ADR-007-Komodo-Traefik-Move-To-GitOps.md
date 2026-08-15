# ADR-007: Komodo + Traefik Move to GitOps-Managed Nodes (Supersedes ADR-006 §Deploy Mechanism)

| | |
|---|---|
| **Status** | Accepted |
| **Supersedes** | ADR-006-Pi-Non-MCP-Services-Komodo-Traefik.md's deploy mechanism (bundling Komodo + Traefik into this repo's `docker-compose.yml` and `scripts/install.sh`) |
| **Date** | 2026-08-15 |

## Context

ADR-006 standardized the Pi's non-MCP services on Komodo (container
management/logs/update detection) and Traefik (central ingress), and bundled
both directly into `homelab-registry-mcp`'s own `docker-compose.yml` as
opt-in Compose profiles (`komodo`, `traefik`), with `scripts/install.sh`
prompting for them and writing their secrets to `.env`.

That coupling means this repo's compose file — and its installer — carry
config and secrets for two services that have nothing to do with the MCP
server itself. It also means Komodo and Traefik can only change via a new
`homelab-registry-mcp` release, not through the GitOps pipeline
(`ansible/roles/docker-stack-deploy` + `.github/workflows/deploy.yml`,
ADR-001 Phase 4) this project already ships specifically so operators can
declare arbitrary node services in their own private homelab repo.

Operator decision: the MCP server's `docker-compose.yml` should describe the
MCP server, full stop. Komodo and Traefik move to the deploy path every
other node service already uses.

## Decision

`docker-compose.yml` in this repo now defines only the `homelab-registry-mcp`
service. Komodo (Core + Periphery + Mongo) and Traefik + its Redis backing
store are removed from it, along with:

- `scripts/phases/install/03-configure.sh`'s Komodo/Traefik yes/no gates and
  the `COMPOSE_PROFILES`/`CONTROL_PLANE_HOST`/`KOMODO_*`/`TRAEFIK_REDIS_PASSWORD`
  prompts and state plumbing they fed into `06-write-env.sh`.
- The corresponding `.env.example` block.
- `.github/workflows/install-validation.yml`'s Komodo/Traefik container-health
  assertions (it now only checks `homelab-registry-mcp`).

Operators who want Komodo and/or Traefik on this node instead declare them as
an ordinary `nodes/<node>/<service>/compose.yaml` entry in their private
homelab repo, deployed by the same Ansible + GitHub Actions pipeline every
other node service uses — no special-casing in `homelab-registry-mcp` itself.

ADR-006 §1-2 (Komodo replaces the ADR-005 observability set; Traefik moves
onto this node as the lab's central ingress) are unchanged as standing
decisions — only the deploy mechanism moves.

## Consequences

### Positive

- `docker-compose.yml` and `scripts/install.sh` only carry MCP-server config
  and secrets — no Komodo/Traefik credentials pass through this repo's
  installer or `.env` at all.
- Komodo/Traefik version bumps, config changes, and redeploys go through the
  same PR → merge → Ansible pipeline as every other node service, instead of
  requiring a fresh `homelab-registry-mcp` install/upgrade.
- One fewer thing the fast-loop `install-validation.yml` CI job has to bring
  up and assert healthy.

### Negative

- An operator adopting this repo fresh no longer gets Komodo/Traefik offered
  during `install.sh` — they set them up as a separate `nodes/` entry
  afterward (see `docs/SOPs/SOP-001-Deploy-New-Service.md`), one more manual
  step than the previous all-in-one prompt flow.
- Existing installs with the `komodo`/`traefik` Compose profiles enabled need
  to migrate those services into a `nodes/<node>/` compose file by hand (or
  keep running a pinned older `docker-compose.yml` for them) — this ADR does
  not include an automated migration path.

## Open Items

1. A migration guide/script for operators who already have the ADR-006
   Compose profiles running is not addressed here.
