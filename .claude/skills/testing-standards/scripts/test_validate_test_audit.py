"""Tests for validate_test_audit.py.

Run with:

    python3 -m pytest .claude/skills/testing-standards/scripts -q
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "validate_test_audit.py"

_spec = importlib.util.spec_from_file_location("validate_test_audit", SCRIPT)
vta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vta)

validate = vta.validate
load_test_list = vta.load_test_list
main = vta.main


def keep_entry(**overrides):
    entry = {
        "file_name": "test_example.py",
        "logic_flaws": "None",
        "is_redundant": False,
        "unique_value": "Covers the widget parser's happy path",
        "verdict": "KEEP",
        "rationale": "Every test earns its place",
        "split_recommendation": None,
        "findings": [],
    }
    entry.update(overrides)
    return entry


def finding(**overrides):
    f = {
        "test": "test_something",
        "verdict": "REWRITE",
        "rules": ["T1"],
        "rationale": "Assertion cannot fail",
    }
    f.update(overrides)
    return f


# --------------------------------------------------------------- load_test_list


def test_load_test_list_parses_tab_separated_lines(tmp_path):
    p = tmp_path / "tests.txt"
    p.write_text("test_example.py\ttest_a\ntest_example.py\ttest_b\n")
    assert load_test_list(p) == {"test_example.py": {"test_a", "test_b"}}


def test_load_test_list_skips_lines_with_no_tab(tmp_path):
    p = tmp_path / "tests.txt"
    p.write_text("test_example.py\ttest_a\nnot a valid line\n\n")
    assert load_test_list(p) == {"test_example.py": {"test_a"}}


# --------------------------------------------------------------- schema-only mode


def test_keep_entry_with_no_findings_is_valid_without_test_list():
    errors = validate([keep_entry()], None, complete=False)
    assert errors == []


def test_rewrite_entry_without_test_list_skips_existence_check():
    entry = keep_entry(
        verdict="REWRITE",
        rationale="One test is a tautology",
        findings=[finding(test="test_does_not_exist_anywhere")],
    )
    # No --tests-file supplied: existence can't be checked, so this is valid.
    errors = validate([entry], None, complete=False)
    assert errors == []


def test_complete_without_test_list_errors():
    errors = validate([keep_entry()], None, complete=True)
    assert any("--complete requires --tests-file" in e for e in errors)


# --------------------------------------------------------------- with a test list


def test_finding_referencing_unknown_test_errors():
    collected = {"test_example.py": {"test_a", "test_b"}}
    entry = keep_entry(
        verdict="REWRITE",
        rationale="One test is a tautology",
        findings=[finding(test="test_nonexistent")],
    )
    errors = validate([entry], collected, complete=False)
    assert any("is not a collected test" in e for e in errors)


def test_finding_referencing_known_test_is_valid():
    collected = {"test_example.py": {"test_a", "test_b"}}
    entry = keep_entry(
        verdict="REWRITE",
        rationale="One test is a tautology",
        findings=[finding(test="test_a")],
    )
    errors = validate([entry], collected, complete=False)
    assert errors == []


def test_delete_requires_every_test_flagged_delete():
    collected = {"test_example.py": {"test_a", "test_b"}}
    entry = keep_entry(
        verdict="DELETE",
        rationale="Whole file is dead weight",
        # Only flags test_a, not test_b — can't derive DELETE.
        findings=[finding(test="test_a", verdict="DELETE")],
    )
    errors = validate([entry], collected, complete=False)
    assert any("but findings derive REWRITE" in e for e in errors)


def test_delete_with_every_test_flagged_delete_is_valid():
    collected = {"test_example.py": {"test_a", "test_b"}}
    entry = keep_entry(
        verdict="DELETE",
        rationale="Whole file is dead weight",
        findings=[
            finding(test="test_a", verdict="DELETE"),
            finding(test="test_b", verdict="DELETE"),
        ],
    )
    errors = validate([entry], collected, complete=False)
    assert errors == []


def test_complete_reports_missing_file():
    collected = {"test_example.py": {"test_a"}, "test_other.py": {"test_x"}}
    errors = validate([keep_entry()], collected, complete=True)
    assert any("no entry for" in e and "test_other.py" in e for e in errors)


def test_no_entry_for_a_file_with_no_collected_tests():
    collected = {"test_example.py": {"test_a"}}
    entry = keep_entry(file_name="test_missing.py")
    errors = validate([entry], collected, complete=False)
    assert any("no collected tests in test_missing.py" in e for e in errors)


# --------------------------------------------------------------- derivation rules


def test_keep_with_nonempty_findings_is_invalid():
    entry = keep_entry(findings=[finding()])  # verdict still says KEEP
    errors = validate([entry], None, complete=False)
    assert any("findings derive REWRITE" in e for e in errors)


def test_is_redundant_must_match_redundancy_rules():
    entry = keep_entry(
        verdict="REWRITE",
        rationale="Duplicate of another test",
        is_redundant=False,  # should be True given an R1 finding
        findings=[finding(rules=["R1"])],
    )
    errors = validate([entry], None, complete=False)
    assert any("is_redundant must be True" in e for e in errors)


def test_edge_case_no_assertions_requires_delete():
    entry = keep_entry(verdict="REWRITE", rationale="No active assertions found")
    errors = validate([entry], None, complete=False)
    assert any("requires DELETE" in e for e in errors)


def test_edge_case_syntax_invalid_requires_rewrite():
    entry = keep_entry(verdict="DELETE", rationale="Syntax invalid")
    errors = validate([entry], None, complete=False)
    assert any("requires REWRITE" in e for e in errors)


# --------------------------------------------------------------- structural checks


def test_missing_required_key_errors():
    entry = keep_entry()
    del entry["rationale"]
    errors = validate([entry], None, complete=False)
    assert any("missing keys" in e for e in errors)


def test_unknown_key_errors():
    entry = keep_entry(extra_field="not allowed")
    errors = validate([entry], None, complete=False)
    assert any("unknown keys" in e for e in errors)


def test_invalid_verdict_errors():
    entry = keep_entry(verdict="MAYBE")
    errors = validate([entry], None, complete=False)
    assert any("is not one of" in e for e in errors)


def test_invalid_rule_code_errors():
    entry = keep_entry(
        verdict="REWRITE", rationale="x", findings=[finding(rules=["Z9"])]
    )
    errors = validate([entry], None, complete=False)
    assert any("non-empty subset" in e for e in errors)


def test_finding_with_wrong_keys_errors():
    entry = keep_entry(
        verdict="REWRITE",
        rationale="x",
        findings=[{"test": "test_a", "verdict": "REWRITE"}],  # missing keys
    )
    errors = validate([entry], None, complete=False)
    assert any("keys must be exactly" in e for e in errors)


def test_duplicate_test_within_findings_errors():
    entry = keep_entry(
        verdict="REWRITE",
        rationale="x",
        findings=[finding(test="test_a"), finding(test="test_a")],
    )
    errors = validate([entry], None, complete=False)
    assert any("listed twice" in e for e in errors)


def test_duplicate_file_entry_errors():
    errors = validate([keep_entry(), keep_entry()], None, complete=False)
    assert any("duplicate file entry" in e for e in errors)


def test_empty_rationale_errors():
    entry = keep_entry(rationale="   ")
    errors = validate([entry], None, complete=False)
    assert any("rationale is empty" in e for e in errors)


def test_top_level_must_be_array():
    errors = validate({"not": "a list"}, None, complete=False)
    assert errors == ["top level must be a JSON array"]


# --------------------------------------------------------------- CLI (main)


def test_main_exits_zero_on_valid_audit(tmp_path, capsys):
    audit = tmp_path / "files.json"
    audit.write_text(json.dumps([keep_entry()]))
    rc = _run_main([str(audit)])
    assert rc == 0
    assert "OK:" in capsys.readouterr().out


def test_main_exits_one_on_invalid_audit(tmp_path, capsys):
    audit = tmp_path / "files.json"
    audit.write_text(json.dumps([keep_entry(verdict="MAYBE")]))
    rc = _run_main([str(audit)])
    assert rc == 1
    assert "ERROR:" in capsys.readouterr().out


def test_main_with_tests_file_reports_total_count(tmp_path, capsys):
    audit = tmp_path / "files.json"
    audit.write_text(json.dumps([keep_entry()]))
    tests_file = tmp_path / "tests.txt"
    tests_file.write_text("test_example.py\ttest_a\ntest_example.py\ttest_b\n")
    rc = _run_main([str(audit), "--tests-file", str(tests_file)])
    assert rc == 0
    assert "covering 2 test functions" in capsys.readouterr().out


def test_main_complete_without_tests_file_exits_two(tmp_path):
    audit = tmp_path / "files.json"
    audit.write_text(json.dumps([keep_entry()]))
    rc = _run_main([str(audit), "--complete"])
    assert rc == 2


def _run_main(argv):
    import sys

    old_argv = sys.argv
    sys.argv = ["validate_test_audit.py", *argv]
    try:
        return main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
