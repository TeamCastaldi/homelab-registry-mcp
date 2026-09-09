# Incident reference

For when something is down **right now**. The ordinary Observe → Theorize →
Act sequence assumes there is time to be careful. An active outage inverts
that: stabilize first, diagnose after.

## Contents

- [First five minutes](#first-five-minutes)
- [Severity](#severity)
- [Status updates](#status-updates)
- [Returning to root cause](#returning-to-root-cause)
- [Postmortem](#postmortem)

## First five minutes

1. **Assess blast radius** — what is down, who is affected, is it degraded or
   fully unavailable
1. **Check for a recent change** — a deploy, a compose edit, a playbook run,
   an image pull. Most outages are self-inflicted and recent
1. **Prefer rollback over fix** — if a change caused it, reverting is faster
   and safer than diagnosing forward. Diagnose after service is restored
1. **Note the time things happened** as you go. Timeline reconstruction after
   the fact is guesswork; captured timestamps are evidence

> [!IMPORTANT]
> Mitigation and root cause are different goals with different urgency. It is
> correct to restore service with a change you do not fully understand, then
> investigate properly once nothing is on fire. Say explicitly when you are
> doing this so the unexplained part does not get forgotten.

Rollback shortcuts:

```bash
# Compose stack — revert file, redeploy
git checkout ~/homelab/nodes/<node>/<service>/compose.yaml
docker compose -f ~/homelab/nodes/<node>/<service>/compose.yaml up -d --force-recreate

# Application — revert the commit, do not amend history mid-incident
git revert <sha>
```

## Severity

This is a homelab and a set of personal projects, not an enterprise on-call
rotation. Scale the response to the actual stakes — a SEV1 template for a
media server is theater, and treating a real outage casually is worse.

| Level | Criteria | Response |
|---|---|---|
| High | Family-facing service fully down (Plex, SSO, anything gating other services), or data at risk | Stop other work, mitigate now |
| Medium | A service degraded or one non-critical service down | Same session, but no need to abandon everything |
| Low | Cosmetic, or affects only Nathan's own tooling | Normal troubleshooting flow, no incident handling |

Authentik going down is High regardless of what it is protecting — everything
behind ForwardAuth becomes unreachable with it. Same for Traefik and the NFS
mounts: their failure cascades far past the service itself.

## Status updates

Only worth writing when someone other than Nathan is affected and waiting —
family who cannot reach a service, or a collaborator on a shared project.

Keep it factual: what is happening, who is affected, what is being done, when
the next update comes. No speculation about cause in a status update; a wrong
guess broadcast early is expensive to walk back.

```markdown
**Status:** Investigating | Identified | Monitoring | Resolved
**Impact:** [what is unavailable, for whom]
**Current:** [what is known now]
**Next:** [action in progress, and when the next update lands]
```

## Returning to root cause

Once service is restored, go back to Step 1 of the main workflow with what
was learned during mitigation as evidence. The rollback that fixed it is a
strong signal about cause but is not itself the diagnosis — knowing which
change broke it is not the same as knowing why it broke.

If a mitigation is still in place that is not a real fix — a disabled
healthcheck, a pinned old image, a bypassed middleware — name it explicitly
in the wrap-up as a deferred item. Temporary workarounds become permanent
precisely because nobody wrote them down.

## Postmortem

Worth writing when the outage lasted long enough to be annoying, recurred, or
had a cause that was not obvious. Skip it for a five-minute self-inflicted
typo — the ceremony costs more than the lesson.

Blameless means focused on systems, not on who typed the command. In a
one-person homelab that framing still matters: "I was careless" produces no
change, while "a compose edit was applied without `config` validation first"
produces a check that prevents it.

```markdown
## Postmortem: [title]

**Date:** [date] | **Duration:** [how long] | **Severity:** [High/Medium/Low]

### Summary
[2-3 sentences, plain language]

### Impact
[What was unavailable, to whom, for how long]

### Timeline
| Time | Event |
|---|---|
| [HH:MM] | [event] |

### Root cause
[What actually caused it, and the mechanism]

### Why it was not caught sooner
[Missing monitoring, no validation step, silent failure mode]

### What went well
[What made this shorter than it could have been]

### Action items
| Action | Priority | Status |
|---|---|---|
| [action] | [P0/P1/P2] | [open] |
```

The "why it was not caught sooner" section is the one that pays for itself.
Root cause tells you what broke; that section tells you what to build so the
next one surfaces faster.

Consider whether the failure pattern belongs in
[homelab.md](homelab.md#common-failure-patterns) or
[code-repo.md](code-repo.md#common-failure-patterns). A pattern that took real
effort to diagnose should be cheap to diagnose the second time.
