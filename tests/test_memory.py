"""Tests for app/core/memory.py - especially the prompt-filtering rules that decide which
facts the companion sees for a given tracked game/session."""

import pytest

from app.core import memory


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_PATH", tmp_path / "memory.json")


def test_add_update_remove_roundtrip():
    entry = memory.add_memory("Likes roguelikes")
    assert memory.load_memories() == [entry]

    updated = memory.update_memory(entry["id"], "Loves roguelikes", process="hades.exe")
    assert updated["content"] == "Loves roguelikes"
    assert updated["process"] == "hades.exe"
    # saved_at intentionally preserved (rollback anchors on creation time)
    assert updated["saved_at"] == entry["saved_at"]

    assert memory.update_memory("nonexistent", "x") is None
    assert memory.remove_memory(entry["id"]) is True
    assert memory.load_memories() == []
    assert memory.remove_memory(entry["id"]) is False


def test_prompt_includes_all_when_no_active_process():
    memory.add_memory("global fact")
    memory.add_memory("game fact", process="bg3.exe")
    text = memory.format_memories_for_prompt(None, None)
    assert "global fact" in text
    assert "game fact" in text


def test_prompt_filters_by_process_and_session():
    memory.add_memory("global fact")
    memory.add_memory("bg3 game-level fact", process="bg3.exe")
    memory.add_memory("bg3 run A fact", process="bg3.exe", session_id="runA")
    memory.add_memory("bg3 run B fact", process="bg3.exe", session_id="runB")
    memory.add_memory("elden fact", process="eldenring.exe")

    text = memory.format_memories_for_prompt("bg3.exe", "runA")
    assert "global fact" in text            # global: always shown
    assert "bg3 game-level fact" in text    # process-level: all sessions of this game
    assert "bg3 run A fact" in text         # matching session
    assert "bg3 run B fact" not in text     # other session excluded
    assert "elden fact" not in text         # other game excluded


def test_prompt_process_match_is_case_insensitive():
    memory.add_memory("fact", process="BG3.exe")
    text = memory.format_memories_for_prompt("bg3.EXE", None)
    assert "fact" in text


def test_prompt_without_session_context_includes_all_process_memories():
    memory.add_memory("session fact", process="bg3.exe", session_id="runA")
    text = memory.format_memories_for_prompt("bg3.exe", None)
    assert "session fact" in text


def test_prompt_empty_when_no_memories():
    assert memory.format_memories_for_prompt(None, None) == ""


def test_prompt_lists_ids_for_removal():
    entry = memory.add_memory("removable fact")
    text = memory.format_memories_for_prompt(None, None)
    assert f"[{entry['id']}]" in text
