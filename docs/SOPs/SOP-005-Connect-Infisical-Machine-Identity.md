# SOP: Create a Registry-MCP Machine Identity in Infisical

**Owner:** the maintainer
**Frequency:** Once (initial setup), then again after any Client Secret rotation
**Last Updated:** 2026
**Status:** Draft — prep work for a planned read-only Infisical integration; no
ADR exists yet and no registry-mcp code depends on this today

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
- [ ] The `homelab-registry-mcp` project already exists in Infisical with its
      secrets populated (confirmed — it currently holds ~74 keys mirroring
      `CLAUDE.md`'s environment variable table)
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

Open the `homelab-registry-mcp` **project → Access Control → Identities →
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

#### Step 4: Find the Project ID and environment slug

**Project Settings** (gear icon, top of the project) shows a copyable
**Project ID** (a UUID) — this is what the API calls in Step 5 need as
`workspaceId`. A documented Infisical bug means `workspaceSlug` doesn't
reliably resolve on some versions, so prefer the ID.

**Project Settings → Environments** lists each environment's **Name** and
**Slug** side by side — confirm the slug for whichever environment holds the
secrets you saw in the dashboard (likely `Production`, commonly slugged
`prod`, but don't assume — read it off this screen).

**Expected result:** You have a Project ID (UUID) and an environment slug,
both copied somewhere you'll reuse in Step 5.

---

#### Step 5: Verify the credential works, end to end, before any code exists

This also settles a real open question: Infisical's secrets-read endpoint has
moved between `/api/v3/secrets/raw` and `/api/v4/secrets` across versions (a
still-open upstream CLI bug conflates the two), so confirm which one your
self-hosted instance actually serves rather than assuming.

```bash
# 1. Exchange Client ID/Secret for a short-lived access token
TOKEN=$(curl -sS -X POST https://infisical.castaldifamily.com/api/v1/auth/universal-auth/login \
  -H 'Content-Type: application/json' \
  -d '{"clientId":"<client id from Step 2>","clientSecret":"<client secret from Step 2>"}' \
  | jq -r '.accessToken')

echo "$TOKEN"   # sanity check: should be a long JWT-looking string, not empty/null

# 2. Try the v3 path first
curl -sS "https://infisical.castaldifamily.com/api/v3/secrets/raw" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "workspaceId=<project id from Step 4>" \
  --data-urlencode "environment=<environment slug from Step 4>" \
  -G | jq '.secrets | length'
```

**Expected result:** Step 1 prints a real token. Step 2 prints a number
matching (or close to) the ~74 secrets visible in the dashboard.
**If Step 2 returns 404 or an empty/error body:** retry against
`/api/v4/secrets` with the same query parameters and compare — whichever one
returns the real count is what a future client should target.
**If Step 1 returns 401:** Client ID/Secret mismatch, or Step 2's identity
setup didn't complete — redo Step 2.
**If Step 2 returns 403:** the role from Step 3 doesn't actually grant
Secrets:Read on this project/environment — recheck the role assignment.

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
- [ ] It holds a read-only role on the `homelab-registry-mcp` project (not
      Admin/Member)
- [ ] Step 5's login call returns a real access token
- [ ] Step 5's secrets call returns a count matching the dashboard, confirming
      both the correct API path (v3 vs v4) and the correct Project ID/environment slug
- [ ] The Client ID/Secret are recorded in a password manager, not left only
      in shell history

---

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Login call returns `401` | Wrong Client ID/Secret, or pasted with trailing whitespace | Regenerate the Client Secret (Step 2) and re-copy carefully |
| Login call returns a token, but the secrets call 403s | The identity's project role doesn't grant `Secrets: Read` | Recheck Step 3's role assignment; a Custom Role may be needed |
| Secrets call 404s on both `/api/v3/secrets/raw` and `/api/v4/secrets` | Wrong Project ID, or `workspaceId` param name doesn't match this server version | Re-confirm the Project ID from Step 4; check this Infisical version's own API reference if both paths fail |
| Secrets call succeeds but returns 0 or far fewer than expected | Wrong environment slug (e.g. `production` vs `prod`) | Re-read the exact slug from Project Settings → Environments, don't guess |
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
  plaintext value of every secret the `homelab-registry-mcp` project holds.
