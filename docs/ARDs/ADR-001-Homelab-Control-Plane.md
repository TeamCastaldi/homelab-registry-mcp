# ADR-001: Homelab Control Plane — Full Vision

| | |
|---|---|
| **Status** | PROPOSED — update to ACCEPTED before starting Phase A |
| **Replaces** | Komodo-based deploy model |
| **Org** | github.com/TeamCastaldi |
| **License** | MIT |
| **Date** | 2026 |

---

## 1. Purpose

This document is the single authoritative record for the homelab-registry-mcp control plane design. It captures infrastructure topology, tooling decisions, security posture, the public release strategy, and the out-of-box experience (OOBE). Implementation phases and runbooks derive from this document.

This ADR covers three decisions made together:

- A Raspberry Pi running homelab-registry-mcp is the dedicated control plane for the lab
- Deployment automation is handled by Ansible and GitHub Actions — no additional orchestration tool required
- homelab-registry-mcp is a public, adoptable project published under github.com/TeamCastaldi; each operator's homelab configuration lives in a private GitHub repository created on first run

> **SCOPE** — This project is designed for a Raspberry Pi + one or more workload node(s) environment. The minimum supported topology is two machines: one Pi (control plane) and one workload node. Running the MCP on the same machine as workload services is explicitly not supported and not documented.

---

## 2. Context

A common pattern in self-hosted homelabs is to run orchestration tooling on the same nodes as workload services. This creates several problems as the lab grows:

- The control path depends on the workload it manages — if the workload node is unhealthy, you lose the ability to fix it
- Orchestration tools add complexity without adding automation value when they still require manual UI interaction to trigger deploys
- There is no single document describing the intended architecture — decisions accumulate in scattered plans, session notes, and runbooks
- Tools built for personal use are often not structured for others to adopt, even when the design already anticipates it

This ADR addresses all four. The Raspberry Pi 5 is the intended control plane hardware because it is inexpensive, energy-efficient, ARM-compatible with all required software, and — when connected via Ethernet to a UPS-backed switch — reliable enough to serve as a persistent control plane even during workload node maintenance or failure.

---

## 3. Hardware Requirements

### 3.1 Control Plane Node (Required)

The control plane node runs homelab-registry-mcp and Ansible. It must be a dedicated machine — it does not run workload services.

| Requirement | Minimum / Recommended |
|---|---|
| **Hardware** | Raspberry Pi 5 (recommended). Pi 4 with 4GB+ is the minimum. |
| **RAM** | 4GB minimum. 8GB+ recommended. 16GB for comfortable headroom. |
| **Storage** | 32GB+ microSD. Persistent state should live on NFS or external storage, not the SD card. See Section 4.2. |
| **Network** | Wired Ethernet strongly recommended. PoE simplifies power. Static IP required. |
| **Power** | UPS-backed power recommended. The control plane should outlast a brief power event. |
| **OS** | Debian 12 (Bookworm) or Ubuntu 24.04 LTS. 64-bit required. |
| **Internet access** | Required. GitHub is the canonical remote for both the MCP image and the homelab repo. |

### 3.2 Workload Node(s) (Required — minimum one)

Workload nodes run your services. They have no orchestration responsibility. The MCP deploys to them via Ansible over SSH.

| Requirement | Notes |
|---|---|
| **OS** | Ubuntu 22.04+ or Debian 12. Docker must be installable. |
| **Docker** | Installed and running. The OOBE will install Docker if not present. |
| **SSH access** | The control plane node must be able to SSH to all workload nodes. The OOBE handles key distribution. |
| **Network** | Static IP recommended. Must be reachable from the control plane node. |
| **Architecture** | x86_64 (most common) or aarch64. Both are supported. |

### 3.3 NAS / Shared Storage (Optional but Recommended)

A NAS provides NFS-backed persistent storage for both the control plane and workload nodes. It is optional — workload nodes can use local storage — but strongly recommended for any lab with more than one node.

| Mount | Purpose |
|---|---|
| `<NAS_IP>:/appdata  →  /mnt/appdata` | Registry DB, homelab repo clone, service app data |
| `<NAS_IP>:/media    →  /mnt/media` | Media library (Plex, Jellyfin, etc.) |

