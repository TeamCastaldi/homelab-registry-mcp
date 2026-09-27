## Session Goals
Enrich the new PatchMon integration (ADR-020) with PatchMon's Integration API and
Ansible dynamic inventory. Along the way, pick one execution path (PatchMon's
trigger API vs Ansible) for simplicity, and write the patch playbook in the
homelab repo.

## Accomplishments
- **Found that ADR-020's primary path couldn't work.** PatchMon's source (commit
  `0557606`) mounts `POST /api/v1/patching/trigger` behind `AuthWithSessionCheck`:
  it accepts only a logged-in user's JWT access token (`JWT_EXPIRES_IN`, 1h
  default). A static `PATCHMON_API_TOKEN` died within the hour and every approval
  took the Ansible fallback. The docs site was unreachable (egress policy), so the
  source was the reference throughout.
- **homelab-registry-mcp**, 8 commits on `claude/peaceful-cannon-g1jpw7` →
  [TeamCastaldi/homelab-registry-mcp#176](https://github.com/TeamCastaldi/homelab-registry-mcp/pull/176):
  - `patching/executor.py` runs only `PATCHMON_ANSIBLE_PLAYBOOK`. `can_execute`
    needs only the playbook; the cfg/key paths stay read-only mode's gate, so the
    links still mount and refuse.
  - Dropped `patchmon_callback_url` (ignored if sent) and
    `PatchApproval.payload_callback_url` (the nullable column stays in deployed DBs).
  - Retired `PATCHMON_CALLBACK_URL` / `PATCHMON_API_TOKEN` /
    `PATCHMON_CALLBACK_TIMEOUT_SECONDS` via `_RETIRED_PREFIXES`. The webhook
    feature now needs the playbook, `ANSIBLE_CFG_PATH` and `SSH_KEY_PATH`.
  - New `integrations/patchmon/client.py`: HTTP Basic, `host:get` only, GET only,
    UUID-checked ids, no redirects. Settings: `PATCHMON_API_URL/_KEY/_SECRET/_TIMEOUT_SECONDS`.
  - Host resolution: a native alert whose `host_name` isn't a plain inventory name
    is looked up by `host_id` → `hostname`. Same-id check, same allowlist, 8s
    budget. A plain name is never looked up.
  - New `webhooks/patchmon_details.py`: the email's "What PatchMon last saw"
    section (packages security-first, capped at 20; reboot status and reason;
    kernels; last report). Best effort, 8s budget.
  - New `webhooks/common.one_line`: every PatchMon value, plus the alert's
    title/message/severity, is flattened to one line so it can't forge a plain-text
    `Approve:` line.
  - Docs: ADR-020 amendment; SOP-007 rewritten (API credential step,
    dynamic-inventory caveats, passwordless sudo prerequisite); CLAUDE.md,
    SETUP.md, README updated.
  - Tests: 948 → 979, all green; ruff clean. Every new safety property was
    mutation-probed (each probe failed its own test).
- **ncastaldi/homelab**, 2 commits → [ncastaldi/homelab#32](https://github.com/ncastaldi/homelab/pull/32):
  - `ansible/playbooks/patch-host.yml`: one-host guard, apt only,
    `upgrade: safe`, `lock_timeout: 300`, no reboot, one-line summary.
  - `ansible/playbooks/passwordless-sudo.yml`: one-time, `-K`, visudo-validated drop-in.
  - ADR-005 (the user chose full NOPASSWD over a wrapper script); CLAUDE.md and
    ADR index updated.
  - Compose pass-throughs for the seven `PATCHMON_*` settings (webhook off by default).

## Technical Debt / Pending
- **Nothing is validated live yet.** No real PatchMon instance or host was touched.
  The `patch-host.yml` apt task never ran: this sandbox's system Python is broken,
  so only `--syntax-check`, ansible-lint and the one-host guard were exercised.
- **Release version:** two commits are `!` (breaking), so release-please will
  propose 2.0.0 unless the release PR is edited to 1.12.0. Decide before merging #176.
- **ADR-005 widens the control-plane SSH key to root on every inventory host.** The
  narrower wrapper-script design is recorded in ADR-005 as the way back.
- **Empty `PATCHMON_*` values from compose** (unset in Infisical) will show up in
  `config_status` as empty or same-as-default noise. That's harmless, and matches
  how the stack's other pass-throughs already behave.
- **Removing the empty-hostname check in the lookup** still ignores the alert,
  only with a vaguer reason: that check affects the message, not safety.
- No snapshot was written for the ADR-020 session itself (v1.11.0).
- Still open from before: mutation-remediation Phases 1–7, and the two stale
  Dependabot PRs (#80 mcp `<3`, #63 actions).

## Next Steps
- Check CI on #176 and #32, and address any review comments.
- Run in order, from the control plane:
  1. `ansible-playbook -i ansible/inventory.yml ansible/playbooks/passwordless-sudo.yml -K --check --diff`, then without the check flags.
  2. `ansible-playbook -i ansible/inventory.yml ansible/playbooks/patch-host.yml --limit <host> --check --diff`.
- In PatchMon: create an "API" credential with `host: get` only, and a webhook
  destination with a signing secret.
- In Infisical: set `PATCHMON_WEBHOOK_ENABLED/_SECRET`, `PATCHMON_APPROVAL_BASE_URL`
  and `PATCHMON_API_URL/_KEY/_SECRET`; remove any `PATCHMON_API_TOKEN` /
  `PATCHMON_CALLBACK_URL`.
- Merge #176 (choose 2.0.0 or 1.12.0), let the release publish, merge #32, and
  redeploy through Dockhand.
- Then SOP-007 steps 6–7 (signed test alert, then a real PatchMon alert). Confirm
  a friendly-named host resolves and the email lists its packages.
- Follow-ups from the ADR-020 open items: read-only `patchmon_*` MCP tools, and a
  post-run check that pending counts dropped.
