# Test suite audit and remediation plan (2026-09-25)

A file-by-file audit of all 40 test files (845 tests) against one question: is each
test necessary, logically sound, and non-redundant? Every test was assumed to be
redundant or a tautology until the application logic it guards could be named. The
per-file verdicts, one JSON object per file in the audit's schema
(`file_name`, `logic_flaws`, `is_redundant`, `unique_value`, `verdict`, `rationale`),
are in [`2026-09-test-suite-audit-verdicts.jsonl`](2026-09-test-suite-audit-verdicts.jsonl).

## How the claims were checked

Reading a test only shows what it asserts, not whether those assertions would ever
fail. So each suspected flaw was proven with a mutation probe: the code the test
claims to cover was broken in a throwaway git worktree, and the test (and, when it
still passed, the whole suite) was rerun. A test that still passed with its named
behavior deleted is recorded as hollow. Each file also got at least one control
probe, a breakage its tests should catch, to confirm they bite at all.

About 160 mutation probes ran. All 46 labelled controls were caught. "Survives the
full suite" below means all 845 tests still passed with that change applied.
Appendix B has the probe runner so any claim can be reproduced.

## Headline

**The suite is not bloated.** Only 13 tests (1.5%) are dead weight: tautologies or
exact duplicates of stronger tests. The real problem is the opposite: under-assertion.
Dozens of realistic breakages pass the entire suite, mostly through four recurring
patterns:

1. **Fakes more forgiving than the real service**, against `tests/README.md`'s own
   rule. The Dockhand, Traefik, and Authentik transports answer by URL path alone,
   ignoring method, query string, and headers. The Git fakes ignore headers and the
   PR `state` filter, and the docs doubles ignore `topic` and the token. So a
   read-only client sending POST, a dropped `superuser_full_list`, or a provider
   sending no auth header all go unnoticed.
2. **`"error" in result` assertions that are satisfied by something else**: a
   field that is always present (`DiscoveryEvent.error`), or an unrelated downstream
   failure (adoption's unpatched `gitcrypt.repo_path("/repo")`, Infisical's
   disabled/unconfigured tests sharing one branch).
3. **Read tools tested only on their empty or not-found case**, so a stub
   returning `[]` or "not found" passes: `discovery_list_stale`,
   `proposal_list_open`, `proposal_get`.
4. **Settings-to-code wiring almost never asserted**: `ANSIBLE_CONFIG`, schedule
   intervals, TTLs, `INFISICAL_MAX_FOLDERS`, `NOTIFICATION_SMTP_USE_TLS`,
   `DOCKHAND_WEBHOOK_VULNERABILITY_MIN_SEVERITY`, the comment-poll interval.

## Verdicts

20 files KEEP, 20 files REWRITE, no file redundant as a whole. REWRITE here means
the file is valuable but one or more existing tests must be changed or deleted,
usually narrowly. KEEP means every test earns its place; any gaps listed need new
assertions or tests, not changes to existing ones.

