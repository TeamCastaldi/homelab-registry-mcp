---
name: testing-standards
description: >
  Stack-agnostic standard for what makes a test worth keeping, applied whenever
  writing a new test, adding coverage for a bug fix or feature, reviewing a test
  file or a PR's test diff, or when asked whether tests are good, complete,
  redundant, or worth trusting. Encodes the four properties of a good test,
  which mocks and fakes may stand in for what, and the recurring smells that let
  a broken feature pass a green suite. Use it for any language or test runner —
  pytest, vitest/jest, go test, cargo test, JUnit, RSpec, and so on. Distinct
  from the /audit-tests command, which is the occasional, deliberate full-suite
  review this skill's rules feed into; this skill is the everyday discipline
  applied one test at a time, without being asked.
---

# Testing standards

Count is not a quality signal in either direction. A hundred focused
parametrized checks can be healthier than five tautologies. What matters is
whether each test would catch a real regression without breaking on a
harmless refactor. Apply this every time a test is written or reviewed — not
just at audit time.

## 1. The four properties of a good test

From Vladimir Khorikov, *Unit Testing Principles, Practices, and Patterns*
(Manning, 2020). Every test trades these off. A test that scores zero on
either of the first two has no value, however fast or tidy it is.

| Property | The question | Fails when |
|---|---|---|
| **Protection against regressions** | If the code under test broke, would this test fail? | The assertion can't fail (a tautology), or it only checks a mock's own configured return |
| **Resistance to refactoring** | If the code were restructured without changing behavior, would this test still pass? | It asserts *how* the code works (which internal function it called) instead of *what* it produced |
| **Fast feedback** | Does it run in milliseconds? | Real network calls, sleeps, or heavy renders with no need for them |
| **Maintainability** | Can a reader tell what it proves in under a minute? | Large setup, logic inside the test, or an unclear name |

## 2. Pick the right shape

Route/handler-level integration tests (a real router or CLI entrypoint against
an in-memory or containerized dependency, with only genuinely external
services replaced) are usually the right shape for testing an API, CLI, or
service boundary — this is Kent C. Dodds' "testing trophy," not a fallback
from a unit-test ideal. Pure unit tests are right for pure logic: parsers,
validators, scoring/ranking functions, schema adapters. Test behavior at the
level a caller actually observes it.

## 3. Mocks and fakes: what may be replaced, and what may be asserted

The line is not "asserts on a mock." It is **what the mock stands in for, and
which direction the data flows.** Apply the first matching row.

| Code | What is replaced | Asserting on the call is… |
|---|---|---|
| **M1** | **Outgoing call to a service you don't control** (a payment API, an email provider, an LLM request, a webhook you send) | **Fine.** The call is the observable output. Assert on the arguments your code computed — recipient, amount, the exact payload — not just that the call happened. |
| **M2** | **Incoming data stub** — the reply from an external service, an inbound webhook body | **A flaw**, unless whether the call happens at all is itself the property being tested (a paid API call, a security-relevant side effect). Then it must be paired with a state or response assertion — never just `assert_called_once()`. |
| **M3** | **Something you own and can run for real** (your own DB via an in-memory/test instance, your own cache) | **Assert on state instead of the call.** Patching only to *inject a failure* is fine when the assertions check what happened afterward. |
| **M4** | **Your own internal function**, patched to prove a caller chose it | **Fine only if the test also checks the real output** — the response body, the persisted row. A test that checks only *which function ran* is a flaw: rewrite it to check what came out. |
| **M5** | **A mock checked against its own configured return** — `db.save = fake(returns=True); assert db.save()` | **Worthless. Delete it.** It tests the mocking library, not your code. |

Two rules apply to every row:

- A bare "was this called" assertion with no argument check and no state or
  response check is a flaw, even when the row above says the call itself may
  be asserted.
- Assert on a log line only when logging it is an operational requirement (an
  event ID on a failed payment webhook). Routine diagnostic logging isn't
  worth a test.

