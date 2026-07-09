# Homelab Registry MCP: Phased Implementation Plan

## Phase 1: The Installation Pipeline
* **Goal:** Create a seamless, automated bootstrapping experience for the Raspberry Pi control plane.
* **Tasks:**
  * Create `scripts/install.sh` as a curl-bash wrapper that installs Git, clones the repository, and prompts the user for necessary secrets (e.g., GitHub Token, Traefik URL).
  * Include an interactive prompt during installation to ask the user if they want to enable Advanced AI Reasoning (DSPy) and capture their LLM API key if they opt-in.
  * Update `scripts/bootstrap.sh` to remove all hardcoded references to the `chester` username, replacing them with dynamic variables (e.g., `$SUDO_USER` or `$USER`).
  * Ensure the `install.sh` script starts the Docker Compose environment (`docker compose up -d`) *before* executing the final network/IP swap to guarantee the server is running when the SSH session drops.

## Phase 2: Startup Validation & Health Checks
* **Goal:** Ensure the MCP Python server validates its environment before attempting to execute infrastructure commands.
* **Tasks:**
  * Create `src/registry_mcp/health.py` to evaluate the presence of the Git repository, `ansible.cfg`, and SSH keys.
  * Update `src/registry_mcp/server.py` to implement **Graceful Degradation**. If health checks fail, the server must start in a Read-Only mode (disabling all Write/GitOps tools).
  * Create and always register a `system_health_check` MCP tool so the AI can diagnose the environment and report issues to the user.

## Phase 3: GitOps Workflow & Conversational Loop
* **Goal:** Implement strict PR-only infrastructure modifications and allow human operators to request changes via GitHub comments.
* **Tasks:**
  * Enforce that the AI never commits directly to the `main` branch. All modifications must be done on feature branches and submitted as Pull Requests.
  * Update `src/registry_mcp/providers/git/github.py` to include a method for fetching recent comments on Pull Requests.
  * Update `src/registry_mcp/server.py` to run a continuous asynchronous background task that polls for new PR comments.
  * Integrate the DSPy reasoning engine to read human PR feedback, check out the respective branch, apply the requested code fixes, and push a new commit to the open PR.