| # | File | Tests | Verdict | Why |
|---|---|---:|---|---|
| 1 | `test_adoption.py` | 52 | REWRITE | 5 finalize/cancel/rejection tests pass with their named behavior deleted |
| 2 | `test_intake.py` | 58 | REWRITE | End-to-end clone-isolation test survives removal of every isolation layer; reasoner inputs never asserted |
| 3 | `test_secrets.py` | 54 | REWRITE | Suffix-branch test passes via the content heuristic and hides a real bug; one redundant test, one type-only assertion |
| 4 | `test_infisical.py` | 26 | REWRITE | Two `isinstance` tautologies, three redundant tool tests; the "disabled" test can't detect the enable flag being deleted |
| 5 | `test_discovery.py` | 41 | REWRITE | `discovery_list_stale` tested only when empty; disabled-source assertion satisfied by a schema field |
| 6 | `test_service_deploy_tools.py` | 10 | KEEP | The path-safety regex is pinned by one input |
| 7 | `test_ansible_facts.py` | 20 | REWRITE | argv tests ignore the env, so `ANSIBLE_CONFIG` is unverified; one redundant test; 6 suite-wide survivors |
| 8 | `test_hardware_discover_now.py` | 11 | KEEP | Inventory and key passthrough to `gather_facts` never asserted |
| 9 | `test_providers_notification.py` | 10 | KEEP | TLS passthrough, diff escaping and truncation untested; the null-provider test is load-bearing |
| 10 | `test_proposal_engine.py` | 41 | REWRITE | The `find_open` type-scoping test never varies the type; the CVE fixed-tag context is never checked |
| 11 | `test_dockhand.py` | 14 | REWRITE | Path-only fake: the POST read-only invariant, query params and envelope unwrap untested |
| 12 | `test_traefik.py` | 10 | REWRITE | Exit-criteria test computes its own assertion; 3 of 7 tools untested anywhere |
| 13 | `test_authentik.py` | 12 | REWRITE | `superuser_full_list` and POST untested; 4 tools untested anywhere |
| 14 | `test_docs.py` | 12 | REWRITE | Doubles swallow topic and token; the real Bearer session factory never runs |
| 15 | `test_linking.py` | 8 | KEEP | Full-context `hardware_node` section untested anywhere |
| 16 | `test_config_report.py` | 36 | KEEP | A dead `SecretStr` guard makes one test name overclaim; `.env` key read untested |
| 17 | `test_deletion.py` | 15 | KEEP | Confirmed-challenge replay, startup purge and TTL wiring untested |
| 18 | `test_inventory_gate.py` | 7 | KEEP | Test-for-test copy of the deletion-gate tests because the source duplicates the gate; same gaps |
| 19 | `test_inventory_writer.py` | 12 | REWRITE | The duplicate-membership test can't fail by construction; the overwrite guard it should cover is untested |
| 20 | `test_proposal_generator.py` | 20 | KEEP | Tab normalization and middleware context untested |
| 21 | `test_reasoning.py` | 24 | KEEP | Three Reasoner methods' output mapping never exercised |
| 22 | `test_reconcile_reasoning.py` | 11 | KEEP | No call count, so every item could be sent to the LLM unnoticed |
| 23 | `test_providers_git.py` | 46 | REWRITE | Close-PR test asserts nothing; fakes ignore headers (auth) and the PR `state` filter |
| 24 | `test_dockhand_webhook.py` | 32 | REWRITE | Dispatch tests assert only a downstream failure caused by a real network call |
| 25 | `test_webhook_schemas.py` | 24 | KEEP | Pure, exact, table-driven |
| 26 | `test_normalization.py` | 83 | KEEP | Security-label, rename-callout and formatter-equivalence invariants unguarded |
| 27 | `test_service_deploy_generator.py` | 14 | REWRITE | The "append" test can't detect the conventions precedence being inverted |
| 28 | `test_hardware.py` | 36 | REWRITE | Two pydantic tautologies; the full-context test never calls the tool |
| 29 | `test_events.py` | 20 | REWRITE | Retention test only covers "delete everything"; per-service filter tested with one service |
| 30 | `test_health.py` | 14 | REWRITE | Two redundant registration tests; `hardware-discover-now`'s read-only gate untested |
| 31 | `test_tool_call_logging.py` | 13 | KEEP | Clean |
| 32 | `test_mcp_surface.py` | 8 | KEEP | Closed-world classification pinned by five spot checks |
| 33 | `test_server_runtime.py` | 7 | KEEP | Poll interval and startup event purge untested |
| 34 | `test_transport_security.py` | 7 | KEEP | Clean |
| 35 | `test_proposal_tools.py` | 8 | REWRITE | List and get tested only on empty or not-found; both stubbable |
| 36 | `test_registry_tools.py` | 4 | REWRITE | One redundant test; list filters never passed through in any test |
| 37 | `test_store.py` | 8 | KEEP | Clean |
| 38 | `test_config.py` | 5 | KEEP | Credential list is hand-maintained (currently complete) |
| 39 | `test_discovery_dockhand.py` | 4 | KEEP | Clean |
| 40 | `test_ansible_inventory_tools.py` | 8 | KEEP | `ansible_host` precedence and group sync untested |

