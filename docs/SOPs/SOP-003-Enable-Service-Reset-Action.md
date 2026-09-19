# SOP: Enable and Confirm the Service Reset Action

**Owner:** the maintainer
**Frequency:** Once, to stand the capability up — then once per Shortcut/device added
**Last Updated:** 2026
**Status:** Draft — the capability this checklist stands up does not exist yet

---

### Purpose

Get from the registry's actual current deployed state — no state-reset capability of
any kind — to a confirmed, working write path: a linked service can be reset by
solving a math challenge, over HTTP, from an iOS Shortcut that isn't on the LAN.

See [ADR-014](../ARDs/ADR-014-Service-State-Reset-Action-Tool.md) for the full design
and the alternatives it rules out. Unlike this folder's other SOPs, this one is not
yet a runbook for an existing feature — §1 and §2 below are implementation work, §3
onward is the operational checklist once that work lands. Read ADR-014 first; nothing
here is a substitute for it.

---

### When to use it

- You've decided to build the capability ADR-014 sketches and want a single ordered
  list from "nothing exists" to "verified working," code and config both.
- You've already built it (in a prior pass through this checklist) and are adding a
  second Shortcut, a second device, or re-verifying after a secret rotation — start
  at §4.

---

### Current state (as of this checklist's writing)

Confirm each of these is still true before starting — if any has changed, this
checklist and ADR-014 need to be revisited first:

- [ ] No `SERVICE_RESET_*` environment variable exists in `config.py` or `.env.example`
- [ ] No `service_reset_request`/`service_reset_confirm` MCP tool is registered in `server.py`
- [ ] No `/actions/*` route exists — `webhooks/dockhand.py` is the only `FastMCP.custom_route` consumer
- [ ] No `ActionEvent` model exists in `models/`
- [ ] No code path SSHes into a `HardwareNode` to execute a command against a live container (`adoption/ssh.py` only ever reads)
- [ ] The Docker socket mount in `docker-compose.yml` is still `:ro`

---

## Part 1 — Implement the gate and executor (code)

- [ ] Add the pending-action record: either a new `PendingAction`/`ActionEvent` model
      pair in `models/`, or an `action_type` column on the existing `PendingDeletion`
      table (ADR-014 §6, Open Question 1 — pick one before writing code)
- [ ] Add `actions/reset.py` (or similar) holding the gate logic (request → challenge
      → confirm → execute) and the SSH/Ansible executor, mirroring
      `deletion/store.py`'s `DeletionGateStore` shape
- [ ] The executor runs one scoped command against the service's linked
      `HardwareNode` only — reuse the SSH/Ansible connection helpers `adoption/ssh.py`
      and `hardware/ansible_facts.py` already have; do not open a new connection
      mechanism
- [ ] A service with no linked `HardwareNode` fails the request step with a clear
      reason (`"service has no linked hardware node"`), not a stack trace
- [ ] Add `SERVICE_RESET_ENABLED` (default `false`) and
      `SERVICE_RESET_CHALLENGE_TTL_MINUTES` to `config.py` and `.env.example`,
      documented in CLAUDE.md's environment variable table
- [ ] Wire the same gate/executor into an MCP tool pair
      (`service_reset_request`/`service_reset_confirm`) registered in `server.py`,
      gated the same way every other write tool is gated by `read_only` mode
- [ ] Unit tests: correct-answer path executes and logs an `ActionEvent`;
      wrong-answer, expired, and already-resolved challenges all invalidate without
      executing anything; a service with no linked node never reaches the executor
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest` all pass

---

## Part 2 — Add the HTTP surface (code)

- [ ] Add `SERVICE_RESET_SECRET` to `config.py`/`.env.example` (unset by default)
- [ ] Register `GET /actions/services`, `POST /actions/services/{id}/reset`, and
      `POST /actions/services/{id}/reset/confirm` via `FastMCP.custom_route`, in a new
      `webhooks/actions.py` (or `http/actions.py`) alongside `webhooks/dockhand.py`
- [ ] Auth: `Authorization: Bearer <secret>` compared with `hmac.compare_digest` —
      copy `webhooks/dockhand.py`'s check rather than re-deriving it
- [ ] Fail-closed at registration: `SERVICE_RESET_ENABLED=true` with no
      `SERVICE_RESET_SECRET` set (or blank/whitespace) leaves the routes **unmounted**
      — log a `service_reset_disabled_no_secret` line, same idiom as
      `dockhand_webhook_disabled_no_secret`
- [ ] `GET /actions/services` filters to services with a linked `HardwareNode` and
      returns the flat shape ADR-014 §3.3 specifies — no MCP envelope, this is for a
      Shortcut's "Choose from List," not an MCP client
- [ ] The two `POST` routes call into Part 1's gate/executor directly — no duplicated
      gate logic in the HTTP layer
- [ ] Add tests: missing/wrong bearer token → `403`; routes absent (404) when the
      secret is unset; a full request→confirm round trip against an in-memory
      fixture actually flips the pending-action row and would call the (mocked) SSH
      executor
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest` all pass

---

## Part 3 — Turn it on and confirm the write actually happens (operational)

Do this against a real but low-stakes service first — not something you'd mind
being down for a minute.

#### Step 1: Confirm prerequisites the tool inherits from the existing GitOps health check

- [ ] `ANSIBLE_CFG_PATH` and `SSH_KEY_PATH` are set and `system_health_check` reports
      the server is not in read-only mode — the SSH/Ansible executor has the same
      dependency `hardware-discover-now` does
- [ ] The target service is linked to a `HardwareNode` — check with
      `service_get_full_context(service_id)`; if it isn't, call
      `hardware-link-service` first

#### Step 2: Enable the capability

Add to `.env`:

```bash
SERVICE_RESET_ENABLED=true
SERVICE_RESET_SECRET=<output of: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
```

```bash
docker compose up -d
docker compose logs homelab-registry-mcp | grep service_reset
```

**Expected result:** a `service_reset_registered` (or equivalent) startup log line.
**If it fails:** a `..._disabled_no_secret` line means the secret is empty or
whitespace — re-check the `.env` line.

#### Step 3: Verify the HTTP surface directly, before involving a Shortcut

```bash
# List — should return only linked services
curl -sS http://localhost:8765/actions/services \
  -H "Authorization: Bearer <the secret>"

# No token — should 403
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8765/actions/services
```

- [ ] Authenticated `GET /actions/services` returns the target service
- [ ] Unauthenticated request returns `403`

#### Step 4: Confirm the full request → confirm → execute round trip

```bash
curl -sS -X POST http://localhost:8765/actions/services/<id>/reset \
  -H "Authorization: Bearer <the secret>"
# -> {"challenge_id": "...", "question": "3 + 4 = ?"}

curl -sS -X POST http://localhost:8765/actions/services/<id>/reset/confirm \
  -H "Authorization: Bearer <the secret>" \
  -H "Content-Type: application/json" \
  -d '{"challenge_id": "...", "answer": 7}'
```

- [ ] The confirm call returns success and the target container actually restarted
      (check its uptime/start time on the host, not just the HTTP response)
- [ ] A new `ActionEvent` row exists for this service with outcome `ok`
- [ ] A wrong answer on a fresh challenge is rejected and the challenge cannot be
      retried (request a new one and confirm the old `challenge_id` no longer works)
- [ ] Letting a challenge sit past `SERVICE_RESET_CHALLENGE_TTL_MINUTES` and then
      confirming it fails with an expiry error

**This is the point at which "confirmed write capability" is actually true** — every
step before this only proves the gate logic; this step proves a live container was
actually reset through it.

#### Step 5: Decide reachability, then expose it (or don't)

Per ADR-014 §3.4, this is your call, not something the codebase decides:

- [ ] If LAN-only is acceptable: nothing further — the Shortcut will only work on
      home Wi-Fi (or over a VPN back to it, which sidesteps needing a public route at
      all and is worth considering before opening `/actions/*` to the internet)
- [ ] If genuinely location-agnostic: add a second Traefik router in your homelab
      repo matching a `PathPrefix` on `/actions` (or a distinct hostname), pointed at
      this service, with TLS — leaving `/mcp`'s existing router untouched
- [ ] Whichever you choose, re-run Step 3's `curl` checks against the externally
      reachable URL, not just `localhost`, before wiring up the Shortcut

---

## Part 4 — Build the Shortcut

- [ ] **Get Contents of URL** → `GET https://<your-host>/actions/services`, header
      `Authorization: Bearer <secret>` → parse JSON
- [ ] **Choose from List** → built from the parsed `display_name` values
- [ ] **Get Contents of URL** → `POST .../actions/services/<selected id>/reset` with
      the same header → parse the returned `question`
- [ ] **Show Alert** or **Ask for Input** → present the question, capture the typed
      answer
- [ ] **Get Contents of URL** → `POST .../actions/services/<id>/reset/confirm` with
      `challenge_id` + `answer` as JSON body
- [ ] **Show Result** → surface success/failure back to the user
- [ ] Store the bearer secret in the Shortcut via **Text** action reference or the
      Shortcuts app's built-in credential storage, not hardcoded in a shared/exported
      Shortcut file

---

### Verification (full checklist, re-run after any change to this path)

- [ ] `SERVICE_RESET_ENABLED=false` (the shipped default) leaves `/actions/*` entirely
      unmounted — confirm with a `curl` returning `404`, not `403`
- [ ] Enabling without a secret leaves the routes unmounted (Part 2's fail-closed
      check) — confirm the specific log line
- [ ] Enabled + secreted: unauthenticated request → `403`; authenticated list →
      correct services only
- [ ] A full reset round trip (request → confirm → executor runs) actually restarts
      the target container, confirmed by its changed start time
- [ ] Wrong answer, expired challenge, and reused challenge_id all fail closed with no
      restart executed
- [ ] `ActionEvent` (or equivalent) rows exist for both successful and rejected
      attempts
- [ ] The Shortcut works from off the home network (cellular data, not Wi-Fi) end to
      end, from list to confirmed restart

---

### Rollback

```bash
SERVICE_RESET_ENABLED=false
```

Restart. The routes unmount immediately; any Shortcut calling them starts getting
`404`s. No registry state needs cleanup — pending-action rows simply expire on their
own TTL. If you exposed a Traefik router for `/actions/*` in your homelab repo,
remove that router too so the path stops resolving externally.

---

### Escalation

If Step 4's confirm call succeeds (`200`) but the container did not actually restart:
this is a gate-vs-executor split failure — the math gate is working but the SSH/
Ansible command either didn't run or failed silently. Do not treat the `200` as
proof of a reset; check the executor's own log line for the Ansible/SSH exit status
before trusting the HTTP response, and fix the executor before re-enabling the
capability for real use.

---

### Notes

- This SOP intentionally covers implementation work (Parts 1-2), not just
  configuration — the capability doesn't exist as of this writing, unlike this
  folder's other SOPs. Once built, a future edit to this file should trim Parts 1-2
  down to a one-line "confirm this is already merged" prerequisite, matching the
  shape of [SOP-002](SOP-002-Connect-Dockhand-Webhook.md).
- All environment variables referenced here belong in the Dockhand-adjacent block of
  [`.env.example`](../../.env.example) and CLAUDE.md's environment-variable table once
  Part 1/2 land — not yet added, since the variables don't exist in code yet.