> **NOTE** — Without a NAS, the control plane's persistent state must live on the Pi's SD card. This works but is a single point of failure. If you use local storage, establish a backup routine before putting the lab into production use.

---

## 4. Decisions

### 4.1 Dedicated Control Plane on the Pi

> **DECISION** — The Raspberry Pi is the control plane. All other nodes are workload nodes. No workload node has any orchestration responsibility.

Reference topology (node names and IPs are user-defined):

| Node (example name) | Role | Responsibilities |
|---|---|---|
| `control-node` | Control Plane | registry-mcp, Ansible. Raspberry Pi. |
| `workload-01` | Workload | General services (Traefik, Authentik, applications) |
| `workload-02` | Workload | Specialised services (e.g. media server with GPU) |
| `nas` | Storage (optional) | NFS shares for app data and media |
| `dev-node` | Dev (optional) | Scratch / test environment. Added to Ansible inventory when available. |

The topology is flexible. A minimal deployment is one control-node and one workload-01. Additional workload nodes are added through the OOBE or the `hardware_add_node` MCP tool at any time.

### 4.2 Persistent State on External Storage

> **DECISION** — All persistent state for control plane services is stored on external storage (NFS or a mounted volume), not the Pi's SD card. The SD card runs the process only.

If a NAS is available, mount its shares on the control plane node via `/etc/fstab`:

```
# /etc/fstab — add these lines on the control plane node
<NAS_IP>:/appdata  /mnt/appdata  nfs  defaults  0  0
<NAS_IP>:/media    /mnt/media    nfs  defaults  0  0
```

If no NAS is available, create local directories and accept the single-point-of-failure tradeoff:

```bash
sudo mkdir -p /mnt/appdata /mnt/media
# Back these up regularly
```

### 4.3 Deployment — Ansible + GitHub Actions

> **DECISION** — Ansible handles all deployments. GitHub Actions triggers Ansible on PR merge. The human-in-the-loop gate is a PR approval email. No additional orchestration tool is required.

The full deploy loop:

| # | Actor | Action |
|---|---|---|
| 1 | registry-mcp | Detects configuration drift or receives a deploy intent. Opens a PR in the operator's private homelab repo on GitHub. |
| 2 | Email (SMTP) | Templated HTML email to the operator: PR summary, diff, Approve button, Request Changes button. |
| 3 | Operator | Clicks Approve (links to GitHub PR) or Request Changes (opens a conversation). |
| 4 | GitHub Actions | On PR merge, workflow fires. Triggers Ansible playbook via the self-hosted runner on the control plane node. |
| 5 | Ansible | SSHes to the target workload node. Pulls the repo. Runs `docker compose up -d` for the affected service. |
| 6 | registry-mcp | Next discovery pass confirms the change landed. Marks the proposal verified. |

> **IMPORTANT** — The registry-mcp holds no merge authority. It opens PRs and monitors outcomes only. GitHub Actions owns the merge-to-deploy pipeline. This limits the blast radius of any MCP bug to PR creation — never live deployments.

### 4.4 GitHub as Canonical Remote — Two Repositories

> **DECISION** — All code and homelab configuration lives on GitHub. The MCP is a public repository licensed MIT. Each operator's homelab configuration lives in a private repository on their own GitHub account or organisation, created during first run by the OOBE.

| Repository | Visibility | Purpose |
|---|---|---|
| `github.com/TeamCastaldi/homelab-registry-mcp` | Public — MIT | The MCP server. Cloned and run by any operator. |
| `github.com/<your-org>/homelab` | Private | Your homelab configuration. Created by the OOBE on first run. |

Internet access is a deliberate dependency. The rationale:

- GitHub uptime is not dependent on your homelab hardware being healthy
- Secrets never leave the GitHub Actions environment — no deploy tokens need to live on your nodes
- The project is designed to be discovered and adopted — GitHub is where that happens
- The vast majority of container images already require pulling from public registries — internet is already in the critical path for any running lab

> **NOTE** — The homelab repo lives on GitHub by design. A self-hosted Git instance is explicitly not supported as the canonical remote. The reliability and security benefits of an offsite repo are a core architectural goal, not a convenience.

### 4.5 Docker Image on GitHub Container Registry

> **DECISION** — The registry-mcp Docker image is published to ghcr.io under the TeamCastaldi organization. No local Docker registry is required or assumed.

