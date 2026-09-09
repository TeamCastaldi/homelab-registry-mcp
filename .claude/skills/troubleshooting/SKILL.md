---
name: troubleshooting
description: >
  Structured debugging and incident response for Nathan Castaldi — a gated
  Observe → Theorize → Act workflow that finds root cause instead of patching
  symptoms. Covers code repositories (failing tests, stack traces, regressions
  after a commit) and the Team Castaldi homelab (Docker Compose, Traefik v3,
  traefik-kop, Authentik, NFS mounts, Ansible, any castaldifamily.com
  subdomain, and the nodes homelab-control-plane, heimdall, waldorf,
  panoptichron). Use this skill whenever Nathan reports something broken,
  failing, erroring, crashing, hanging, unreachable, or behaving unexpectedly
  — pastes an error message, stack trace, or log excerpt; says a container
  won't start, a service 404s or 502s, a cert won't issue, a mount is gone, a
  playbook fails, a test suite broke, or "this worked yesterday". Also use it
  for live outages needing severity triage and status updates, and for writing
  a blameless postmortem afterward. Trigger it even when he doesn't name it —
  a pasted error is enough on its own.
---

# Troubleshooting

Find the root cause, not the symptom, and fix it without breaking something
adjacent.

This skill replaces the older `devops-sre`, `engineering:debug`, and
`engineering:incident-response` skills. If any of those are still installed
and trigger alongside this one, follow this skill and mention the collision
to Nathan so he can remove them.

## Why this is gated

Nathan stops between phases on purpose. The failure mode this guards against
is confident momentum: reading a symptom, pattern-matching to a familiar
cause, and shipping a fix for a problem that was never the actual problem.
The gates cost seconds and catch that early, when it is still cheap.

They also matter because most of what breaks here is either running
infrastructure his family uses or a repository he has to live in afterward. A
wrong fix applied fast is worse than a right fix applied slowly.

Do not proceed past a gate on implied approval. "Sounds right", "yeah", or
silence are not the confirmation phrase. Ask again rather than assuming.

## Step 0 — Classify and route

Before gathering anything, decide which situation this is. Read the matching
reference file — it holds the diagnostic commands, known failure patterns,
and safety rules for that domain. Do not load all three.

| Signal | Read |
|---|---|
| Failing test, stack trace, regression after a commit, application logic | [references/code-repo.md](references/code-repo.md) |
| Container, compose, Traefik, routing, TLS, Authentik, NFS, Ansible, a `castaldifamily.com` subdomain | [references/homelab.md](references/homelab.md) |
| Something is down right now, users or family are affected, or a postmortem is being written | [references/incident.md](references/incident.md) |

Live outage takes priority over root cause. If a service is currently down,
read `incident.md` first and stabilize — communication and mitigation come
before diagnosis. Return to Step 1 once it is contained.

Ambiguous cases are common and usually resolve with one question. A failing
CI job that only fails inside a container could be either; ask which layer he
suspects rather than guessing and loading the wrong reference.

## Step 0.5 — Establish what you can actually see

Diagnostic ability differs sharply by surface, and pretending otherwise
produces confident nonsense built on invented file contents.

**In Claude Code or VS Code** — you have a shell. Detect rather than ask:

```bash
# Test command: check in this order, first hit wins
cat Makefile 2>/dev/null | grep -E '^(test|check):'
cat package.json 2>/dev/null | grep -A5 '"scripts"'
cat pyproject.toml tox.ini pytest.ini 2>/dev/null | grep -A5 -E '\[tool\.(pytest|poetry)|testenv'

# Source root and logs
ls -d src app lib backend server 2>/dev/null
ls -d logs log var/log 2>/dev/null
```

State what you detected before using it — "detected `npm test`, source in
`src/`" — so a wrong guess gets corrected before it shapes the diagnosis.

**In Claude.ai chat** — you cannot reach his machine. Two real options:

- Homelab problems: use the Komodo MCP and Registry MCP connectors. These
  give live container state, service topology, and Traefik routing. Prefer
  them over anything written in a reference file.
- Version-specific config syntax or behavior, on either surface: use
  Registry MCP's `get_service_documentation(service_name, version, topic?)`
  tool rather than trusting memory. It relays official, version-pinned
  documentation from documentation-mcp — a Traefik v3 label name, a Qdrant
  v1.19 API shape, or an Authentik v2026.x config key can all differ from
  what an earlier or later version used, and guessing produces the same
  "confident nonsense" this section already warns about. `version` is
  required and never inferred — pull the actual pinned version from the
  compose file or `registry_get_service` before calling it, don't guess
  that either.
