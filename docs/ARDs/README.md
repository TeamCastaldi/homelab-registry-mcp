# Architecture Decision Records (ARDs)

This folder contains Architecture Decision Records for the project. Each record
documents a significant technical or structural decision — what was decided, why,
what was ruled out, and what the consequences are.

Records are written when a decision is made and updated if circumstances change.
They are not deleted — superseded decisions are marked as such and kept for
historical context.

> **Naming note:** existing files use a mix of `ADR-` and `ARD-` prefixes for
> historical reasons. Prefer the `ADR-NNN-` convention below for new records;
> existing files are not renamed.

## What belongs here

- Technology choices (language, framework, database, external APIs)
- Structural patterns (provider/adapter pattern, module boundaries)
- Constraint decisions (read-only upstreams, Git-only write path, LAN-only scope)
- Anything where future-you (or a new collaborator) would ask "why did we do it
  this way?"

## What does not belong here

- Implementation details (those go in `docs/specs/`)
- Project timelines or milestones (those go in `docs/plans/`)
- Runbooks or procedures (those go in `docs/SOPs/`)

## Naming convention

`ADR-NNN-short-description.md` — e.g. `ADR-001-Homelab-Control-Plane.md`

## Status values

| Status | Meaning |
|--------|---------|
| Accepted | In effect, follow this decision |
| Draft | Under discussion, not yet binding |
| Deprecated | No longer relevant but kept for history |
| Superseded | Replaced by a later record — link provided |
