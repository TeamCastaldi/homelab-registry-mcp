# Standard Operating Procedures (SOPs)

This folder contains SOPs and runbooks for repeatable tasks — procedures that need
to be followed consistently, whether by a developer, an operator, or an LLM
assistant.

SOPs are written for execution, not reading. Each one should be specific enough
that someone unfamiliar with the project can follow it without guesswork. See
[`SOP-001-Deploy-New-Service.md`](SOP-001-Deploy-New-Service.md) for an example.

## What belongs here

- Environment setup and onboarding procedures
- Deployment and release procedures
- Backup and recovery / cold-restore runbooks
- Incident response procedures
- Any task that is repeated and must be done the same way each time

## What does not belong here

- One-off scripts (those go in `scripts/`)
- Architecture decisions behind the procedures (those go in `docs/ARDs/`)
- High-level plans or roadmaps (those go in `docs/plans/`)

## Naming convention

`SOP-NNN-short-description.md` — e.g. `SOP-001-Deploy-New-Service.md`

## Recommended SOP structure

Each SOP should include:
- **Purpose** — what this procedure accomplishes
- **When to use it** — trigger conditions
- **Prerequisites** — what must be true before starting
- **Steps** — numbered, atomic, verifiable
- **Verification** — how to confirm it worked
- **Rollback** — how to undo it if something goes wrong
- **Escalation** — what to do if the SOP fails
