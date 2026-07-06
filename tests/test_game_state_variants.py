"""Tests for the session variant (modpack) plumbing in app/core/game_state.py and the
variant-keyed training data documents."""

import pytest

from app.core import game_state, game_state_training_data


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(game_state, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(game_state, "ACTIVE_SESSIONS_PATH", tmp_path / "active.json")
    monkeypatch.setattr(game_state, "_active_process", None)
    monkeypatch.setattr(game_state, "_active_session", None)
    monkeypatch.setattr(game_state, "_cached_values", None)
    monkeypatch.setattr(game_state, "_cached_variant", None)
    monkeypatch.setattr(game_state, "_cached_variant_loaded", False)
    monkeypatch.setattr(game_state_training_data, "TRAINING_DATA_PATH", tmp_path / "training.json")
    monkeypatch.setattr(game_state_training_data, "_OLD_GLOSSARY_PATH", tmp_path / "glossary.json")


def test_session_variant_set_get_find():
    game_state.start_tracking("javaw.exe")
    session_id = game_state.get_active_session_id("javaw.exe")
    assert game_state.get_session_variant("javaw.exe", session_id) is None

    assert game_state.set_session_variant("javaw.exe", session_id, "FTB StoneBlock 4") is True
    assert game_state.get_session_variant("javaw.exe", session_id) == "FTB StoneBlock 4"
    assert game_state.find_session_by_variant("javaw.exe", "ftb stoneblock 4") == session_id
    assert game_state.find_session_by_variant("javaw.exe", "Nolvus") is None

    # get_game_state surfaces the active session's variant (cache invalidated by the setter)
    assert game_state.get_game_state()["variant"] == "FTB StoneBlock 4"

    assert game_state.set_session_variant("javaw.exe", session_id, None) is True
    assert game_state.get_session_variant("javaw.exe", session_id) is None
    assert game_state.set_session_variant("javaw.exe", "missing", "X") is False


def test_create_session_with_variant_switches_active():
    game_state.start_tracking("skyrimse.exe")
    created = game_state.create_session("skyrimse.exe", "Nolvus", variant="Nolvus")
    state = game_state.get_game_state()
    assert state["session_id"] == created["session_id"]
    assert state["variant"] == "Nolvus"
    listed = next(s for s in game_state.get_sessions("skyrimse.exe") if s["session_id"] == created["session_id"])
    assert listed["variant"] == "Nolvus"


def test_session_is_pristine_only_before_values():
    game_state.start_tracking("javaw.exe")
    session_id = game_state.get_active_session_id("javaw.exe")
    assert game_state.session_is_pristine("javaw.exe", session_id) is True
    game_state.set_game_state("javaw.exe", {"activity": "In the quest book"})
    assert game_state.session_is_pristine("javaw.exe", session_id) is False


def test_training_data_variant_keys_and_fallback():
    game_state_training_data.set_training_data("skyrimse.exe", "vanilla notes")
    # No variant doc yet: variant read falls back to the base document
    assert game_state_training_data.get_training_data("skyrimse.exe", "Nolvus") == "vanilla notes"
    assert game_state_training_data.has_own_training_data("skyrimse.exe", "Nolvus") is False

    game_state_training_data.set_training_data("skyrimse.exe", "nolvus notes", variant="Nolvus")
    assert game_state_training_data.get_training_data("skyrimse.exe", "Nolvus") == "nolvus notes"
    assert game_state_training_data.get_training_data("skyrimse.exe") == "vanilla notes"
    assert game_state_training_data.has_own_training_data("skyrimse.exe", "Nolvus") is True

    # Deleting the process drops the base AND every variant document
    game_state_training_data.delete_process("skyrimse.exe")
    assert game_state_training_data.get_training_data("skyrimse.exe") == ""
    assert game_state_training_data.get_training_data("skyrimse.exe", "Nolvus") == ""
