# Homelab Registry MCP: Phased Implementation Plan

## Phase 1: The Installation Pipeline (COMPLETED)

* **Goal:** Create a seamless, automated bootstrapping experience for the Raspberry Pi control plane.
* **Tasks:**
  * Create `scripts/install.sh` as a curl-bash wrapper that installs Git, clones the repository, and prompts the user for necessary secrets (e.g., GitHub Token, Traefik URL).
  * Include an interactive prompt during installation to ask the user if they want to enable Advanced AI Reasoning (DSPy) and capture their LLM API key if they opt-in.
  * Update `scripts/bootstrap.sh` to remove all hardcoded references to the `chester` username, replacing them with dynamic variables (e.g., `$SUDO_USER` or `$USER`).
  * Ensure the `install.sh` script starts the Docker Compose environment (`docker compose up -d`) *before* executing the final network/IP swap to guarantee the server is running when the SSH session drops.

## Phase 2: Startup Validation & Health Checks (COMPLETED)

* **Goal:** Ensure the MCP Python server validates its environment before attempting to execute infrastructure commands.
* **Tasks:**
  * Create `src/registry_mcp/health.py` to evaluate the presence of the Git repository, `ansible.cfg`, and SSH keys.
  * Update `src/registry_mcp/server.py` to implement **Graceful Degradation**. If health checks fail, the server must start in a Read-Only mode (disabling all Write/GitOps tools).
  * Create and always register a `system_health_check` MCP tool so the AI can diagnose the environment and report issues to the user.

## Phase 3: GitOps Workflow & Conversational Loop (COMPLETED)

* **Goal:** Implement strict PR-only infrastructure modifications and allow human operators to request changes via GitHub comments.
* **Tasks:**
  * Enforce that the AI never commits directly to the `main` branch. All modifications must be done on feature branches and submitted as Pull Requests.
  * Update `src/registry_mcp/providers/git/github.py` to include a method for fetching recent comments on Pull Requests.
  * Update `src/registry_mcp/server.py` to run a continuous asynchronous background task that polls for new PR comments.
  * Integrate the DSPy reasoning engine to read human PR feedback, check out the respective branch, apply the requested code fixes, and push a new commit to the open PR.

## Phase 4: Automated Deployment Pipeline (GitOps CD) (COMPLETED)

* **Goal:** Close the loop. When a human approves and merges an AI-generated PR, the homelab should automatically deploy the changes without manual terminal intervention.
* **Tasks:**
  * Implement the GitHub Actions workflow (`.github/workflows/deploy.yml`) in the user's homelab repository to trigger on `push` to the `main` branch.
  * Write the Ansible `docker-stack-deploy` role (as referenced in ADR-001 Phase E).
  * Ensure the GitHub Actions runner on the Raspberry Pi correctly catches the webhooks and executes the Ansible playbook against the workload nodes.

## Phase 5: Proactive Email Notifications (COMPLETED)

* **Goal:** Alert the human operator immediately when the AI has generated a proposal (PR) that requires review, minimizing the need to constantly check GitHub.
* **Tasks:**
  * Implement the `SMTP2GO` (or similar SMTP) `NotificationProvider` (ADR-001 Phase F).
  * Generate a templated HTML email containing a summary of the PR, the diff, and direct links to "Approve" or "Request Changes" on GitHub.
  * Wire the notification provider into the existing `ProposalEngine` so it fires successfully when the AI opens a PR.

## Phase 6: Public Release Readiness (COMPLETED)

* **Goal:** Polish the repository for public consumption, ensuring safety, usability, and clean documentation for external homelab operators.
* **Tasks:**
  * Perform a full codebase scrub to ensure absolutely no real hostnames, IPs, usernames (like `chester`), or personal domain names exist in the public repository.
  * Finalize `.env.example` with clear placeholder values for all new Phase 1-5 additions (e.g., LLM keys, SMTP credentials).
  * Update `README.md` to perfectly reflect the curl-bash `install.sh` experience and the Pi/Ansible topology. 
  * Validate that the `LICENSE`, `SECURITY.md`, and `CONTRIBUTING.md` files are accurate and present.

## Phase 7: Brownfield Adoption & Secret Interception (COMPLETED)

* **Goal:** Allow the AI to safely reverse-engineer live, pre-existing services and bring them under GitOps management without leaking hardcoded secrets.
* **Tasks:**
  * Implement the `proposal_adopt_service` tool in `src/registry_mcp/tools/adoption.py`.
  * The tool must inspect the live Docker container via SSH/Docker API and cross-reference it with the original `docker-compose.yml` (found via Docker labels).
  * **Secure by Default Logic:** The DSPy reasoner must be instructed to identify any hardcoded secrets in the legacy compose files, strip them out, and replace them with variable interpolations.
  * **Human-in-the-Loop (HITL):** The adoption tool must pause and yield back to the AI chat interface *before* opening the PR if secrets are found, asking the operator whether to keep the existing secrets or generate new ones.
  * Integrate the `git-crypt` commands to ensure the newly generated `.env` file is fully encrypted on the feature branch before the PR is pushed to GitHub.
  