```bash
docker pull ghcr.io/teamcastaldi/homelab-registry-mcp:latest
```

A GitHub Actions workflow builds and pushes the image on every tagged release. The control plane node pulls from ghcr.io on deploy. Operators who run a local registry may mirror the image, but it is not a requirement or a supported configuration.

### 4.6 Secrets — git-crypt on the Homelab Repo

> **DECISION** — The private homelab repo uses git-crypt to encrypt `.env` files at rest on GitHub. git-crypt operations are exposed as MCP tools so the AI assistant can manage secrets without the operator touching the command line.

> **IMPORTANT** — The git-crypt symmetric key must be stored outside the homelab repo — never committed to any repository. Recommended storage: a password manager or secret store already deployed in your lab (e.g. Vaultwarden). The OOBE generates the key and guides you through storing it safely before completing setup.

---

## 5. Out-of-Box Experience (OOBE)

The OOBE runs automatically when no configuration is detected on first start. It is conversational — driven by an AI assistant via MCP tool calls. The operator answers questions; the MCP does the work. No config files are edited manually during onboarding.

The OOBE covers the full path from a fresh Debian install to a running, configured lab registry. It assumes:

- The control plane node has Debian 12 or Ubuntu 24.04 installed and SSH is accessible
- The operator has a GitHub account
- At least one workload node is reachable from the control plane node over the network
- Nothing else is assumed — Docker, Ansible, SSH keys, and the homelab repo are all created by the OOBE

### 5.1 OOBE Conversation Flow

| # | Question / Action | Branches to / Result |
|---|---|---|
| **1** | What is your GitHub username or organization? | Stores git provider config. Walks through GitHub org creation if needed. |
| **2** | Do you have an existing homelab repo, or should I create one? | Existing: ask for repo name and clone it. New: create private repo on GitHub, scaffold `nodes/` structure, initial commit. |
| **3** | What is the hostname and IP address of this control plane node? | Registers the control plane in the hardware node registry. |
| **4** | Tell me about your workload node(s). Name, IP, and role for each. | Registers each node. Builds the initial Ansible inventory file. |
| **5** | Do you have a NAS for shared storage? | Yes: collect NAS IP and share paths, write `/etc/fstab` entries. No: configure local `/mnt/appdata` and `/mnt/media`. |
| **6** | Installing Docker on the control plane node... | Runs install playbook if Docker is not present. Verifies `docker run hello-world`. |
| **7** | Setting up Ansible... | Installs ansible-core and ansible-lint. Writes `ansible.cfg` and inventory file based on nodes registered in steps 3–4. |
| **8** | Generating and distributing SSH keys... | Creates ED25519 key pair on control plane. Runs `ssh-copy-id` to each workload node (prompts for password once per node). Tests passwordless login. |
| **9** | Configuring workload nodes... | Installs Docker on workload nodes that need it. Configures passwordless sudo for the Ansible user. |
| **10** | Validating connectivity to all nodes... | Runs `ansible all -m ping`. Reports pass/fail per node. Pauses on failure with diagnostic guidance. |
| **11** | Registering GitHub Actions runner... | Registers a self-hosted runner on the control plane node to the homelab repo on GitHub. Configures it to run as a system service. |
| **12** | Where should notification emails go? | Collects SMTP credentials and recipient address. Sends a test email. Confirms receipt before continuing. |
| **13** | Generating your `.env` and encrypting secrets... | Creates `.env` with all collected config. Initialises git-crypt. Encrypts `.env`. Guides operator to store the key in a password manager before proceeding. |
| **14** | Committing initial configuration to your homelab repo... | Commits inventory, `.gitattributes`, encrypted `.env`, and node structure. Pushes to GitHub. |
| **15** | Running first discovery pass... | Discovers services on all reachable workload nodes. Registry populates. OOBE complete. |

### 5.2 OOBE MCP Tools

> **NOTE** — The OOBE is implemented as MCP tools so any MCP-capable AI client can drive onboarding conversationally. The operator never runs a setup script directly.

