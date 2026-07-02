"""Tests for app/core/game_state_trackers.py - id slugify/dedupe and the locked activity
tracker, plus the reserved-key guard (tracker ids share a JSON namespace with the extraction
response's own keys)."""

import pytest

from app.core import game_state_trackers as trackers


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(trackers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(trackers, "TRACKERS_PATH", tmp_path / "trackers.json")


def test_get_trackers_seeds_defaults():
    result = trackers.get_trackers("game.exe")
    assert result == trackers.DEFAULT_TRACKERS
    assert result[0]["id"] == trackers.ACTIVITY_TRACKER_ID
    assert result[0]["locked"] is True


def test_set_trackers_always_restores_locked_activity_first():
    result = trackers.set_trackers("game.exe", [{"label": "Rank", "description": "current rank"}])
    assert result[0]["id"] == trackers.ACTIVITY_TRACKER_ID
    assert result[0]["locked"] is True
    assert [t["label"] for t in result[1:]] == ["Rank"]


def test_set_trackers_slugifies_missing_ids():
    result = trackers.set_trackers("game.exe", [{"label": "1v1 Rank!", "description": ""}])
    assert result[1]["id"] == "1v1_rank"


def test_set_trackers_drops_blank_labels():
    result = trackers.set_trackers("game.exe", [{"label": "  ", "description": "x"}])
    assert len(result) == 1  # only activity remains


def test_set_trackers_dedupes_ids():
    result = trackers.set_trackers(
        "game.exe",
        [{"label": "Rank"}, {"label": "rank"}],
    )
    ids = [t["id"] for t in result[1:]]
    assert ids == ["rank", "rank_2"]


def test_set_trackers_guards_reserved_response_keys():
    result = trackers.set_trackers("game.exe", [{"label": "Confidence"}, {"label": "Save Memories"}])
    ids = [t["id"] for t in result[1:]]
    assert "confidence" not in ids
    assert "save_memories" not in ids
    assert ids == ["confidence_2", "save_memories_2"]


def test_reset_trackers_restores_defaults():
    trackers.set_trackers("game.exe", [{"label": "Custom"}])
    result = trackers.reset_trackers("game.exe")
    assert result == trackers.DEFAULT_TRACKERS
