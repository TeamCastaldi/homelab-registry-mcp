# Code repository reference

Diagnostic patterns for application and repository failures — failing tests,
stack traces, regressions, and behavior that diverges from expectation.

## Contents

- [Detecting project conventions](#detecting-project-conventions)
- [Reading a stack trace](#reading-a-stack-trace)
- [Finding the breaking change](#finding-the-breaking-change)
- [Common failure patterns](#common-failure-patterns)
- [Verifying a fix](#verifying-a-fix)

## Detecting project conventions

Never assume the test command. `pytest tests/ -v` is a reasonable default for
one repo and wrong for the next. Detect, then state what was detected so a bad
guess is corrected before it shapes the diagnosis.

```bash
# Test command — first hit wins
grep -E '^(test|check|lint):' Makefile 2>/dev/null
jq -r '.scripts' package.json 2>/dev/null
grep -A10 -E '\[tool\.(pytest|poetry)\]|\[testenv\]' pyproject.toml tox.ini 2>/dev/null
ls .github/workflows/*.y*ml 2>/dev/null   # CI config shows the real command

# Source root
ls -d src app lib backend server cmd internal 2>/dev/null

# Logs
ls -d logs log var/log tmp 2>/dev/null
```

CI workflow files are the most reliable source — they hold the command that
actually has to pass, including flags a local `Makefile` may omit.

If detection returns nothing, ask. Naming the command Nathan runs takes him
five seconds and beats guessing.

### When there is no shell

In Claude.ai chat there is no access to his filesystem. Ask for specific
command output rather than "send me your logs":

> Run `npm test 2>&1 | tail -40` and paste the output.

Name the exact command. Vague requests produce vague pastes and another round
trip.

## Reading a stack trace

Work from the bottom up. The deepest frame is where it threw; the frame that
matters is usually the last one in his own code before it enters a library.

- **Exception type** narrows the class of cause more than the message does.
  `KeyError` and `AttributeError` on the same line mean very different things
- **The last first-party frame** is where to look, not the library frame
  underneath it
- **`None` / `undefined` in the message** means something upstream returned
  empty. The bug is where it became empty, not where it was dereferenced
- **Truncated traces** hide the real cause. Ask for the full trace, including
  any `During handling of the above exception, another exception occurred` or
  `Caused by:` chain — the chained cause is often the actual root

Quote the exact error verbatim in the Step 1 symptom. Paraphrased errors lose
the specific token that identifies the failure.

## Finding the breaking change

If it worked before, the diff is the fastest lead.

```bash
git log -n 10 --oneline
git log -n 5 --stat                      # which files changed
git diff HEAD~1                          # what changed last commit
git log -S "<symbol>" --oneline          # commits touching a specific symbol
git log --since="3 days ago" --oneline   # if he knows roughly when it broke
```

### Bisect

When the history is long and there is a clean pass/fail test, `git bisect`
beats reading diffs. It is worth proposing whenever "it worked last week" and
more than about ten commits have landed since.

```bash
git bisect start
git bisect bad                    # current commit is broken
git bisect good <known-good-sha>
# git checks out a midpoint; run the test, then:
git bisect good    # or: git bisect bad
# repeat until it names the commit
git bisect reset
```

Automate it when the test is scriptable:

```bash
git bisect start HEAD <known-good-sha>
git bisect run <test-command>
```

> [!NOTE]
> Bisect needs a deterministic test. A flaky test produces a confidently wrong
> answer — confirm the failure is reproducible before starting, or the whole
> run is wasted.

## Common failure patterns

### Works locally, fails in CI

Almost always environment, not logic. Check in this order:

1. Dependency versions — lockfile committed? CI installing from lockfile or
   resolving fresh?
1. Environment variables and secrets present in CI
1. Filesystem case sensitivity — Linux CI on a macOS-authored repo
1. Test ordering and shared state — CI may run in a different order or in
   parallel
1. Absolute paths, or anything assuming the developer's home directory

### Works in staging, fails in production

1. Config and env var differences — diff them explicitly rather than assuming
1. Data shape. Production has rows staging does not: nulls, unicode, very old
   records, much larger payloads
1. Scale — connection pool exhaustion, timeouts, memory limits
1. Version drift between the two environments

### Intermittent or flaky

Treat as a real bug, not noise. Usual causes:

1. Test order dependence — run the failing test alone, then run the suite with
   a fixed seed to reproduce
1. Shared mutable state between tests
1. Real time or timezone dependence
1. Race conditions in async code
1. External network calls that should be mocked

### Dependency-related

1. Did the lockfile change? `git log -p -- package-lock.json poetry.lock`
1. Transitive upgrade — the direct dependency did not change but something
   underneath it did
1. Check upstream release notes for a breaking change before patching around
   the symptom

## Verifying a fix

Confirm the fix actually addresses the root cause, not just the visible test:

1. Run the specific failing test — confirm it passes now
1. Run the full suite — confirm nothing else broke
1. Reverse the fix and confirm the failure returns. If it does not, the fix
   was not what resolved it, and the real cause is still there
1. Consider whether a regression test should be added, and say so in the
   wrap-up

Step 3 is the one most often skipped and the one that most often reveals a
coincidental fix.
