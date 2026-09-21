# ADR-017: Infisical Whole-Project Visibility (Recursive Scan)

| | |
|---|---|
| **Status** | Accepted |
| **Related** | [ADR-016](ADR-016-Read-Only-Infisical-Integration.md) (the single-folder read-only integration this extends) |
| **Date** | 2026-09-20 |

## Context

ADR-016's `infisical_status` reads exactly one folder — `INFISICAL_PROJECT_ID`'s
`INFISICAL_ENVIRONMENT`/`INFISICAL_SECRET_PATH`. In the operator's real Infisical
layout, that one folder (`/homelab-registry-mcp`) is a sibling of 18 others — one
per homelab service (`authentik`, `komodo`, `vaultwarden`, `prowlarr`, `qbit`, ...)
— all inside a single shared "Homelab" project. After confirming ADR-016's tool
worked end to end against its own folder, the operator asked whether it could see
the whole project instead: every service's secret *key names*, not just this one's.

## Decision

Add an opt-in whole-project mode to the existing `infisical_status` tool, gated by
a new setting, rather than a separate tool or a per-call parameter:

- **`INFISICAL_RECURSIVE_SCAN`** (bool, default `false`). When `false`, behavior is
  byte-for-byte what ADR-016 shipped — single folder, `{"keys": [...]}`. When
  `true`, the tool walks the folder tree rooted at `INFISICAL_SECRET_PATH` (set it
  to `/` for the whole project) and returns `{"secrets_by_path": {...}}`, grouping
  key names by the exact folder each lives in.
- **`INFISICAL_MAX_FOLDERS`** (int, default `50`) bounds how many folders a single
  sweep visits, so a very large or deeply nested project can't make one tool call
  balloon unboundedly. Folders past the cap are simply not visited.

**Walks the tree via folder listing, not a `recursive` query flag.** Infisical's
`GET /api/v3/secrets/raw` reportedly supports a `recursive=true` parameter that
would return an entire subtree in one call. This rollout has twice found this
self-hosted instance's real behavior diverging from Infisical's documented API —
the `workspaceSlug` parameter, and the exact `viewSecretValue=false` masking shape
ADR-016 had to correct after live verification. Rather than build around an
unverified flag on a newer endpoint, `InfisicalClient.list_secret_tree()` calls
`GET /api/v1/folders` (a long-stable, widely-used part of Infisical's API) to
enumerate each folder's immediate children, and reuses the already-proven
`list_secret_keys()` per folder. **Confirmed live** against the operator's
self-hosted instance (see Open items, now resolved): `GET /api/v1/folders` returns
exactly the assumed `{"folders": [{"name": ...}, ...]}` shape, and the walk
correctly recurses to at least two levels deep (`/heimdall/<service>`,
`/ollama/<service>`, `/waldorf/<service>` subfolders under top-level node folders).

**Same read-only, never-a-value guarantee, generalized:**
- The defensive value-leak gate is unchanged in logic and still applies per secret,
  per folder. A leak in *any* folder anywhere in the tree still fails the whole
  sweep closed — `InfisicalSecretValueLeakedError` propagates immediately and is
  never caught by the folder-walk loop.
- `InfisicalSecretValueLeakedError` now carries an optional `path` alongside `key`,
  since the same key name (`GIT_TOKEN`, `SMTP_PASSWORD`, ...) can plausibly exist in
  more than one service's folder — the log event and notification now name exactly
  which folder's key needs rotation, not just the bare key name.
- A folder the Machine Identity can't read (a 403) is **skipped and reported** under
  `inaccessible_paths`, not treated as a leak and not treated as a hard failure —
  that's an expected permission boundary, distinct from a security incident.

**Prerequisite the code cannot satisfy on its own**: the Machine Identity's
Universal Auth policy in Infisical must actually grant read access beyond
`/homelab-registry-mcp` — to the whole project, or explicitly to each folder — or
`INFISICAL_RECURSIVE_SCAN=true` will just report every other folder under
`inaccessible_paths`. Widening that policy is the operator's action in Infisical's
own Identity/Permissions UI, not something this ADR or its code can do.

## Consequences

### Positive

- Answers questions ADR-016's tool structurally couldn't — "does Vaultwarden have
  an SMTP_PASSWORD configured" — without eyeballing Infisical's dashboard.
- Fully backward compatible: the setting defaults to `false`, so every existing
  ADR-016 deployment (including the operator's own, currently pointed at
  `/homelab-registry-mcp`) is unaffected until explicitly opted in.
- Reuses `list_secret_keys()` and its already-tested value-leak gate per folder
  rather than duplicating that logic for the tree-walk path.
- A folder-level permission gap degrades to a reported, non-fatal partial result
  instead of failing the whole call — useful signal on its own (it tells the
  operator the Machine Identity's Infisical policy doesn't yet cover a folder they
  expected it to).

### Negative / accepted tradeoffs

- **Real scope expansion, consciously accepted**: once enabled, any AI conversation
  using this tool can see which *services* have secrets configured and by what key
  names, across the entire homelab — not just this server's own settings. Still
  zero values exposed anywhere, same gate as ADR-016, but the blast radius of "what
  can this tool tell an LLM" grows by one dimension. The operator explicitly asked
  for this after seeing the single-folder version work.
- **The folder-listing endpoint's exact shape was unverified at merge time** (see
  Open items, now resolved by a live test). The code was written to fail toward the
  already-proven single-path behavior if `/api/v1/folders`'s response didn't match
  what's assumed — that failure mode would have been silent (an empty child list
  looks identical to "this folder genuinely has no subfolders"), so the live test
  confirming multiple folders' keys actually returned, including nested subfolders,
  mattered before trusting an empty `secrets_by_path` as ground truth in the future.
