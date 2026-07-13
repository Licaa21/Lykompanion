"""Tests for app/services/llm/game_correction_tool.py - the chat-facing tools that let the model
fix a wrong tracked-game title or modpack tag (and force a training-data + tracker refresh under
the corrected name), instead of only saving the correction as an inert memory fact."""

import asyncio

import pytest

from app.core import game_art, game_state, game_state_trackers, game_state_training_data
from app.services.llm import game_correction_tool, game_knowledge_bootstrap


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
    monkeypatch.setattr(game_state_training_data, "TRAINING_DATA_PATH", tmp_path / "training.json")
    monkeypatch.setattr(game_state_training_data, "_OLD_GLOSSARY_PATH", tmp_path / "glossary.json")
    game_knowledge_bootstrap._attempted.clear()

    # Never let a real bootstrap (network/LLM call) fire from these tests - just record that
    # scheduling was requested, with what arguments.
    scheduled = []
    monkeypatch.setattr(game_knowledge_bootstrap, "schedule_bootstrap", lambda process: scheduled.append(("base", process)))
    monkeypatch.setattr(
        game_knowledge_bootstrap, "schedule_variant_bootstrap",
        lambda process, base_title, modpack: scheduled.append(("variant", process, base_title, modpack)),
    )
    return scheduled


def test_correct_game_title_fixes_title_resets_trackers_and_schedules_refresh(isolated):
    game_state.start_tracking("javaw.exe")
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Custom Field", "description": "whatever"}])
    game_state_training_data.set_training_data("javaw.exe", "stale notes seeded under the wrong name")

    result = asyncio.run(game_correction_tool.execute_correct_game_title({"title": "Minecraft"}))

    assert "Minecraft" in result
    assert game_art.get_display_title("javaw.exe") == "Minecraft"
    assert game_art.get_art("javaw.exe")["title_overridden"] is True
    # Trackers reset back to the untouched defaults, so the fresh bootstrap is free to replace them
    ids = [t["id"] for t in game_state_trackers.get_trackers("javaw.exe")]
    assert ids == [t["id"] for t in game_state_trackers.DEFAULT_TRACKERS]
    assert game_state_training_data.get_training_data("javaw.exe") == ""
    assert ("base", "javaw.exe") in isolated


def test_correct_game_modpack_switches_variant_and_schedules_refresh(isolated):
    game_state.start_tracking("javaw.exe")
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Custom Field", "description": "whatever"}])

    result = asyncio.run(game_correction_tool.execute_correct_game_modpack({"modpack": "FTB StoneBlock 4"}))

    assert "FTB StoneBlock 4" in result
    assert game_state.get_game_state()["variant"] == "FTB StoneBlock 4"
    ids = [t["id"] for t in game_state_trackers.get_trackers("javaw.exe")]
    assert ids == [t["id"] for t in game_state_trackers.DEFAULT_TRACKERS]
    assert ("variant", "javaw.exe", "javaw", "FTB StoneBlock 4") in isolated


def test_correct_game_modpack_empty_clears_variant(isolated):
    game_state.start_tracking("javaw.exe")
    session_id = game_state.get_active_session_id("javaw.exe")
    game_state.set_session_variant("javaw.exe", session_id, "Minecraft")

    result = asyncio.run(game_correction_tool.execute_correct_game_modpack({"modpack": ""}))

    assert "vanilla" in result.lower()
    assert game_state.get_game_state()["variant"] is None
    assert isolated == []  # clearing to vanilla needs no training-data/tracker refresh


def test_correct_game_title_no_active_game():
    result = asyncio.run(game_correction_tool.execute_correct_game_title({"title": "Minecraft"}))
    assert "no game" in result.lower()


def test_correct_game_modpack_no_active_game():
    result = asyncio.run(game_correction_tool.execute_correct_game_modpack({"modpack": "FTB StoneBlock 4"}))
    assert "no game" in result.lower()
