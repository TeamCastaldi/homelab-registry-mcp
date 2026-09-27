# SOP: Connect PatchMon to the Email-Approval Webhook

**Owner:** the maintainer  
**Frequency:** Once per PatchMon instance, and again after rotating the signing secret  
**Last Updated:** 2026-09  
**Status:** Current

---

### Purpose

Point PatchMon's webhook alerts at this server's `/webhooks/patchmon` endpoint.
Each pending-patch alert then becomes an email with **Approve** and **Cancel**
buttons. Approving (and confirming the page the link opens) starts the patch
through PatchMon's trigger API, or through your own Ansible playbook if that
isn't available.

See [ADR-020](../ADRs/ADR-020-Patchmon-Webhook-Email-Approval.md) for the design
and the risks it accepts. This SOP covers only the setup.

---

### When to use it

- You run PatchMon and want its update alerts to become approve-to-patch emails.
- You rotated the signing secret, or moved the registry to a new hostname (the
  links in the email are built from `PATCHMON_APPROVAL_BASE_URL`).

---

### Prerequisites

- [ ] The server is not in read-only mode: `system_health_check` reports
      `"mode": "read-write"`. In read-only mode the webhook answers 403.
- [ ] SMTP notifications work: `NOTIFICATION_PROVIDER=smtp` with
      `NOTIFICATION_SMTP_HOST`, `NOTIFICATION_FROM_EMAIL`, and
      `NOTIFICATION_TO_EMAIL` set. Approval links are sent by email only.
- [ ] You have at least one way to execute an approved patch:
  - PatchMon's trigger API (its Plus-tier `patching` module) plus a token it
    accepts, **or**
  - a playbook on this server, and working Ansible plumbing
    ([SOP-004](SOP-004-Verify-Ansible-Control-Plane-Plumbing.md)).
- [ ] The hosts PatchMon reports are named exactly as they are in your Ansible
      inventory (the fallback patches by inventory name). A PatchMon *friendly
      name* with spaces is ignored rather than guessed at.

---

### Procedure

#### Step 1: Generate a signing secret

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Expected result:** A long random string. You'll paste it into PatchMon and
into the registry's settings.

---

#### Step 2: Write the fallback playbook (skip if you'll only use PatchMon's API)

The playbook lives wherever your deployment can read it, usually your private
homelab repo's clone on this node. It runs with `--limit <one host>`, so
`hosts: all` is correct. It receives these extra-vars:
`patchmon_target_host`, `patchmon_host_id`, `patchmon_service`,
`patchmon_current_version`, `patchmon_target_version`, `patchmon_event`, and
`patchmon_approval_id`.

```yaml
# patch-host.yml — a minimal example: apply every pending package update.
- hosts: all
  become: true
  tasks:
    - name: Upgrade packages (Debian/Ubuntu)
      ansible.builtin.apt:
        update_cache: true
        upgrade: dist
      when: ansible_facts.pkg_mgr == "apt"
```

**Expected result:** `ansible-playbook --check --limit <host> patch-host.yml`
runs cleanly from this server's container.

---

#### Step 3: Configure the registry