## Remediation plan

Ordered by risk. The mutation IDs in brackets refer to Appendix A.

### Tier 1: security, data loss, and wrong writes

1. **Make the HTTP fakes as strict as the real services.** Give the integration
   tests a shared transport helper that fails on any non-GET method and routes on
   query params and auth headers, not just the path. Closes the read-only invariant
   for Dockhand and Authentik [K1, U3], `superuser_full_list` [U1], Dockhand
   filters and envelopes [K2, K3], Traefik's protocol argument [R1], and the Git
   providers' auth header and `state=open` [GP3, GP4]. Separately, run the docs
   client's real session factory once, so its Bearer header is checked [M1].
2. **Event retention.** Test that `purge_old_events` keeps events newer than the
   cutoff and deletes older ones, for both change and discovery events, and that
   `build_app` runs it at startup. Today the audit log could be wiped on every
   restart, or never trimmed, without any test noticing [EV1, EV2, SR2].
3. **Proposal routing invariants.**
   - Assert `NORMALIZATION_LABEL` on normalization PRs, since the "never bundled"
     rule is unguarded [NZ1].
   - Vary the finding type in the `find_open` test [E2].
   - Assert the fixed tag reaches the CVE generator context [E3].
   - Assert what the webhook dispatches, using a recording engine rather than a
     real outbound call to `https://git.test` [WH1, WH2, plus hermeticity].
   - Assert `close_pr`'s request body for both providers [GP1, GP2].
4. **Secret-handling paths.**
   - Assert that the intake clone error names the approved host, which proves the
     isolated environment reached the clone [intake Q1–Q3].
   - Run the real `ensure_unlocked` and assert that the temp key file is deleted
     [S4].
   - Test the startup purge of adoption drafts, which hold captured live secret
     values [G3].
   - Add stand-in predictor tests for `Reasoner.detect_hardcoded_secrets`,
     `generate_remediation_patch`, and `apply_review_feedback`, whose output mapping
     never runs today [reasoning Q1, Q2].

### Tier 2: rewrite hollow tests

- `test_adoption.py`: make the five finalize/cancel/rejection tests assert the
  specific error message and the persisted status [P1–P5].
- `test_discovery.py`: seed a stale service for `discovery_list_stale` [D1], and
  assert the disabled-source error message, not the key [D2].
- `test_proposal_tools.py`: cover the success paths of `proposal_list_open` and
  `proposal_get` [PT1, PT2].
- `test_events.py`: query per-service events with two services present [EV3].
- `test_inventory_writer.py`: point the membership test at a pre-populated group
  host entry [W1].
- `test_service_deploy_generator.py`: assert this repo's rules come before the
  homelab spec [SG1].
- `test_hardware.py`: make `test_service_get_full_context_includes_hardware`
  actually call the tool [L1].
- `test_infisical.py`: test "disabled" with an otherwise complete configuration
  [I1].
- `test_intake.py`: assert the reasoner's `detected` and `readme` inputs [Q5, Q6].

### Tier 3: delete 13 tests

