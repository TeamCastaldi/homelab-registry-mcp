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
- [ ] A `caronc/apprise-api` instance is deployed and reachable from both
      Dockhand and this registry (Step 4 covers why and how — Dockhand's own
      built-in webhook sender cannot authenticate to this endpoint directly)

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

#### Step 4: Route through a real Apprise instance

Dockhand's built-in **Webhooks** channel accepts Apprise-style scheme names
(`json://`, `discord://`, ...) as a UI convenience, but does not run requests
through the real Apprise engine — testing against a live instance (a
`webhook.site` capture) showed a native `node`-flavored sender, a payload
shape Apprise never produces, and a `+X-Dockhand-Token=<secret>` query
parameter that arrives as a literal, inert query-string entry — the `+` is
stripped, but it is never converted into a header. No URL-encoding variant
fixes this; the capability just isn't implemented in that code path. So the
secret has to travel through something that *does* run real Apprise.

**4a. Deploy `caronc/apprise-api`** somewhere both Dockhand and this registry
can reach — a small sidecar alongside Dockhand is the natural place. As an
ordinary `nodes/<node>/apprise-api/compose.yaml` entry in your private
homelab repo (adapt to your own conventions; this is not a file this
public repo ships):

```yaml
services:
  apprise-api:
    image: caronc/apprise:latest
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - apprise-config:/config
volumes:
  apprise-config:
```

**4b. Store the real Apprise URL** in apprise-api's own config UI
(`http://<apprise-api-host>:8000/`) under a config key, e.g. `dockhand`. This
is where the `+` header syntax actually works, because apprise-api runs the
genuine Apprise library:

```
json://<registry-host>:8765/webhooks/dockhand?+X-Dockhand-Token=<secret>
```

or, through Traefik:

```
jsons://registry-mcp.<your-domain>/webhooks/dockhand?+X-Dockhand-Token=<secret>
```

**4c. Point Dockhand at the sidecar, not at the registry.** In Dockhand:
**Settings → Notifications → Add channel**.

- **Name:** `homelab-registry-mcp`
- **Type:** `Webhooks`
- **Status:** Enabled
- **Webhook URLs (one per line):**

```
apprise://<apprise-api-host>:8000/dockhand
```

This is the exact escape hatch Dockhand's own dialog footer documents for a
provider outside its built-in list ("Run a caronc/apprise-api server,
configure the provider there, and point Dockhand at it with
`apprise://host/key`").

**If that doesn't produce a working header** — apprise-api's `/notify/<key>`
endpoint requires a `body` field, and Dockhand's native payload uses
`message` instead — point Dockhand at apprise-api's HTTP endpoint directly,
with field remapping, as a fallback:

```
json://<apprise-api-host>:8000/notify/dockhand?:message=body
```

The secret lives in apprise-api's stored config, not in anything Dockhand
holds. Treat access to apprise-api's UI as equivalent to holding the secret,
and rotate both sides together if it's ever exposed.

**A query-string token was considered and rejected as the default guidance
here.** Dockhand's native sender *can* deliver `?X-Dockhand-Token=<secret>`
as a plain query parameter with no sidecar at all — but a reverse proxy
(Traefik included) commonly logs the full request line, query string
included, by default, while header values are not logged unless explicitly
configured. That trades a standing secret leak in your access logs for
skipping one extra container. If you don't run access logging, that trade
may be yours to make, but it is not the path this SOP walks through — see
[ADR-010](../ARDs/ADR-010-Dockhand-Update-Webhook.md) for the full reasoning.

**Expected result:** The Dockhand channel saves without error, and a Test
notification shows as delivered in apprise-api's own log/UI.

---

#### Step 5: Press Test, then read the registry log

The delivery now makes two hops — Dockhand → apprise-api → this registry —
so check apprise-api's own delivery log/UI first if nothing shows up on the
registry side; that tells you which hop failed. Use the dialog's **Test**
button, then on the registry host:

```bash
docker compose logs -f homelab-registry-mcp | grep dockhand
```

**Expected result:** A `dockhand_alert_ignored` line. That is success, not
failure — a test notification is not an update alert, so there is nothing to
propose. The line proves apprise-api reached the registry, authenticated,
and that content type and payload parsing all worked.  
**If nothing appears on the registry side but apprise-api shows the delivery
as sent:** the header still isn't reaching this endpoint — recheck the
stored Apprise URL in apprise-api against Step 4b, and confirm apprise-api
itself can reach the registry host.  
**If apprise-api never shows the delivery at all:** Dockhand cannot reach
apprise-api. Check firewall rules and that you used a reachable address, not
`localhost`.  
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
| `403 unauthorized`, and Dockhand points directly at the registry (no apprise-api) | Dockhand's built-in Webhooks channel never delivers the header — this is expected, not a config mistake | Route through apprise-api per Step 4; no URL variant against Dockhand directly fixes this |
| Dockhand's Test shows delivered, but apprise-api never logs a delivery | apprise-api unreachable from Dockhand, or the `apprise://host/key` URL is malformed | Confirm the host:port and key against Step 4c; check apprise-api's own log |
| apprise-api logs an attempt but the registry never sees it (or still 403s) | The stored Apprise URL in apprise-api doesn't match Step 4b, or the secret doesn't match `.env` | Re-check the stored URL in apprise-api's config UI; re-paste both secrets from the same source |
| apprise-api delivery fails with a body/format error | `apprise://host/key` didn't produce a request apprise-api's `/notify/<key>` accepts (it needs `body`, Dockhand sends `message`) | Use the `:message=body` remapped `json://.../notify/<key>` form from Step 4c instead |
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