- N+1 API calls (one per folder visited, plus one folder-listing call per non-leaf
  folder) instead of Infisical's hypothetical single `recursive=true` call — traded
  deliberately for not depending on a flag this rollout has no live confirmation of,
  and for a straightforward per-folder inaccessibility signal.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Rely on `GET /api/v3/secrets/raw`'s documented `recursive=true` flag | Rejected for now — this exact self-hosted instance has already diverged from Infisical's documented behavior twice; a folder-listing walk depends only on a long-stable endpoint. Could still be revisited as a v2 if live testing shows the folder walk has problems `recursive=true` wouldn't. |
| A separate `infisical_status_all` tool instead of a setting on the existing tool | Rejected — the response shape and behavior already branch cleanly on one setting; a second tool would duplicate the disabled/unconfigured error handling and the leak-notification path for no real benefit. |
| A per-call `recursive` tool parameter instead of a deployment-level setting | Rejected — the operator's own preference (this ADR's design questions were put to them directly): a static setting keeps the scope decision a deliberate, visible deployment choice rather than something any single conversation could flip. |
| Silently treat an inaccessible folder identically to an empty one | Rejected — conflates "nothing here" with "the Machine Identity's Infisical policy doesn't cover this yet," which is exactly the kind of permission-scope surprise this rollout has hit before (SOP-005's project/path corrections). Reporting it costs one list field. |

## Open items

| # | Question | Status |
|---|---|---|
| 1 | Does `GET /api/v1/folders` on this self-hosted instance actually accept `workspaceId`/`environment`/`path` and return `{"folders": [{"name": ...}, ...]}` as assumed? | **Resolved** — confirmed live: yes, exactly as assumed, and the walk correctly recurses at least two levels deep |
| 2 | Does the current Machine Identity's Universal Auth policy grant read access beyond `/homelab-registry-mcp`? | **Resolved** — the operator widened it to project-wide read; a live scan returned all 24 folders with zero `inaccessible_paths` |
| 3 | Should a v2 revisit `recursive=true` on `/api/v3/secrets/raw` as a faster path, now that the folder-walk gives a working (if slower) baseline to compare against? | Deferred — no need identified yet; the current folder-walk works and needs no further verification |

**Deployment note discovered during rollout**: this operator's registry-mcp container is deployed via Dockhand, which injects secret values into the container directly from Infisical at deploy time — it does not read a `.env` file for these values. A new setting like `INFISICAL_RECURSIVE_SCAN` only reaches the running container once it exists as a key in Infisical's `/homelab-registry-mcp` folder itself; adding it only to `compose.yaml`'s `environment:` block is not sufficient on this deployment path. A raw `docker compose up -d` bypasses Dockhand's injection entirely and falls back to whatever plain `.env` sits next to the compose file (a placeholder, not real secrets) — redeploys for this stack should go through Dockhand, not the CLI.

## References

- [ADR-016](ADR-016-Read-Only-Infisical-Integration.md) — the single-folder integration this extends; its defensive value-leak gate is reused unchanged per folder
- `docs/SOPs/SOP-005-Connect-Infisical-Machine-Identity.md` — Machine Identity setup; its permission scope is this ADR's Open item 2

---

*ADR-017 | github.com/TeamCastaldi/homelab-registry-mcp | MIT License | 2026*
