# ADR-002: Client Interfaces — Web UI and Discord Bot

| | |
|---|---|
| **Status** | PROPOSED |
| **Companion to** | ADR-001 (Homelab Control Plane — Full Vision) |
| **Org** | github.com/TeamCastaldi |
| **License** | MIT |
| **Date** | 2026 |

> **UPDATE (2026-07-20)** — The Discord bot (Section 5) is an abandoned idea; do not resource it. The Web UI (Section 4) is unaffected and remains PROPOSED.

---

## 1. Purpose

This document defines the architecture for two additive client interfaces to homelab-registry-mcp: an embedded web UI and a Discord bot. Both are optional components that extend the ways an operator can interact with the registry — they do not change the core MCP, the control plane topology, or the deploy pipeline defined in ADR-001.

The two interfaces are related but independent. Each has its own repository and Docker image under the TeamCastaldi organization. Each can be deployed on its own or together. Neither is required for a functioning lab registry.

---

## 2. Context

The registry-mcp exposes its capabilities through MCP tools — designed to be called by an AI assistant. That model works well for complex, conversational interactions. It is less convenient for quick read-only queries ("what services are stale?") or for operators who are not in an AI chat session when an alert fires.

Two complementary channels address this:

- A web UI provides a persistent, browser-based view of registry state that is always available without opening a chat session. It is the natural home for a status dashboard and, over time, for interactive controls.
- A Discord bot brings registry awareness into a communication platform many homelab operators already use. It makes the most common queries available as chat commands and can deliver notifications to a channel.

A Discord bot already exists at github.com/TeamCastaldi/frank-discord. It is functional but not yet ready for public adoption. This ADR formalizes its architecture, defines its relationship to the MCP, and establishes the migration path to the TeamCastaldi organization.

---

## 3. Shared Principles

Both interfaces follow the same architectural constraints:

- Neither interface contains homelab business logic. All data and operations flow through the registry-mcp API. The interfaces are presentation and interaction layers only.
- Both are optional. The OOBE asks whether the operator wants them. A lab runs correctly without either.
- Both are separate Docker images with separate repositories under the TeamCastaldi organization. They are not bundled into the registry-mcp image.
- Both follow the public release standards defined in ADR-001 Section 7.2 before being made public.
- Only features validated in production by the maintainer are documented as supported.

---

## 4. Web UI

### 4.1 Repository and Image

| | |
|---|---|
| **Repository** | `github.com/TeamCastaldi/homelab-registry-mcp` |
| **Location** | Served by the registry-mcp process — not a separate container |
| **Technology** | React (embedded as static assets in the MCP image) |
| **Default port** | Same port as the MCP API (e.g. 8000). Served at `/ui` |

> **NOTE** — The web UI is served by the registry-mcp process itself rather than a separate container. The React app is built to static assets and bundled into the MCP image. This keeps the deployment model simple — one container, one port — and avoids adding an nginx or separate web server to the stack.

### 4.2 Phase 1 — Status Dashboard

> **DECISION** — Phase 1 delivers a read-only status dashboard. No write operations. No authentication beyond what already protects the MCP endpoint.

The status dashboard answers the most common operational questions at a glance:

| Panel | Content |
|---|---|
| **Node Health** | All registered nodes with last-seen status and role |
| **Service Registry** | Full service list with category, host, auth mode, and conflict flags |
| **Discovery Status** | Last run time per source (Traefik, Docker, Authentik), next scheduled run |
| **Open Proposals** | Active PRs with title, target service, confidence score, and GitHub link |
| **Stale Services** | Services not seen in recent discovery passes |
| **Recent Changes** | Last N change events across all services |

All data is fetched from the MCP API on page load and on a configurable auto-refresh interval. No websocket connection is required for Phase 1.

### 4.3 Phase 2 — Interactive Controls

> **DECISION** — Phase 2 adds write operations to the web UI. Operators can approve or reject proposals, trigger discovery runs, and manage services directly from the browser.

Phase 2 interactive capabilities:

- Approve or Request Changes on an open proposal — mirrors the email approval flow
- Trigger a discovery pass (all sources or a specific source)
- Manually register or update a service
- Link a service to an Authentik application
- View the full event log for a specific service

Phase 2 does not change the deploy pipeline. Approving a proposal in the browser creates the same GitHub PR approval as clicking the email button — the merge and Ansible deploy flow is unchanged.

> **IMPORTANT** — Phase 2 introduces write operations through the browser. The MCP endpoint must be protected by Authentik forward auth before Phase 2 features are enabled. A read-only Phase 1 dashboard behind a less strict auth boundary is acceptable; write operations are not.

### 4.4 Phase 3 — Conversational Interface

> **THEORY** — Phase 3 is a directional concept, not a committed design. It is documented here to inform earlier phase decisions, not to prescribe an implementation.

