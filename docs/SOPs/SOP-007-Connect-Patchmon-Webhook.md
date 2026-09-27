# SOP: Connect PatchMon to the Email-Approval Webhook

**Owner:** the maintainer  
**Frequency:** Once per PatchMon instance, and again after rotating the signing secret or the API credential  
**Last Updated:** 2026-09  
**Status:** Current

---

### Purpose

Point PatchMon's webhook alerts at this server's `/webhooks/patchmon` endpoint.
Each pending-patch alert then becomes an email with **Approve** and **Cancel**
buttons. Approving (and confirming the page the link opens) runs your Ansible
playbook against that one host.

Optionally, give the server a read-only PatchMon API credential. The email then
lists what PatchMon thinks is pending on the host, and alerts that name a host
by its PatchMon friendly name can still be matched to the real hostname.

See [ADR-020](../ADRs/ADR-020-Patchmon-Webhook-Email-Approval.md) and its
2026-09-27 amendment for the design and the risks it accepts. This SOP covers
only the setup.

---

### When to use it

- You run PatchMon and want its update alerts to become approve-to-patch emails.
- You rotated the signing secret or the API credential, or moved the registry to
  a new hostname (the links in the email are built from
  `PATCHMON_APPROVAL_BASE_URL`).

---

### Prerequisites

- [ ] The server is not in read-only mode: `system_health_check` reports
      `"mode": "read-write"`. In read-only mode the webhook answers 403.
- [ ] SMTP notifications work: `NOTIFICATION_PROVIDER=smtp` with
      `NOTIFICATION_SMTP_HOST`, `NOTIFICATION_FROM_EMAIL`, and
      `NOTIFICATION_TO_EMAIL` set. Approval links are sent by email only.
- [ ] Working Ansible plumbing from this server's container
      ([SOP-004](SOP-004-Verify-Ansible-Control-Plane-Plumbing.md)):
      `ANSIBLE_CFG_PATH`, `SSH_KEY_PATH`, and `SSH_DEFAULT_USER`.
- [ ] The hosts PatchMon reports are in your Ansible inventory under the same
      name. An alert that carries a PatchMon friendly name with spaces is matched
      only through the API credential (Step 3), never guessed at. Every approval
      is checked against the inventory before anything runs.

PatchMon's own `POST /api/v1/patching/trigger` is **not** used: it accepts only a
logged-in user's short-lived session token. If you set `PATCHMON_CALLBACK_URL`
or `PATCHMON_API_TOKEN` for an earlier version, remove them;
`registry-mcp-config-check` reports them as doing nothing.

---

### Procedure

#### Step 1: Generate a signing secret

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Expected result:** A long random string. You'll paste it into PatchMon and
into the registry's settings.

---

#### Step 2: Put the patch playbook where this server can read it

The playbook lives in your private homelab repo, in the clone this server
already mounts. It runs with `--limit <one host>`, so `hosts: all` is correct.
It receives these extra-vars: `patchmon_target_host`, `patchmon_host_id`,
`patchmon_service`, `patchmon_current_version`, `patchmon_target_version`,
`patchmon_event`, and `patchmon_approval_id`.

A minimal one, if you're starting from nothing:

```yaml
# patch-host.yml — apply every pending package update on the one host given.
- hosts: all
  become: true
  tasks:
    - name: Upgrade packages (Debian/Ubuntu)
      ansible.builtin.apt:
        update_cache: true
        upgrade: dist
      when: ansible_facts.pkg_mgr == "apt"
    - name: Upgrade packages (Fedora/RHEL)
      ansible.builtin.dnf:
        name: "*"
        state: latest
      when: ansible_facts.pkg_mgr == "dnf"
```

**Expected result:**
`ansible-playbook --check --limit <host> <path to the playbook>` runs cleanly
from this server's container.

---

#### Step 3 (optional): Create a read-only PatchMon API credential

In PatchMon, go to **Settings → Integrations**, create a new token, and choose
**API** as its type:

- Name: e.g. `homelab-registry-mcp`
- Scopes: **`host: get` only**. Never grant `host: delete`: nothing here needs
  it, and it can delete a host and all its history.
- Allowed IP addresses: this server's address, if you want the credential
  pinned to it.

Save the **Token Key** (`patchmon_ae_...`) and the **Token Secret** (shown once).

**Expected result:** From this server's host,
`curl -s -u '<key>:<secret>' https://patchmon.example.com/api/v1/api/hosts | head -c 200`
prints JSON starting `{"hosts":[`. A 401 means the key or secret is wrong; a 403
means the scope is missing or the IP allowlist excludes this server.

---

#### Step 4: Configure the registry

