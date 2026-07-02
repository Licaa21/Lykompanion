"""Tests for app/core/observations.py - the OCR pass's staging journal (Layer 2)."""

import pytest

from app.core import observations


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "OBSERVATIONS_PATH", tmp_path / "observations.json")


def test_add_and_get_roundtrip():
    added = observations.add_observations("bg3.exe", "runA", [("Defeated the goblin camp", 0.8)])
    assert len(added) == 1
    assert added[0]["confidence"] == 0.8
    got = observations.get_observations("bg3.exe", "runA")
    assert [o["content"] for o in got] == ["Defeated the goblin camp"]


def test_duplicates_within_session_skipped():
    observations.add_observations("bg3.exe", "runA", [("Reached level 5", 0.9)])
    added = observations.add_observations("bg3.exe", "runA", [("reached level 5", 0.7)])
    assert added == []
    # Same content in a different session is a separate observation
    added = observations.add_observations("bg3.exe", "runB", [("Reached level 5", 0.7)])
    assert len(added) == 1


def test_get_without_session_returns_all_for_process():
    observations.add_observations("bg3.exe", "runA", [("a", None)])
    observations.add_observations("bg3.exe", "runB", [("b", None)])
    observations.add_observations("elden.exe", "runA", [("c", None)])
    assert len(observations.get_observations("bg3.exe")) == 2


def test_cap_drops_oldest(monkeypatch):
    monkeypatch.setattr(observations, "MAX_PER_SESSION", 3)
    for i in range(5):
        observations.add_observations("bg3.exe", "runA", [(f"fact {i}", None)])
    got = observations.get_observations("bg3.exe", "runA")
    assert [o["content"] for o in got] == ["fact 2", "fact 3", "fact 4"]


def test_remove_by_id():
    added = observations.add_observations("bg3.exe", "runA", [("x", None), ("y", None)])
    assert observations.remove_observations([added[0]["id"]]) == 1
    assert [o["content"] for o in observations.get_observations("bg3.exe", "runA")] == ["y"]
    assert observations.remove_observations(["nonexistent"]) == 0


def test_clear_session():
    observations.add_observations("bg3.exe", "runA", [("x", None)])
    observations.add_observations("bg3.exe", "runB", [("y", None)])
    assert observations.clear_session("bg3.exe", "runA") == 1
    assert observations.get_observations("bg3.exe", "runB")


def test_prompt_formatting_marks_unconfirmed():
    observations.add_observations("bg3.exe", "runA", [("Defeated the boss", 0.9)])
    text = observations.format_observations_for_prompt("bg3.exe", "runA")
    assert "UNCONFIRMED" in text
    assert "Defeated the boss" in text
    assert observations.format_observations_for_prompt("bg3.exe", "runZ") == ""