| Tool | Purpose |
|---|---|
| `oobe_status` | Current onboarding state and next required step |
| `oobe_set_git_provider` | Configure GitHub account/org and token |
| `oobe_create_repo` | Create private homelab repo via GitHub API |
| `oobe_add_node` | Register a node (name, IP, role) |
| `oobe_set_storage` | Configure NFS mounts or local storage paths |
| `oobe_install_docker(node)` | Run Docker install playbook on a node |
| `oobe_setup_ansible` | Install Ansible, write `ansible.cfg` and inventory |
| `oobe_distribute_ssh_keys` | Generate ED25519 keys and push to all nodes |
| `oobe_validate_connectivity` | Run `ansible all -m ping` and report results |
| `oobe_register_actions_runner` | Register GitHub Actions self-hosted runner |
| `oobe_set_notifications` | Configure SMTP credentials and send test email |
| `oobe_encrypt_secrets` | Initialise git-crypt and encrypt `.env` |
| `oobe_commit_and_push` | Commit initial configuration to homelab repo |
| `oobe_complete` | Run first discovery pass and mark onboarding done |

---

## 6. Security

### 6.1 Access Posture

All services should be protected regardless of which node they run on. The recommended stack is Traefik for ingress with Authentik for SSO — both running on a workload node, not the control plane.

| Surface | Auth Method | Notes |
|---|---|---|
| registry-mcp (via Traefik) | Authentik SSO | Forward auth via static backend pointing to control plane IP. |
| Control plane direct ports | Network only | Emergency fallback when Traefik/Authentik node is down. Restrict to trusted network devices where possible. |
| Ansible SSH | ED25519 key auth | Keys generated on the control plane and never leave it. Passwordless sudo on managed nodes. |
| GitHub Actions | GitHub App token | Deploy credentials stored in GitHub Actions secrets. Never in the repo. |
| Homelab repo secrets | git-crypt | `.env` files encrypted at rest on GitHub. Key stored in operator's password manager. |

### 6.2 Ingress — Dual Access Path

Control plane services are accessible via two paths. The control plane node does not run its own Traefik instance — routing all lab traffic through the Pi would create a bottleneck and defeat the purpose of a dedicated control plane.

| Path | Available when | Notes |
|---|---|---|
| DNS via Traefik static backend on workload node | Traefik node is up | e.g. `registry.<your-domain>` routes to `<CONTROL_PLANE_IP>:8000` via static backend. Authentik forward auth applied. |
| Direct `<CONTROL_PLANE_IP>:PORT` | Always | Emergency fallback when Traefik/Authentik node is down. Internal network only. |

Static backend entry in Traefik dynamic config (example):

```yaml
# nodes/<workload-node>/core/traefik/dynamic/static-backends.yml
registry.<your-domain>  →  <CONTROL_PLANE_IP>:8000   (registry-mcp)
```

---

## 7. Public Release

homelab-registry-mcp is designed to be discovered and adopted by other homelab operators. All instance-specific values are environment variables. A new user pulls the image, runs it, and the OOBE handles the rest.

### 7.1 What Gets Published

| Artifact | Visibility | Notes |
|---|---|---|
| `github.com/TeamCastaldi/homelab-registry-mcp` | Public — MIT | Full source. MIT license. |
| `ghcr.io/teamcastaldi/homelab-registry-mcp` | Public | Docker image. Tagged releases + latest. |
| Operator homelab repo | Private | Created per-operator by OOBE on their own GitHub account. Never public. Contains real node config and encrypted secrets. |

### 7.2 Public Release Checklist

Before the repository is made public, all of the following must be true:

- [ ] No real hostnames, IPs, usernames, or domain names appear anywhere in the public repo
- [ ] All secrets are env-var driven with a complete `.env.example` containing only placeholder values
- [ ] The OOBE completes successfully end-to-end on a clean Pi with no prior configuration
- [ ] README explains what the project is, who it is for, and the minimum hardware requirement
- [ ] `CONTRIBUTING.md` documents how to run tests, the code style, and the PR process
- [ ] `LICENSE` file is present (MIT)
- [ ] All Docker images pull from ghcr.io — no references to local or private registries
- [ ] Only SMTP providers validated in production by the maintainer are documented

---

## 8. Full Tool Inventory

Complete MCP tool surface across all domains. All tools are callable by any MCP-capable AI client.

### 8.1 Registry

