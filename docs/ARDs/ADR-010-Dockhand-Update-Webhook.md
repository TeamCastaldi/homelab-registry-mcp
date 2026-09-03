# ADR-010: Dockhand Update Webhook → Staged Proposals

| | |
|---|---|
| **Status** | Accepted |
| **Resolves** | ADR-006 "no update-triggered proposal path until a future ADR wires one up" |
| **Advances** | ADR-004 action item "Define new proposal type: `IMAGE_UPDATE`" |
| **Date** | 2026-09-03 |

## Context

ADR-005 gave the registry an update-triggered proposal path: WUD (What's Up Docker)
polled upstream registries and POSTed to a `/webhooks/wud` route, which opened an
`image_update` pull request. ADR-006 removed the entire ADR-005 stack, and deleted that
flow as dead code along with it — the route, `FindingType.image_update`, and
`ProposalEngine.create_for_image_update` (commit `5036267`). ADR-006 was explicit that
this left a hole and deferred filling it:

> Re-adding an update-triggered proposal path — via Komodo's own update detection, or
> ADR-004's still-unimplemented polling `UpstreamRegistrySource` — is a decision for a
> future ADR, not addressed here.

This is that ADR. Since ADR-006, the operator runs **Dockhand**, which performs both
halves of the detection WUD did (upstream image updates) plus CVE scanning of pulled
images, and which can POST outbound notifications. That makes a third option available
that ADR-006 did not list: **push-based detection from a tool already deployed**, rather
than building the polling source ADR-004 designed.

## Decision

### Detection is pushed, not polled

Dockhand notifies; the registry receives. ADR-004's `UpstreamRegistrySource` /
`HomelabrepoDiscoverySource` / `ResolveLatestTag` design stays unimplemented.

The decisive advantage is that **Dockhand supplies the exact target tag**. ADR-004's
design note explains at length why tag interpretation needs a confidence-gated DSPy module
— Linuxserver's `1.32.8.1865-ls237`, Plex's date-based tags, and latest-only images all
defeat rule-based comparison. A push alert sidesteps that problem entirely: there is no
"which of these tags is newest" question to answer, so there is no `ResolveLatestTag` gate
to build, tune, or fail open. The patch generator receives the tag as a literal context
string and applies it.

ADR-004 is *advanced*, not superseded. Its three-way drift model (intended / actual /
available) still wants a repo-reading source, and an operator without Dockhand still has
no detection at all. This ADR fills the gap for the deployment that exists.

### The webhook opens proposals; it never mutates

`POST /webhooks/dockhand` (`src/registry_mcp/webhooks/dockhand.py`) resolves the alert to
a registered `Service` and calls the existing proposal engine. It writes nothing to the
registry, touches no container, and merges no PR. This is the same degree-3 boundary every
other write path in this server observes — the pull request and a human merge are the gate.

Concretely, the route contributes only *parsing and dispatch*: `ProposalEngine._open_proposal`
already carries duplicate suppression, target-file resolution, the DSPy confidence and
YAML-validity gates, branch/commit/PR creation, persistence, and notification. The
`context: str` parameter that `_open_proposal` and `PatchGenerator.generate` have carried
since ADR-005 — dead since `5036267` removed its only caller — is re-activated rather than
duplicated.

### Two payload shapes, and an honest failure mode

Dockhand's documented **generic webhook** body is flat prose:

```json
{"title": "Container updated: c1", "message": "image=sha256:new old_image=sha256:old",
 "agent": "Dockhand"}
```

The image references there are **digests, not tags**. A digest pins an exact image but
names no version that can be written into a `compose.yaml`. The webhook therefore accepts
two models — `DockhandStructuredAlert` (explicit `current_image` / `latest_image` /
`server` fields) and `DockhandGenericAlert` (the stock body) — and a generic alert whose
message yields no tag normalizes to `AlertKind.ignored` with a stated reason.

This is the important decision in the whole change: **the endpoint refuses to guess.**
Inventing a tag from a digest would produce a confidently wrong pull request against a
real compose file. Acknowledging and explaining is strictly better, and the response body
names the reason and points at `DOCKHAND_WEBHOOK_LOG_RAW_PAYLOAD` so the operator can see
the body for themselves.

### Vulnerability alerts, and the no-fix case

CVE alerts above `DOCKHAND_WEBHOOK_VULNERABILITY_MIN_SEVERITY` (default `high`) create a
`vulnerability_scan` proposal. Where Dockhand names a fixed image, this is an image bump
with CVE motivation and goes through the normal pipeline.

Where it does not, **no PR is opened**. There is no file change to propose, and an empty
or speculative PR is worse than none. The finding is persisted as a `Proposal` with
`status=rejected` and a `rejection_reason` naming the CVEs, and a notification fires — an
audit trail and an alert, without a broken pull request.

### Authentication is a bearer secret, because that is what exists

Dockhand does not HMAC-sign its outbound webhook bodies; it offers no signature option.
A shared secret presented as `Authorization: Bearer <secret>` (or `X-Dockhand-Token`),
compared with `hmac.compare_digest`, is the mechanism actually available. There is no
signature to verify, so none is implemented — noted here so a future reader does not
mistake its absence for an oversight.

**How the secret actually travels (established after the initial decision).** Dockhand's
webhook notification channel takes **Apprise-style URL schemes**, not plain `http(s)://`
URLs — its "Webhook URLs" field is populated with `gotify://`, `discord://`, `ntfy://` and
friends, and the generic-JSON channel among them is Apprise's `json://` (`jsons://` over
TLS). Apprise promotes any query parameter prefixed with `+` into an HTTP request header,
so the working configuration is:

