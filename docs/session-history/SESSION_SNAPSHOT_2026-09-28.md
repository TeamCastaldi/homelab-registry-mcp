## Session Goals
Carry the Patchmon rework (TeamCastaldi/homelab-registry-mcp#176) through to the
hosts: grant passwordless sudo, run the first real patch with `patch-host.yml`,
and log whatever that turned up.

## Accomplishments
- **Shipped:** #176 merged and release-please cut **v2.0.0** (#177; the two `!`
  commits made it a major). On the homelab side, ncastaldi/homelab#32
  (playbooks, ADR-005, compose pass-throughs) and #33 (sudo-rs note) merged.
- **Troubleshot the `p1ollama` become timeout** (Ubuntu 26.04):
  `Timeout (12s) waiting for privilege escalation prompt`, reported UNREACHABLE.
  - Cause: Ubuntu 25.10+ makes sudo-rs the default `sudo`. It wraps a custom
    `-p` prompt as `[sudo: <prompt>] Password:`, and ansible-core's
    `check_password_prompt` only matches a line that starts with its prompt
    (still true in 2.21.1).
  - Confirmed with `-e ansible_become_exe=/usr/bin/sudo.ws`.
  - Fixed on the host with `update-alternatives --set sudo /usr/bin/sudo.ws`
    (`visudo` followed). Logged in the homelab `CLAUDE.md` (#33).
- **`passwordless-sudo.yml` applied to all four inventory hosts.**
  `ansible all -m ping --become` now works with no `-K`.
- **First real `patch-host.yml` run (p1ollama)**: 26 packages upgraded,
  0 removed, reboot flagged and not done. A second run showed `changed=0`,
  `nothing to upgrade`, so it's idempotent.
- **Found a restart-policy gap:** the `docker-ce` upgrade restarted the daemon,
  and `openwebui-openwebui-1` (`restart=no`) stayed down. A repo-wide scan
  found 7 services with no `restart:` policy on heimdall, ollama and waldorf.
  Logged in the homelab roadmap, ncastaldi/homelab#34 (open).

## Technical Debt / Pending
- **Open WebUI on p1ollama is down.** `docker update --restart unless-stopped`
  took, but `docker start` fails: `open /run/nvidia-persistenced/socket: no such
  file or directory`. The same run upgraded `nvidia-container-toolkit`, and the
  host has a reboot pending. Unconfirmed whether the reboot fixes it.
- **The 7 services without `restart:` would go down on any approved patch that
  upgrades Docker.** Fix before the PatchMon flow goes live.
- **Nothing is validated against live PatchMon yet.** No API credential, no
  webhook destination, no Infisical values. Check whether v2.0.0's `publish.yml`
  redeploy ran and the container is on 2.0.0.
- **ADR-005:** the control-plane SSH key is now root-equivalent on all inventory
  hosts.
- Carried over: mutation-remediation Phases 1–7; Dependabot #80 and #63.

## Next Steps
1. Reboot p1ollama: `ansible p1ollama -i ansible/inventory.yml -b -m ansible.builtin.reboot`.
   Then run `docker start openwebui-openwebui-1`. If the NVIDIA socket error
   persists, take it through troubleshooting as its own problem.
2. Merge ncastaldi/homelab#34. Then add `restart: unless-stopped` to the seven
   services, one PR per node.
3. Confirm the registry container is on 2.0.0 (the redeploy via Dockhand).
4. PatchMon: create an "API" credential with `host: get` only, and a webhook
   destination with a freshly generated secret.
5. Infisical: set the `PATCHMON_*` values, redeploy, then run SOP-007 steps 6–7.
6. Follow-ups: read-only `patchmon_*` MCP tools; a post-run pending-count check.