| Tool | Purpose |
|---|---|
| `registry_add_service` | Manual service registration |
| `registry_get_service` | Fetch by id or name |
| `registry_list_services` | Filter by category, tag, host |
| `registry_update_service` | Patch mutable fields |
| `registry_delete_service` | Hard delete (audit log preserved) |
| `service_get_full_context` | Aggregated view: service + Traefik + Authentik + recent events |
| `service_link_authentik` | Manual override for Authentik application link |

### 8.2 Discovery

| Tool | Purpose |
|---|---|
| `discovery_run_now` | Trigger a pass (optional: specific source) |
| `discovery_status` | Last run summary per source |
| `discovery_list_stale` | Services not seen recently |

### 8.3 Proposals

| Tool | Purpose |
|---|---|
| `proposal_create` | Manually trigger a remediation PR for a service |
| `proposal_list_open` | List all open PRs opened by this server |
| `proposal_get` | Full detail including diff and status |
| `proposal_cancel` | Close PR without merging |
| `proposal_verify` | Force discovery pass, check if conflict cleared |

### 8.4 Hardware Nodes

| Tool | Purpose |
|---|---|
| `hardware_list_nodes` | List all nodes with status |
| `hardware_get_node` | Fetch a node by id or hostname |
| `hardware_add_node` | Manually register a node |
| `hardware_update_node` | Patch mutable node fields |
| `hardware_delete_node` | Remove a node record |
| `hardware_link_service` | Manually link a service to a node |
| `hardware_node_services` | List all services running on a node |
| `hardware_capacity_summary` | Aggregate storage across all confirmed nodes |

### 8.5 Secrets (git-crypt)

| Tool | Purpose |
|---|---|
| `secrets_status` | Show encrypted files and current lock state |
| `secrets_encrypt(path)` | Add file to `.gitattributes` and encrypt |
| `secrets_decrypt(path)` | Temporarily read encrypted file — no plaintext written to disk |
| `secrets_add(key, value, path)` | Add or update a key in an encrypted `.env` |
| `secrets_rotate(path)` | Re-encrypt with a new key |
| `secrets_list_keys(path)` | List keys in encrypted `.env` without exposing values |

### 8.6 OOBE

| Tool | Purpose |
|---|---|
| `oobe_status` | Current onboarding state and next required step |
| `oobe_set_git_provider` | Configure GitHub account/org and token |
| `oobe_create_repo` | Create private homelab repo via GitHub API |
| `oobe_add_node` | Register a node (name, IP, role) |
| `oobe_set_storage` | Configure NFS mounts or local storage paths |
| `oobe_install_docker(node)` | Run Docker install playbook on a node |
| `oobe_setup_ansible` | Install Ansible, write `ansible.cfg` and inventory |
| `oobe_distribute_ssh_keys` | Generate ED25519 keys and push to all nodes |
| `oobe_validate_connectivity` | Run `ansible all -m ping` and report results |
| `oobe_register_actions_runner` | Register GitHub Actions self-hosted runner |
| `oobe_set_notifications` | Configure SMTP credentials and send test email |
| `oobe_encrypt_secrets` | Initialise git-crypt and encrypt `.env` |
| `oobe_commit_and_push` | Commit initial configuration to homelab repo |
| `oobe_complete` | Run first discovery pass and mark onboarding done |

### 8.7 Traefik

| Tool | Purpose |
|---|---|
| `traefik_list_routers` | List all HTTP routers |
| `traefik_get_router` | Fetch a single router by name |
| `traefik_list_services` | List Traefik backend services |
| `traefik_list_middlewares` | List configured middlewares |
| `traefik_get_entrypoints` | List configured entrypoints |
| `traefik_get_overview` | Traefik summary (router/service/middleware counts) |
| `traefik_list_tls_certificates` | TLS certificate and store information |

### 8.8 Authentik

| Tool | Purpose |
|---|---|
| `authentik_list_applications` | List all Authentik applications |
| `authentik_get_application` | Fetch an application by slug |
| `authentik_list_providers` | List all providers (proxy, OAuth2, LDAP, etc.) |
| `authentik_list_outposts` | List configured outpost instances |
| `authentik_get_outpost_status` | Fetch an outpost and its health status |
| `authentik_list_policies` | List all policies |
| `authentik_list_users` | List users (optional search filter) |
| `authentik_list_groups` | List groups (optional search filter) |
| `authentik_search_events` | Query the Authentik audit log |
| `authentik_summarize_events` | DSPy-backed access event summary for a service |

