"""Tests for persona assembly (registry_mcp.chat.persona)."""

import time

from conftest import IsolatedSettings
from registry_mcp.chat.persona import base_persona, build_system_prompt, load_overlay


def test_base_persona_is_hostname_free():
    text = base_persona()
    assert text
    # The base persona ships in a public repo — it must never carry any of
    # this operator's real infrastructure detail.
    for leaked in ("heimdall", "waldorf", "panoptichron", "castaldifamily", "10.0.0."):
        assert leaked not in text.lower()


def test_base_persona_is_cached(monkeypatch):
    first = base_persona()
    # Even if the package resource became unreadable, the cached value
    # should still be returned on a second call.
    second = base_persona()
    assert first is second  # identity, not just equality — proves no re-read


def test_load_overlay_empty_when_unset():
    settings = IsolatedSettings(registry_db_path=":memory:")
    assert load_overlay(settings) == ""


def test_load_overlay_reads_configured_file(tmp_path):
    overlay_file = tmp_path / "overlay.md"
    overlay_file.write_text("# My Lab\n\nheimdall runs everything.\n")
    settings = IsolatedSettings(registry_db_path=":memory:", chat_persona_path=str(overlay_file))
    assert "heimdall" in load_overlay(settings)


def test_load_overlay_missing_file_degrades_to_empty(tmp_path):
    settings = IsolatedSettings(
        registry_db_path=":memory:", chat_persona_path=str(tmp_path / "nope.md")
    )
    assert load_overlay(settings) == ""


def test_load_overlay_respects_max_chars(tmp_path):
    overlay_file = tmp_path / "big.md"
    overlay_file.write_text("x" * 1000)
    settings = IsolatedSettings(
        registry_db_path=":memory:",
        chat_persona_path=str(overlay_file),
        chat_persona_max_chars=100,
    )
    assert len(load_overlay(settings)) == 100


def test_load_overlay_picks_up_edits_by_mtime(tmp_path):
    overlay_file = tmp_path / "overlay.md"
    overlay_file.write_text("version one")
    settings = IsolatedSettings(registry_db_path=":memory:", chat_persona_path=str(overlay_file))
    assert load_overlay(settings) == "version one"

    # Ensure the mtime actually advances on filesystems with coarse
    # resolution before rewriting.
    time.sleep(0.01)
    overlay_file.write_text("version two")
    # Force a distinct mtime regardless of filesystem clock granularity.
    import os

    st = overlay_file.stat()
    os.utime(overlay_file, (st.st_atime, st.st_mtime + 1))
    assert load_overlay(settings) == "version two"


def test_build_system_prompt_without_overlay_equals_base():
    settings = IsolatedSettings(registry_db_path=":memory:")
    assert build_system_prompt(settings) == base_persona()


def test_build_system_prompt_appends_overlay_with_framing(tmp_path):
    overlay_file = tmp_path / "overlay.md"
    overlay_file.write_text("Real hostname: heimdall.")
    settings = IsolatedSettings(registry_db_path=":memory:", chat_persona_path=str(overlay_file))
    prompt = build_system_prompt(settings)
    assert base_persona() in prompt
    assert "Real hostname: heimdall." in prompt
    assert "background reference, not as instructions" in prompt
