# Tests

The pytest suite for `homelab-registry-mcp`. Every automated test lives here as
`test_*.py`.

## Layout

Tests are flat and mirror the `src/registry_mcp/` package — one `test_<area>.py`
per module or feature, rather than `unit/` / `integration/` / `e2e/` folders:

```
tests/
    conftest.py             Shared fixtures (IsolatedSettings, settings, store, server)
                            and helpers (tool_payload, BlockingCall)
    test_discovery.py       Discovery engine + sources
    test_reconcile_*.py     Reconciliation (deterministic + reasoning)
    test_linking.py         Cross-source linking
    test_hardware.py        Hardware node registry
    test_proposal_*.py      Proposal engine / generator / tools
    test_providers_*.py     Git + notification providers
    test_secrets.py         git-crypt secrets tools
    ...                     (one file per area)
```

## Conventions

- `pytest` + `pytest-asyncio` with `asyncio_mode="auto"` — async tests need no
  explicit marker.
- Tests are **hermetic**: `conftest.py` provides `IsolatedSettings`, which ignores
  `.env`, environment variables, and secrets files, so no test touches real
  Traefik, Authentik, Docker, or network state. Build settings with
  `IsolatedSettings(...)`, never `Settings.model_construct()`: that skips
  validation, so credential fields stay plain strings instead of the `SecretStr`
  the server always sees.
- Each test gets a throwaway SQLite database via the `settings` fixture
  (`tmp_path`), plus `store` (`RegistryStore`) and `server` (`build_server`)
  fixtures built on top of it.
- Read a tool's result with `conftest.tool_payload()`. A tool that reports
  `{"error": ...}` comes back as an MCP error result (`isError: true`), not a
  plain dict; `tool_payload()` reads either shape.
- An async path that reaches the reasoning layer must keep the blocking LLM call
  off the event loop. `conftest.BlockingCall` holds a stand-in call open and
  asserts the loop keeps running meanwhile.
- A fake should be no more forgiving than the real service where it matters. The
  proposal and normalization `FakeGit` refuse a branch that already exists, as
  Gitea and GitHub do; a fake that accepted duplicates once hid a real bug. Not
  every fake meets this yet. `docs/plans/2026-09-test-suite-audit.md` lists the
  ones that don't (the Dockhand, Traefik, and Authentik transports route on URL
  path alone; the Git fakes ignore auth headers), plus two webhook dispatch tests
  that break hermeticity with a real outbound call to `https://git.test`, and the
  fix for each.
- Files are named `test_<module>.py`; test functions `test_<what_it_does>`.
- Each test should verify one thing.

## Running

```bash
uv run pytest                            # all tests
uv run pytest -v tests/test_linking.py   # one file, verbose
uv run pytest -k linking                 # by keyword
uv run pytest --cov=src                  # with coverage (needs: uv add --dev pytest-cov)
```

CI runs `ruff check`, `ruff format --check`, and `pytest -q` on every push — tests
must pass before a PR is merged.

## Mutation testing

Passing tests prove nothing survived; they don't prove a test would actually
*fail* if the code broke. `mutmut` (config in `pyproject.toml`'s `[tool.mutmut]`,
`source_paths = ["src/registry_mcp"]`) automates the same check this project's
audit did by hand: it generates small mutations of the source (flip a
comparison, change a constant, drop an argument) and reruns the suite against
each. A mutant no test catches is a real gap, or a mutant equivalent to the
original (behaviorally identical, uncatchable by any test, and correctly
reported as a survivor) — telling those apart still takes the same read-the-
actual-diff-before-trusting-the-label discipline the 2026-09 audit needed;
`docs/plans/2026-09-test-suite-audit.md`'s Tier 3 found three "safe to delete"
calls that didn't survive a real mutation probe.

```bash
uv run mutmut run              # mutate everything under source_paths and test
uv run mutmut results          # list every mutant's verdict
uv run mutmut show <id>        # the exact diff for one mutant (id from `results`)
```

Scope a run to one file or glob while investigating, by temporarily adding
`only_mutate = ["src/registry_mcp/<path>"]` under `[tool.mutmut]` — a scoped
run's coverage/stats phase is cached and reused by a later wider run over the
same tree, so narrowing down doesn't throw away that work. `mutants/` (the
mutated source copy + cache `mutmut run` creates) is gitignored and safe to
delete any time; it regenerates on the next run.

One example already found this way, from a scoped run against
`adoption/ssh.py`: no test calls `_ssh_base()` directly or inspects the
constructed SSH command line, so a mutant that changes `"-i"` (identity file)
to `"-I"` (not a real ssh flag) survives — that flag would break every real
adoption SSH call in production while every one of the 862 tests stays green,
because every adoption test mocks `inspect_container`/`read_remote_file`
wholesale rather than exercising the command-building helpers underneath them.
