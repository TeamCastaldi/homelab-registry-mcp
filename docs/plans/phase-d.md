# Phase D — Service Migration

## Context

Phase D moves registry-mcp from the workload node to the dedicated control-plane node (`<control-plane-ip>`). Traefik stays on the workload node and routes `registry-mcp.<your-domain>` to the control-plane node via a static backend — no Docker label magic, just an IP:port pointer. The prior orchestration tool is left running and untouched.

Known infrastructure:
- **Control-plane node**: Pi at `<control-plane-ip>`, registry-mcp will run here
- **Workload node**: Traefik on `proxy-net`, dynamic config at `/mnt/appdata/<orchestrator>/repos/<workload-node>/homelab/nodes/<workload-node>/core/traefik/dynamic/` (managed by the prior orchestration tool's git repo, mounted `:ro` into Traefik)
- **TLS**: Cloudflare DNS challenge (`certResolver: cloudflare`)
- **Domain**: `<your-domain>`
- **Old registry-mcp**: was on the workload node, already shut down — no data migration needed (starting fresh)

---

## Part 1 — docker-compose.yml (commit to repo, pull on the control-plane node)

Two changes needed for control-plane deployment:

**Port binding**: Change `127.0.0.1:8765:8765` → `0.0.0.0:8765:8765`
Traefik on the workload node reaches the control-plane node over LAN. Localhost-only binding blocks it.

**Remove Traefik Docker labels and network**: The existing labels assume Traefik is on the same Docker network as registry-mcp. On the control-plane node, Traefik is on a different host entirely — routing is via static backend, not Docker label discovery. The `traefik` network serves no purpose here and would need to be manually created before `docker compose up` would succeed.

Replace the labels and network blocks with a comment explaining the routing model.

**File**: `docker-compose.yml`

---

## Part 2 — Traefik static backend on the workload node (manual step)

Create a new dynamic config file in the directory Traefik already watches:

**Path on the workload node**: `/mnt/appdata/<orchestrator>/repos/<workload-node>/homelab/nodes/<workload-node>/core/traefik/dynamic/registry-mcp.yml`

```yaml
# registry-mcp.yml
# Routes registry-mcp.<your-domain> → the control-plane node
# No auth middleware — MCP clients cannot follow Authentik redirect flows.
# LAN-only by design; do not add ForwardAuth until a token/mTLS strategy lands.
http:
  routers:
    registry-mcp:
      rule: "Host(`registry-mcp.<your-domain>`)"
      entrypoints:
        - websecure
      tls:
        certResolver: cloudflare
      service: registry-mcp-svc

  services:
    registry-mcp-svc:
      loadBalancer:
        servers:
          - url: "http://<control-plane-ip>:8765"
```

Traefik watches `/dynamic` with a file provider — the new file is picked up immediately, no restart needed.

**Also commit this file** to whatever git remote backs the workload node's homelab checkout so a sync from the prior orchestration tool doesn't wipe it. Check the remote with:
```bash
git -C /mnt/appdata/<orchestrator>/repos/<workload-node>/homelab remote -v
```

---

## Part 3 — Seed the new homelab repo structure (on the control-plane node)

The new `<your-org>/homelab` repo at `/opt/homelab` needs the same path structure so Phase E (Ansible) has a home. Create the skeleton now:

```bash
mkdir -p /opt/homelab/nodes/<workload-node>/core/traefik/dynamic
cp <the registry-mcp.yml we just wrote> /opt/homelab/nodes/<workload-node>/core/traefik/dynamic/registry-mcp.yml
cd /opt/homelab && git add . && git commit -m "chore: seed nodes/<workload-node> traefik dynamic config" && git push
```

This makes the new homelab repo the eventual source of truth for Traefik dynamic config, which Ansible (Phase E) will deploy.

---

## Part 4 — Deploy on the control-plane node

```bash
cd ~/homelab-registry-mcp
git pull                          # get the docker-compose.yml changes
docker compose pull               # pull latest image from ghcr.io
docker compose up -d              # start the container
docker compose logs -f registry-mcp   # watch for "scheduler_started"
```

---

## Part 5 — DNS

`registry-mcp.<your-domain>` needs a DNS entry pointing to the workload node (Traefik's host), not the control-plane node directly. Check whether this is:
- **Local DNS** (AdGuard/Pi-hole on the LAN) — add an A record for `registry-mcp.<your-domain>` → the workload node's LAN IP
- **Cloudflare public DNS** — add an A record there (but ADR says LAN-only, so local DNS is preferred)

Determine which DNS is authoritative for LAN devices before proceeding.

---

## Verification

1. From the workload node: `curl -v http://<control-plane-ip>:8765/` — confirms the control-plane node's port is reachable
2. From any LAN device: `curl -v https://registry-mcp.<your-domain>/` — confirms Traefik routing + TLS
3. Traefik dashboard at `https://proxy.<your-domain>/dashboard/` — confirm `registry-mcp` router appears
4. `docker compose logs registry-mcp` on the control-plane node — confirm `scheduler_started` and no errors
5. Call `secrets_status` via an MCP client — confirm it returns repo state

---

## Files touched

| File | Change |
|---|---|
| `docker-compose.yml` | Port → `0.0.0.0:8765`, remove Traefik labels + network |
| Workload node: `.../dynamic/registry-mcp.yml` | New — Traefik static backend (manual step on the workload node) |
| `/opt/homelab/nodes/<workload-node>/core/traefik/dynamic/registry-mcp.yml` | New — same file seeded into new homelab repo |

---

# Phase C — git-crypt + Secrets Tools (completed)

## Context

Phase C wires secrets management into the homelab control plane. The private homelab repo (containing `.env` files for each service) needs to be created, git-crypt initialized in it, and six `secrets_*` MCP tools implemented so an AI assistant can manage secrets without the operator touching the command line.

This phase depends on Phase B (GitHub migration) being complete. The OOBE (Phase G) will later wrap this capability into a conversational onboarding flow — Phase C builds the foundation those OOBE tools will call.

User decisions captured before planning:
- **Key loading**: hybrid — `SECRETS_KEY_PATH` (file) takes priority; `SECRETS_GIT_CRYPT_KEY` (base64 env var) is the fallback
- **Decrypt output**: smart — `.env` files return `{"KEY": "value"}` dict; unstructured files (YAML, certs, SSH keys) return raw plaintext string
- **Homelab repo**: does not exist yet — Phase C creates it

---

## What git-crypt actually does (brief)

`git-crypt` is a binary that hooks into git's smudge/clean filter system. When you run `git-crypt init`, it generates a symmetric key and installs git hooks. Files matching patterns in `.gitattributes` (e.g. `**/.env filter=git-crypt diff=git-crypt`) are transparently encrypted on `git push` and decrypted on `git pull` — but only if you have the key. Without the key the files appear as binary blobs on GitHub.

`git-crypt unlock <keyfile>` decrypts the working tree in place. `git-crypt lock` re-encrypts it. The MCP tools read/write files after unlocking — they never write a separate plaintext copy.

---

## Part 1 — One-time repo setup (shell script, run once on the control-plane node)

This is NOT an MCP tool. It is a shell script (`scripts/setup-homelab-repo.sh`) that runs once on the control-plane node to bootstrap the homelab repo. The OOBE (Phase G) will eventually replace this with `oobe_create_repo` + `oobe_encrypt_secrets` tool calls.

The script does:
1. Creates a private GitHub repo (e.g. `<your-org>/homelab`) via `gh repo create`
2. Clones it to `SECRETS_REPO_PATH` (default `/mnt/appdata/homelab`)
3. Runs `git-crypt init` in the clone
4. Writes `.gitattributes` with `**/.env filter=git-crypt diff=git-crypt`
5. Creates `nodes/` directory skeleton (one subdir per workload node)
6. Exports the key to `SECRETS_KEY_PATH` (default `/mnt/appdata/secrets/git-crypt.key`), `chmod 400`
7. Makes the initial commit and pushes
8. Prints next steps: store the key in Vaultwarden, set env vars in `.env`

**File**: `scripts/setup-homelab-repo.sh`

---

## Part 2 — Dockerfile

Add `git-crypt` to the Dockerfile so the binary is available inside the container.

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends git-crypt && rm -rf /var/lib/apt/lists/*
```

**File**: `Dockerfile`

---

## Part 3 — Config

Add to `src/registry_mcp/config.py` (following the existing Pydantic `BaseSettings` pattern):

```python
# Secrets / git-crypt
secrets_enabled: bool = Field(default=False)
secrets_repo_path: str | None = Field(default=None)      # path to homelab repo clone
secrets_key_path: str | None = Field(default=None)        # path to exported key file
secrets_git_crypt_key: str | None = Field(default=None)  # base64-encoded key bytes (fallback)
```

Add to `CLAUDE.md` env var table and `.env.example`.

---

## Part 4 — Secrets tools (`src/registry_mcp/tools/secrets.py`)

New file. Exports `register_secrets_tools(mcp: FastMCP, settings: Settings) -> None`.

### Internal helpers (not MCP tools)

**`_key_bytes(settings) -> bytes`**
Load the key using the hybrid strategy: read from `settings.secrets_key_path` first; if absent or unset, base64-decode `settings.secrets_git_crypt_key`; raise `RuntimeError` if neither is set.

**`_repo(settings) -> Path`**
Return `Path(settings.secrets_repo_path)`. Raise `RuntimeError` if not set or path doesn't exist.

**`_run(cmd, cwd) -> tuple[int, str, str]`**
`asyncio.create_subprocess_exec` wrapper. Returns `(returncode, stdout, stderr)`.

**`_is_locked(repo: Path) -> bool`**
Read the first 10 bytes of one file listed by `git-crypt status -e`. If they start with `\x00GITCRYPT`, the repo is locked.

**`_ensure_unlocked(repo, key_bytes) -> None`**
If locked: write key_bytes to a `tempfile.NamedTemporaryFile`, call `git-crypt unlock <tmpfile>`, delete the temp file immediately. If already unlocked, no-op.

**`_parse_dotenv(content: str) -> dict[str, str]`**
Parse `KEY=value` lines (skip comments and blanks). Returns dict.

**`_serialize_dotenv(data: dict[str, str]) -> str`**
Write back as `KEY=value\n` lines, preserving insertion order.

**`_detect_format(path: Path, content: str) -> dict | str`**
If path suffix is `.env` or content matches `KEY=VALUE` majority pattern → return `_parse_dotenv(content)`. Otherwise return raw string.

---

### The six MCP tools

#### `secrets_status() -> dict`
- Check `secrets_enabled`; return `{"error": "..."}` if not.
- Run `git-crypt status` in repo.
- Parse output: lines starting with `    encrypted:` and `not encrypted:`.
- Determine lock state via `_is_locked()`.
- Return:
  ```json
  {
    "locked": true,
    "encrypted_files": ["nodes/workload-01/app/.env"],
    "unencrypted_files": [".gitattributes", "README.md"]
  }
  ```

#### `secrets_encrypt(path: str) -> dict`
- Validate path is relative (no `..` traversal).
- Append `{path} filter=git-crypt diff=git-crypt` to `.gitattributes` if not already present.
- Run `git add .gitattributes` + `git commit -m "chore: encrypt {path}"` in the repo.
- Return `{"encrypted": path, "gitattributes_updated": true}`.
- Note: git-crypt encrypts on the next `git push`; if the file already exists unencrypted in history, document that history is not rewritten.

#### `secrets_decrypt(path: str) -> dict`
- Ensure unlocked via `_ensure_unlocked()`.
- Read `repo / path`.
- Return `{"path": path, "content": _detect_format(path, raw)}`.
- Values are returned to the AI context; no secondary plaintext file is written.

#### `secrets_add(key: str, value: str, path: str) -> dict`
- Ensure path is in `.gitattributes`; if not, call `secrets_encrypt(path)` logic first.
- Ensure unlocked.
- Read current file content (or start empty if file doesn't exist).
- Parse with `_parse_dotenv`, set `data[key] = value`, serialize back.
- Write to `repo / path`.
- Run `git add {path}` (stage, do NOT commit — operator controls commits).
- Return `{"path": path, "key": key, "staged": true}`.

#### `secrets_rotate(path: str) -> dict`
- Ensure unlocked.
- Write current key bytes to temp file A.
- Run `git-crypt init` (generates new key in `.git/git-crypt/`).
- Run `git-crypt export-key <SECRETS_KEY_PATH>.new` (export new key).
- Run `git-crypt lock` then `git-crypt unlock <new-key>`.
- Delete temp file A.
- Return `{"rotated": true, "new_key_path": "...", "warning": "Old key still decrypts historical commits. Store new key and discard old key."}`.
- Note: historical commits remain accessible via old key. True history rewrite is out of scope.

#### `secrets_list_keys(path: str) -> dict`
- Ensure unlocked.
- Read `repo / path`.
- Parse with `_parse_dotenv`.
- Return `{"path": path, "keys": list(data.keys())}` — values are NOT included.

---

## Part 5 — Registration

**`src/registry_mcp/tools/__init__.py`**: add `register_secrets_tools` to imports and `__all__`.

**`src/registry_mcp/server.py`**: in `build_server()`, add:
```python
from registry_mcp.tools.secrets import register_secrets_tools
...
register_secrets_tools(mcp, settings)
```
(No external dependency like `store` needed — secrets tools only use `settings` and subprocess.)

---

## Part 6 — Tests (`tests/test_secrets.py`)

Pattern: mock `_run()` and filesystem operations, use `tmp_path` fixture.

Test cases:
- `secrets_status` when disabled → error dict
- `secrets_status` when enabled, locked → `locked: true`
- `secrets_status` when enabled, unlocked → `locked: false` with file lists
- `secrets_decrypt` on a `.env` file → returns parsed dict
- `secrets_decrypt` on a non-`.env` file → returns raw string
- `secrets_add` new key → file updated, git staged
- `secrets_add` existing key → value overwritten
- `secrets_list_keys` → returns keys, no values
- `secrets_encrypt` → `.gitattributes` updated
- `_key_bytes` file path takes priority over env var
- `_key_bytes` env var used when no file path
- `_key_bytes` raises when neither is set

---

## Verification

1. Run `uv run pytest tests/test_secrets.py -v` — all new tests pass
2. Run `uv run pytest -q` — full suite (148 + new) still green
3. Run `uv run ruff check . && uv run ruff format --check .` — clean
4. Manually: set `SECRETS_ENABLED=true`, `SECRETS_REPO_PATH`, and key vars in `.env`; start server; call `secrets_status` via MCP client; confirm it returns locked/unlocked state correctly
5. Run `scripts/setup-homelab-repo.sh` on the control-plane node to create the homelab repo and confirm key is exported to `SECRETS_KEY_PATH`

---

## Files touched

| File | Change |
|---|---|
| `Dockerfile` | Add `git-crypt` apt install |
| `src/registry_mcp/config.py` | Add 4 `secrets_*` settings |
| `src/registry_mcp/tools/secrets.py` | New — 6 tools + helpers |
| `src/registry_mcp/tools/__init__.py` | Add `register_secrets_tools` |
| `src/registry_mcp/server.py` | Call `register_secrets_tools(mcp, settings)` |
| `scripts/setup-homelab-repo.sh` | New — one-time repo bootstrap script |
| `tests/test_secrets.py` | New — ~12 test cases |
| `.env.example` | Add `SECRETS_*` vars |
| `CLAUDE.md` | Add env var table entries |
