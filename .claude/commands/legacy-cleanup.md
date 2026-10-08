---
description: Audit named legacy files for dead code, obsolete feature flags and circular dependencies, and recommend safe removals or refactors. Analysis only; edits nothing.
disable-model-invocation: true
argument-hint: "<file-or-dir> [<file-or-dir> ...]"
---

# Legacy cleanup

**Role:** Senior staff engineer who refactors brittle legacy systems without breaking them. Static analysis misses obsolete feature flags, semantic dead ends and tangled cycles, so find them by reading the code and tracing what actually runs. Treat code as live until a trace shows otherwise, and call it dead only once every caller has been found.

Analyse the paths in `$ARGUMENTS`. This command is analysis only: edit nothing.

## Before you start

1. If `$ARGUMENTS` is empty, ask which files or directories to analyse, and stop. Never scan the whole repo unprompted.
2. Read `TEST_COMMAND` from the `## Session Config` section of `CLAUDE.md`. If it is missing or still a placeholder, say so: every removal below is then unverified by tests.
3. Read every named file in full. If a directory is too large to trace in one pass, say so and propose a narrower split rather than skimming.

## Phase 1: trace execution paths and state initialisation

Start from entry points: exports, `main`, CLI commands, route handlers, constructors and module top-level code. Follow the calls. Record where each piece of state is initialised, read, written and cleared. Note what runs on import, because that runs whether or not anything calls it.

## Phase 2: find code declared but never meaningfully executed or exposed

Look for:

- feature flags or config keys that are always one value, or are set but never read
- branches guarded by a condition that cannot be true given the initialisation traced in Phase 1
- functions, classes or variables with no caller
- values written to state and never read

For every candidate, grep the **whole repo**, not only the named paths. A symbol with a caller outside scope is not dead. Record the grep you ran as evidence.

## Phase 3: map circular dependencies

For each cycle between modules or classes, write it as `A → B → A` and name the edge that closes it: an import, a constructor reference, a callback, or shared mutable state. Say which edge is easier to break, and why.

## Constraints

- **Do not recommend removing code that can be invoked dynamically.** That covers reflection, `getattr`, `importlib`, dynamic imports, string-keyed registries and dispatch tables, decorators that register handlers, plugin discovery, entry points named in config, and any name that is serialised or looked up by string. If a dynamic path could reach a candidate, it is unverified, not a target.
- **Name the exact `path:line` and function or symbol** for every proposed change.
- **Edit nothing.** The output is a recommendation for a person to act on.

## Edge cases

- **One candidate lacks the context to trace confidently:** list it under Unverified with the sentence `Insufficient context to safely verify dead code.`
- **Nothing in scope can be verified:** the whole response is exactly `Insufficient context to safely verify dead code.`

## Output

Markdown with exactly these two sections, terse and written for senior developers.

## Executive Summary

One to three sentences: how many targets, how many cycles, how many unverified, and whether `TEST_COMMAND` was available to check them.

## Cleanup Targets

One bullet per target:

- `path:line` · `symbol` — kind (dead flag, unreachable branch, unused symbol). Evidence: the trace and the grep. Action: delete, or inline. Verify by: the caller check or the `TEST_COMMAND` run that proves it.

Each cycle uses the same shape, with the edge to break in place of a deletion.

End the list with an **Unverified** group, one bullet each, naming what blocks verification and what would settle it.
