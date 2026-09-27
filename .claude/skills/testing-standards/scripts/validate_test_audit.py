#!/usr/bin/env python3
"""Validate a test suite audit's files.json against the testing-standards schema.

Checks every rule the /audit-tests command and the testing-standards skill
define that a machine can check: required keys and types, allowed verdicts
and rule codes, that every named test really exists (when a test list is
supplied), that each file's verdict follows from its findings, and that
is_redundant matches its R-rule findings. With --complete it also requires
exactly one entry per test file in the supplied test list.

This script is stack-agnostic: it never runs a test runner itself. Point it
at a plain-text test list instead, one line per test as
"<file_name><TAB><test_id>" — see the /audit-tests command for a one-liner
that produces this format for common stacks (pytest, vitest, jest, go test,
cargo test). Without --tests-file, existence and completeness checks are
skipped; every other check still runs.

Usage:
    validate_test_audit.py <files.json>
    validate_test_audit.py <files.json> --tests-file <tests.txt>
    validate_test_audit.py <files.json> --tests-file <tests.txt> --complete

Exits 0 when valid, 1 on any schema error, 2 on bad usage. Cross-platform,
stdlib only.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

VERDICTS = {"KEEP", "REWRITE", "DELETE"}
RULES = {"M1", "M2", "M3", "M4", "M5", "T1", "R1", "R2", "R3", "G1"}
REDUNDANCY_RULES = {"R1", "R2", "R3"}
REQUIRED = {
    "file_name": str,
    "logic_flaws": str,
    "is_redundant": bool,
    "unique_value": str,
    "verdict": str,
    "rationale": str,
    "findings": list,
}
OPTIONAL = {"split_recommendation"}
FINDING_KEYS = {"test", "verdict", "rules", "rationale"}
EDGE_CASES = {"No active assertions found": "DELETE", "Syntax invalid": "REWRITE"}


def load_test_list(path: Path) -> dict[str, set[str]]:
    """Parse a plain-text test list into {file_name: {test_id, ...}}.

    Each non-blank line is "<file_name>\t<test_id>". Lines with no tab, or
    that are blank, are skipped rather than treated as errors, so a list
    built by hand-editing a runner's raw output doesn't need to be perfectly
    clean first.
    """
    tests: dict[str, set[str]] = defaultdict(set)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip("\n")
        if "\t" not in line:
            continue
        file_name, test_id = line.split("\t", 1)
        file_name = file_name.strip()
        test_id = test_id.strip()
        if file_name and test_id:
            tests[file_name].add(test_id)
    return tests


def _check_findings(
    where: str, findings: list, names: set[str] | None, errors: list[str]
) -> tuple[set[str], bool]:
    """Validate one file's findings; return (flagged test names, any redundancy).

    names is None when no test list was supplied — existence is then
    unverifiable and skipped, but every other check still runs.
    """
    flagged: set[str] = set()
    any_redundant = False
    for j, f in enumerate(findings):
        fw = f"{where} finding {j}"
        if not isinstance(f, dict) or set(f) != FINDING_KEYS:
            errors.append(f"{fw}: keys must be exactly {sorted(FINDING_KEYS)}")
            continue
        if names is not None and f["test"] not in names:
            errors.append(f"{fw}: test {f['test']!r} is not a collected test")
        if f["test"] in flagged:
            errors.append(f"{fw}: test {f['test']!r} listed twice")
        flagged.add(f["test"])
        if f["verdict"] not in {"REWRITE", "DELETE"}:
            errors.append(f"{fw}: finding verdict must be REWRITE or DELETE")
        rules = f["rules"]
        if not isinstance(rules, list) or not rules or not set(rules) <= RULES:
            errors.append(f"{fw}: rules must be a non-empty subset of {sorted(RULES)}")
        elif set(rules) & REDUNDANCY_RULES:
            any_redundant = True
        if not str(f["rationale"]).strip():
            errors.append(f"{fw}: rationale is empty")
    return flagged, any_redundant


def _check_entry(e: object, i: int, collected: dict[str, set[str]] | None) -> list[str]:
    """Validate one file entry against the schema and derivation rules."""
    if not isinstance(e, dict):
        return [f"entry {i}: not an object"]
    where = f"entry {i} ({e.get('file_name', '?')})"
    missing = REQUIRED.keys() - e.keys()
    if missing:
        return [f"{where}: missing keys {sorted(missing)}"]
    errors = []
    extra = e.keys() - REQUIRED.keys() - OPTIONAL
    if extra:
        errors.append(f"{where}: unknown keys {sorted(extra)}")
    for key, typ in REQUIRED.items():
        if not isinstance(e[key], typ):
            errors.append(f"{where}: {key} must be {typ.__name__}")
    if errors:
        return errors
    split = e.get("split_recommendation")
    if split is not None and not isinstance(split, str):
        errors.append(f"{where}: split_recommendation must be a string or null")
    for key in ("logic_flaws", "unique_value", "rationale"):
        if not e[key].strip():
            errors.append(f"{where}: {key} is empty")
    if e["verdict"] not in VERDICTS:
        errors.append(f"{where}: verdict {e['verdict']!r} is not one of {VERDICTS}")

    names = None
    if collected is not None:
        names = collected.get(e["file_name"])
        if names is None:
            return errors + [f"{where}: no collected tests in {e['file_name']}"]

    if e["rationale"] in EDGE_CASES:
        expected = EDGE_CASES[e["rationale"]]
        if e["verdict"] != expected:
            errors.append(f"{where}: rationale {e['rationale']!r} requires {expected}")
        return errors

    flagged, any_redundant = _check_findings(where, e["findings"], names, errors)

    if not e["findings"]:
        derived = "KEEP"
    elif names is not None and flagged >= names and all(
        f.get("verdict") == "DELETE" for f in e["findings"]
    ):
        derived = "DELETE"
    elif names is not None:
        derived = "REWRITE"
    else:
        # No test list: can't verify "findings cover every test in the
        # file", so accept REWRITE or DELETE as given rather than forcing
        # DELETE's stricter derivation.
        derived = e["verdict"] if e["verdict"] in {"REWRITE", "DELETE"} else "REWRITE"

    if e["verdict"] != derived:
        errors.append(f"{where}: verdict {e['verdict']} but findings derive {derived}")
    if e["is_redundant"] != any_redundant:
        errors.append(f"{where}: is_redundant must be {any_redundant}")
    return errors


def validate(
    entries: object, collected: dict[str, set[str]] | None, complete: bool
) -> list[str]:
    """Return every schema error in an audit's parsed files.json."""
    if not isinstance(entries, list):
        return ["top level must be a JSON array"]
    errors = []
    seen: set[str] = set()
    for i, e in enumerate(entries):
        errors += _check_entry(e, i, collected)
        name = e.get("file_name") if isinstance(e, dict) else None
        if name in seen:
            errors.append(f"entry {i} ({name}): duplicate file entry")
        seen.add(name)
    if complete:
        if collected is None:
            errors.append("--complete requires --tests-file")
        else:
            absent = sorted(set(collected) - seen)
            if absent:
                errors.append(f"incomplete: no entry for {absent}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files_json", type=Path)
    parser.add_argument(
        "--tests-file",
        type=Path,
        default=None,
        help="plain-text '<file_name><TAB><test_id>' list; enables existence checks",
    )
    parser.add_argument(
        "--complete",
        action="store_true",
        help="require one entry per file in --tests-file",
    )
    args = parser.parse_args()

    if args.complete and not args.tests_file:
        print("ERROR: --complete requires --tests-file", file=sys.stderr)
        return 2

    entries = json.loads(args.files_json.read_text(encoding="utf-8"))
    collected = load_test_list(args.tests_file) if args.tests_file else None
    errors = validate(entries, collected, args.complete)
    for err in errors:
        print(f"ERROR: {err}")
    if errors:
        return 1

    if collected is not None:
        total = sum(
            len(collected[e["file_name"]])
            for e in entries
            if isinstance(e, dict) and e.get("file_name") in collected
        )
        print(f"OK: {len(entries)} file entries covering {total} test functions")
    else:
        print(f"OK: {len(entries)} file entries (schema only — no --tests-file supplied)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
