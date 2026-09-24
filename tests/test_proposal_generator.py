"""Tests for the patch generator's confidence and YAML-validity gates."""

import pytest

from registry_mcp.proposal.generator import PatchGenerator

LEAKED = "abcdefghijklmnopqrstuvwxyz0123456789"
QUOTED = f"drop the hardcoded AUTHENTIK_TOKEN={LEAKED}"

VALID = {
    "patch": "services:\n  plex:\n    image: plex\n",
    "commit_message": "fix: add auth middleware",
    "pr_title": "Secure plex",
    "pr_body": "Adds the auth middleware.",
    "confidence": 0.95,
    "reasoning": "router had no auth",
}


VALID_REVISION = {
    "revised_file": "services:\n  plex:\n    image: plex\n    restart: unless-stopped\n",
    "commit_message": "fix: apply review feedback",
    "confidence": 0.9,
    "reasoning": "reviewer asked for a restart policy",
}


class FakeReasoner:
    def __init__(self, result, revision_result=None):
        self._result = result
        self._revision_result = revision_result

    def generate_remediation_patch(self, **kwargs):
        return self._result

    def apply_review_feedback(self, **kwargs):
        return self._revision_result


def _gen(result, threshold=0.8):
    return PatchGenerator(FakeReasoner(result), threshold=threshold)


async def _call(generator):
    return await generator.generate(
        service={"name": "plex"},
        finding_type="auth_mode_conflict",
        current_file="services: {}\n",
        file_path="nodes/workload-01/plex/compose.yaml",
        apply_mode="ansible",
    )


async def test_none_result_is_rejected():
    result = await _call(_gen(None))
    assert result.ok is False
    assert "unavailable" in result.rejection_reason


async def test_low_confidence_is_rejected():
    result = await _call(_gen({**VALID, "confidence": 0.5}))
    assert result.ok is False
    assert "below threshold" in result.rejection_reason
    assert result.confidence == 0.5


async def test_empty_patch_is_rejected():
    result = await _call(_gen({**VALID, "patch": "   "}))
    assert result.ok is False
    assert "empty" in result.rejection_reason


async def test_invalid_yaml_is_rejected():
    result = await _call(_gen({**VALID, "patch": "foo: [unclosed"}))
    assert result.ok is False
    assert "not valid YAML" in result.rejection_reason


async def test_valid_patch_passes():
    result = await _call(_gen(VALID))
    assert result.ok is True
    assert result.confidence == 0.95
    assert result.commit_message == "fix: add auth middleware"
    assert result.pr_title == "Secure plex"


async def test_credentials_are_scrubbed_before_commit():
    leaky_patch = (
        "services:\n"
        "  authentik-proxy:\n"
        "    environment:\n"
        "      AUTHENTIK_TOKEN: abcdefghijklmnopqrstuvwxyz0123456789\n"
        "      LOG_LEVEL: info\n"
    )
    result = await _call(_gen({**VALID, "patch": leaky_patch}))
    assert result.ok is True
    # The real token never survives into the patch; a placeholder takes its place.
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in result.patch
    assert "AUTHENTIK_TOKEN: <replace-with-credential>" in result.patch
    # Non-secret values are left untouched.
    assert "LOG_LEVEL: info" in result.patch


@pytest.mark.parametrize("field", ["commit_message", "pr_title", "pr_body", "reasoning"])
async def test_credentials_in_generated_text_are_scrubbed(field):
    """These fields reach Git and the notification channel just as the patch does."""
    result = await _call(_gen({**VALID, field: QUOTED}))
    assert result.ok is True
    value = getattr(result, field)
    assert LEAKED not in value
    assert "AUTHENTIK_TOKEN=<replace-with-credential>" in value


# ---------------------------------------------------------------------------
# revise() — same gates, applied to review-feedback revisions
# ---------------------------------------------------------------------------


def _revise_gen(revision_result, threshold=0.8):
    return PatchGenerator(FakeReasoner(None, revision_result), threshold=threshold)


async def _call_revise(generator):
    return await generator.revise(
        file_path="nodes/workload-01/plex/compose.yaml",
        current_file="services:\n  plex:\n    image: plex\n",
        feedback="please add a restart policy",
    )


async def test_revise_none_result_is_rejected():
    result = await _call_revise(_revise_gen(None))
    assert result.ok is False
    assert "unavailable" in result.rejection_reason


async def test_revise_low_confidence_is_rejected():
    result = await _call_revise(_revise_gen({**VALID_REVISION, "confidence": 0.3}))
    assert result.ok is False
    assert "below threshold" in result.rejection_reason


async def test_revise_empty_result_is_rejected():
    result = await _call_revise(_revise_gen({**VALID_REVISION, "revised_file": ""}))
    assert result.ok is False
    assert "empty" in result.rejection_reason


async def test_revise_invalid_yaml_is_rejected():
    result = await _call_revise(_revise_gen({**VALID_REVISION, "revised_file": "foo: [unclosed"}))
    assert result.ok is False
    assert "not valid YAML" in result.rejection_reason


async def test_revise_valid_revision_passes():
    result = await _call_revise(_revise_gen(VALID_REVISION))
    assert result.ok is True
    assert result.confidence == 0.9
    assert result.commit_message == "fix: apply review feedback"
    assert "restart: unless-stopped" in result.patch


async def test_revise_credentials_are_scrubbed():
    leaky = {
        **VALID_REVISION,
        "revised_file": (
            "services:\n"
            "  authentik-proxy:\n"
            "    environment:\n"
            "      AUTHENTIK_TOKEN: abcdefghijklmnopqrstuvwxyz0123456789\n"
        ),
    }
    result = await _call_revise(_revise_gen(leaky))
    assert result.ok is True
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in result.patch
    assert "AUTHENTIK_TOKEN: <replace-with-credential>" in result.patch


@pytest.mark.parametrize("field", ["commit_message", "reasoning"])
async def test_revise_credentials_in_generated_text_are_scrubbed(field):
    result = await _call_revise(_revise_gen({**VALID_REVISION, field: QUOTED}))
    assert result.ok is True
    value = getattr(result, field)
    assert LEAKED not in value
    assert "AUTHENTIK_TOKEN=<replace-with-credential>" in value


async def test_patch_generation_runs_off_the_event_loop():
    from conftest import BlockingCall

    reasoner = FakeReasoner(VALID)
    reasoner.generate_remediation_patch = BlockingCall(VALID)
    result = await reasoner.generate_remediation_patch.assert_off_loop(
        _call(PatchGenerator(reasoner, threshold=0.8))
    )
    assert result.ok


async def test_revision_runs_off_the_event_loop():
    from conftest import BlockingCall

    reasoner = FakeReasoner(None, VALID_REVISION)
    reasoner.apply_review_feedback = BlockingCall(VALID_REVISION)
    result = await reasoner.apply_review_feedback.assert_off_loop(
        PatchGenerator(reasoner, threshold=0.8).revise(
            file_path="nodes/workload-01/plex/compose.yaml",
            current_file="services: {}\n",
            feedback="add a restart policy",
        )
    )
    assert result.ok
