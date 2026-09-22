# ADR-018: Repo Intake for Conversational Deploy (Phase 1)

| | |
|---|---|
| **Status** | Accepted |
| **Related** | `docs/plans/conversational-deploy.md` (the multi-phase plan this is Phase 1 of); [ADR-002](ADR-002-Client-Interfaces.md) (write-path client-interface precedent); [ADR-016](ADR-016-Read-Only-Infisical-Integration.md) (precedent for an off-by-default, read-only integration landing its flag at introduction) |
| **Date** | 2026-09-22 |

## Context

`docs/plans/conversational-deploy.md` sketches an eight-phase build toward "Nathan
says deploy `<service>` with a link, the system drafts a compose file, picks a node,
and opens a PR." Phase 0 (recon) resolved enough of that plan's open questions to
unblock Phase 1 without unblocking Phase 3 or 7 — see the plan doc's "Phase 0 recon
findings" section. This ADR covers Phase 1 only: **repo intake** — given a foreign
source repo's URL, read its Dockerfile/compose file/README and produce structured
runtime requirements (base image, ports, env vars, volumes, dependencies). Nothing
downstream of intake (compose generation, placement, secrets, PR assembly) is built
yet; this phase is read-only and writes nothing.

Repo intake is a new kind of I/O for this codebase: every existing integration talks
to infrastructure this homelab already runs and controls (Traefik, Authentik,
Dockhand, the operator's own Git host via `providers/git/`). Intake instead clones a
repo the operator names at call time, which may be anyone's — a mainstream project on
GitHub, a friend's fork, a self-hosted Gitea instance. That repo's content, and the
URL itself, are both untrusted input in a way nothing else in this codebase handles.

## Decision

### Shell out to `git`, not `providers/git/`

`intake/fetch.py` shallow-clones via `asyncio.create_subprocess_exec(["git", ...])`,
the same pattern `gitcrypt.run` and `adoption/ssh.py` already use, rather than
reusing the `GitProvider` protocol (`providers/git/`). `GitProvider` is bound to the
operator's own `GIT_BASE_URL`/`GIT_TOKEN`/`GIT_REPO` and authenticates every call —
offering that token to a clone of a stranger's repo would leak it to a host that
never should have seen it. A plain `git clone` needs no credential at all for a
public repo, and intake never asks the operator to configure one.

### The URL is the trust boundary: allowlist `https`, not a denylist

`check_repo_url()` accepts only `https://` and rejects everything else, including
`http://` (no unauthenticated transport for code this homelab is about to run).
This is deliberately an allowlist, not an attempt to enumerate dangerous schemes:
- `ext::<command>` runs an arbitrary local command as part of the "clone" — the
  sharpest edge on `git clone`, undocumented by most users of the CLI.
- `file://` reads this node's own filesystem.
- `ssh://` and bare `git@host:path` (git's scp-like syntax, which has no URL scheme
  to allowlist against and is caught by requiring one explicitly) would spend the
  control-plane's own `SSH_KEY_PATH` against a foreign host — the same key Ansible
  uses to reach every workload node.
- A leading `-` is rejected outright; `git clone -- <url>` isn't used inside
  `check_repo_url` itself, but the check runs before any argument construction so a
  flag-shaped string never reaches a shell or `git`'s own argument parser.

A private-range `https` host (a homelab's own Gitea, `https://gitea.lan/...`) is
explicitly **allowed** — the scheme is the boundary, not the address. Restricting to
public internet hosts would break the most likely real use (an operator's own
self-hosted git) for a homelab-context threat model that doesn't apply: this server
already runs inside the operator's network and already talks to other private-range
services (Traefik, Authentik, Dockhand).

The clone itself additionally disables credential helpers
(`-c credential.helper=`) and terminal prompts (`GIT_TERMINAL_PROMPT=0`), so a
private repo simply fails to clone rather than blocking on, or silently reusing,
some ambient credential.

### Cloned content is untrusted too: symlink containment on every read

Accepting the URL is not the end of the trust boundary — the repo's *contents* are
attacker-controlled the moment the clone succeeds. Git stores symlinks natively, so
a malicious repo shipping `README.md -> /etc/passwd` (or any host path readable by
this process) would otherwise hand that file's content back through the MCP tool.
`fetch.py`'s file reads check `Path.is_symlink()` before `exists()` (which follows
the link, so it would report a symlink to a real file as an ordinary present file)
and separately re-verify `resolve().is_relative_to(root)` — the same two-step
containment `gitcrypt.check_path` already uses for repo-relative writes, applied
here to reads out of an untrusted clone instead. A refused symlink is recorded in
the response's `skipped` list rather than silently reported as "file not found," so
a caller can distinguish "this repo has no README" from "this repo tried something."

### No LLM in extraction; `InferServiceRequirements` only fills the gap

`intake/parse.py` extracts `FROM`/`EXPOSE`/`ENV`/`VOLUME` from a Dockerfile and
`image`/`ports`/`environment`/`volumes`/`depends_on` from a compose file
deterministically — no LLM call, the same discipline that keeps
`registry/reconcile.py` detection-only. Where a repo ships both, compose wins on
conflict (it describes how the thing is actually run; the Dockerfile only describes
how the image was built), and Dockerfile-only facts are additive rather than
discarded.

`InferServiceRequirements` (`dspy/signatures.py`) is scoped narrowly: given the
README plus the already-extracted facts as ground truth it may not contradict, infer
only what deterministic parsing structurally cannot — which backing services a repo
needs (a database, a cache) and which detected env vars need an operator-supplied
real value versus already carrying a working default. It returns raw outputs
including `confidence` rather than gating internally (the same split
`generate_remediation_patch`/`detect_hardcoded_secrets`/`normalize_config` use), so
the `service-intake-repo` tool applies `SERVICE_DEPLOY_CONFIDENCE_THRESHOLD` and
reports a discarded low-confidence guess as discarded — `inference: null` plus
`inference_rejection_reason` — never as if it were a fact, mirroring
`proposal/generator.py`'s `PatchResult(ok=False, confidence=..., rejection_reason=...)`
shape.

### `SERVICE_DEPLOY_ENABLED` lands now, not at the plan's Phase 6

The plan document originally scoped the feature flag to Phase 6 ("dry run + feature
gate"), reasoning by analogy to `PROPOSAL_DRY_RUN`/`NORMALIZATION_DRY_RUN`, which are
about the *write* path. Phase 1 has no write path — but it does grant an MCP client
the ability to make this server issue outbound network fetches against a caller-
supplied host, which is itself a capability worth gating on its own terms, separate
from anything downstream. Every comparable capability in this codebase
(`ADOPTION_ENABLED`, `NORMALIZATION_ENABLED`, `INFISICAL_ENABLED`) ships its flag
off-by-default from the phase that introduces the capability, not deferred to a
later "make it dry-runnable" phase — `SERVICE_DEPLOY_ENABLED` follows that precedent.
`SERVICE_DEPLOY_DRY_RUN` remains a real Phase 6 concern once there is a write to make
dry.

### Bounds on one intake: timeout and size cap

A foreign repo is untrusted input in the ordinary availability sense too:
`SERVICE_DEPLOY_CLONE_TIMEOUT_SECONDS` (default 60) bounds a hung fetch from pinning
the event loop, and `SERVICE_DEPLOY_MAX_REPO_MB` (default 100) rejects a repo too
large to be worth parsing after the clone completes — git offers no server-side
transfer-size limit, so the cap is enforced by measuring the cloned directory,
not by aborting the transfer early. Both bounds are ordinary availability hygiene,
not a security control on their own; the URL scheme allowlist and symlink
containment above are the actual trust-boundary decisions.

## Consequences

### Positive

- `service-intake-repo` gives the eventual `deploy_service` flow (Phase 2 onward) a
  read-only, independently useful building block — "what does this repo need to run"
  is a legitimate standalone question even before compose generation exists.
- The URL/symlink hardening is intentionally general: any future phase that reads
  more of a foreign repo (a `docker-compose.yml` sibling, a `.env.example`) inherits
  the same containment for free by going through `fetch.py`'s helpers rather than
  reimplementing file access.
- Landing `SERVICE_DEPLOY_ENABLED` now means the capability is off in every existing
  deployment until the operator deliberately opts in, with no silent widening
  hiding inside a later "just a config flag" phase.

### Negative / accepted tradeoffs

- **New outbound-network capability, even gated.** Once enabled, this server will
  fetch from any `https://` host an MCP client names, which is qualitatively
  different from every other integration here (all of which point at homelab-local,
  operator-configured endpoints). The scheme allowlist and clone bounds mitigate the
  sharpest edges; they do not make this equivalent in risk to a fixed-endpoint
  integration. Off by default, and worth re-examining if a future phase's threat
  model (e.g. once compose generation runs on intake's output) needs more.
- **No SSRF-style network-destination filtering.** A private-range or
  link-local `https` host is reachable exactly like a public one — deliberately, for
  the self-hosted-Gitea case above — so this does not protect against, say, an
  operator's own conversational agent being tricked into cloning from an internal
  service that happens to speak enough of the git smart-HTTP protocol to look like a
  repo. Considered out of scope for a homelab-local MCP server whose caller is
  already trusted to reach every other tool here.
- **`InferServiceRequirements` adds one more DSPy signature to maintain**, though it
  follows the existing no-fallback, confidence-gated pattern exactly, so the marginal
  complexity is mostly in the signature's prompt, not new machinery.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Reuse `providers/git/`'s `GitProvider` for the clone | Rejected — it authenticates every call with the operator's own token, which must never be offered to an arbitrary caller-supplied host. |
| A denylist of dangerous URL schemes instead of an allowlist | Rejected — a denylist only stops schemes someone thought to list; `git`'s scheme space (`ext::`, `fd::`, others) is not fixed, so allowlisting `https` alone is the only approach that doesn't need to anticipate every future dangerous transport. |
| Defer `SERVICE_DEPLOY_ENABLED` to Phase 6 as the plan doc originally sketched | Rejected — Phase 1 already grants outbound-fetch capability to an MCP client, which is the thing worth gating; every comparable integration in this codebase gates from its introducing phase, not a later "add dry-run" phase. |
| Skip symlink containment; trust the clone since it's a fresh temp directory | Rejected — "fresh temp directory" describes where the clone landed, not what's inside it. The repo's content is exactly as untrusted as the URL that produced it. |
| Let `InferServiceRequirements` gate its own confidence internally, like `resolve_identity`/`infer_metadata` do | Rejected — those two enrichment modules have a deterministic path to fall back to (skip the enrichment, keep the discovered service as-is). Intake has nothing to fall back to; returning the raw result with confidence lets the tool report *why* an inference was discarded, matching how the write-path modules (which also have no fallback) already do it. |

## References

- `docs/plans/conversational-deploy.md` — the full eight-phase plan; this ADR covers
  Phase 1 only
- `src/registry_mcp/intake/` — `fetch.py` (URL validation, clone, symlink-safe reads),
  `parse.py` (deterministic extraction)
- `src/registry_mcp/dspy/signatures.py` — `InferServiceRequirements`
- `src/registry_mcp/tools/intake.py` — the `service-intake-repo` MCP tool
- `src/registry_mcp/gitcrypt.py` — `check_path`, the existing containment pattern this
  ADR's symlink handling mirrors

---

*ADR-018 | github.com/TeamCastaldi/homelab-registry-mcp | MIT License | 2026*
