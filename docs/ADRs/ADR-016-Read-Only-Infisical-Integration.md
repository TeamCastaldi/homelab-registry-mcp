# ADR-016: Read-Only Infisical Integration

| | |
|---|---|
| **Status** | Accepted |
| **Related** | [ADR-013](ADR-013-Dockhand-Read-Only-API-Integration.md) (closest existing read-only-integration precedent), [ADR-010](ADR-010-Dockhand-Update-Webhook.md) (`NotificationProvider` usage precedent), `docs/SOPs/SOP-005-Connect-Infisical-Machine-Identity.md` (credential setup this ADR builds on) |
| **Date** | 2026-09-20 |

## Context

Live secrets for the operator's actual running services are managed in a
self-hosted Infisical instance (`infisical.castaldifamily.com`) and delivered
to containers by Dockhand's native Infisical integration at deploy time.
Registry-mcp's existing `secrets_*` tools (Phase C) protect a different,
narrower thing — `.env` files committed to the private homelab repo, at rest
in git via git-crypt — and have no visibility into Infisical itself.

That gap was found the hard way, by hand, earlier in this same rollout: a
dashboard screenshot had to be eyeballed against `CLAUDE.md`'s environment
variable table to notice that `ANSIBLE_INVENTORY_PATH` had never been added
to Infisical, and separately that most of the ~75 Infisical-held secrets were
never reaching the running container because `compose.yaml`'s `environment:`
block had never grown to match what Infisical actually held. A read-only tool
that queries Infisical directly turns that into something an MCP client can
check on demand instead.

`docs/SOPs/SOP-005-Connect-Infisical-Machine-Identity.md` already covers
creating the Machine Identity and Universal Auth credential this integration
will authenticate with, and has been verified end to end against the live
instance: Universal Auth login works, `/api/v3/secrets/raw` is the correct
endpoint for this self-hosted version (not `/api/v4/secrets`), and the real
secrets live in the `Homelab` project (ID `7fcb8436-e0a0-40fb-bfa5-57472a453571`),
`prod` environment, `/homelab-registry-mcp` secret path — not at the project
root, and not under a project literally named `homelab-registry-mcp` as the
dashboard's folder breadcrumb alone suggests.

## Decision

Add `integrations/infisical/` — an httpx client mirroring
`integrations/traefik/`/`integrations/authentik/`/`integrations/dockhand/`'s
shape, with one genuinely new element: Universal Auth (Client ID/Secret
exchanged for a short-lived access token) rather than a single static Bearer
token, so the client must cache the token and re-authenticate on expiry
rather than logging in on every call.

**Off by default.** `INFISICAL_ENABLED` (bool, default `false`) gates the
integration's existence entirely, matching every other opt-in capability in
this project.

**Read-only, by design and by construction, not just by convention:**

- The client requests `viewSecretValue=false` on every secrets call, so no
  secret value is fetched in the first place. **Confirmed live** against the
  operator's self-hosted instance (see Open item 2, now resolved): with
  `viewSecretValue=false`, each entry in the response carries an explicit
  `secretValueHidden: true` plus `secretValue: "<hidden-by-infisical>"` — a
  literal placeholder string, never `null` or an omitted field.
- The MCP tool surface never returns a secret's value under any
  circumstance — only its key name, exposed to the tool's caller. A
  `has_value: bool` (whether the real value is non-empty) was considered but
  is **not** deliverable: Infisical's placeholder is uniform across every
  key regardless of whether the underlying value is empty or not, so there
  is no signal available here to distinguish "masked" from "genuinely
  empty" without fetching the real value — which this client will never do.
  Key existence is the only thing reported.
- `INFISICAL_ALLOW_WRITE` (bool, default `false`) is reserved for a future
  phase and does nothing yet in this ADR's scope — it exists now only as the
  visible seam a later phase would flip, matching `ADOPTION_ENABLED`'s and
  `DSPY_ENABLED`'s shape.

**Defensive value-leak gate**, independent of and not reliant on
`viewSecretValue` working as documented: every secrets response is inspected
for confirmation the value was actually masked, trusting neither field
alone — `secretValueHidden` must be exactly `True` *and* `secretValue` must
be one of the known-safe placeholders (the confirmed-live
`"<hidden-by-infisical>"`, plus the more conservative `None`/`""` a future
version might use instead). Either signal failing that check — an
unrecognized placeholder string despite `secretValueHidden: true`, or a
`secretValueHidden: false` despite a masked-looking value — is treated as a
leak, so a future Infisical version changing its exact placeholder text
fails closed rather than silently being accepted as new-but-fine. If a leak
is detected:

- The tool call **fails closed** — it returns an error to the caller, never
  the leaked value and never a partial or "sanitized" version of the
  response.
- A structured log event is emitted naming only the affected secret's
  **key** and its Infisical secret path — never the value itself, so the
  log/alert can never become a second leak vector.
- The existing `NotificationProvider` (already wired for Ntfy/Smtp/Null,
  see ADR-010) sends an urgent, out-of-band alert recommending that specific
  key be **rotated in Infisical immediately** — this is treated as a live
  credential-compromise signal, not a someday cleanup item.

