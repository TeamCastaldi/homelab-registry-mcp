# SOP: Redeploy the Registry on Every Release

**Owner:** the maintainer  
**Frequency:** Once per deployment, and again after rotating the webhook secret  
**Last Updated:** 2026  
**Status:** Current

---

### Purpose

Have each release reach the running registry without a manual redeploy. When a
`v*` tag is pushed (release-please does this when its release PR merges), the
`Publish Docker image` workflow builds and pushes the image, then its `redeploy`
job calls the webhook of the Dockhand git stack that runs the registry. Dockhand
pulls the new image and recreates the container.

The trigger is the finished image push, not the release PR's merge. The image
takes about five minutes to build, and a redeploy started at the merge pulls the
previous release.

---

### When to use it

- The registry runs as a Dockhand git stack and you want releases to deploy
  themselves.
- You rotated the stack's webhook secret, or recreated the stack (its ID, and so
  its webhook URL, changes).

This only works from this repository's own publish workflow. If you run a
release someone else publishes, use Dockhand's scheduled update check instead
(**Settings → Environments → Updates**, with automatic deployment on). It needs
no webhook, but it deploys on its schedule, not right away.

---

### Prerequisites

- [ ] Dockhand v1.0.46 or later (the version this procedure was checked against)
- [ ] The registry is a Dockhand **git stack**: created from a repository, not
      from pasted compose. Only git stacks have a webhook.
- [ ] The stack's image tag moves with releases. With the usual
      `image: ghcr.io/teamcastaldi/homelab-registry-mcp:${REGISTRY_MCP_VERSION:-latest}`,
      leave `REGISTRY_MCP_VERSION` unset or set it to `latest`. A pinned version
      redeploys the same version every time. Image tags have no `v`: a `v1.10.1`
      release is published as `1.10.1`, `1.10`, and `latest`.
- [ ] Dockhand is reachable from the internet over HTTPS with a publicly trusted
      certificate. GitHub's hosted runners make the call, so a LAN-only address
      won't work.
- [ ] Nothing that redirects to a login page (Authentik forward auth, for one)
      sits in front of `/api/git/stacks/<id>/webhook`. The runner can't log in.
- [ ] You can add Actions secrets to this repository.

---

### Procedure

#### Step 1: Turn on the stack's webhook and redeploy options

In Dockhand, open the registry's stack and edit its git settings. Set:

- **Enable webhook:** on. Dockhand fills in a **Webhook secret**; keep it or
  generate a new one.
- **Re-pull images:** on. Without it, Dockhand redeploys the image it already
  has.
- **Force redeployment:** on. The webhook syncs the homelab repo first and
  skips the deploy when nothing changed there, and a registry release changes
  nothing there.

Save, then copy the **Webhook URL**
(`https://<dockhand-host>/api/git/stacks/<id>/webhook`) and the secret.

**Expected result:** The dialog saves without a "webhook secret is required" error.

Force redeployment also applies to the stack's scheduled sync, if that's on:
every sync then runs a deploy. The deploy only recreates the container when
its image or configuration changed.

---

#### Step 2: Call the webhook from outside your network

Run this from a machine that isn't on your LAN (a laptop on a phone hotspot
works), since GitHub's runners call from the internet. Dockhand checks the same
signature GitHub sends: an HMAC-SHA256 of the raw body, keyed by the secret.

```bash
URL='https://<dockhand-host>/api/git/stacks/<id>/webhook'
read -rs SECRET        # paste the webhook secret; it stays out of shell history
BODY='{}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')
curl -sS -X POST "$URL" \
  -H 'Content-Type: application/json' \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  -d "$BODY"
```

**Expected result:** After a minute or two (Dockhand answers once the deploy
finishes), `{"success":true,...}`, and no `"skipped":true`.  
**If it fails:** See the Troubleshooting table; the HTTP status codes map the
same way as the job's messages.

---

#### Step 3: Add the two Actions secrets

In this repository: **Settings → Secrets and variables → Actions → New
repository secret**.

| Name | Value |
|------|-------|
| `DOCKHAND_REDEPLOY_URL` | The webhook URL from Step 1 |
| `DOCKHAND_REDEPLOY_SECRET` | The webhook secret from Step 1 |

Store the URL as a secret too, not a variable. This repository's run logs are
public, and GitHub masks secrets in them. The job skips itself while both are
unset, and fails if only one is set.

---

#### Step 4: Run it once by hand

**Actions → Publish Docker image → Run workflow**, on `main`. From a branch the
build job is skipped and only `redeploy` runs, redeploying whatever the stack
points at. The same button is a one-click redeploy later.

**Expected result:** The `redeploy` job passes and logs
`Dockhand redeployed the stack for main`, and an MCP client's `health` call
reports the newest release's version.

---

### Verification

- [ ] Step 2's call returns `"success":true` from outside the LAN
- [ ] The manual run in Step 4 passes, and `health` reports the newest release
- [ ] The next release's `Publish Docker image` run passes both `push` and
      `redeploy`, and `health` reports that release a few minutes after the tag

---

### Troubleshooting

| Job message | Likely cause | Fix |
|-------------|--------------|-----|
| `... are not set; skipping` | The secrets are missing | Step 3 |
| `Set both ... or neither` | Only one secret is set | Step 3 |
| `returned 401: the signature didn't verify` | The secret doesn't match the stack's, or the stack has none | Re-copy the secret from Step 1 into `DOCKHAND_REDEPLOY_SECRET` |
| `returned 403: the webhook is turned off` | **Enable webhook** is off | Step 1 |
| `returned 404: ... no git stack with the ID` | Wrong stack ID, or the stack was recreated | Re-copy the Webhook URL from Step 1 |
| `skipped the redeploy because the homelab repo hasn't changed` | **Force redeployment** is off | Step 1 |
| `The webhook request was redirected` | Forward auth or a redirect sits in front of Dockhand | Exempt the webhook path; see Notes |
| `Could not reach Dockhand` | Not reachable from the internet, a firewall, or a certificate problem | Repeat Step 2 from outside the LAN |
| `Dockhand's deploy failed: ...` | The compose deploy failed | Read the stack's deploy log in Dockhand |
| Job passes but `health` shows the old version | `REGISTRY_MCP_VERSION` is pinned, or **Re-pull images** is off | Prerequisites; Step 1 |

---

### Rollback

Delete the two Actions secrets. The job then skips itself on every release. To
also close the endpoint, turn off **Enable webhook** on the stack. Do that after
deleting the secrets, or every release's `redeploy` job fails with a 403.

---

### Notes

- The webhook redeploys the stack as Dockhand has it. It builds nothing and
  writes nothing to Git.
- Dockhand checks the signature but has no replay protection: anyone who
  captured one signed request could send it again to trigger another redeploy
  of the same stack. HTTPS keeps it from being read in transit, and the job
  refuses a non-`https://` URL.
- Only the webhook path needs to be public. Dockhand holds the Docker socket of
  every host it manages, so a separate Traefik router for just
  `PathRegexp(`^/api/git/stacks/[0-9]+/webhook$`)`, with the rest of Dockhand
  kept LAN-only or behind forward auth, exposes much less than the whole UI.
