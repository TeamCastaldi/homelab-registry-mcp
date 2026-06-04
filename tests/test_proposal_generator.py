"""Tests for the patch generator's confidence and YAML-validity gates."""

from registry_mcp.proposal.generator import PatchGenerator

VALID = {
    "patch": "services:\n  plex:\n    image: plex\n",
    "commit_message": "fix: add auth middleware",
    "pr_title": "Secure plex",
    "pr_body": "Adds the auth middleware.",
    "confidence": 0.95,
    "reasoning": "router had no auth",
}


class FakeReasoner:
    def __init__(self, result):
        self._result = result

    def generate_remediation_patch(self, **kwargs):
        return self._result


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
