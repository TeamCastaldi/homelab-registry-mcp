# Vagrant workload-node fixture

A disposable VM running Traefik plus a couple of demo backend services, so a
running `homelab-registry-mcp` (started via `uv run registry-mcp` on your
host, or from `../slow-loop/Vagrantfile`'s control-plane VM) has a real
Traefik API to discover against instead of mocks.

This is **not** part of the install.sh/bootstrap.sh two-tier validation loop
— see [`../slow-loop/README.md`](../slow-loop/README.md) and CLAUDE.md's
"Installer validation (two-tier)" section for that. This fixture is for
developing/testing the discovery, linking, and proposal code paths
(`TRAEFIK_API_URL`, `discovery_connect_traefik`, `service_get_full_context`,
etc.) against something live.

## Prerequisites

Same as `../slow-loop/Vagrantfile`: Vagrant, the `vagrant-libvirt` plugin,
and a working libvirt/KVM setup (`virt-host-validate qemu`).

## Usage

From this directory:

```bash
vagrant up
```

This boots a Debian 12 VM at `192.168.56.20`, installs Docker, and brings up
`docker-compose.yml` (synced from this directory — no reclone, unlike the
installer-testing VM). You get:

| Service | URL | Notes |
|---|---|---|
| Traefik API/dashboard | `http://192.168.56.20:8080` | `--api.insecure=true`; point `TRAEFIK_API_URL` at this |
| `whoami-1` router | `Host(\`whoami1.homelab.test\`)` on port 80 | demo backend #1 |
| `whoami-2` router | `Host(\`whoami2.homelab.test\`)` on port 80 | demo backend #2 |

Point your `.env` at it:

```bash
TRAEFIK_API_URL=http://192.168.56.20:8080
```

then `uv run registry-mcp` (or restart your dev server) and run a discovery
pass — `discovery_run_now` or wait for the scheduler — to see the two
`whoami` routers land in the registry.

## Editing

Unlike `../slow-loop/Vagrantfile`, this one keeps the synced folder live. Change
`docker-compose.yml`, then either:

```bash
vagrant provision   # re-runs `docker compose up -d`
```

or ssh in and drive Compose by hand:

```bash
vagrant ssh
cd /vagrant
docker compose up -d
docker compose logs -f traefik
```

Add more demo services, real images with Docker labels, an Authentik
container to test the auth-linking path, etc. — this VM is meant to be
shaped to whatever you're testing.

## Teardown

```bash
vagrant destroy -f
```

Nothing here is meant to persist between rounds; treat state as disposable,
same as `../slow-loop/Vagrantfile`.
