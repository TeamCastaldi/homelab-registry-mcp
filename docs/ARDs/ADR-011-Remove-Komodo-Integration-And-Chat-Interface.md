# ADR-011: Withdraw the Komodo Integration and the `/chat` Interface

| | |
|---|---|
| **Status** | Accepted |
| **Supersedes** | ADR-009 (Conversational Chat Interface) in full |
| **Amends** | ADR-006 §1 — withdraws this server's *integration* with Komodo; ADR-006's Komodo-on-the-Pi deployment decision stands |
| **Reopens** | ADR-002 §8 Open Questions 1-4, which ADR-009 had resolved |
| **Date** | 2026-09-04 |

## Context

The server's supported external-service list has narrowed. Two surfaces are being
withdrawn, for different reasons.

**Komodo** entered the codebase through ADR-006, which chose it as the Pi's container
management, log, and update-detection tool after the ADR-005 stack was removed. The
integration built on that choice was deliberately thin: seven read-only tools, an httpx
client authenticating with `X-Api-Key` / `X-Api-Secret` headers, a `komodo://stacks/{name}`
resource, and a `diagnose_stack` prompt. It was never a discovery source — it has no `SourceType` member, no reconciler
path, and never wrote a row to the registry. Its update-detection role, the one function
that could have fed the write path, was taken over by ADR-010's Dockhand webhook, which
pushes an exact target tag rather than requiring a poll. What remained was a read-only
window onto a system the operator can already open directly.

**The `/chat` interface** (ADR-009) was the larger surface: Starlette routes for
`/chat`, `/chat/auth/*` and `/chat/api/*`, an SSE agent loop, an Ollama client, Authentik
OIDC and static-password auth, HMAC-signed stateless sessions, a live context pack, a
persona loader, a hand-rolled markdown renderer, and — the security-critical piece — the
`READ_TOOLS`/`WRITE_TOOLS`/`DENY_ALWAYS` allowlist in `chat/bridge.py`. That allowlist was
the actual boundary between a language model and the lab, and it needed to stay correct
as every future tool was added. ADR-009 accepted that maintenance burden in exchange for
a conversational surface. This record withdraws that trade rather than re-litigating it:
the burden is real and ongoing, and it is carried by a feature that was off by default.

## Decision

1. **Remove `integrations/komodo/` and its five `KOMODO_*` settings.** The seven
   `komodo_*` tools, the `komodo://stacks/{name}` resource and the `diagnose_stack`
   prompt are no longer registered. Tool count drops from 71 to 64.

2. **Remove the `chat/` package in full**, its seven pytest modules, the two `node --test`
   files covering the JS markdown renderer, and the 30 `CHAT_*` settings.

3. **ADR-006 §1 is amended, not reversed.** Komodo continues to run on the Pi for
   operational visibility. Per ADR-007 that deployment is an ordinary
   `nodes/<node>/<service>/compose.yaml` entry in the operator's private homelab repo,
   reached through the GitOps pipeline — nothing about it depends on this server. Only
   this server's API integration with Komodo is withdrawn, so `docs/SETUP.md` and
   `scripts/README.md` keep their Komodo deployment guidance unchanged.

4. **`/mcp` is unaffected.** It stays exactly as unauthenticated and LAN-only as before;
   no auth posture changes in either direction.

5. **Alongside this, four enum/config symbols that never had a producer are removed** —
   `FindingType.missing_auth`, `.missing_security_headers`, `.exposed_dashboard`, and
   `SourceType.network`, plus the `DISCOVERY_DOCKER_ENABLED` and
   `DISCOVERY_NETWORK_ENABLED` settings that nothing read. These are unrelated to the two
   withdrawals; they are recorded here because they were removed in the same pass.

## Consequences

### Positive

- The server exposes exactly one HTTP surface beyond `/mcp`: `POST /webhooks/dockhand`.
  There is no browser-facing route on the port any more, which restores the simpler
  pre-ADR-009 reasoning about ForwardAuth and MCP clients.
- `chat/bridge.py`'s allowlist no longer has to be updated in step with every new tool —
  a correctness obligation that grew with the tool surface and had no test that would
  fail if it were forgotten.
- ~6,300 lines removed across source, tests, config and docs; 14 fewer registered tools.

### Negative

- **No conversational surface.** ADR-002 §4.4's Phase 3 is unbuilt again, and ADR-002's
  Open Questions 1-4 are open. Any future attempt starts from ADR-009's design, which is
  kept for exactly that reason.
- **No in-server view of Komodo.** An operator asking "what stacks are on this node?"
  goes to Komodo's own UI rather than through an MCP client. The registry still answers
  the service-catalog half of that question.
- `starlette` remains a required dependency even though ADR-009 introduced it, because
  `webhooks/dockhand.py` uses it. Removing chat does not shrink the dependency set.

### Neutral

- No data migration. Komodo never wrote to the database, and the removed enum members
  were never written by any released version — verified with `git log -S` across all
  branches, which shows each appearing only in the commit that defined it.
- `Settings` uses `extra="ignore"`, so a deployed `.env` still carrying `CHAT_*` or
  `KOMODO_*` lines starts normally. They are inert, not errors.

## Alternatives considered

**Leave both disabled rather than removing them.** Both were already off by default
(`CHAT_ENABLED=false`, `KOMODO_API_URL` unset), so the runtime cost of keeping them was
near zero. Rejected because the cost was never runtime: it was the allowlist that had to
stay correct, the seven-client httpx retry idiom that had to stay consistent, and the
35 settings that had to stay documented. Dead-but-present code is the tech debt this
change exists to remove — ADR-006 made the same call about WUD rather than leaving the
`/webhooks/wud` route mounted and inert.

**Keep Komodo's `list_updates` as an update-detection fallback for operators without
Dockhand.** Rejected as a partial feature: `KomodoClient.execute` was already dead, the
detection path it would feed is ADR-010's, and ADR-004's three-way drift model still
wants a repo-reading source that neither Komodo nor Dockhand provides. An operator
without Dockhand has no detection either way; a single read-only tool would not have
changed that.

## Open items

1. **ADR-002 §8 Open Questions 1-4 are unanswered again.** No conversational interface is
   planned; ADR-009 stands as the reference design if one is revisited.
2. **ADR-006 Open Item 2** — whether Komodo's own webhook gets wired into the proposal
   engine — is now moot on the integration side. ADR-010 chose Dockhand, and this record
   removes the client that would have consumed a Komodo webhook.
3. **ADR-004's three-way drift model** (intended / actual / available) still has no
   repo-reading source. Unchanged by this record; noted because removing Komodo removes
   one of the two systems that could plausibly have supplied "available".