### 8.9 Events

| Tool | Purpose |
|---|---|
| `events_list_changes` | Query the registry change log |
| `events_list_discoveries` | Query discovery pass log |
| `events_get_for_service` | All events for one service |

---

## 9. Consequences

### 9.1 Positive

- No orchestration tool required beyond Ansible, which is installed by the OOBE.
- The control plane survives workload node failure — the Pi operates independently.
- Clean node separation — workload nodes have zero orchestration responsibility.
- Single recovery target — restore external storage + re-image the Pi = full control plane recovery.
- GitHub as remote adds reliability, auditability, and a public home for the project.
- The OOBE makes the project genuinely adoptable — no reading source code required to get started.
- git-crypt tools mean secrets management is fully AI-assisted from the first run.
- Only validated tools and providers are documented — the project recommends what it runs.

### 9.2 Accepted Tradeoffs

- Internet required for deployments. GitHub being unreachable blocks the PR-merge-deploy path. Public image pulls are already an internet dependency, so this does not add a new category of risk.
- Traefik/Authentik dependency for SSO. If the node running Traefik and Authentik is down, SSO-protected URLs for registry-mcp are unreachable. Direct IP:Port access on the Pi is the mitigation.
- Flat network is the default assumption. Operators who want VLAN segmentation implement it separately.
- SD card risk on the Pi. Mitigated by storing all persistent state on external storage.
- GitHub Actions runner must be healthy to trigger automated deploys. Manual Ansible is always available as a fallback.

### 9.3 Known Gaps

- VLAN / network segmentation — not in scope. Recommended as a separate network infrastructure project for operators who need it.
- Paid SMTP provider — deferred until validated in production by the maintainer.
- OOBE implementation is a new phase not yet sequenced into the existing numbered phase plan.
- git-crypt tool domain is new — not yet implemented in any phase.
- docker-stack-deploy Ansible role is referenced but not yet written.

---

## 10. Open Questions

All actionable questions have been resolved. One item remains open as a future consideration.

| # | Question | Owner | Status |
|---|---|---|---|
| 1 | VLAN segmentation guidance — should the README recommend a specific network approach (e.g. pfSense, OPNsense) or remain network-agnostic? | Maintainer | Future |

---

## 11. Implementation Phases

Phases are ordered by dependency. Each phase should be executed as a focused session. Phases 1–9 from the original project plan are prerequisites for Phase A onward.

| Phase | Name | Scope | Depends on |
|---|---|---|---|
| **A** | Control Plane Bootstrap | Fresh OS on Pi, NFS or local storage mounts, Docker install, static IP confirmed | — |
| **B** | GitHub Migration | Create TeamCastaldi org, migrate repo to GitHub, set up ghcr.io image publish workflow, configure GitHub Actions runner | A |
| **C** | git-crypt + Secrets Tools | Encrypt `.env` files in homelab repo, implement `secrets_*` MCP tools | B |
| **D** | Service Migration | Move registry-mcp to control plane node. Update Traefik static backends. Remove old orchestration tooling. | B, C |
| **E** | Ansible Deploy Role | Write docker-stack-deploy role. Wire GitHub Actions workflow to trigger Ansible on PR merge. | D |
| **F** | Email Notifications | Implement email NotificationProvider via SMTP2GO. Templated HTML with Approve / Request Changes buttons. | D |
| **G** | OOBE | Implement `oobe_*` tool surface. End-to-end test on a clean Pi. Full path from fresh OS to running registry. | E, F |
| **H** | Public Release | README, CONTRIBUTING.md, MIT LICENSE, `.env.example` scrub, public release checklist sign-off. | G |

> **NOTE** — Phases 1–9 from the original project plan (foundation through DSPy and proposals) are prerequisites for Phase A. The lettered phases above represent the control plane redesign and public release work that layers on top of that foundation.

---

## 12. References

- `docs/agentic-design-intent.md` — MCP architectural philosophy and standing policies
- `docs/plans/project-plan-registry-mcp.md` — Original phased project plan (Phases 1–9)
- `docs/plans/plan-ansibleSetup.md` — Ansible control node setup reference
- SMTP2GO documentation — https://www.smtp2go.com/docs/

---

*ADR-001 | github.com/TeamCastaldi/homelab-registry-mcp | MIT License | 2026*