```
json://<registry-host>:8765/webhooks/dockhand?+X-Dockhand-Token=<secret>
```

The `X-Dockhand-Token` header this endpoint accepts alongside `Authorization: Bearer` is
therefore the one to document: a `+Authorization=Bearer <secret>` form would need the space
percent-encoded. This does not change the decision above; it records how it is realized.

Two consequences follow. First, the body that actually arrives is Apprise's
`{"version": "1.0", "title": …, "message": …, "type": "info"}` — which the two-model union
already handles (`DockhandStructuredAlert` fails on the absent `event`/`container`,
`DockhandGenericAlert` accepts it, `extra="ignore"` drops `version`/`type`), but it means
the **generic** parse path is the one that runs in production and the structured path exists
for a payload template Dockhand may or may not support. Second, since the payload shape a
given Dockhand build sends cannot be known in advance,
`DOCKHAND_WEBHOOK_LOG_RAW_PAYLOAD` (default off) echoes one delivery body into the log so an
operator can settle it in a single click of Dockhand's Test button. It logs the body as one
string, so field-name secret redaction does not reach inside it — off by default, and the
setup procedure says to turn it back off. See
[SOP-002](../SOPs/SOP-002-Connect-Dockhand-Webhook.md).

Gating is fail-closed **at registration**, one step stricter than the removed WUD route
(which mounted unconditionally and returned 403 per request): disabled, or enabled with no
secret configured, leaves the route unmounted entirely. An unauthenticated endpoint never
exists, even briefly. This matches ADR-009's posture for `/chat`.

**Correction: Dockhand's built-in Webhooks channel is not Apprise (established against a
live instance, after the above).** The `+header` mechanism above is real — verified against
Apprise's own documentation — but testing it against an actual Dockhand deployment showed it
never reaches this endpoint at all. A direct `curl` with `X-Dockhand-Token` as a real header
succeeded immediately, proving this endpoint's own auth logic was never the problem. Dockhand
itself, configured with `?+X-Dockhand-Token=<secret>` (and, separately, the percent-encoded
`?%2BX-Dockhand-Token=<secret>`), consistently produced `403 unauthorized`. A `webhook.site`
capture of Dockhand's actual outbound request settled why: `User-Agent: node` (a native
Node.js sender, not the Python Apprise library), a payload shape of `{title, message, type,
environment, timestamp}` — not Apprise's own `{version, title, message, type}` — and the `+`
character stripped from the query key but never converted into a header; `X-Dockhand-Token`
just lands as an inert, ignored query-string parameter. Dockhand's "Webhooks" channel borrows
Apprise's scheme *names* (`json://`, `discord://`, ...) as a UI convenience; it does not run
requests through the real Apprise engine, so no amount of URL-encoding recovers the header.

