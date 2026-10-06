## Session Goals
Plan, without writing code, an HTMX + Jinja2 diagnostic dashboard fed by Glances,
built into homelab-registry-mcp.

## Accomplishments
- Tested the brief's browser-polls-Glances design against the code and upstream docs.
  Four things block it: mixed content, Glances returning JSON where HTMX expects HTML,
  htmx 2 refusing cross-origin requests, and a CORS preflight on every poll. Also,
  Glances 4 serves only /api/4.
- Settled eight decisions:
  - the registry polls Glances, not the browser;
  - stale on a missed poll, offline after 3, then 30s polling;
  - metrics every 5s, containers every 30s;
  - Glances 4.x;
  - Authentik ForwardAuth plus an in-app secret header;
  - a per-node glances_url;
  - CPU % and memory % only;
  - containers only, no VM integration.
- Wrote and approved docs/plans/2026-10-glances-dashboard.md. It covers the file layout,
  settings, the glances_url migration, the Glances client and parsers, the host-state
  logic, routes, templates, sorting, security notes, 10 build steps with their own
  checks, and an end-to-end check.

## Technical Debt / Pending
- Nothing is implemented yet. Glances isn't deployed anywhere, so its response field
  names come from its docs only.
- Anyone who can read Traefik's API or run `docker inspect` can read the secret header.
  Checking Authentik's signed JWT is the follow-up.
- Still open from before: live Patchmon validation (SOP-007 steps 6–7),
  mutation-remediation Phases 1–7, and Dependabot PRs #80 and #63.

## Next Steps
- Start step 1 of docs/plans/2026-10-glances-dashboard.md (`uv add 'jinja2>=3.1'`),
  then steps 2–10 in order, each checked before the next.
- Before finishing step 9's SOP, deploy Glances 4.x on one node and compare its live
  JSON with what parse.py expects.
