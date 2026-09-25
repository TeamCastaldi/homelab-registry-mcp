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