**F1 — a fake must be no more forgiving than the real thing it stands in
for.** A fake HTTP transport that matches a request by path alone, ignoring
method, query string, and headers, will let a client that sends `POST`
instead of `GET`, drops an auth header, or ignores a filter parameter pass
every test while breaking against the real service. When you write a fake:
make it reject what the real dependency would reject (wrong method, missing
auth, malformed body, a duplicate create). A fake that accepts something the
real service refuses has hidden a bug rather than caught one.

## 4. Other rules

- **One behavior per test, not one assertion per test.** Several asserts are
  fine when they describe the same outcome.
- **No tautologies (T1).** An assertion must be able to fail when the code
  under test is wrong — not `assert x == x`, not a check of a value the test
  itself just set up, not a check of a constant against a copy of itself. A
  common disguised form: testing an id, resource, or user that doesn't exist
  at all when the code path being tested is really about *ownership* (does
  this route 404 a resource that exists but belongs to someone else?) — a
  nonexistent-id test only exercises the "not found" half of that check, never
  the "found but not yours" half.
- **No testing the standard library or framework (R3).** Don't prove that
  your framework rejects a wrong type or a missing required field unless the
  test is really about *your* field definition, validator, or route-level
  check.
- **No duplicates** — within a file (R1) or across files (R2). When two tests
  would always fail together for the same reason, keep the one closer to the
  behavior a user or caller actually sees. Two tests hitting the same lines
  are not necessarily duplicates: an error-path test often shares every line
  with the happy-path test that reaches the same code while asserting
  something different (a distinct HTTP status, a distinct persisted state).
- **Spec, contract, and golden tests are guardrails, not smells.** A test that
  pins a literal string or byte-for-byte output — a wire-format constant, a
  security-relevant prompt fragment, a rendered template, a documented
  contract value — is correct by default when that text is real behavior and
  can be edited outside code review (a config value, a CMS-managed string, a
  prompt template). Flag it (**G1**) only when it restates a literal with no
  documented rule behind it: a test that would break the moment someone
  fixed a typo, for no reason anyone could name.
- **Environment-sensitive tests are judged on what they check, not on
  whether they currently pass.** A test blocked by a broken local toolchain,
  a missing system library, or a sandbox network restriction is judged on its
  assertions.

## 5. Common smells found in real audits

These four patterns show up disproportionately often when a suite is fully
green but a real regression would slip through:

1. **Fakes more forgiving than the real service** (see F1 above) — the single
   highest-yield thing to check in any integration-heavy suite.
2. **A weak existence check that something else satisfies.** `"error" in
   result` or `response.ok` style assertions pass for the wrong reason when
   the field checked is always present, or when an unrelated downstream
   failure produces the same shape of result. Assert the *specific* error, not
   just that "an error" of some kind occurred.
3. **A read/list tool tested only on its empty or not-found case.** A stub
   that always returns `[]` or a 404 passes every test that never seeds real
   data and checks it comes back. For any list/get endpoint or function, at
   least one test must seed data belonging to someone else too, and assert
   scoping — otherwise "returns only what's mine" is unverified.
4. **Settings-to-code wiring never asserted.** A config value, timeout, TTL,
   feature threshold, or severity filter is set in config but nothing proves
   the code actually reads it — changing or deleting the setting would go
   unnoticed. If a setting exists, something should assert changing it
   changes behavior.

## 6. Organizing test files

There is no correct number of tests per file. Organize by **what is being
tested**:

- One test file per module, unit, or route group under test, named to match
  the unit (`test_<module>.py`, `<module>.test.ts`, `<module>_test.go`) —
  match whatever this repo's test runner already expects.
- Once a file grows past roughly 500–800 lines, or starts covering more than
  one unit, review it for a split. The line count is a trigger for review,
  not a hard limit — split along the unit under test, never at an arbitrary
  size.
- Shared helpers and fixtures belong in the runner's shared setup file
  (`conftest.py`, a test-utils module, etc.), not copied between files.

## Related

- The `/audit-tests` command runs the full procedure this skill's rules feed
  into: a file-by-file suite audit, a coverage-based redundancy shortlist, and
  mutation-testing spot checks, written up with a fixed JSON schema so audits
  are comparable over time.
- This repo's `TEST_COMMAND` (`CLAUDE.md`'s `## Session Config`) is how any
  of the above actually runs here.
