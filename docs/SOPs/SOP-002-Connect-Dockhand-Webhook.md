# SOP: Connect Dockhand to the Registry Update Webhook

**Owner:** the maintainer  
**Frequency:** Once per Dockhand instance  
**Last Updated:** 2026  
**Status:** Current

---

### Purpose

Point a Dockhand instance at this server's `/webhooks/dockhand` endpoint so its
container-update and CVE-scan alerts become staged proposals — pull requests a
human reviews — instead of being read and acted on by hand.

See [ADR-010](../ARDs/ADR-010-Dockhand-Update-Webhook.md) for the full design
rationale; this SOP is just the setup steps.

---

### When to use it

- You run Dockhand somewhere on the LAN and want its update detection to feed
  the proposal engine.
- You are re-pointing an existing Dockhand channel after rotating the shared
  secret or moving the registry to a new host.

---

### Prerequisites

- [ ] The write path is configured — `GIT_BASE_URL`, `GIT_TOKEN`, `GIT_REPO` are
      all set (without them the endpoint accepts alerts but every one returns
      `write path not configured`)
- [ ] The server is not in read-only mode — ask your MCP client to run
      `system_health_check` and confirm it reports `"mode": "read-write"`
- [ ] Dockhand can reach the registry host on the network (a different host is
      the normal case — Dockhand often runs on a NAS or a workload node)
- [ ] The container names Dockhand reports match registry service names, and
      those services have a `host` set — matching is by exact name, and without
      a `host` there is no `nodes/<node>/<service>/compose.yaml` path to patch

---

### Procedure

#### Step 1: Generate a shared secret

Dockhand does not sign its webhook bodies, so a shared secret presented as a
request header is the authentication mechanism.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Expected result:** A long random string. Keep it — you will paste it twice.

---

#### Step 2: Enable the endpoint on the registry

Add to the registry's `.env`:

```bash
DOCKHAND_WEBHOOK_ENABLED=true
DOCKHAND_WEBHOOK_SECRET=<the secret from Step 1>
```

Restart and watch the startup log:

```bash
docker compose up -d
docker compose logs homelab-registry-mcp | grep dockhand_webhook
```

**Expected result:** A `dockhand_webhook_registered` line naming the path.  
**If it fails:** A `dockhand_webhook_disabled_no_secret` line means the secret is
empty or whitespace-only. The route is deliberately left unmounted in that case
rather than served without auth, so the endpoint will 404 until it is set.

---

#### Step 3: Verify the endpoint before involving Dockhand

Run this on the registry host. It isolates registry-side problems from
Dockhand-side ones, which is worth the extra minute.

```bash
# Authorized, but naming a container the registry doesn't know
curl -sS -X POST http://localhost:8765/webhooks/dockhand \
  -H 'X-Dockhand-Token: <the secret from Step 1>' \
  -H 'Content-Type: application/json' \
  -d '{"event":"update_available","container":"definitely-not-a-real-service"}'

# No token
curl -sS -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8765/webhooks/dockhand \
  -H 'Content-Type: application/json' -d '{}'
```

**Expected result:** The first returns `200` with
`{"skipped": "no matching service", ...}`. The second prints `403`.  
**If the first returns 404:** See Step 2. **If it returns 403 with a read-only
message:** the startup health check is failing — run `system_health_check`.

---

#### Step 4: Add the notification channel in Dockhand

In Dockhand: **Settings → Notifications → Add channel**.

- **Name:** `homelab-registry-mcp`
- **Type:** `Webhooks`
- **Status:** Enabled
- **Webhook URLs (one per line):** one of the two forms below

Direct to the published port:

```
json://<registry-host>:8765/webhooks/dockhand?+X-Dockhand-Token=<secret>
```

Or through Traefik, if the registry is fronted by it:

```
jsons://registry-mcp.<your-domain>/webhooks/dockhand?+X-Dockhand-Token=<secret>
```

Two things about that URL are not obvious:

- **`json://` is not a typo.** Dockhand's webhook channel takes Apprise-style
  schemes, not plain `http(s)://` URLs. `json://` is Apprise's generic-JSON
  channel over HTTP; `jsons://` is the same over HTTPS.
- **The `+` prefix promotes a query parameter into an HTTP request header.**
  `?+X-Dockhand-Token=abc` sends `X-Dockhand-Token: abc` and does not appear in
  the query string. Without the `+` the secret travels as an ordinary query
  parameter, the endpoint never sees a token, and every delivery 403s.

The secret is stored in Dockhand's own channel configuration and is readable by
anyone with access to that UI. Treat access to Dockhand's settings as equivalent
to holding the secret, and rotate both sides together.

**Expected result:** The channel saves without error.

---

#### Step 5: Press Test, then read the registry log

Use the dialog's **Test** button, then on the registry host:

```bash
docker compose logs -f homelab-registry-mcp | grep dockhand
```

**Expected result:** A `dockhand_alert_ignored` line. That is success, not
failure — a test notification is not an update alert, so there is nothing to
propose. The line proves reachability, authentication, content type and payload
parsing all at once.  
**If nothing appears:** Dockhand cannot reach the host. Check firewall rules and
that you used the reachable address, not `localhost`.  
**If you see 403s:** see the Troubleshooting table below.