Phase 3 embeds a conversational AI interface in the web UI — a chat panel that gives any browser user the same natural language access to the registry that an AI assistant provides in a chat session. The operator types a question or instruction; the interface calls MCP tools on their behalf and renders the response.

This is architecturally significant because it decouples registry interaction from any specific AI provider or chat client. An operator without a Claude subscription, or one who prefers not to leave the browser, gets full conversational access to their lab.

Open questions that must be resolved before Phase 3 can be designed:

- Which model powers the conversational interface, and how are API credentials managed per-operator?
- Does the interface call the AI API directly from the browser (requires exposing a key), via the MCP server as a proxy, or via a separate backend service?
- What is the session model — does conversation history persist, and if so, where?
- How does this interact with the existing MCP tool surface? Does it use the same tool definitions or a simplified subset?

Phase 1 and Phase 2 decisions should not be made in ways that foreclose Phase 3. Specifically: the React component architecture should leave room for a chat panel, and the MCP API should be designed with browser-originated calls in mind.

---

## 5. Discord Bot

> **ABANDONED (2026-07-20)** — This section is kept as a historical record only. The Discord bot is no longer planned; `github.com/TeamCastaldi/frank-discord` is not being migrated or built out further. Sections 6, 8, and 9 below have been annotated accordingly — treat any Discord-bot item in this ADR as cancelled, not as a live plan.

### 5.1 Repository and Image

| | |
|---|---|
| **Current repository** | `github.com/TeamCastaldi/frank-discord` |
| **Target repository** | `github.com/TeamCastaldi/homelab-discord-bot` |
| **Docker image** | `ghcr.io/teamcastaldi/homelab-discord-bot:latest` |
| **Current status** | Functional. Commands work. Formatting needs improvement. Not yet ready for public adoption. |

### 5.2 Integration Architecture

> **DECISION** — The Discord bot is an MCP client. It authenticates to the registry-mcp API and calls MCP tools directly. The bot contains no homelab business logic — it translates Discord commands into tool calls and formats the responses for Discord's rendering model.

This model keeps the bot thin and maintainable. When the MCP gains new capabilities, the bot gains them too — the only work is adding a command mapping and a formatter. The bot never needs to know about nodes, services, or proposals directly.

Current command-to-tool mapping:

| Command | MCP Tool | Notes |
|---|---|---|
| `!health` | `health` | Server liveness and version |
| `!status` | `discovery_status` | Last run summary per discovery source |
| `!changes` | `events_list_changes` | Recent change events across all services |
| `!proposals` | `proposal_list_open` | All open PRs with status |
| `!services` | `registry_list_services` | Full service list |
| `!stale` | `discovery_list_stale` | Services not seen in recent passes |
| `!help` | (static) | Command list — no tool call required |

### 5.3 Formatting

Discord renders Markdown in messages. The bot's current formatting needs improvement before the project is suitable for public adoption. The target formatting standard for each command type:

| Response type | Formatting target |
|---|---|
| **Health / single value** | One-line response with a status emoji. ✅ healthy / ❌ degraded. |
| **Status summary** | Bold section headers, one line per source, last-run timestamp in relative format (e.g. "3 minutes ago"). |
| **List responses** | Discord code block for tabular data. Truncate to top 10 with a "N more — use `!services all`" prompt if results exceed limit. |
| **Proposals** | One Discord embed per proposal: title, target service, confidence, GitHub PR link as a button. |
| **Empty results** | Affirmative phrasing. "No stale services." not "No results found." |
| **Errors** | Plain language. "Could not reach the registry." not a stack trace. |

### 5.4 Notification Integration

Beyond responding to commands, the bot can receive push notifications from the registry-mcp and post them to a designated Discord channel. This is the event-driven companion to the email approval flow — not a replacement for it.

| Event | Discord notification |
|---|---|
| Proposal opened | Posted to notifications channel with PR link. Does not replace the approval email — both fire. |
| Proposal verified | Brief confirmation that a change landed successfully. |
| Service went stale | Alert with service name and last-seen time. |
| Discovery pass failed | Alert with source name and error summary. |

> **NOTE** — Notification integration requires the bot to be running and connected. If the bot is offline, notifications are dropped — they are not queued. Email remains the authoritative notification channel. Discord notifications are supplementary.

### 5.5 Configuration

The bot is configured entirely via environment variables. No config files are edited manually. The OOBE handles bot setup when the operator opts in.

```bash
DISCORD_BOT_TOKEN=<your-bot-token>
DISCORD_GUILD_ID=<your-server-id>
DISCORD_NOTIFICATION_CHANNEL_ID=<channel-id>
MCP_API_URL=http://<CONTROL_PLANE_IP>:8000
MCP_API_TOKEN=<service-account-token>
```

> **IMPORTANT** — The bot authenticates to the MCP API using a service account token — not the operator's personal credentials. The token should be scoped to read-only operations until interactive Discord commands (Phase 2 equivalent) are implemented and the security implications are reviewed.

### 5.6 Migration from frank-discord

