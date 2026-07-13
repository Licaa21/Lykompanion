"""Tests for app/services/llm/game_state_extraction.py's _push_overlay_game_state - the overlay
panel's title used to be a naive cleanup of the raw process name ("javaw.exe" -> "Javaw")
regardless of what the Gaming Journal actually resolved the game's title to, and there was no
indication of the active modpack/session in the overlay at all."""

import pytest

from app.core import game_art, game_state, game_state_trackers
from app.services.llm import game_state_extraction


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(game_state, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(game_state, "ACTIVE_SESSIONS_PATH", tmp_path / "active.json")
    monkeypatch.setattr(game_state, "_active_process", None)
    monkeypatch.setattr(game_state, "_active_session", None)
    monkeypatch.setattr(game_state, "_cached_values", None)
    monkeypatch.setattr(game_state, "_cached_variant", None)
    monkeypatch.setattr(game_state, "_cached_variant_loaded", False)
    monkeypatch.setattr(game_art, "GAME_ART_PATH", tmp_path / "game_art.json")
    monkeypatch.setattr(game_state_trackers, "TRACKERS_PATH", tmp_path / "trackers.json")
    monkeypatch.setattr(game_state_extraction, "_last_process", None)

    pushed = []
    monkeypatch.setattr(game_state_extraction.overlay_process, "push", lambda command: pushed.append(command))
    return pushed


def test_overlay_uses_resolved_title_not_raw_process_name(isolated):
    game_state.start_tracking("javaw.exe")
    game_art.set_title_override("javaw.exe", "Minecraft")

    game_state_extraction._push_overlay_game_state("javaw.exe")

    assert isolated[0]["title"] == "Minecraft"


def test_overlay_shows_modpack_row_when_variant_active(isolated):
    game_state.start_tracking("javaw.exe")
    session_id = game_state.get_active_session_id("javaw.exe")
    game_state.set_session_variant("javaw.exe", session_id, "FTB StoneBlock 4")

    game_state_extraction._push_overlay_game_state("javaw.exe")

    assert isolated[0]["rows"][0] == ["Modpack", "FTB StoneBlock 4"]


def test_overlay_shows_session_row_for_named_non_default_profile(isolated):
    game_state.start_tracking("javaw.exe")
    game_state.create_session("javaw.exe", "NG+")

    game_state_extraction._push_overlay_game_state("javaw.exe")

    assert isolated[0]["rows"][0] == ["Session", "NG+"]


def test_overlay_omits_identity_row_for_plain_default_session(isolated):
    game_state.start_tracking("javaw.exe")

    game_state_extraction._push_overlay_game_state("javaw.exe")

    row_labels = [r[0] for r in isolated[0]["rows"]]
    assert "Modpack" not in row_labels
    assert "Session" not in row_labels


def test_forget_tracked_process_resets_matching_process(monkeypatch, isolated):
    # Regression test (2026-07-13): deleting a tracked game via the API while it's still the
    # focused/running process never changed _last_process (the foreground process name is
    # identical before and after), so _capture_tick's "process != _last_process" check - the only
    # thing that triggers a fresh start_tracking/variant-detection/bootstrap - never fired again.
    monkeypatch.setattr(game_state_extraction, "_last_process", "javaw.exe")

    game_state_extraction.forget_tracked_process("javaw.exe")

    assert game_state_extraction._last_process is None


def test_forget_tracked_process_is_case_insensitive(monkeypatch, isolated):
    monkeypatch.setattr(game_state_extraction, "_last_process", "JavaW.exe")

    game_state_extraction.forget_tracked_process("javaw.exe")

    assert game_state_extraction._last_process is None


def test_forget_tracked_process_ignores_a_different_process(monkeypatch, isolated):
    monkeypatch.setattr(game_state_extraction, "_last_process", "eldenring.exe")

    game_state_extraction.forget_tracked_process("javaw.exe")

    assert game_state_extraction._last_process == "eldenring.exe"