Add these to wherever your deployment's settings live: `.env`, or Infisical for a
Dockhand-deployed stack (see CLAUDE.md's deployment note):

```bash
PATCHMON_WEBHOOK_ENABLED=true
PATCHMON_WEBHOOK_SECRET=<the secret from Step 1>
# The address your browser uses to reach this server; the email links start here.
PATCHMON_APPROVAL_BASE_URL=https://registry-mcp.example.com

# Execution, first choice: PatchMon's own trigger API.
PATCHMON_CALLBACK_URL=https://patchmon.example.com/api/v1/patching/trigger
PATCHMON_API_TOKEN=<a PatchMon token with can_manage_patching>

# Execution, fallback: your playbook, over ANSIBLE_CFG_PATH / SSH_KEY_PATH.
PATCHMON_ANSIBLE_PLAYBOOK=/opt/homelab/ansible/playbooks/patch-host.yml
```

Restart, then check the log and the config report:

```bash
docker compose logs homelab-registry-mcp | grep patchmon_webhook
docker exec homelab-registry-mcp registry-mcp-config-check
```

**Expected result:** A `patchmon_webhook_registered` line.  
**If it fails:** A `patchmon_webhook_disabled` line names what's missing: the
secret, the base URL, an SMTP provider, or an execution path. When it's
refused, every route is left unmounted, so the endpoint and the links all 404.

---

#### Step 4: Route the three paths through Traefik, without ForwardAuth

PatchMon has to reach `/webhooks/patchmon`, and your browser has to reach
`/patch/approve` and `/patch/cancel`. All three authenticate in-process (the
HMAC signature, and the single-use token). Don't put an Authentik ForwardAuth
middleware on them: it would redirect PatchMon's POST and the email links to a
login page.

**Expected result:**
`curl -s -o /dev/null -w '%{http_code}\n' https://registry-mcp.example.com/patch/approve`
prints `404`: the route is mounted, and a request without a token gets the
"link isn't valid" page.

---

#### Step 5: Send a signed test alert before involving PatchMon

```bash
SECRET='<the secret from Step 1>'
BODY='{"event":"patch_available","target_host":"<an inventory host>","service":"test","current_version":"1.0.0","target_version":"1.0.1"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -sS -X POST https://registry-mcp.example.com/webhooks/patchmon \
  -H 'Content-Type: application/json' \
  -H "X-PatchMon-Signature: sha256=$SIG" \
  --data "$BODY"
```

**Expected result:** HTTP 202 with `"status": "pending_approval"`, and an
email titled `Approve patch: test on <host>` within a minute. Open its
**Cancel** link and confirm: the page says the request was cancelled and
nothing ran.  
**If it fails:** A 401 means the signature didn't match. Check that the secret
matches, and that nothing between curl and the server rewrites the body. A 502
means the email couldn't be sent: check the SMTP settings.

---

#### Step 6: Point PatchMon at the endpoint

In PatchMon, add a **Webhook** notification destination (Admin Guide →
Notification Destinations):

- URL: `https://registry-mcp.example.com/webhooks/patchmon`
- Signing secret: the secret from Step 1
- Route the `host_security_updates_exceeded` and/or
  `host_pending_updates_exceeded` alerts to it. Other events are acknowledged
  and ignored (`PATCHMON_WEBHOOK_EVENTS` controls which count).

**Expected result:** The next threshold alert produces an approval email that
names the host and PatchMon's summary.

---

### Verification

- [ ] `config_status` lists `Patchmon webhook` under `features_on` with no problems
- [ ] The Step 5 test produced an email, and its Cancel link worked once, then
      showed "already used" when opened again
- [ ] A real PatchMon alert produced an email

---

### Rollback

1. Set `PATCHMON_WEBHOOK_ENABLED=false` and restart. Every route unmounts, and
   links in emails already sent start returning 404.
2. Remove the webhook destination in PatchMon.

---

### Escalation

| Symptom | Likely cause |
|---|---|
| Endpoint 404s | Not registered: read the `patchmon_webhook_disabled` log line |
| 401 on every delivery | Secret mismatch, or a proxy rewrote the body (the HMAC covers the exact bytes) |
| 200 `ignored` | Event not in `PATCHMON_WEBHOOK_EVENTS`, or a host name that isn't a plain inventory name |
| 200 `skipped` | An approval for the same alert is already pending |
| Link page says "already used" | Someone (or something) confirmed it already; each link works once |
| Result email says "exactly one inventory host" | The alert's host name isn't in your inventory, or names a group |
| Result email shows `HTTP 401` from PatchMon | Its trigger API refused the token; the Ansible fallback ran instead (or failed, if unset) |