The working fix is the escape hatch Dockhand's own dialog already documents for a provider
outside its built-in list: run a real Apprise engine — `caronc/apprise-api` — as a small
sidecar, store the header-carrying URL there (`json://<registry-host>:8765/webhooks/dockhand
?+X-Dockhand-Token=<secret>`, where `+` genuinely is honored because it's genuine Apprise),
and point Dockhand at `apprise://<apprise-api-host>:8000/<key>` instead of at this endpoint
directly. One more mismatch to plan for: `apprise-api`'s own `/notify/<key>` endpoint requires
a `body` field, while Dockhand's native payload uses `message` — if the `apprise://` scheme
doesn't produce a compatible request, `apprise-api` supports remapping via query parameters
(`json://<apprise-api-host>:8000/notify/<key>?:message=body`) as a documented fallback. See
[SOP-002](../SOPs/SOP-002-Connect-Dockhand-Webhook.md) for the full procedure.

A query-string token — having this endpoint also accept the secret as a plain query
parameter, which Dockhand's native sender *can* deliver without any sidecar — was considered
and rejected. A header value is not written to a reverse proxy's access log by default; a
query string is part of the logged request line. An operator running Traefik access logging
(a common homelab setup, including the one this was tested against) would end up with the
shared secret sitting in plaintext log files for as long as they're retained. The sidecar
costs an extra container; the query-string path costs a standing secret leak. This ADR keeps
the header requirement rather than relaxing it.

### Unactionable alerts answer 200

An unknown container, a container-state event, a below-threshold CVE, and a digest-only
payload all return `200` with `{"skipped": ...}` or `{"ignored": ...}`. A non-2xx makes
Dockhand retry, and these conditions never resolve on retry. Only a malformed payload
(`422`), a bad content type or body (`400`), a failed auth check (`403`), an oversized body
(`413`), or an internal fault (`500`) earn a non-2xx — each of those is worth either
retrying or fixing.

### Path: `/webhooks/dockhand`, not `/api/v1/webhooks/dockhand`

An `/api/v1` prefix advertises a versioned REST resource API that this server does not
expose and does not intend to (`docs/api/README.md`). A webhook receiver's contract is
versioned by its payload schema — which is exactly what the two-model union above handles
— not by its URL, so a version segment would be decoration that never increments.
`/webhooks/<source>` also matches the removed WUD precedent and leaves room for a
`/webhooks/komodo` beside it. The path remains configurable via `DOCKHAND_WEBHOOK_PATH`.

## Consequences

### Positive

- The update-triggered proposal path ADR-006 removed is restored, from a tool the operator
  already runs, with no new polling infrastructure and no tag-interpretation reasoning gate.
- CVE findings gain a staged, auditable response for the first time.
- `Proposal`'s `context` seam is live again; no parallel pipeline was added.
- No schema migration: `FindingType` is a `StrEnum` stored as a string, so
  `image_update` and `vulnerability_scan` are additive.

### Negative / accepted tradeoffs

- **The stock Dockhand generic webhook produces no proposals.** Its digest-only payload is
  acknowledged and ignored by design. Getting value from this endpoint requires configuring
  a structured payload on the Dockhand side; that is a deliberate refusal to guess, not a
  gap to patch later.
- Detection is only as good as Dockhand's coverage — a service Dockhand does not watch gets
  no alerts, and there is no reconciliation sweep to catch what the push missed.
- Alert-to-service matching is by container name (`store.get_service`). A container whose
  name differs from its registry entry is skipped rather than fuzzily matched; the reasoning
  layer is deliberately not consulted here, keeping the receive path deterministic.
- A service with no `host` cannot have its compose path resolved, so the engine returns an
  error rather than inferring one from the alert's `server` field — the webhook does not
  write to the registry, and curated fields stay sacred.

## Open Items

1. **`rejected` vs `snoozed` for a no-fix CVE.** `rejected` is currently used, since
   `rejection_reason` is the field that carries the explanation and the verification sweep
   already ignores non-open proposals. But the status semantically means "a patch failed a
   gate", not "no fix exists upstream". `ProposalStatus.snoozed` is unused and may be the
   better home, possibly with a re-check when a later alert names a fixed version.
2. **A newer update while a PR is open is skipped, not superseded.** `find_open(service_id,
   finding_type)` suppresses duplicates per service, so an alert for `1.32.2` arriving while
   the `1.32.1` PR is open is skipped (the response returns the open proposal so the
   operator can see which version is staged). Superseding — closing the stale PR and opening
   a fresh one — is deferred; it needs a policy for a PR that already has review comments.
3. **No verification sweep for these finding types.** `sweep_verifications` only clears
   proposals whose `auth_mode_conflict` resolved. An image-update PR merged outside the
   registry's view stays `open` until cancelled.
4. **Rate limiting.** The endpoint has none beyond the body-size cap. A misconfigured
   Dockhand could open one PR per service per alert storm; the duplicate gate bounds this to
   one open proposal per service per finding type, which is judged sufficient for a LAN
   deployment.
