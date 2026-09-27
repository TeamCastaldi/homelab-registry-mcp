# ADR-020: Patchmon Webhook Intake and Email-Approved Patch Execution

| | |
|---|---|
| **Status** | Accepted; [amended 2026-09-27](#amendment-2026-09-27-ansible-only-patchmon-read-only): Ansible is the only execution path, and PatchMon is read-only again |
| **Related** | [ADR-010](ADR-010-Dockhand-Update-Webhook.md) (the first inbound webhook, whose fail-closed shape this copies); [ADR-014](ADR-014-Service-State-Reset-Action-Tool.md) (draft: the first sketch of a human-confirmed live action); [SOP-007](../SOPs/SOP-007-Connect-Patchmon-Webhook.md) (setup) |
| **Date** | 2026-09-27 |

## Context

[PatchMon](https://github.com/PatchMon/PatchMon) monitors pending package updates
across the homelab's Linux hosts and can post alerts to a webhook. The operator
wants those alerts to arrive as an email with **Approve** and **Cancel** buttons,
and wants Approve to actually start the patch rather than open a pull request.

Every write path in this server so far ends at a human merging a PR
(`proposal/`, `normalization/`, adoption) or at this server's own database. Four
project conventions stand in the way of a patch that runs when someone clicks a
button:

- *An inbound webhook never mutates.* The Dockhand webhook only stages PRs.
- *The write path writes to Git only.* Nothing here runs a live change on a host.
- *Upstream APIs are read-only.* Traefik, Authentik, Docker, and Dockhand are
  never written to.
- *There is no browser-facing route on this port.*

ADR-014 (draft) already sketched the shape a human-confirmed live action should
take: off by default, deterministic (no LLM), a single-use confirmation, and
execution over the same Ansible/SSH plumbing `hardware-discover-now` uses rather
than a writable Docker socket.

What PatchMon actually sends was read from its source
(`server-source-code/internal/queue/notification_worker.go`), not from its docs
site. A generic webhook destination gets a JSON body
`{event_type, severity, title, message, reference{type,id}, metadata, text, app_link}`.
When a signing secret is set, the request carries
`X-PatchMon-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>`. There is no
timestamp header. Its threshold alerts (`host_security_updates_exceeded`,
`host_pending_updates_exceeded`) put `host_id` (a UUID) and `host_name` in
`metadata`. Its patch API, `POST /api/v1/patching/trigger`, takes
`{host_id, patch_type: "patch_all" | "patch_package", ...}` behind session-style
bearer auth.

## Decision

### 1. Intake: `POST /webhooks/patchmon`, HMAC-verified, stages nothing but a question

- **Signature before parsing.** The body is read under a size cap
  (`PATCHMON_WEBHOOK_MAX_BODY_BYTES`, checked before the HMAC so an unsigned
  sender can't make the server hash an unbounded body). Then the
  `X-PatchMon-Signature` header is compared against the HMAC-SHA256 of the raw
  bytes with `hmac.compare_digest`. A missing or wrong signature is **401**,
  before content type, JSON, or schema are looked at.
- **Two payload shapes.** `PatchmonWebhookSchema` is the flat
  `{event, service, target_host, current_version, target_version, patchmon_callback_url}`
  shape. `PatchmonNativeAlert` is PatchMon's own generic body. Both normalize to
  one `PatchAlert`. *(Amended: `patchmon_callback_url` is now ignored if sent.)*
- **An allowlist at the edge.** Host, service, version, and event values are
  held to a character allowlist in the schema: no spaces, no Ansible pattern
  characters (`,:!&*`), no leading `-`, no `{`/`}`. Nothing that reaches
  `--limit` or the playbook's extra-vars can widen the target or carry a Jinja
  template. PatchMon sends a host's *friendly* name when it has one. A name that
  fails the allowlist is acknowledged (200, `ignored`), never mapped to a guessed
  inventory name. *(Amended: with the PatchMon API configured, the alert's
  `host_id` is looked up and PatchMon's recorded `hostname` stands in, under the
  same allowlist. That is a lookup, not a guess.)*
- **Response conventions follow ADR-010.** An event not in
  `PATCHMON_WEBHOOK_EVENTS`, an unusable host name, or an alert that already has
  a pending approval gets a 200, so PatchMon doesn't retry a condition that
  won't change. A malformed body is 400/422. A stored approval is 202. An
  approval email that couldn't be sent is 502: the row is marked `undelivered`,
  and PatchMon's retry starts a fresh one.

### 2. Approval: a `PatchApproval` row and two single-use, time-bound links

- The alert becomes a `pending` `PatchApproval` row
  (`models/patch_approval.py`, `patching/store.py`) that expires after
  `PATCHMON_APPROVAL_TTL_MINUTES`.
- **Random tokens, stored hashed, not HMAC-signed URLs.** Each link carries 32
  random bytes. The row stores only their SHA-256, so a copy of the database
  can't be replayed as a click. Single use needs server-side state anyway, so a
  signed URL would add a key to manage and rotate without removing the lookup.
  The expiry is explicit in the email and on every page.
- **One-way, race-safe.** Approve or Cancel moves the row off `pending` with a
  conditional `UPDATE ... WHERE status='pending' AND expires_at > now`. Of two
  simultaneous clicks, only one changes a row. Using either link disarms both.
- **GET shows, POST acts.** Mail security scanners and link previewers fetch
  every URL in a message before a person sees it, so a one-click GET would be
  approved by the scanner. `GET /patch/approve?token=…` renders a confirmation
  page and changes nothing. Its form POSTs the token back, and only that POST
  consumes it. An expired, reused, cross-action, or unknown token gets a plain
  page (410/409/404) that says no action was taken.
- **Email only.** The approval email goes through `SmtpNotificationProvider`'s new
  `send_actionable()`. Unlike `send()`, it raises on failure, because an approval
  that never arrived can never be answered. The links are bearer credentials,
  and ntfy topics can be readable by anyone who knows the name, so the routes
  refuse to register unless the configured provider is SMTP.
- **Browser hardening.** Every page is `no-store`, `Referrer-Policy: no-referrer`
  (the token is in the URL), unframeable (`X-Frame-Options: DENY`,
  `frame-ancestors 'none'`, since the confirm button is a clickjacking target),
  and under a CSP that allows no script at all.

### 3. Execution: PatchMon first, the operator's playbook second

> **Superseded by the [2026-09-27 amendment](#amendment-2026-09-27-ansible-only-patchmon-read-only).**
> Step 1 (the PatchMon callback) never worked for longer than a login session and
> was removed; step 2's playbook run is now the only path, with the same one-host
> check. Kept below as the original decision.

`patching/executor.py`, reached only from a confirmed Approve POST and run after
the response is sent (a playbook can outlast a proxy's timeout). The operator
gets a result email when it finishes.

1. **PatchMon callback.** POST to `PATCHMON_CALLBACK_URL`, or to the payload's
   `patchmon_callback_url` only when it is on the same origin
   (scheme + host + port). The configured URL is the trust boundary, so a
   delivery can never point this server's `PATCHMON_API_TOKEN` at another host.
   Redirects aren't followed. The body carries `host_id` and
   `patch_type: "patch_all"`, the two fields PatchMon's trigger reads, alongside
   the rest of the alert and an `approval_id` a receiver can dedupe on.
2. **Ansible fallback.** When the callback is unset, unreachable, returns
   404/405/501 (unsupported), or returns any other non-2xx, run
   `PATCHMON_ANSIBLE_PLAYBOOK` with `ANSIBLE_CONFIG=ANSIBLE_CFG_PATH`,
   `--private-key SSH_KEY_PATH`, `-u SSH_DEFAULT_USER`, and `--limit <host>`.
   That is the plumbing `hardware-discover-now` already uses. `--limit` takes a
   pattern, and `all` or a group name is a valid one, so the executor first runs
   `ansible --list-hosts -- <host>` and patches only when the name resolves to
   exactly itself. The playbook gets the alert as `patchmon_*` extra-vars. A
   timeout (`PATCHMON_ANSIBLE_TIMEOUT_SECONDS`) kills the run.
3. **Every fallback is logged** as `patch_execution_fallback_ansible` (warning,
   with the reason), after `patchmon_callback_failed`. It is also recorded on
   the row (`executed_via`, `detail`) and stated in the result email.

The executor never raises. A crash in either path is recorded as `failed`.

### 4. Fail closed, stay out of read-only mode

The routes register only when `PATCHMON_WEBHOOK_ENABLED=true` **and** a signing
secret, `PATCHMON_APPROVAL_BASE_URL`, an SMTP provider, and at least one
execution path (callback URL or playbook) are all present. *(Amended: the
playbook, `PATCHMON_ANSIBLE_PLAYBOOK`, is now required.)* Otherwise nothing is
mounted, and the refusal is logged as `patchmon_webhook_disabled` naming the
missing setting. In read-only mode (the startup health check failed), the
webhook answers 403 and the links show a 503 page without consuming the token.
Once the server comes back healthy, a still-unexpired link works.

## Conventions this amends

- **An inbound webhook never mutates** still holds for the webhook itself. It
  stores a pending question and sends an email. The *approval link* is the new
  thing: a human-confirmed trigger for a live action.
- **The write path writes to Git only** now has one exception: a patch the
  operator approved from the email. It runs on exactly one inventory host,
  through the operator's own playbook, and is recorded on the approval row.
- **Upstream APIs are read-only** now has one exception: the PatchMon trigger
  callback, which is PatchMon's own API for exactly this action and runs only
  after a human confirms. Traefik, Authentik, Docker, and Dockhand are
  unaffected. *(Withdrawn by the amendment: nothing here writes to PatchMon
  any more, so the convention holds without exception.)*
- **There is no browser-facing route on this port** is no longer true:
  `/patch/approve` and `/patch/cancel` are. Like `/webhooks/*`, they sit outside
  `/mcp`'s transport-security check and authenticate in-process, so they must
  not be put behind a redirect-based ForwardAuth either.

## Consequences

**Positive**

- A patch alert becomes a two-click decision from an inbox, and nothing runs
  without a person confirming a page.
- Execution prefers the tool that knows the host's packages (PatchMon), and
  falls back to the operator's own playbook when PatchMon can't be reached or
  lacks the API (its patch routes sit behind its Plus-tier `patching` module).
  *(Superseded by the amendment. The module gate doesn't apply to a
  self-hosted, single-context PatchMon; the session-only auth does.)*
- Every property the design depends on (401 before parsing, GET never acts,
  single use, expiry, origin pinning, the one-host check, the fallback) has a
  test that was confirmed to fail when that property is removed.

**Negative / accepted risks**

- **Whoever can read the operator's inbox can approve a patch.** The links are
  bearer credentials by design. The TTL, single use, and confirm page bound the
  damage, but they don't remove it.
- **No replay protection beyond dedupe.** PatchMon signs the body but sends no
  timestamp, so a captured delivery can be replayed. While one is pending, the
  replay is deduped. After that, it can only produce another approval email,
  never a patch.
- **A callback that times out may still have started a run on PatchMon's side.**
  *(Gone with the callback.)* The fallback then runs too. The playbook should be safe to run against a host
  that's already patching (package managers take a lock, so the second run
  fails rather than interleaving).
- **An approval whose server stops mid-execution stays `approved`.** The
  outcome is lost. Execution runs as a background task, not a durable job queue.
- PatchMon's `/patching/trigger` expects a user session token. A long-lived
  `PATCHMON_API_TOKEN` may not satisfy it on every PatchMon version. When it
  doesn't, the call returns 401 and the fallback runs. *(It never does; see the
  amendment.)*

## Amendment (2026-09-27): Ansible only, PatchMon read-only

### What was found

PatchMon's source (`server-source-code/internal/server/router.go`, read at
commit `0557606`) mounts `POST /api/v1/patching/trigger` inside the router group
guarded by `AuthWithSessionCheck`. That middleware accepts only a JWT *access*
token bound to a login session, issued by `POST /api/v1/auth/login` for a
username and password (and a 2FA code when it's on), and expiring after
`JWT_EXPIRES_IN` (default `1h`). PatchMon has no long-lived token that reaches
that route. Its Integration API credentials are checked by a different
middleware (`ApiAuth`) that covers only `/api/v1/api/*`.

So a static `PATCHMON_API_TOKEN` stopped working within the hour, and every
approval after that took the Ansible fallback. The design was never validated
against a live PatchMon, which is how this went unnoticed. Separately, the
`patching` module gate (`RequireModule`) restricts only a multi-context (hosted)
deployment; a self-hosted, single-context PatchMon allows every module.

### Decision

1. **The operator's playbook is the only execution path.** `patching/executor.py`
   runs `PATCHMON_ANSIBLE_PLAYBOOK` exactly as §3 step 2 described: one inventory
   host, confirmed by `ansible --list-hosts` to resolve to exactly itself, with
   the alert as `patchmon_*` extra-vars. The playbook is required for the routes
   to register. `PATCHMON_CALLBACK_URL`, `PATCHMON_API_TOKEN`, and
   `PATCHMON_CALLBACK_TIMEOUT_SECONDS` are removed; `config_report` lists them as
   retired, so a leftover key is reported as doing nothing. The flat payload's
   `patchmon_callback_url` is ignored if sent, and `PatchApproval` no longer has
   `payload_callback_url` (deployed databases keep the nullable column).

   Keeping the trigger would have meant this server logging in as a PatchMon
   user: storing a password with `can_manage_patching`, refreshing tokens, turning
   2FA off for that account, and depending on an endpoint PatchMon doesn't offer
   to integrations. A second path also carried the double-run hazard noted above.
2. **PatchMon is read through its Integration API, and only read.**
   `integrations/patchmon/client.py` uses an "API" credential (Settings →
   Integrations) over HTTP Basic: `PATCHMON_API_URL`, `PATCHMON_API_KEY` (the
   Token Key), and `PATCHMON_API_SECRET` (the Token Secret). It needs only the
   `host:get` scope and has no method for the one write on that surface
   (`DELETE /api/v1/api/hosts/{id}`). It checks a host id is a UUID before it
   reaches a path, and follows no redirects. Optional; without it, the webhook
   behaves as before. It's used for two things:
   - **Naming a host.** A native alert's `host_name` is PatchMon's display name
     (the friendly name when one is set). When that isn't a plain inventory name
     and the alert carries `host_id`, `GET /api/v1/api/hosts/{id}/info` supplies
     the recorded `hostname`, used only if the answer is for that id and passes
     the same allowlist. An alert that already names an inventory host is never
     looked up, so its target can't change.
   - **Saying what's pending.** The approval email gains "What PatchMon last saw
     on this host": pending packages (security first, capped at 20), whether a
     reboot is needed and why, running and installed kernels, and the latest
     agent report's time and status (`webhooks/patchmon_details.py`).

   Each has an 8-second budget, together inside PatchMon's 30-second delivery
   timeout. A failed or slow read is named in the email, which goes out anyway.
3. **Text from PatchMon is flattened before it reaches the email.** Its API data
   is written by agents, and the alert's own `title`/`message`/`severity` can
   carry a host's display name. Control characters, line breaks, and
   bidirectional overrides become spaces, and each value is clipped
   (`webhooks/common.one_line`), so nothing can forge a line of the plain-text
   part, such as a second `Approve:` link.

### Conventions, as amended

- **Upstream APIs are read-only** holds again without exception: PatchMon is
  read, never written.
- **The write path writes to Git only** keeps its one exception: an approved
  patch runs the operator's playbook on exactly one inventory host.

### Consequences

- No PatchMon user credential is held, and there is one execution path to test
  and reason about.
- The approver sees what PatchMon thinks is pending before deciding. That list
  is PatchMon's as of the host's last report; the playbook decides what it
  actually installs.
- PatchMon's own patch history doesn't show these runs. It sees their effect at
  the host's next report. The approval row and result email still record each
  run.
- The operator maintains the playbook, and only hosts in their Ansible
  inventory can be patched. PatchMon's Ansible dynamic inventory
  (`patchmon.dynamic_inventory`) can supply those names; SOP-007 covers its
  trade-offs.

## Open items

- An MCP tool to list recent approvals and their outcomes (the rows already
  carry everything it would need).
- Read-only `patchmon_*` MCP tools over the Integration API client (hosts with
  patch state, a host's packages, reports, and agent queue), like Dockhand's.
- Confirming after a run that PatchMon's pending counts actually dropped (the
  agent reports on its own interval, so this needs a scheduled check).
- Timestamped signatures, if PatchMon adds them.
- ADR-014's reset action could reuse `PatchApprovalStore`'s token gate and the
  executor's one-host Ansible runner rather than building its own.
