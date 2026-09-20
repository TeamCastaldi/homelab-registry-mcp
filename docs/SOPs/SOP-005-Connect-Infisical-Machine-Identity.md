# SOP: Create a Registry-MCP Machine Identity in Infisical

**Owner:** the maintainer
**Frequency:** Once (initial setup), then again after any Client Secret rotation
**Last Updated:** 2026
**Status:** Verified against the live instance (Steps 1–5 run end to end,
2026-09-20) — prep work for a planned read-only Infisical integration; no ADR
exists yet and no registry-mcp code depends on this today

---

### Purpose

Create a dedicated, least-privilege Machine Identity in the operator's
self-hosted Infisical instance (`https://infisical.castaldifamily.com`) that a
future read-only `integrations/infisical/` client can authenticate as, to
answer questions like "does `ANSIBLE_INVENTORY_PATH` actually have a value
configured for this project?" without eyeballing the Infisical dashboard by
hand.

This SOP deliberately does **not** wire the resulting credential into
registry-mcp's own running environment. The credential that lets registry-mcp
authenticate *to* Infisical cannot itself be delivered *by* Infisical (via
Dockhand's native sync, or any other Infisical-sourced env var) — that's
circular. Exactly how the Client ID/Secret this SOP produces reaches
registry-mcp's actual container environment is still an open design question
for the pending ADR, not something this SOP resolves. Stop at Step 6.

---

### When to use it

- First-time setup, before any Infisical-integration code exists in
  registry-mcp — this SOP's job is to produce and verify working credentials
  ahead of that work, not to configure the server itself.
- Rotating the Machine Identity's Client Secret.

---

### Prerequisites

- [ ] Admin access to `https://infisical.castaldifamily.com`
- [ ] The `Homelab` project (slug `homelab-yo-qb`) already exists in
      Infisical, with a `homelab-registry-mcp` secrets **folder** inside it
      already populated (confirmed — it currently holds 75 keys mirroring
      `CLAUDE.md`'s environment variable table). This is one shared project
      organized by per-service folders, not a project named
      `homelab-registry-mcp` — don't assume otherwise from the dashboard
      breadcrumb alone (see Step 4)
- [ ] A place to record the Client ID/Secret once generated (a password
      manager — e.g. Vaultwarden, matching how `SECRETS_KEY_PATH`'s exported
      key file is handled per `CLAUDE.md`) — Infisical shows the Client
      Secret exactly once

---

### Procedure

#### Step 1: Create the Machine Identity

Org-level **Access Control → Identities → Create Identity**. Name it
something unambiguous, e.g. `registry-mcp`. Note the **Identity ID** Infisical
assigns — you won't need it for API calls, but it's useful for finding this
identity again later.

**Expected result:** A new identity appears in the Identities list with no
authentication method configured yet.

---

#### Step 2: Add Universal Auth

Open the new identity → **Authentication** tab → **Add Universal Auth**.
Infisical generates a **Client ID** immediately. Then **Create Client
Secret** — this value is shown exactly once.

**Expected result:** You have both a Client ID (safe to treat as a username —
not secret on its own) and a Client Secret (treat as the actual credential).
Save both now, per Prerequisites.

**If you navigate away before copying the Client Secret:** it cannot be
retrieved again — delete it and generate a new one.

---

#### Step 3: Grant least-privilege, read-only access

Open the `Homelab` **project → Access Control → Identities →
Add Identity**, and add `registry-mcp`.

Assign the **narrowest role Infisical's UI offers that only grants Secrets:
Read** — not the built-in Admin/Member roles, which also grant write and
project-management permissions this identity has no reason to hold. If no
built-in role is narrow enough, use **Project Settings → Roles → Create
Custom Role** with only the `Secrets` resource's `Read` permission enabled,
and assign that instead. If Infisical's role editor lets you scope the grant
to a specific environment (e.g. only `Production`), do that too — no reason
for a read-only audit identity to see Development/Staging if this project
doesn't use them for anything sensitive.

**Expected result:** The `registry-mcp` identity appears under the project's
Identities tab with a read-only role, not Admin/Member.

**Why this matters here specifically:** the eventual integration is designed
read-only-to-start on purpose (an env flag would later flip it to read-write)
— granting write access at the Infisical layer now would make that
application-level gate cosmetic.

---

#### Step 4: Find the Project ID, environment slug, and secret path

**Project Settings** (gear icon, top of the project) shows a copyable
**Project ID** (a UUID) — this is what the API calls in Step 5 need as
`workspaceId`. A documented Infisical bug means `workspaceSlug` doesn't
reliably resolve on some versions, so prefer the ID.

**Project Settings → Environments** lists each environment's **Name** and
**Slug** side by side — confirm the slug for whichever environment holds the
secrets you saw in the dashboard (likely `Production`, commonly slugged
`prod`, but don't assume — read it off this screen).

**Also note the secret path** the 75 keys actually live under. If this
project holds secrets for more than one service (organized as folders rather
than one project each), the keys will not be at the root path (`/`) — the
breadcrumb shown when you first browse to them in the dashboard (e.g.
`🗂 / homelab-registry-mcp`) is a **folder path within the project**, not the
project's own name. Confirmed live: this instance's `Homelab` project holds a
`homelab-registry-mcp` folder, and a root-path query (`secretPath` omitted,
defaulting to `/`) returns a valid but empty `{"secrets": [], "imports": []}`
— not an error — because nothing lives at the root itself.

**Expected result:** You have a Project ID (UUID), an environment slug, and a
secret path (e.g. `/homelab-registry-mcp`), all copied somewhere you'll reuse
in Step 5.

---

#### Step 5: Verify the credential works, end to end, before any code exists

This also settles a real open question: Infisical's secrets-read endpoint has
moved between `/api/v3/secrets/raw` and `/api/v4/secrets` across versions (a
still-open upstream CLI bug conflates the two). **Confirmed against this
instance: `/api/v3/secrets/raw` is correct** — don't spend time on v4 unless
v3 starts erroring after an upgrade.

```bash
# 1. Exchange Client ID/Secret for a short-lived access token
TOKEN=$(curl -sS -X POST https://infisical.castaldifamily.com/api/v1/auth/universal-auth/login \
  -H 'Content-Type: application/json' \
  -d '{"clientId":"<client id from Step 2>","clientSecret":"<client secret from Step 2>"}' \
  | jq -r '.accessToken')

echo "$TOKEN"   # sanity check: should be a long JWT-looking string, not empty/null

# 2. Read the secrets folder -- secretPath matters, see Step 4
curl -sS "https://infisical.castaldifamily.com/api/v3/secrets/raw" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "workspaceId=<project id from Step 4>" \
  --data-urlencode "environment=<environment slug from Step 4>" \
  --data-urlencode "secretPath=<secret path from Step 4>" \
  -G | jq '.secrets | length'
```

**Expected result:** Step 1 prints a real token. Step 2 prints `75`, matching
the count visible in the dashboard.
**If Step 1 returns 401:** Client ID/Secret mismatch, or Step 2's identity
setup didn't complete — redo Step 2. Double-check you copied the Client ID
from the *same* identity you generated the Client Secret for, not a
different one.
**If Step 2 returns a valid `{"secrets": [], "imports": []}` (empty, not an
error):** this is not a wrong-API-version symptom — it means `secretPath` is
missing or wrong. Re-check Step 4; the root path (`/`) is genuinely empty
when secrets live in a named folder.
**If Step 2 returns 403:** the role from Step 3 doesn't actually grant
Secrets:Read on this project/environment/path — recheck the role assignment.
**If Step 2 returns 404 on `/api/v3/secrets/raw` itself:** try `/api/v4/secrets`
with the same query parameters — your instance's version may differ from the
one this SOP was verified against.

---

#### Step 6: Stop here

Do not add `INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET` to registry-mcp's
own `.env` or the `homelab` compose file yet — where they should actually
live (a literal, non-Infisical-sourced value; a git-crypt-encrypted file;
something else) is exactly the chicken-and-egg question the pending ADR needs
to answer, not something to improvise here. Keep both values in your password
manager until that's settled.

---

### Verification

- [ ] The `registry-mcp` Machine Identity exists with Universal Auth configured
- [ ] It holds a read-only role on the `Homelab` project (not Admin/Member)
- [ ] Step 5's login call returns a real access token
- [ ] Step 5's secrets call returns `75`, confirming the correct API path
      (`/api/v3/secrets/raw`), Project ID, environment slug, and secret path
- [ ] The Client ID/Secret are recorded in a password manager, not left only
      in shell history

---

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Login call returns `401` | Wrong Client ID/Secret (e.g. Client ID copied from a different identity than the Client Secret), or pasted with trailing whitespace | Regenerate the Client Secret (Step 2) and re-copy both values from the same identity |
| Login call returns a token, but the secrets call 403s | The identity's project role doesn't grant `Secrets: Read` | Recheck Step 3's role assignment; a Custom Role may be needed |
| Secrets call returns valid JSON (`{"secrets": [], "imports": []}`) but 0 results | Missing or wrong `secretPath` — secrets live in a named folder, not the project root | Re-check Step 4; a root-path query is genuinely empty when secrets are foldered |
| Secrets call succeeds but returns fewer results than expected (nonzero, still wrong) | Wrong environment slug (e.g. `production` vs `prod`) | Re-read the exact slug from Project Settings → Environments, don't guess |
| Secrets call 404s on `/api/v3/secrets/raw` | Wrong Project ID, or this instance's version serves `/api/v4/secrets` instead | Re-confirm the Project ID from Step 4; try `/api/v4/secrets` with the same params |
| `curl` TLS errors against `infisical.castaldifamily.com` | Self-hosted instance's cert chain isn't trusted from wherever you're running `curl` | Confirm you're running this from a host that already trusts the instance's cert (e.g. the control-plane node), not an unrelated machine |

---

### Rollback

Delete the Client Secret (or the whole `registry-mcp` identity) in Infisical.
Nothing outside Infisical was touched by this SOP — no registry-mcp
configuration, no git-crypt state, no running container.

---

### Notes

- This is prerequisite setup for work that hasn't been scoped into an ADR
  yet. The read-only-first, env-flag-to-flip-to-read-write shape, and the
  git-crypt-protects-the-bootstrap-credential constraint, are agreed; the
  exact tool surface and credential delivery mechanism are not.
- Least-privilege matters more here than for the other integrations
  (Traefik/Authentik/Dockhand) this project already has read-only clients
  for: those read infrastructure state, this identity would read the literal
  plaintext value of every secret in the `Homelab` project's
  `homelab-registry-mcp` folder.
- This SOP's Step 5 has been run end to end against the real instance: login
  succeeded, the root-path empty-result gotcha was hit and diagnosed live,
  and the corrected query (with `secretPath`) returned all 75 secrets.
