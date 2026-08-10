# ADR-005: Monitoring, Alerting, Disaster Recovery, and Ingress Architecture

## Status
Superseded by [ADR-006](ADR-006-Pi-Non-MCP-Services-Komodo-Traefik.md) (2026-08-10) — kept for
historical context. The Pi's non-MCP services are now Komodo + Traefik only; the stack described
below (Beszel, Gatus, Dozzle, WUD, Homepage, Glance, docker-socket-proxy, Autorestic,
Healthchecks.io) has been removed.

## Date
2026-07-31

## Context
Following a cascading outage, a critical gap in system observability, log inspection, update detection, and routing automation was identified. The homelab environment requires a unified, GitOps-driven observability and operations platform integrated with the `homelab-registry-mcp` project.

The design satisfies the following constraints:
1. **Hardware & OS Lifecycle:** The initial node runs on a Raspberry Pi 5 booting from an external USB 3.0 SSD, eliminating MicroSD wear and disk I/O bottlenecks. Host OS security patches (`unattended-upgrades`) and quarterly Pi EEPROM firmware updates maintain baseline hardware health.
2. **GitOps & Automation:** All service configurations, monitoring targets, and dashboard layouts must be managed declaratively via YAML through Git.
3. **Least-Privilege Security:** Raw Docker socket exposure must be restricted to prevent host compromise.
4. **Multi-Node Ingress Readiness:** Services will run initially accessible via `IP:PORT` on the Pi, but must natively publish routing definitions to a central Traefik ingress controller located on a separate node (Node B).
5. **Human-in-the-Loop Updates:** Upstream container image updates must hook into the MCP registry's update proposal workflow for pull request review rather than applying unattended updates.

## Decision

We will adopt a modular, lightweight Go-based stack secured by a Docker socket proxy, unified across nodes via `traefik-kop` and Redis, and monitored externally via a dead man's switch.

### 1. Observability & Telemetry Stack
* **Hardware & Container Telemetry (Beszel):** Collects host performance metrics (CPU, RAM, Disk I/O) and per-container resource utilization.
* **Synthetic Endpoint Uptime (Gatus):** Performs synthetic HTTP/TCP/DNS uptime probes, validates SSL certificates, and enforces flapping protection thresholds to reduce alert noise.
* **Real-Time Log Viewing (Dozzle):** Provides a stateless, lightweight UI (`amir20/dozzle`) for streaming container logs via standard Docker API calls.
* **Container Update Detection (What's Up Docker / WUD):** Monitors local containers and upstream image registries for new tags.
* **Dead Man's Switch (Healthchecks.io):** Out-of-band external heartbeat monitor. A local scheduled check pings Healthchecks.io; if the Pi loses power, network, or kernel stability, Healthchecks.io dispatches an out-of-band alert.

### 2. Notifications, Secrets, & Disaster Recovery
* **Uniform Alerts (SMTP):** Email is established as the single, consistent notification pathway across Gatus, WUD, and Beszel.
* **Secret Management (GitCrypt):** Repository secrets (SMTP credentials, tokens) are encrypted at rest using GitCrypt.
* **Disaster Recovery (Autorestic):** Scheduled, encrypted backups of persistent stateful Docker volumes (Beszel database, GitCrypt keys, app state) sent to offsite or remote storage.

### 3. Dual-Dashboard Architecture
* **Infrastructure Control Plane (Homepage - `gethomepage.dev`):** Runs on Port 3000 as the primary dashboard, displaying native API widgets for Beszel, Gatus, WUD, and Dozzle.
* **Daily Start Page (Glance - `glanceapp/glance`):** Runs on Port 8080 as a mobile-optimized, content-focused dashboard for RSS feeds, daily links, and personal information.

### 4. Ingress & Security Topology
* **Local Docker Socket Lockdown:** `tecnativa/docker-socket-proxy` runs on the Pi 5, exposing read-only TCP endpoints internally for WUD, Beszel, and Dozzle.
* **Cross-Node Routing (`traefik-kop` + Redis):**
  * **Node B (Central Ingress Node):** Hosts the primary Traefik instance and a password-protected Redis container.
  * **Node A (Pi 5 & Worker Nodes):** Runs `traefik-kop` locally to read Docker labels from local compose files and publish dynamic routing rules across the LAN to Redis on Node B. Traefik consumes these rules without needing network access to Node A's Docker socket.

### 5. Registry Integration
WUD is configured with HTTP Webhook triggers that dispatch JSON payloads upon detecting upstream container updates. These webhooks route to `homelab-registry-mcp` to generate Git update proposals for human review.

## Consequences

### Positive
* **High Efficiency & Low Memory:** Entire stack consists of Go binaries and lightweight agents, preserving the Raspberry Pi 5's compute overhead for primary workloads.
* **Zero Cross-Node Socket Exposure:** `traefik-kop` decouples Traefik from remote socket access, maintaining a strict security perimeter.
* **Out-of-Band Reliability:** Healthchecks.io guarantees alert dispatch even during total hardware or power failure on the Pi.

### Open Items
1. **WUD Webhook Ingestion Listener (MCP):** Implementation of the HTTP API endpoint within `homelab-registry-mcp` to process incoming WUD JSON payloads and automate Git branch/PR generation.