Add these to wherever your deployment's settings live: `.env`, or Infisical for a
Dockhand-deployed stack (see CLAUDE.md's deployment note):

```bash
PATCHMON_WEBHOOK_ENABLED=true
PATCHMON_WEBHOOK_SECRET=<the secret from Step 1>
# The address your browser uses to reach this server; the email links start here.
PATCHMON_APPROVAL_BASE_URL=https://registry-mcp.example.com
# The playbook from Step 2, as a path inside this server's container.
PATCHMON_ANSIBLE_PLAYBOOK=/opt/homelab/ansible/playbooks/patch-host.yml

# Optional, from Step 3. The URL is PatchMon's root, not /api/v1/api/hosts.
PATCHMON_API_URL=https://patchmon.example.com
PATCHMON_API_KEY=<the Token Key>
PATCHMON_API_SECRET=<the Token Secret>
```

Restart, then check the log and the config report:

```bash
docker compose logs homelab-registry-mcp | grep patchmon_webhook
docker exec homelab-registry-mcp registry-mcp-config-check
```

**Expected result:** A `patchmon_webhook_registered` line, with
`patchmon_api_configured=true` if you did Step 3.  
**If it fails:** A `patchmon_webhook_disabled` line names what's missing: the
secret, the base URL, an SMTP provider, or the playbook. When it's refused,
every route is left unmounted, so the endpoint and the links all 404.

---

#### Step 5: Route the three paths through Traefik, without ForwardAuth

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

#### Step 6: Send a signed test alert before involving PatchMon

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
nothing ran. (This test body carries no PatchMon host id, so even with Step 3
done the email says PatchMon details are unavailable.)  
**If it fails:** A 401 means the signature didn't match. Check that the secret
matches, and that nothing between curl and the server rewrites the body. A 502
means the email couldn't be sent: check the SMTP settings.

---

#### Step 7: Point PatchMon at the endpoint

In PatchMon, add a **Webhook** notification destination (Admin Guide →
Notification Destinations):

- URL: `https://registry-mcp.example.com/webhooks/patchmon`
- Signing secret: the secret from Step 1
- Route the `host_security_updates_exceeded` and/or
  `host_pending_updates_exceeded` alerts to it. Other events are acknowledged
  and ignored (`PATCHMON_WEBHOOK_EVENTS` controls which count).

**Expected result:** The next threshold alert produces an approval email that
names the host and PatchMon's summary and, with Step 3 done, a "What PatchMon
last saw on this host" section listing the pending packages.

---

### Optional: PatchMon's Ansible dynamic inventory

PatchMon publishes an inventory plugin, `patchmon.dynamic_inventory`
([PatchMon-ansible](https://github.com/PatchMon/PatchMon-ansible)). It turns
PatchMon's host list into an Ansible inventory: each host is keyed by its
PatchMon `hostname`, `ansible_host` is set to the IP PatchMon has for it, and
each PatchMon host group becomes an Ansible group. It reads the same
`/api/v1/api/hosts` endpoint with the same kind of `host:get` credential (use a
separate one, so either can be revoked alone).

Adding it alongside your static inventory makes every PatchMon host name
resolve in the approval check with no renaming. That is a change to your
homelab repo's `ansible.cfg`, not to this server. Weigh these first:

- **PatchMon decides where the patch lands.** Each host's IP comes from what its
  agent reported, and Ansible connects there with the control-plane SSH key. A
  compromised agent could point a patch run at another machine. The approval
  email names the host, not the address.
- **One machine, two names.** If your static inventory uses short names and
  PatchMon reports FQDNs, the same machine appears twice. A plain name keeps
  working, but `all` (and `hardware-discover-now`) reaches the machine twice.
- **Wider reach.** `hardware-discover-now` runs `ansible all -m setup`, so it
  would then fact-gather every PatchMon host too.
- **Two sources for one host.** `ansible-inventory-sync-node` (ADR-015) writes
  the static YAML inventory from the hardware registry; the plugin reads
  PatchMon. They can disagree on a host's address.
- **Group names become patterns.** A PatchMon host group can share a name with a
  host. The approval check refuses any name that doesn't resolve to exactly
  itself, so this can't widen a patch, but it can make one fail.

If you add it, install the collection where Ansible runs (this server's
container, for approvals), keep `verify_ssl: true`, and read the credential from
the environment rather than committing it:

```yaml
# patchmon_inventory.yml
plugin: patchmon.dynamic_inventory
api_url: https://patchmon.example.com/api/v1/api/hosts/
api_key: "{{ lookup('env', 'PATCHMON_INVENTORY_KEY') }}"
api_secret: "{{ lookup('env', 'PATCHMON_INVENTORY_SECRET') }}"
verify_ssl: true
```

---

### Verification

- [ ] `config_status` lists `Patchmon webhook` (and `PatchMon API`, if you did
      Step 3) under `features_on` with no problems
- [ ] The Step 6 test produced an email, and its Cancel link worked once, then
      showed "already used" when opened again
- [ ] A real PatchMon alert produced an email, with the PatchMon details section
      if you did Step 3

---

### Rollback

1. Set `PATCHMON_WEBHOOK_ENABLED=false` and restart. Every route unmounts, and
   links in emails already sent start returning 404.
2. Remove the webhook destination in PatchMon, and revoke the API credential
   from Step 3 under Settings → Integrations.

---

### Escalation

| Symptom | Likely cause |
|---|---|
| Endpoint 404s | Not registered: read the `patchmon_webhook_disabled` log line |
| 401 on every delivery | Secret mismatch, or a proxy rewrote the body (the HMAC covers the exact bytes) |
| 200 `ignored`, "not a plain inventory host name" | The alert names a friendly name. Set up Step 3, or rename the host in PatchMon |
| 200 `ignored`, "looking up host … failed" | The API credential is wrong (401), lacks `host:get` or is IP-restricted (403), or PatchMon was unreachable |
| 200 `ignored`, other reasons | Event not in `PATCHMON_WEBHOOK_EVENTS`, or PatchMon's recorded hostname isn't a plain name either |
| 200 `skipped` | An approval for the same alert is already pending |
| Email says "PatchMon details: unavailable" | The alert had no host id, or a read failed or timed out; the reason is on that line |
| Link page says "already used" | Someone (or something) confirmed it already; each link works once |
| Result email says "exactly one inventory host" | The host name isn't in your inventory, or names a group |
| Result email says "ansible-playbook exited" | The playbook ran and failed; its output tail is in the email and on the approval row |
