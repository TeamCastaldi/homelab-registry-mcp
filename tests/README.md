# Tests

The pytest suite for `homelab-registry-mcp`. All automated tests live here.

## Layout

Tests are flat and mirror the `src/registry_mcp/` package — one `test_<area>.py`
per module or feature, rather than `unit/` / `integration/` / `e2e/` folders:

```
tests/
    conftest.py             Shared fixtures (IsolatedSettings, settings, store, server)
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
  Traefik, Authentik, Docker, or network state.
- Each test gets a throwaway SQLite database via the `settings` fixture
  (`tmp_path`), plus `store` (`RegistryStore`) and `server` (`build_server`)
  fixtures built on top of it.
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