| File | Test | Reason |
|---|---|---|
| `test_infisical.py` | `test_null_notification_provider_is_default` | Tautology plus duplicate assertion |
| `test_infisical.py` | `test_tool_returns_keys` | Subsumed by `test_tool_never_returns_values` |
| `test_infisical.py` | `test_tool_non_recursive_by_default` | Tautology plus subsumed |
| `test_hardware.py` | `test_storage_disk_model` | Only exercises pydantic |
| `test_hardware.py` | `test_storage_pool_model` | Only exercises pydantic |
| `test_traefik.py` | `test_exit_criteria_routers_using_middleware` | Assertion computed in test code |
| `test_health.py` | `test_build_server_registers_health` | Subsumed by `test_health_returns_ok` |
| `test_health.py` | `test_system_health_check_always_registered` | Subsumed by the mode tests |
| `test_registry_tools.py` | `test_get_missing_returns_error` | Subsumed by `test_tool_call_logging.py` |
| `test_secrets.py` | `test_serialize_round_trip` | Format is pinned by the `secrets_add` tests |
| `test_ansible_facts.py` | `test_gather_facts_parses_stdout` | Same path as the partial-success test |
| `test_adoption.py` | `test_labels_from_inspect` | `dict.get`, covered by the happy path |
| `test_adoption.py` | `test_labels_from_inspect_missing_config` | `dict.get`, covered by the happy path |

`test_inventory_gate.py` duplicates `TestDeletionGateStore` test for test only
because `InventoryGateStore` duplicates `DeletionGateStore`. Merging the two gate
stores would let one set of tests cover both.

### Tier 4: wiring and coverage gaps

- **Ansible chain.** No test covers the path from `ANSIBLE_CFG_PATH`/`SSH_KEY_PATH`
  to the ansible process: the `ANSIBLE_CONFIG` env [A4], `--private-key` [A5], and
  the `discover_now` passthrough [H1, H2]. Nor do disk-size units and type mapping
  [A1–A3], the unparseable host line [A6], or the alias fallback [H5].
- **Settings.** Discovery intervals [D3], comment-poll interval [SR1], delete TTL
  [X3], Infisical token lifetime and max folders [I2, I3], SMTP `USE_TLS` [N2],
  webhook minimum severity [SC1], Authentik source without a token [D4].
- **Read-only and classification.** `hardware-discover-now`'s read-only gate
  [HE1]; assert the exact `_CLOSED_WORLD_TOOLS` set [MS1, MS2].
- **Tab normalization.** It is untested in all three generators [adoption P9,
  generator P2, SG2]; a shared helper with one test would cover them.
- **Smaller gaps.**
  - Inventory sync `ansible_host` precedence and groups [AI1, AI2].
  - `registry_list_services` filters [RT1].
  - Full-context `hardware_node` [L1].
  - Service-name regex path safety [T1].
  - Email diff escaping and truncation [N3, N4].
  - Docs topic and token [M2, M3].
  - LLM call scoping [RR1].
  - Middleware context [P3].
  - `.env` key read [C2].
  - Challenge replay and startup purges [X1, X2, G1, G2].
  - Group-entry overwrite [W1].

### Tier 5: one real application bug

`gitcrypt.detect_format` checks `path.suffix == ".env"`, but `Path(".env").suffix`
is `""`. For a file literally named `.env` the check never fires, and parsing falls
to the content heuristic, which requires uppercase keys. So `secrets_decrypt`
returns a `.env` file with lowercase keys as raw text instead of the parsed dict
its docstring promises. The test named for the suffix branch
(`test_detect_format_dotenv_suffix`) passes only through the heuristic. Fix: also
match `path.name == ".env"` (and `.env.*`), and point the test at lowercase keys.

## Appendix A: mutations that survived

"Suite" means all 845 tests still passed. "File" or "test" means the probe ran only
against that scope.