---

#### Step 6: Choose which events fire

In the same dialog, open the **System events** tab and enable the update and
vulnerability-scan events.

Container start/stop/health events are acknowledged and ignored by the endpoint,
so leaving them enabled is harmless but adds no-op traffic to the log.

**Expected result:** Only update and CVE events are selected.

---

#### Step 7: Find out what your Dockhand actually sends

This step exists because Dockhand's generic-JSON body carries image references
inside a prose `message` field, and its documented sample uses **digests**
(`image=sha256:...`). A digest names no version that can be written into a
compose file, so the endpoint acknowledges such an alert and stops rather than
guessing a tag and opening a wrong pull request. Whether a given Dockhand build
sends digests or tags is worth confirming rather than assuming.

Temporarily add to `.env` and restart:

```bash
DOCKHAND_WEBHOOK_LOG_RAW_PAYLOAD=true
```

Trigger a real update alert (or wait for one), then:

```bash
docker compose logs homelab-registry-mcp | grep dockhand_webhook_raw_payload
```

Remove the setting and restart when you have the sample.

**Expected result:** One log line containing the verbatim request body. If its
image references carry tags (`plex:1.32.1`), update proposals will work. If they
are digests, updates will keep being ignored by design — that is the known
limitation recorded in ADR-010, not a misconfiguration.

This setting logs the body as a single string, so the usual field-name secret
redaction does not reach anything inside it. Leave it off outside this step.

---

#### Step 8: Take the first real run in dry-run

Before letting it open a pull request against the homelab repo:

```bash
PROPOSAL_DRY_RUN=true
```

**Expected result:** An actionable alert returns the generated patch in the
response and log without creating a branch, commit, or PR. Review the patch,
then remove the setting.

---

### Verification

- [ ] `dockhand_webhook_registered` appears at startup
- [ ] An unauthenticated `POST` returns `403`
- [ ] Dockhand's Test button produces a `dockhand_alert_ignored` line
- [ ] A real update alert produces `dockhand_alert_received` naming the container,
      image and tags
- [ ] `proposal_list_open` shows the staged proposal, and the PR exists in the
      homelab repo under the `homelab-registry-mcp` label

---

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `404` on every delivery | Route never mounted — flag off, or secret unset/whitespace-only | Check for `dockhand_webhook_disabled_no_secret` in the startup log; see Step 2 |
| `403 unauthorized` | `+` prefix omitted, so the token went as a query parameter instead of a header | Re-check the URL against Step 4 |
| `403 unauthorized` | Secret mismatch between `.env` and the Dockhand channel | Re-paste both from the same source; surrounding whitespace is tolerated, so a stray newline is not the cause |
| `403 ... read-only mode` | Startup health check failed (missing Git repo, `ansible.cfg`, or SSH key) | Run `system_health_check`, fix what it names, restart |
| `400 expected application/json` | Sender is not using the `json://`/`jsons://` scheme | Confirm the URL scheme; enable Step 7's logging to see the content type actually sent |
| `400 invalid JSON body` | Body is not JSON | Enable Step 7's logging |
| `422 payload validation failed` | Body matched neither the structured nor the generic shape | Enable Step 7's logging and compare against ADR-010 |
| `200 {"ignored": "...digest-only..."}` | Expected for a digest-carrying body | See Step 7 |
| `200 {"ignored": "...not actionable"}` | A container state event, not an update | Narrow the selection in Step 6 |
| `200 {"skipped": "no matching service"}` | Dockhand's container name differs from the registry service name | Compare against `registry_list_services`; rename one side |
| `200 {"error": "cannot resolve a target file ... (unknown host/node)"}` | The matched service has no `host` | Set it with `registry_update_service`, or link the node with `hardware-link-service` |
| `200 {"error": "write path not configured ..."}` | `GIT_*` incomplete | See Prerequisites |
| `413 payload too large` | Body exceeds the cap | Raise `DOCKHAND_WEBHOOK_MAX_BODY_BYTES` |
| CVE alerts never arrive | Severity below the floor | Lower `DOCKHAND_WEBHOOK_VULNERABILITY_MIN_SEVERITY` |

---

### Rollback

Disable the endpoint:

```bash
DOCKHAND_WEBHOOK_ENABLED=false
```

Restart, then set the Dockhand channel's Status to disabled, or delete it. No
registry state is written by this integration, so nothing needs cleaning up;
proposals already opened remain as ordinary pull requests and can be closed with
`proposal_cancel`.

---

### Notes

- The endpoint never writes to the registry and never touches a container. The
  pull request and a human merge remain the only path to a change.
- Alert-to-service matching is by exact container name, deliberately — the
  reasoning layer is not consulted on the receive path, so an unmatched name is
  skipped rather than fuzzily guessed.
- All the environment variables used here are documented in the Dockhand block of
  [`.env.example`](../../.env.example) and in
  [CLAUDE.md](../../CLAUDE.md)'s environment-variable table.
