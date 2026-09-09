# Homelab reference — Team Castaldi

Diagnostic patterns for the homelab. All defaults, paths, and conventions
below reflect the environment as last confirmed, but topology changes — nodes
get renamed, services move hosts, integrations get added.

> [!IMPORTANT]
> **This file is a map, not gospel.** Before giving out a file path, host IP,
> or infra command based on anything here, confirm current placement against
> live Registry MCP tools when they are available — `registry_list_services`,
> `hardware-list-nodes`, `service_get_full_context`, `traefik_get_overview` /
> `traefik_list_services`, `system_health_check`. The registry is the source
> of truth; this file is orientation.

## Contents

- [Infrastructure reference](#infrastructure-reference)
- [Documentation MCP](#documentation-mcp)
- [Traefik and routing](#traefik-and-routing)
- [Docker diagnostic commands](#docker-diagnostic-commands)
- [Common failure patterns](#common-failure-patterns)
- [Ansible](#ansible)
- [Safety rules](#safety-rules)

## Infrastructure reference

### Nodes (last confirmed 2026-08-17)

| Host | IP | Role | Primary workload |
|---|---|---|---|
| homelab-control-plane | 10.0.0.200 | Pi 5, Ansible control node | Traefik, Redis, Komodo-core, docker-socket-proxy, Authentik |
| heimdall | 10.0.0.151 | Primary Docker host | ~90% of services |
| waldorf | 10.0.0.251 | Media Docker host | Plex, Tunarr, media stack |
| panoptichron | 10.0.0.201 | **Hands-off** — self-developed apps only | Production `jobsquatch`. Not Ansible-onboarded (deliberate) |
| TerraMaster NAS (tnas) | 10.0.0.250 | NFS storage | appdata + media volumes |
| Synology NAS (dsm) | 10.0.0.249 | Secondary NAS, Traefik static-backend route | Not linked to the hardware registry (deliberate) |
| Omada gateway | 10.0.0.2 | Gateway / DNS primary | TP-Link Omada |

> [!NOTE]
> `homelab-control-plane` was previously called "watchtower" in older notes —
> same physical Pi 5, renamed. Any reference to "watchtower" means this node.

### Stack layout

Most stacks are one service per folder:

```text
~/homelab/nodes/<node_name>/<service_name>/compose.yaml
```

The real filesystem path is `~/homelab` (`/home/chester/homelab`), not
`/homelab`.

> [!NOTE]
> `homelab-registry-mcp` no longer runs on control-plane — it moved to
> `nodes/ollama/homelab-registry-mcp/compose.yaml`, and that migration is
> itself still in progress (see `docs/homelab-roadmap.md`). Its old bind
> mount of the repo into its own container at `/opt/homelab` was
> container-internal on control-plane, not a host path — confirm the same
> is still true wherever it ends up.

**Exception:** `nodes/control-plane/core/compose.yaml` is a multi-service
bundle — `docker-socket-proxy`, `traefik-redis`, `traefik`, `komodo-db`,
`komodo-core`, and Infisical all live in that one file. Target individual
services with `docker compose ... logs <service-name>`. There is no
`nodes/control-plane/traefik/` folder.

Each stack has its own `.env` alongside `compose.yaml`. No shared root `.env`.
`nodes/control-plane/core/mcp.env` predates registry-mcp's move off
control-plane — confirm what actually reads it before assuming it's still
registry-mcp's config.

### NFS mounts (all Docker hosts)

```text
10.0.0.250:/Volume1/appdata  →  /mnt/appdata   (service persistent data)
10.0.0.250:/Volume2/media    →  /mnt/media     (media library)
```

Current fstab entry: `defaults 0 0`. NFS tuning is in progress — flag
performance issues for dedicated work rather than patching inline.

## Documentation MCP

`documentation-mcp` is a homelab service that gives grounded, official,
version-specific documentation for the services running here — it checks a
Qdrant cache first, falls back to a SearXNG-driven fetch of the real docs
site when the cache misses, and returns the result. It runs on
`homelab-control-plane` alongside registry-mcp.

**Invoke it through Registry MCP, not directly** — `get_service_documentation
(service_name, version, topic?)` relays documentation-mcp's tool over the
existing Registry MCP connection, so no separate connector or bearer token is
needed. Returns `{"content": ...}` on success or `{"error": ...}` when
documentation-mcp can't find an official source, isn't configured, or is
unreachable.

`version` is required and is never inferred — pull the actual pinned version
from the relevant `compose.yaml` (`${_VERSION}` env var) or
`registry_get_service` before calling it. Guessing a version defeats the
point: the answer would ground the theory in the wrong version's behavior
just as confidently as no grounding at all.

**When to reach for it:**

- Before finalizing a Step 2 theory that depends on version-specific config
  syntax, an API shape, or documented behavior — check what changed between
  versions rather than trusting memory
- Before an image upgrade (see Safety rules below) — the upstream changelog
  for the target version, not just "what usually works"
- When a router label, health check field, or config key stopped working
  after a version bump and the cause isn't obvious from the diff alone

It only knows what it's been asked about before, or can find an official
source for — a niche or newly-added homelab service may come back `ERROR:
Official documentation not found`. That's not a tool failure, it's signal: no
official source was found for that name/version, or the service name given
doesn't match the vendor's actual domain.

## Traefik and routing

### Version

Not independently confirmed recently. Check the actual image tag in
`nodes/control-plane/core/compose.yaml` (`${TRAEFIK_VERSION}`) rather than
trusting a hardcoded number.

### TLS / certresolver

```yaml
- "traefik.http.routers.<service>.tls.certresolver=cloudflare"
- "traefik.http.routers.<service>.tls.domains[0].main=castaldifamily.com"
- "traefik.http.routers.<service>.tls.domains[0].sans=*.castaldifamily.com"
```

Cloudflare DNS challenge. The wildcard cert covers `*.castaldifamily.com`.

### Network topology — the thing that trips people up

Traefik and Redis both run on `homelab-control-plane`, not heimdall. The
`proxy-net` defined there is a local Docker bridge scoped to that host only.

`heimdall` and `waldorf` each declare their own separate `proxy-net` bridge
locally. Same name, different host, **not the same network** — bridge
networks do not span hosts. Containers on those nodes reach Traefik over the
real LAN via **traefik-kop**, not shared Docker networking.

traefik-kop runs on heimdall and waldorf, polls the local Docker daemon, and
pushes routing config into control-plane's Redis (`REDIS_ADDR=10.0.0.200:6379`).
Traefik reads it via the Redis provider. Hence:

- `<service>@redis` — service runs on heimdall or waldorf
- `<service>@docker` — service runs directly on control-plane

When diagnosing routing on heimdall/waldorf, check traefik-kop logs **on that
node**, not just Traefik logs on control-plane.

> [!WARNING]
> Traefik's reported server "UP" status is not reliable on its own when no
> explicit `healthCheck` block is configured — it can reflect an optimistic
> default rather than a live probe. If something shows UP but the browser
> gets a real error, check the container logs before ruling out the backend.

### Authentik middleware

Two patterns are in use. `@file` is the standard for protected services:

```yaml
# Standard ForwardAuth
- "traefik.http.routers.<service>.middlewares=authentik-auth@file"

# With additional security headers (e.g. vscode)
- "traefik.http.routers.<service>.middlewares=security-headers@file,authentik-auth@file"
```

Authentik runs on homelab-control-plane at `sso.castaldifamily.com`,
internal port 9000.

No standard middleware name is enforced on purpose. Default to
`authentik-auth@file` unless the service needs extra headers.

> [!NOTE]
> The `*arr` suite uses **dedicated** Authentik proxy outposts rather than the
> shared embedded outpost. If a router has no `authentik-auth` middleware but
> Authentik shows a dedicated outpost for it, the outpost's proxy container
> was probably never deployed — check that before assuming the fix is adding
> the middleware label.

## Docker diagnostic commands

Ask for node name, stack path, and the exact symptom first. Then:

```bash
# Validate merged config — catches YAML and env errors
docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml config

# Container status
docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml ps

# Recent logs
docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml logs --tail=200 --no-color
```

For routing and TLS issues, add:

```bash
# Traefik lives inside the control-plane "core" bundle — target by service name
docker compose -f ~/homelab/nodes/control-plane/core/compose.yaml logs traefik --tail=100 --no-color

# If the service is on heimdall or waldorf, check traefik-kop on that node
docker compose -f ~/homelab/nodes/<node>/traefik-kop/compose.yaml logs --tail=50 --no-color

# Redis connectivity — Redis is on control-plane, not the node you are checking from
docker exec <traefik_container> redis-cli -h 10.0.0.200 -a <REDIS_PASSWORD> --no-auth-warning ping
```

For NFS volume issues, add:

```bash
mount | grep 10.0.0.250
docker exec <container> ls -la /mnt/appdata/<service>
```

### Preferred fix order

Smallest blast radius first:

1. `.env` variable change (no rebuild needed)
1. Label correction (compose only)
1. Network or port adjustment
1. Health check tuning
1. Volume mount fix

### Verification after a change

```bash
docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml pull
docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml up -d --force-recreate
docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml logs --tail=50 --follow
docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml ps
```

> [!IMPORTANT]
> Env var changes require `--force-recreate`. A plain `up -d` will not pick up
> a changed `.env` or `mcp.env` value on an already-running container. This has
> caused wasted debugging time before.

Rollback:

```bash
git checkout ~/homelab/nodes/<node>/<service>/compose.yaml
docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml up -d
```

## Common failure patterns

### Container restart loop

1. `logs --tail=100` for the crash reason
1. Check `.env` — missing required vars are the most common cause
1. Check health check interval and threshold (often too aggressive at startup)
1. Check the NFS mount is live — containers binding `/mnt/appdata` fail
   quietly when NFS is down
1. Check UID/GID on the volume path against the container user

### Routing 404 / no match

1. Confirm `proxy-net` membership on the relevant host:
   `docker inspect <container> | grep -A20 Networks`
1. On heimdall/waldorf: check traefik-kop is running and Redis (10.0.0.200)
   is reachable from that node
1. Verify the router rule — the `Host()` value must match the subdomain exactly
1. Check `entrypoints=websecure` is set, not `web`
1. Confirm the TLS block is present when using the `cloudflare` certresolver

### Routing 502 / bad gateway

1. Traefik matched a router but could not reach the backend — a different
   problem from a 404, which means no match at all
1. Check the container is running **and listening on the expected port**.
   Komodo showing "running" only confirms the process started, not that the
   app inside is serving
1. Check container logs for startup errors, slow first-run initialization (a
   large git clone or DB migration on first boot), or a crash after start
1. For heimdall/waldorf, compare the backend target reported by
   `traefik_list_services` against what the container should be listening on

### TLS cert not issuing

1. Traefik logs — look for ACME / Cloudflare DNS challenge errors
1. Confirm the `certresolver=cloudflare` label is present
1. The wildcard `*.castaldifamily.com` is already issued, so a new subdomain
   should resolve without a new challenge. If it does not, the router TLS
   config is likely missing or malformed
1. Check Cloudflare API token permissions if the challenge is actively failing

### NFS mount lost

```bash
sudo mount -a
mount | grep 10.0.0.250

# Expected fstab entries:
# 10.0.0.250:/Volume1/appdata /mnt/appdata nfs defaults 0 0
# 10.0.0.250:/Volume2/media   /mnt/media   nfs defaults 0 0
```

### Authentik ForwardAuth not triggering

1. Verify the middleware name in the label matches the Traefik file provider
   definition: `authentik-auth@file`
1. Check the Authentik server container is healthy:
   `docker compose -f ~/homelab/nodes/control-plane/authentik/compose.yaml ps`
1. Confirm the outpost proxy container is running — separate from
   server/worker, and especially relevant for dedicated per-service outposts
1. Check Authentik logs for auth flow errors:
   `docker logs authentik_server --tail=50`

## Ansible

### Environment

- **Control node**: `homelab-control-plane` (10.0.0.200) — the same node
  running Traefik, traefik-redis, and Infisical
- **Inventory**: `ansible/inventory.yml` at the repo root, YAML format — not
  `inventory/hosts.ini`

Confirm current host entries directly rather than relying on a list here.
Inventory does not mirror the hardware registry 1:1 — `panoptichron` is a
confirmed registry node but deliberately not an Ansible host.

> [!WARNING]
> The `host_vars` / `group_vars` / `vault` / `playbooks` / `roles` layout has
> not been re-confirmed recently and may reflect an earlier phase of this
> setup. Inspect the actual structure before issuing commands against it.

### Diagnostics

```bash
ansible --version
ansible-inventory -i ansible/inventory.yml --graph
ansible-playbook -i ansible/inventory.yml <playbook>.yml -vvv

# Connectivity
ansible all -i ansible/inventory.yml -m ping
ansible heimdall -i ansible/inventory.yml -m ping -vvv
```

### Safe change practice

Prefer native modules over `shell`/`command`. Always `--check --diff` before a
real run, `--limit <host>` to scope to one node first, `--tags` to run only
the relevant section.

```bash
ansible-playbook -i ansible/inventory.yml <playbook>.yml --check --diff
ansible-playbook -i ansible/inventory.yml <playbook>.yml --limit heimdall -v
ansible-playbook -i ansible/inventory.yml <playbook>.yml -v
```

Rollback:

```bash
git checkout <playbook>.yml
ansible-playbook -i ansible/inventory.yml <playbook>.yml --limit <affected-host>
```

Onboarding a new node should go through the MCP-driven workflow
(`hardware-discover-now`) rather than hand-editing inventory, to keep the
registry and the real inventory from drifting apart.

## Safety rules

Flag these explicitly at Gate 2, before Nathan approves anything:

- Port changes that affect external dependencies
- Image upgrades — especially Authentik. Check the currently pinned version
  in the compose file (never from memory), then pull the upstream changelog
  for the target version via `get_service_documentation` before proposing
  the bump
- Any change causing a service restart that affects `castaldifamily.com`
  subdomains
- NFS or storage tasks — mount changes cascade to every dependent container
- Playbooks touching SSH key distribution or access enforcement (lockout risk)
- Anything touching Ansible Vault or secrets

Never:

1. Suggest `rm -rf` or destructive volume operations without confirming the
   NAS backup is current
1. Add `proxy-net` declarations to heimdall/waldorf stacks that conflict with
   the traefik-kop pattern, or assume a shared `proxy-net` spans hosts
1. Store secrets in `compose.yaml` — `.env` / `mcp.env` only, and prefer
   `secrets_add` / `secrets_decrypt` via Registry MCP over plaintext edits
1. Propose general homelab services, media stack additions, or infra changes
   for `panoptichron` — it is reserved for apps Nathan built himself
1. Diagnose container volume errors before confirming NFS mounts are live
1. Trust a static fact in this file over live Registry MCP data