| ID | Scope | Mutation |
|---|---|---|
| adoption P1 | test | Drop the `secret_strategy` validation |
| adoption P2 | test | Finalize a non-pending draft |
| adoption P3 | test | Rotate writes an empty value |
| adoption P4 | test | Cancel never persists |
| adoption P5 | test | A rejected sanitization is saved as `open` |
| adoption P6 | file | `.env` written at the repo root |
| adoption P7 | file | Compose `config_files` not split on commas |
| adoption P9 | file | Tab normalization removed |
| adoption P11 | file | PR body drops the reasoning and strategy |
| adoption P12 | file | Invalid `docker inspect` JSON not raised |
| intake Q1 | file | Clone subprocess never receives the isolated env |
| intake Q2 | test | `_git_env` isolates nothing |
| intake Q3 | test | Protocol allowlist removed |
| intake Q4 | file | Confidence equal to the threshold rejected |
| intake Q5 | file | Reasoner gets no detected facts |
| intake Q6 | file | Reasoner gets no README |
| intake Q7 | file | Clone timeout swallowed |
| intake Q8 | file | `readme_found` hard-wired to True |
| intake Q9 | file | Non-mapping compose not warned |
| secrets S1 | file | `detect_format` suffix branch deleted |
| secrets S3 | file | Decrypt blanks non-dotenv content |
| secrets S4 | 3 files | Unlock leaves the git-crypt key file in `/tmp` |
| infisical I1 | file | `INFISICAL_ENABLED` check deleted |
| infisical I2 | file | Token lifetime ignores `expiresIn` |
| infisical I3 | file | Tool ignores `INFISICAL_MAX_FOLDERS` |
| infisical I6 | file | Subfolder listing failure aborts the walk |
| discovery D1 | suite | `discovery_list_stale` returns `[]` |
| discovery D2 | file | `run_now` runs a disabled source |
| discovery D3 | suite | Scheduler ignores the interval settings |
| discovery D4 | suite | Authentik source built without a token |
| service_deploy_tools T1 | suite | Service-name regex admits `/` |
| service_deploy_tools T2 | file | Service-name regex admits uppercase |
| service_deploy_tools T3 | file | Service-name length cap removed |
| service_deploy_tools T4 | file | `reasoning` dropped from the response |
| ansible_facts A1 | suite | TB not scaled |
| ansible_facts A2 | suite | nvme typed as ssd |
| ansible_facts A3 | suite | Rotational disks typed as ssd |
| ansible_facts A4 | suite | `ANSIBLE_CONFIG` never set |
| ansible_facts A5 | suite | `--private-key` dropped |
| ansible_facts A6 | suite | Unparseable host line dropped silently |
| discover_now H1 | suite | `ANSIBLE_CFG_PATH` not passed through |
| discover_now H2 | suite | `SSH_KEY_PATH` not passed through |
| discover_now H5 | suite | No fallback to the inventory alias |
| notification N2 | suite | Factory ignores `USE_TLS` |
| notification N3 | suite | Diff not HTML-escaped |
| notification N4 | suite | Diff truncation removed |
| proposal_engine E2 | suite | `find_open` ignores `finding_type` |
| proposal_engine E3 | suite | CVE path passes the vulnerable tag as `new_tag` |
| dockhand K1 | suite | Client sends POST |
| dockhand K2 | suite | Wrapped list envelopes read as empty |
| dockhand K3 | suite | `list_containers` drops its filters |
| traefik R1 | suite | `list_routers` ignores `protocol` |
| traefik R2 | suite | `list_services` tool miswired |
| traefik R3 | suite | `entrypoints` tool miswired |
| traefik R4 | suite | TLS tool returns all of `rawdata` |
| authentik U1 | suite | `superuser_full_list` dropped |
| authentik U2 | suite | `list_users` tool miswired |
| authentik U3 | suite | Client sends POST |
| docs M1 | suite | Real session factory sends no Bearer header |
| docs M2 | suite | Tool drops `topic` |
| docs M3 | suite | Tool passes the URL as the token |
| linking L1 | suite | Full context never fills `hardware_node` |
| config_report C1 | suite | Dead `SecretStr` guard removed |
| config_report C2 | suite | `.env` keys never read |
| deletion X1 | suite | A confirmed challenge can be replayed |
| deletion X2 | suite | No startup purge of delete challenges |
| deletion X3 | suite | Delete TTL setting ignored |
| inventory_gate G1 | suite | A confirmed inventory challenge can be replayed |
| inventory_gate G2 | suite | No startup purge of inventory challenges |
| inventory_gate G3 | suite | No startup purge of adoption drafts |
| inventory_writer W1 | suite | Existing group host entry overwritten |
| proposal_generator P2 | suite | Tab normalization removed |
| proposal_generator P3 | suite | Existing middlewares never reach the model |
| reasoning Q1 | suite | `detect_hardcoded_secrets` drops the detected keys |
| reasoning Q2 | suite | Remediation `pr_body` returns the title |
| reasoning Q3 | suite | One invalid enum discards all metadata |
| reconcile_reasoning RR1 | suite | Deterministic matches still sent to the LLM |
| providers_git GP1 | suite | GitHub `close_pr` sends `state=open` |
| providers_git GP2 | suite | Gitea `close_pr` sends `state=open` |
| providers_git GP3 | suite | Gitea lists all PR states |
| providers_git GP4 | suite | Gitea sends no auth header |
| dockhand_webhook WH1 | suite | Image-update tags swapped |
| dockhand_webhook WH2 | suite | Every alert dispatched as a CVE |
| webhook_schemas SC1 | suite | Route ignores the minimum-severity setting |
| normalization NZ1 | suite | Normalization PRs opened under the security label |
| normalization NZ2 | suite | Rename callout dropped from the PR body |
| normalization NZ3 | suite | Formatter's equivalence gate bypassed |
| service_deploy_generator SG1 | suite | Homelab spec placed before this repo's rules |
| service_deploy_generator SG2 | suite | Tab normalization removed |
| hardware HW1 | test | `StorageDisk.type` loses enum validation |
| events EV1 | suite | Purge ignores the cutoff |
| events EV2 | suite | Discovery events never purged |
| events EV3 | suite | Change events not filtered by service |
| health HE1 | suite | `hardware-discover-now` ignores read-only mode |
| health HE2 | suite | SSH key check accepts a directory |
| mcp_surface MS1 | suite | `hardware-discover-now` marked closed-world |
| mcp_surface MS2 | suite | `dockhand_list_stacks` marked closed-world |
| server_runtime SR1 | suite | Comment-poll interval setting ignored |
| server_runtime SR2 | suite | Startup event purge never runs |
| proposal_tools PT1 | suite | `proposal_list_open` returns `[]` |
| proposal_tools PT2 | suite | `proposal_get` never finds anything |
| registry_tools RT1 | suite | `registry_list_services` drops its filters |
| ansible_inventory_tools AI1 | suite | IP preferred over a configured `ansible_host` |
| ansible_inventory_tools AI2 | suite | Groups never written to the inventory |

