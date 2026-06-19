# SOP: Deploy a New Service to an Onboarded Node

**Owner:** Nathan Castaldi  
**Frequency:** As needed  
**Last Updated:** 2026-06-09  
**Status:** Interim — supersedes when Ansible bootstrap role (Phase E) is complete

---

### Purpose

Deploy a new service to a node that has already been onboarded (Docker installed,
GitHub Actions runner registered). Covers both third-party public images and
TeamCastaldi private images published to ghcr.io.

---

### Prerequisites

- [ ] Target node is onboarded (Docker running, Actions runner active)
- [ ] Homelab repo cloned on your workstation and git-crypt unlocked
- [ ] You know the image source: public registry or private ghcr.io

---

### Procedure

#### Step 1: Determine image source

Is the image published to a public registry (Docker Hub, ghcr.io public)?

- **Yes → Path A** (public image, no auth required)
- **No, it's a private ghcr.io image → Path B** (private image, PAT required)

---

### Path A — Public Image

#### Step A1: Create the stack directory on your workstation

```bash
mkdir -p ~/homelab/nodes/<node>/<service>
```

**Expected result:** Directory exists.

---

#### Step A2: Create `compose.yaml`

Create `~/homelab/nodes/<node>/<service>/compose.yaml` with the service definition.

Key rules:
- Reference the image by explicit version tag, never `latest`
- Use anonymous Docker volumes (no bind mounts until node is fully onboarded)
- Port mappings are temporary — add a `# temporary` comment on the ports line
- Do not include a `build:` key

**Expected result:** Valid compose file referencing a pinned public image.

---

#### Step A3: Create `.env` if secrets are needed

Create `~/homelab/nodes/<node>/<service>/.env` and populate all required secrets.

```bash
touch ~/homelab/nodes/<node>/<service>/.env
```

Do not share or paste secret values into any chat or terminal session that is
being observed or logged.

**Expected result:** `.env` file exists with all required values populated.

---

#### Step A4: Commit and push

```bash
cd ~/homelab
git add nodes/<node>/<service>/
git commit -m "deploy: add <service> to <node>"
git push
```

**Expected result:** Push succeeds. GitHub Actions workflow triggers automatically.

---

#### Step A5: Verify deployment

1. Open the Actions tab on `github.com/<your-org>/homelab`
2. Confirm the `Deploy Changed Stacks` workflow triggered
3. Confirm the `deploy (<node>/<service>)` job completed successfully
4. Verify the service is reachable at `http://<node-ip>:<port>`

**Expected result:** Workflow passes, service responds.  
**If it fails:** See Troubleshooting section.

---

### Path B — Private ghcr.io Image

#### Step B1: Confirm the image exists in ghcr.io

Navigate to `github.com/orgs/TeamCastaldi/packages` and confirm the expected image
and version tag are published before proceeding.

**Expected result:** Image exists with the correct version tag.  
**If missing:** Check that the release workflow ran successfully in the source repo.

---

#### Step B2: Authenticate Docker on the target node

On the target node, log Docker into ghcr.io using a GitHub Personal Access Token
with `read:packages` scope:

```bash
echo <PAT> | docker login ghcr.io -u <github-username> --password-stdin
```

This writes credentials to `~/.docker/config.json` and persists across reboots.
Only needs to be done once per node.

**Expected result:** `Login Succeeded`  
**If it fails:** Confirm the PAT has `read:packages` scope and has not expired.

---

#### Step B3: Follow Path A steps A1 through A5

Proceed identically to Path A. The image reference in `compose.yaml` should be
the full ghcr.io path:

```yaml
image: ghcr.io/teamcastaldi/<service>:<version>
```

---

### Verification

- [ ] Actions workflow completed with no errors
- [ ] Service is reachable at the expected address and port
- [ ] If the service has a web UI, confirm it loads without errors
- [ ] If the service has a database, confirm it initialized cleanly (check logs)

```bash
# Check logs on the target node if needed
ssh <node> "docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml logs --tail=50"
```

---

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Workflow did not trigger | Push didn't include files under `nodes/` | Confirm `git push` succeeded and files are in the right path |
| `error: untracked working tree files would be overwritten` | Files were created directly on the node before the workflow ran | SSH into node, delete the conflicting files, re-run the workflow |
| `unauthorized` pulling image | Node not authenticated to ghcr.io | Follow Step B2 |
| `unauthorized` pulling image | PAT expired | Generate a new PAT, repeat Step B2 |
| Service starts but immediately exits | Missing required env var | Check `docker logs` on the node, add missing var to `.env`, recommit |
| Database healthcheck fails | DB init taking longer than expected | Increase `retries` in the healthcheck, or check DB logs directly |
| Port already in use | Another service on the node occupies that port | Choose a different host port in `compose.yaml` |

---

### Rollback

To remove a service:

```bash
# On the target node
ssh <node> "cd ~/homelab/nodes/<node>/<service> && docker compose down"
```

Then remove the stack from the homelab repo:

```bash
cd ~/homelab
git rm -r nodes/<node>/<service>/
git commit -m "remove: <service> from <node>"
git push
```

---

### Notes

- This SOP assumes the node was onboarded manually. Once Phase E (Ansible bootstrap
  role) is complete, node onboarding will be automated and this SOP will be
  revised or retired.
- Bind mounts and NFS paths are not used until a node is fully onboarded into the
  NAS/appdata mount structure. Use anonymous Docker volumes in the interim.
- Once a service is behind Traefik, remove the `ports:` mapping and the
  `# temporary` comment. See `docs/deployment/traefik-reverse-proxy.md`.
