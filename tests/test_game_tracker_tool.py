"""Tests for app/services/llm/game_tracker_tool.py - the chat-facing tools that let the model
add/remove/edit the currently tracked game's tracked fields, scoped to the current process + the
active modpack variant (or the base game if none is active)."""

import asyncio

import pytest

from app.core import game_art, game_state, game_state_trackers
from app.services.llm import game_knowledge_bootstrap, game_tracker_tool


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(game_state, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(game_state, "ACTIVE_SESSIONS_PATH", tmp_path / "active.json")
    monkeypatch.setattr(game_state, "_active_process", None)
    monkeypatch.setattr(game_state, "_active_session", None)
    monkeypatch.setattr(game_state, "_cached_values", None)
    monkeypatch.setattr(game_state, "_cached_variant", None)
    monkeypatch.setattr(game_state, "_cached_variant_loaded", False)
    monkeypatch.setattr(game_state_trackers, "TRACKERS_PATH", tmp_path / "trackers.json")
    monkeypatch.setattr(game_art, "GAME_ART_PATH", tmp_path / "game_art.json")
    game_knowledge_bootstrap._attempted.clear()


def test_add_game_tracker(isolated):
    game_state.start_tracking("javaw.exe")

    result = asyncio.run(game_tracker_tool.execute_add_game_tracker({"label": "Combo Count", "description": "current combo"}))

    assert "Combo Count" in result
    labels = [t["label"] for t in game_state_trackers.get_trackers("javaw.exe") if t["id"] != "activity"]
    assert "Combo Count" in labels


def test_add_game_tracker_is_scoped_to_the_active_variant(isolated):
    game_state.start_tracking("javaw.exe")
    session_id = game_state.get_active_session_id("javaw.exe")
    game_state.set_session_variant("javaw.exe", session_id, "FTB StoneBlock 4")

    asyncio.run(game_tracker_tool.execute_add_game_tracker({"label": "Vaults Cleared", "description": "vaults done"}))

    # A fresh variant seeds the same defaults any new process would - "add" appends to that,
    # it doesn't clobber it.
    variant_labels = [
        t["label"] for t in game_state_trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4")
        if t["id"] != "activity"
    ]
    assert variant_labels[-1] == "Vaults Cleared"
    base_labels = [t["label"] for t in game_state_trackers.get_trackers("javaw.exe") if t["id"] != "activity"]
    assert "Vaults Cleared" not in base_labels  # base process untouched


def test_add_game_tracker_duplicate_label_is_a_noop(isolated):
    game_state.start_tracking("javaw.exe")
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Combo Count", "description": "x"}])

    result = asyncio.run(game_tracker_tool.execute_add_game_tracker({"label": "combo count", "description": "y"}))

    assert "already exists" in result.lower()
    labels = [t["label"] for t in game_state_trackers.get_trackers("javaw.exe") if t["id"] != "activity"]
    assert labels == ["Combo Count"]


def test_add_game_tracker_no_label(isolated):
    game_state.start_tracking("javaw.exe")
    result = asyncio.run(game_tracker_tool.execute_add_game_tracker({"label": "", "description": "x"}))
    assert "no label" in result.lower()


def test_add_game_tracker_no_active_game():
    result = asyncio.run(game_tracker_tool.execute_add_game_tracker({"label": "Combo Count", "description": "x"}))
    assert "no game" in result.lower()


def test_remove_game_tracker(isolated):
    game_state.start_tracking("javaw.exe")
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Combo Count", "description": "x"}, {"label": "Ammo", "description": "y"}])

    result = asyncio.run(game_tracker_tool.execute_remove_game_tracker({"label": "Combo Count"}))

    assert "Combo Count" in result
    labels = [t["label"] for t in game_state_trackers.get_trackers("javaw.exe") if t["id"] != "activity"]
    assert labels == ["Ammo"]


def test_remove_game_tracker_not_found(isolated):
    game_state.start_tracking("javaw.exe")
    result = asyncio.run(game_tracker_tool.execute_remove_game_tracker({"label": "Nonexistent"}))
    assert "no tracker" in result.lower()


def test_remove_game_tracker_cannot_remove_activity(isolated):
    game_state.start_tracking("javaw.exe")
    result = asyncio.run(game_tracker_tool.execute_remove_game_tracker({"label": "Current Activity"}))
    assert "built-in" in result.lower()
    labels = [t["id"] for t in game_state_trackers.get_trackers("javaw.exe")]
    assert "activity" in labels


def test_update_game_tracker_renames_and_keeps_the_same_id(isolated):
    game_state.start_tracking("javaw.exe")
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Combo Count", "description": "old desc"}])
    original_id = next(
        t["id"] for t in game_state_trackers.get_trackers("javaw.exe") if t["label"] == "Combo Count"
    )

    result = asyncio.run(
        game_tracker_tool.execute_update_game_tracker({"label": "Combo Count", "new_label": "Combo Meter"})
    )

    assert "Combo Meter" in result
    updated = next(t for t in game_state_trackers.get_trackers("javaw.exe") if t["id"] == original_id)
    assert updated["label"] == "Combo Meter"
    assert updated["description"] == "old desc"  # unchanged since new_description wasn't given


def test_update_game_tracker_description_only(isolated):
    game_state.start_tracking("javaw.exe")
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Combo Count", "description": "old desc"}])

    asyncio.run(
        game_tracker_tool.execute_update_game_tracker({"label": "Combo Count", "new_description": "new desc"})
    )

    updated = next(t for t in game_state_trackers.get_trackers("javaw.exe") if t["label"] == "Combo Count")
    assert updated["description"] == "new desc"


def test_update_game_tracker_not_found(isolated):
    game_state.start_tracking("javaw.exe")
    result = asyncio.run(game_tracker_tool.execute_update_game_tracker({"label": "Nonexistent", "new_label": "X"}))
    assert "no tracker" in result.lower()


def test_update_game_tracker_cannot_edit_activity(isolated):
    game_state.start_tracking("javaw.exe")
    result = asyncio.run(
        game_tracker_tool.execute_update_game_tracker({"label": "Current Activity", "new_label": "Whatever"})
    )
    assert "built-in" in result.lower()


def test_regenerate_game_trackers_for_the_active_variant(isolated, monkeypatch):
    game_state.start_tracking("javaw.exe")
    game_art.set_title_override("javaw.exe", "Minecraft")
    session_id = game_state.get_active_session_id("javaw.exe")
    game_state.set_session_variant("javaw.exe", session_id, "FTB StoneBlock 4")
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Custom"}], variant="FTB StoneBlock 4")

    scheduled = []
    monkeypatch.setattr(
        game_knowledge_bootstrap, "schedule_variant_bootstrap",
        lambda process, base_title, modpack: scheduled.append((process, base_title, modpack)),
    )

    result = asyncio.run(game_tracker_tool.execute_regenerate_game_trackers({}))

    assert "FTB StoneBlock 4" in result
    assert game_state_trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4") == game_state_trackers.DEFAULT_TRACKERS
    assert scheduled == [("javaw.exe", "Minecraft", "FTB StoneBlock 4")]


def test_regenerate_game_trackers_for_the_base_game(isolated, monkeypatch):
    game_state.start_tracking("javaw.exe")
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Custom"}])

    scheduled = []
    monkeypatch.setattr(game_knowledge_bootstrap, "schedule_bootstrap", lambda process: scheduled.append(process))

    result = asyncio.run(game_tracker_tool.execute_regenerate_game_trackers({}))

    assert "this game" in result.lower()
    assert game_state_trackers.get_trackers("javaw.exe") == game_state_trackers.DEFAULT_TRACKERS
    assert scheduled == ["javaw.exe"]


def test_regenerate_game_trackers_no_active_game():
    result = asyncio.run(game_tracker_tool.execute_regenerate_game_trackers({}))
    assert "no game" in result.lower()
