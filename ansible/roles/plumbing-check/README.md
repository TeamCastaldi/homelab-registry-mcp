# plumbing-check

Confirms Ansible plumbing works end-to-end against one node by exercising
the five most common ad-hoc operations as ordinary tasks: `ping`, `setup`,
`command`, `copy`, and `service` (status-check only).

This role ships with `homelab-registry-mcp`, not with an operator's private
homelab repo. It complements `docker-stack-deploy` — where that role
deploys one compose stack, this one verifies the underlying Ansible
plumbing (connectivity, fact-gathering, command execution, file delivery,
service inspection) is actually working, independent of any specific
deploy. See
[`docs/SOPs/SOP-004-Verify-Ansible-Control-Plane-Plumbing.md`](../../../docs/SOPs/SOP-004-Verify-Ansible-Control-Plane-Plumbing.md)
for the operator-facing verification procedure this role backs.

## What it does, and does not, do

- **Ping** — confirms basic connectivity (`ansible.builtin.ping`).
- **Setup** — gathers facts and reports OS/hostname.
- **Command** — runs a configurable diagnostic command (default `uptime`)
  and reports its output. Never uses `shell` where `command` suffices.
- **Copy** — delivers a small marker file with `backup: true`, so any
  existing file at the target path is preserved as a `.bak` rather than
  silently overwritten.
- **Service** — uses `ansible.builtin.service_facts` (read-only) to report
  a named service's state. **Never** starts, stops, restarts, enables, or
  disables anything — there is no `ansible.builtin.service:` task in this
  role, deliberately.

## Required variables

None — every variable has a default (see below). `plumbing_check_service_name`
is unset by default, which skips the service-status report entirely rather
than failing.

## Optional variables (see `defaults/main.yml`)

| Variable | Default | Meaning |
|---|---|---|
| `plumbing_check_diagnostic_command` | `uptime` | Command run by the diagnostic step |
| `plumbing_check_target_file` | `/tmp/plumbing-check-marker.txt` | Where the marker file is written |
| `plumbing_check_target_file_content` | a static string | Marker file content — deliberately not timestamped, so a Molecule idempotence check (converge twice, expect zero changes on the second run) passes by default |
| `plumbing_check_service_name` | unset | Name of a systemd service to report the status of; the report step is skipped entirely if unset |

## Example

```bash
ansible-playbook -i inventory.yml ansible/playbooks/verify-plumbing.yml \
  -e target_node=heimdall
```