- Code problems: ask him to paste the output. Request specific commands, not
  "send me your logs" — name the exact command you want run.

Never fabricate a file path, log line, test name, or command output. If a
detail is needed and unavailable, say which command would produce it and
wait.

## Step 1 — Observe

Gather evidence before forming any theory. The reference file for this domain
lists the specific commands worth running; the goal here is the same in all
three cases — establish what broke, when, and how far it reaches.

For code repositories, recent history is usually the fastest lead:

```bash
git log -n 5 --oneline
git diff HEAD~1
git status
```

If the failure has a clean pass/fail test and the history is long, `git
bisect` beats reading diffs — see `references/code-repo.md`.

Present:

- **Symptom** — the exact error or unexpected behavior, quoted verbatim
- **First seen** — when it started, and after what change
- **Affected scope** — one function, one endpoint, one container, one node,
  or everything

If the scope is still unknown after gathering, say so rather than narrowing
it on a hunch. An honest "scope unclear, here is what would determine it" is
more useful than a confident wrong boundary.

> [!IMPORTANT]
> **Gate 1 — confirm symptom.** Nathan replies `CONFIRMED: <symptom summary>`.
> If his summary differs from yours, his wins — that difference is usually
> the most valuable signal in the whole session.

## Step 2 — Theorize

Form one hypothesis and make it falsifiable.

- Name the most likely failure point: file, function, line, container, label,
  or playbook task
- Explain the mechanism — why this produces that symptom, not just that the
  two correlate
- Check the evidence that would disprove it, and say whether you looked
- If the theory depends on how a specific version of a homelab service is
  supposed to behave or be configured, check `get_service_documentation`
  before presenting it — don't stake a theory on remembered syntax that may
  not match the pinned version
- Confirm the proposed fix preserves existing behavior

Present:

- **Root cause theory** — what is broken and why
- **Evidence** — what supports it, and what would falsify it
- **Proposed fix** — specific file and change, smallest version that works
- **Risk** — what this could break, including anything downstream

Competing theories are fine and often honest. Present the strongest one
first, note the alternative, and say what evidence would separate them.
Manufacturing certainty to sound decisive is the thing this step exists to
prevent.

> [!IMPORTANT]
> **Gate 2 — validate theory.** Nathan replies `THEORY: APPROVED`.

## Step 3 — Act

Deliver the smallest change that fixes the root cause.

1. **File path** — exact path to what changes
1. **Code change** — the specific diff, not the whole file
1. **Verification command** — the detected test command, or the domain's
   verification steps from the reference file

Resist scope creep. Adjacent problems noticed along the way get named in the
wrap-up, not fixed in the same change. A diff that also cleans up formatting
or renames a variable is a diff whose failure cannot be attributed.

> [!WARNING]
> Never propose a destructive command — `rm -rf`, volume deletion, `git reset
> --hard`, force push, database drop — without an explicit backup or snapshot
> step first, and without saying plainly what is lost if it goes wrong.

> [!IMPORTANT]
> **Gate 3 — confirm fix.** Nathan replies `FIXED` or `STILL BROKEN: <new
> symptom>`.
>
> On `STILL BROKEN`, return to Step 1 with the new symptom as input. Carry
> forward what the failed fix ruled out — that is now evidence. Do not
> restart from zero, and do not immediately propose the second-best theory
> from the last round without re-observing; the first fix may have changed
> the system.

## Wrap-up

Runs automatically once Gate 3 clears. No command needed — the value is in
capturing root cause while it is still fresh, which is exactly the step that
gets skipped when it requires deliberate invocation.

Produce:

- **Root cause and fix** — one paragraph, plain language, written so it makes
  sense to him in six months with no memory of this session
- **Documentation call** — whether this warrants an ADR, a spec update, an
  SOP, or an addition to a reference file in this skill. Say which and why,
  or say none is needed. A recurring failure that took three sessions to
  diagnose belongs in `references/`; a typo does not.
- **Deferred items** — anything noticed and deliberately not fixed
- **Commit message** — a Conventional Commit, e.g. `fix(traefik): add missing
  certresolver label to jellyfin router`

Hand the actual commit and push to `dev-session-manager` (`/commit-msg`,
`/session-end`) rather than duplicating that workflow here. If the fix was to
running infrastructure rather than a repository, there may be nothing to
commit — say so instead of inventing a commit message.
