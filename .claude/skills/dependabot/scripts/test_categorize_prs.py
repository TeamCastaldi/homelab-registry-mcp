"""Tests for categorize_prs.py.

Run with:

    python3 -m pytest .claude/skills/dependabot/scripts/test_categorize_prs.py -q
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "categorize_prs.py"

_spec = importlib.util.spec_from_file_location("categorize_prs", SCRIPT)
categorize_prs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(categorize_prs)

categorize = categorize_prs.categorize
_core_version = categorize_prs._core_version


def pr(title):
    return {"number": 1, "title": title, "url": "u", "headRefName": "b"}


# --------------------------------------------------------------- version parsing


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.2.3", (1, 2, 3)),
        ("1.2", (1, 2, 0)),
        ("2", (2, 0, 0)),
        ("v3", (3, 0, 0)),          # GitHub Actions tag style
        ("V3.1", (3, 1, 0)),
        ("1.2.3-beta.1", (1, 2, 3)),  # pre-release stripped
        ("1.2.3+build5", (1, 2, 3)),  # build metadata stripped
    ],
)
def test_core_version_parses(raw, expected):
    assert _core_version(raw) == expected


@pytest.mark.parametrize("raw", ["latest", "", "main", ">=1.0"])
def test_core_version_returns_none_when_unparseable(raw):
    assert _core_version(raw) is None


def test_unparseable_version_never_scored_as_pre_1_0():
    """A failed parse must route to Unknown. Were _core_version to return
    (0, 0, 0) rather than None, this would read as a pre-1.0 package and score
    Major — a confident risk category resting on a parse that failed."""
    out = categorize(pr("Bump mystery from latest to 1.0.0"))
    assert out["category"] == "Unknown"
    assert "Could not parse" in out["reason"]


# -------------------------------------------------------------------- grouped


def test_grouped_update():
    out = categorize(pr("Bump the npm-deps group with 5 updates"))
    assert out["category"] == "Grouped"
    assert out["package"] == "group:npm-deps"
    assert out["old_version"] is None and out["new_version"] is None


def test_grouped_update_across_directories():
    out = categorize(pr("Bump the pip group across 2 directories with 3 updates"))
    assert out["category"] == "Grouped"
    assert out["package"] == "group:pip"


def test_grouped_singular_update():
    assert categorize(pr("Bump the ci group with 1 update"))["category"] == "Grouped"


# ---------------------------------------------------------------------- major


def test_major_bump():
    out = categorize(pr("Bump lodash from 3.10.1 to 4.17.21"))
    assert out["category"] == "Major"
    assert out["package"] == "lodash"
    assert out["old_version"] == "3.10.1"
    assert out["new_version"] == "4.17.21"


def test_actions_style_major_bump():
    assert categorize(pr("Bump actions/checkout from v3 to v4"))["category"] == "Major"


def test_pre_1_0_minor_bump_is_major():
    """Below 1.0 SemVer makes no compatibility promise, so a minor bump carries
    breaking-change risk and must not be batched as safe."""
    out = categorize(pr("Bump anyio from 0.4.1 to 0.5.0"))
    assert out["category"] == "Major"
    assert "pre-1.0" in out["reason"]


# ---------------------------------------------------------------- minor/patch


def test_stable_minor_bump():
    assert categorize(pr("Bump requests from 2.30.0 to 2.31.0"))["category"] == "Minor/Patch"


def test_stable_patch_bump():
    assert categorize(pr("Bump requests from 2.31.0 to 2.31.1"))["category"] == "Minor/Patch"


def test_pre_1_0_patch_bump_is_safe():
    """Within one 0.x minor line, a patch bump is still the safe set."""
    assert categorize(pr("Bump anyio from 0.4.1 to 0.4.3"))["category"] == "Minor/Patch"


# ------------------------------------------------- conventional commit prefixes


@pytest.mark.parametrize(
    "title",
    [
        "chore(deps): Bump lodash from 4.17.20 to 4.17.21",
        "chore: Bump lodash from 4.17.20 to 4.17.21",
        "fix(deps)!: Bump lodash from 4.17.20 to 4.17.21",
        "build(deps-dev): Bump lodash from 4.17.20 to 4.17.21",
    ],
)
def test_conventional_commit_prefix_is_stripped(title):
    out = categorize(pr(title))
    assert out["category"] == "Minor/Patch"
    assert out["package"] == "lodash"


def test_plain_title_without_prefix_still_matches():
    assert categorize(pr("Bump lodash from 4.17.20 to 4.17.21"))["package"] == "lodash"


# -------------------------------------------------------------------- unknown


def test_requirement_widening_is_unknown():
    """A range widening has no single old/new version, so it must never be
    scored as a version transition."""
    out = categorize(pr("Update mcp requirement from <2,>=1.28.1 to >=1.28.1,<3"))
    assert out["category"] == "Unknown"
    assert out["package"] == "mcp"
    assert "Requirement-range widening" in out["reason"]


def test_unrecognized_title_is_unknown():
    out = categorize(pr("Add a shiny new feature"))
    assert out["category"] == "Unknown"
    assert out["package"] is None


def test_unparseable_version_is_unknown():
    out = categorize(pr("Bump mystery from latest to newest"))
    assert out["category"] == "Unknown"
    assert "Could not parse" in out["reason"]


def test_original_fields_are_preserved():
    out = categorize(pr("Bump requests from 2.30.0 to 2.31.0"))
    assert out["number"] == 1 and out["url"] == "u" and out["headRefName"] == "b"


# ------------------------------------------------------------------ end to end


def run_script(stdin_text):
    # check=False: several cases below assert on a non-zero exit code, so a
    # failed run is an expected outcome rather than an error to raise on.
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
    )


def test_end_to_end_emits_json_array():
    result = run_script(json.dumps([pr("Bump requests from 2.30.0 to 2.31.0")]))
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert len(parsed) == 1
    assert parsed[0]["category"] == "Minor/Patch"


def test_empty_array_is_valid_input():
    result = run_script("[]")
    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_invalid_json_exits_nonzero():
    result = run_script("not json at all")
    assert result.returncode == 1
    assert "not valid JSON" in result.stderr


def test_non_array_input_exits_nonzero():
    result = run_script('{"title": "Bump x from 1.0.0 to 1.0.1"}')
    assert result.returncode == 1
    assert "expected a JSON array" in result.stderr