## Appendix B: reproducing a probe

Create a detached worktree (`git worktree add --detach ../probe HEAD`), then run
this script with a JSON spec of `{id, file, old, new, tests}` entries. Each
mutation is applied alone, the named tests run with `-x`, the whole suite runs too
if they still pass, and the file is restored with `git checkout`.

```python
import json, os, subprocess, sys
from pathlib import Path

WT = Path(os.environ.get("PROBE_WORKTREE", "../probe"))
PY = os.environ.get("PROBE_PYTHON", ".venv/bin/python")

def pytest(targets):
    r = subprocess.run([PY, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *targets],
                       cwd=WT, capture_output=True, text=True)
    return r.returncode, (r.stdout.strip().splitlines() or ["?"])[-1]

for m in json.loads(Path(sys.argv[1]).read_text()):
    f = WT / m["file"]
    src = f.read_text()
    assert src.count(m["old"]) == 1, m["id"]
    f.write_text(src.replace(m["old"], m["new"]))
    try:
        rc, last = pytest(m["tests"])
        if rc == 0 and m.get("escalate", True):
            rc, last = pytest(["tests/"])
        print(f"[{m['id']}] {'KILLED' if rc else 'SURVIVED'} :: {last}")
    finally:
        subprocess.run(["git", "checkout", "-q", "--", m["file"]], cwd=WT, check=True)
```

Tests in the worktree import the worktree's own `src/` (pytest's `pythonpath =
["src"]` puts it ahead of the editable install), which a planted exception
confirms.