**Tool surface:** one read-only tool, `infisical_status`, taking no required
arguments and returning, for the configured project/environment/secret
path, the list of key names present (`{"keys": [...]}`). On a leak-gate
trip, a disabled/unconfigured integration, or an upstream error, it returns
`{"error": "..."}` instead — never a partial key list mixed with an error.
No tool exists to fetch or display any individual secret's actual content,
in either direction.

**New settings** (`config.py`), all optional and inert unless set:
`INFISICAL_ENABLED`, `INFISICAL_BASE_URL`, `INFISICAL_CLIENT_ID`,
`INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`, `INFISICAL_ENVIRONMENT`,
`INFISICAL_SECRET_PATH`, and the reserved-but-inert `INFISICAL_ALLOW_WRITE`.

Explicitly **not** in scope for this ADR: the write path itself (registry-mcp
creating or rotating secrets in Infisical), and the exact mechanism for
delivering `INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET` into registry-mcp's
own running environment without looping back through Infisical itself — see
Open items.

## Consequences

### Positive

- Closes the exact gap this rollout found by hand: a live "does what
  `CLAUDE.md` documents as required actually exist and have a value in
  Infisical" check, without eyeballing a dashboard screenshot.
- The value-leak gate turns an Infisical-behavior surprise — the kind
  already hit twice in this same rollout — into a loud, safe failure instead
  of a secret silently landing in an LLM's context.
- Matches the existing read-only integrations' shape closely enough that a
  future maintainer already knows the pattern; Universal Auth's token
  refresh is the one genuinely new piece.

### Negative / accepted tradeoffs

- The credential-delivery chicken-and-egg problem is not solved here —
  `INFISICAL_ENABLED=true` is not production-usable until that's settled
  (Open item 1). This ADR can be implemented and tested in isolation first
  (e.g. locally, with the credential set directly per SOP-005).
- The value-leak gate adds real complexity (token caching, response
  inspection, a new `NotificationProvider` consumer) for a condition that
  should never trigger if `viewSecretValue=false` behaves as intended.
  Accepted because the cost of being wrong here — a live secret value
  reaching an LLM conversation — is categorically worse than the extra code.
- Universal Auth's short-lived-token model is new for this codebase's
  `integrations/` clients (the existing three all use a single static Bearer
  token). No shared plumbing is extracted preemptively; revisit if a future
  integration needs the same shape.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Let the tool return actual secret values, gated behind an `INFISICAL_ALLOW_WRITE`-style flag | Rejected — the read path must never expose live values, independent of any write capability; "can write" and "can read values" are different axes and conflating them was explicitly ruled out |
| Trust `viewSecretValue=false` alone, no defensive check | Rejected — this rollout has already found Infisical's real behavior diverging from its docs twice (`workspaceSlug`, root-path empty-vs-error responses); a security-critical assumption gets a runtime check, not just a request parameter |
| Silently redact/mask a leaked value and return the rest of the response | Rejected — masking implies "handled", but the value was still fetched into this process and is a compromise signal regardless of what the tool ultimately returns; fail the call closed and alert instead |
| Poll Infisical proactively on a schedule, like the discovery sources do | Deferred — no scheduling need identified yet; every gap this integration is meant to catch has so far been found by asking, not by watching |

## Open items

| # | Question | Status |
|---|---|---|
| 1 | How does `INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET` reach registry-mcp's own running environment without looping through Infisical itself — the chicken-and-egg problem SOP-005 deliberately left unresolved? | Open |
| 2 | Does this instance's `/api/v3/secrets/raw` actually honor `viewSecretValue=false` as documented, or does it need the same kind of empirical workaround `secretPath` and the API version needed? | **Resolved** — confirmed live: yes, via an explicit `secretValueHidden: true` plus a literal `"<hidden-by-infisical>"` placeholder (never `null`/omitted). The client's value-leak gate checks both signals together, not either alone |
| 3 | Exact tool name and response shape (`infisical_status` vs. something else) | **Resolved** — `infisical_status`, returning `{"keys": [...]}` or `{"error": "..."}` (see Decision, Tool surface) |
| 4 | Should a future write phase let registry-mcp create/update secrets in Infisical directly, or should that stay a human/Dockhand-only action permanently? | Open — no plan to build this yet |

## References

- [ADR-013](ADR-013-Dockhand-Read-Only-API-Integration.md) — closest existing precedent for a read-only external API client
- [ADR-010](ADR-010-Dockhand-Update-Webhook.md) — `NotificationProvider` usage precedent for an urgent, out-of-band alert
- [`docs/SOPs/SOP-005-Connect-Infisical-Machine-Identity.md`](../SOPs/SOP-005-Connect-Infisical-Machine-Identity.md) — credential setup and live verification this ADR builds on
- `providers/notification/` — existing alert infrastructure the value-leak gate reuses

---

*ADR-016 | github.com/TeamCastaldi/homelab-registry-mcp | MIT License | 2026*