The existing bot at github.com/TeamCastaldi/frank-discord migrates to github.com/TeamCastaldi/homelab-discord-bot. The migration involves:

- Rename and transfer the repository to the TeamCastaldi org
- Refactor command handlers to call MCP tools via the API rather than any direct data access
- Apply the formatting standards defined in Section 5.3
- Add environment variable configuration per Section 5.5
- Remove any personal or instance-specific values from source and history
- Add `CONTRIBUTING.md`, MIT `LICENSE`, and `.env.example` before making the repo public

The bot is not made public until the formatting work is complete and it has been validated against the production MCP API. It follows the same public release standards as the MCP itself.

---

## 6. OOBE Integration

Both interfaces are optional OOBE steps. The operator is asked about each after the core registry setup is complete — they do not block or delay the primary onboarding flow.

| # | OOBE Question / Action | Result |
|---|---|---|
| **16** | Would you like to enable the web UI? | Yes: confirm `/ui` is accessible, note the URL. No: skip. Web UI is bundled in the MCP image — no additional setup required. |
| ~~**17**~~ | ~~Would you like to set up the Discord bot?~~ | **Abandoned — step removed from the OOBE flow.** The Discord bot is not being built; do not implement this step. |

Step 16 is numbered as a continuation of the 15-step OOBE flow defined in ADR-001 Section 5.1.

---

## 7. Consequences

### 7.1 Positive

- Operators get a browser-based view of registry state without opening an AI chat session.
- Discord brings registry awareness into a channel operators may already have open.
- The bot's thin architecture means it gains new MCP capabilities automatically — no bot changes required when the MCP grows.
- Both interfaces are optional — operators who don't want them pay no operational cost.
- Phase 3 web UI, while theoretical, is architecturally possible given the decisions made in Phases 1 and 2.

### 7.2 Accepted Tradeoffs

- Web UI bundled into the MCP image increases image size slightly. Accepted — keeping one container is worth the tradeoff.
- Discord notifications are dropped if the bot is offline. Email remains authoritative. This is the correct priority ordering.
- The bot is not public-ready today. The migration and formatting work is a real investment before it can be recommended to others.
- Phase 3 is underdetermined. Model credentials, session persistence, and the browser-call architecture are open questions that could significantly affect earlier phase decisions if they are not kept in mind.

### 7.3 Known Gaps

- Bot currently does not call MCP tools via the API — refactor is required as part of the migration.
- Formatting standards defined in Section 5.3 are targets, not current state.
- Service account token scoping for the bot is not yet implemented in the MCP.
- Phase 3 model and credential architecture is entirely unresolved.
- Web UI React scaffold does not yet exist.

---

## 8. Open Questions

| # | Question | Owner | Status |
|---|---|---|---|
| 1 | Phase 3: Which model powers the conversational interface and how are API credentials managed per-operator? | Maintainer | Future |
| 2 | Phase 3: Does the conversational interface call the AI API directly from the browser, via the MCP as a proxy, or via a separate backend? | Maintainer | Future |
| 3 | Should the bot support slash commands (`/health`, `/status`) in addition to or instead of prefix commands (`!health`, `!status`)? | Maintainer | Open |
| 4 | Should the bot support interactive Discord components (buttons on proposal embeds to approve/reject) as a Phase 2 equivalent? | Maintainer | Open |

---

## 9. Implementation Phases

These phases are sequenced after ADR-001 Phase H (public release of the core MCP). The web UI and Discord bot should not be published before the core project is public-ready.

| Phase | Name | Scope | Depends on |
|---|---|---|---|
| **I** | Web UI — Phase 1 | React scaffold, read-only status dashboard, bundled into MCP image, accessible at `/ui` | ADR-001 Phase H |
| ~~**J**~~ | ~~Discord Bot Migration~~ **(ABANDONED)** | Refactor to MCP API calls, apply formatting standards, repo transfer to TeamCastaldi, public release prep | ADR-001 Phase H |
| **K** | Web UI — Phase 2 | Interactive controls: proposal approval, discovery triggers, service management | I |
| ~~**L**~~ | ~~Discord Bot — Notifications~~ **(ABANDONED)** | Push notifications from MCP to Discord channel for proposals, stale services, discovery failures | J |
| **M** | Web UI — Phase 3 | Conversational interface. Design and implementation pending resolution of Open Questions 1 and 2. | K + OQ 1, 2 |

> **NOTE** — Phase M (Web UI Phase 3) is blocked on the open questions about model credentials and API architecture. It should not be scoped until those questions are resolved. Phases I through L can proceed independently.

---

## 10. References

- ADR-001: Homelab Control Plane — Full Vision (companion document)
- `github.com/TeamCastaldi/frank-discord` — current Discord bot repository
- discord.py documentation — https://discordpy.readthedocs.io
- Discord Embed documentation — https://discord.com/developers/docs/resources/message#embed-object

---

*ADR-002 | github.com/TeamCastaldi | MIT License | 2026*
