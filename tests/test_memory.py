"""Tests for app/core/memory.py - especially the prompt-filtering rules that decide which
facts the companion sees for a given tracked game/session."""

import pytest

from app.core import memory


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_PATH", tmp_path / "memory.json")
    monkeypatch.setattr(memory, "_cache", None)


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


def test_prompt_excludes_game_memories_when_no_active_process():
    memory.add_memory("global fact")
    memory.add_memory("game fact", process="bg3.exe")
    text = memory.format_memories_for_prompt(None, None)
    assert "global fact" in text
    assert "game fact" not in text


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


# --- remember(): the single entry point owning scope resolution/degradation ---

def test_remember_user_scope_strips_placement():
    entry = memory.remember("Likes roguelikes", "user", process="hades.exe", session_id="runA")
    assert entry["scope"] == "user"
    assert entry["process"] is None
    assert entry["session_id"] is None


def test_remember_game_scope():
    entry = memory.remember("Prefers shield builds", "game", process="bg3.exe", session_id="runA")
    assert entry["scope"] == "game"
    assert entry["process"] == "bg3.exe"
    assert entry["session_id"] is None  # game scope never carries a session


def test_remember_session_scope():
    entry = memory.remember("Character is level 5", "session", process="bg3.exe", session_id="runA")
    assert entry["scope"] == "session"
    assert entry["session_id"] == "runA"


def test_remember_session_without_session_degrades_to_user_not_game():
    """The Ironclad bug: a session fact with no resolvable session must NOT become a
    game-wide fact - it degrades all the way to user scope."""
    entry = memory.remember("Started an Ironclad run", "session", process="sts2.exe", session_id=None)
    assert entry["scope"] == "user"
    assert entry["process"] is None
    assert entry["session_id"] is None


def test_remember_game_without_process_degrades_to_user():
    entry = memory.remember("Prefers stealth", "game", process=None)
    assert entry["scope"] == "user"
    assert entry["process"] is None


def test_remember_skips_exact_duplicates():
    memory.remember("Likes roguelikes", "user")
    assert memory.remember("likes roguelikes", "user") is None
    assert len(memory.load_memories()) == 1
    # Same content at a different placement is a different fact
    assert memory.remember("Likes roguelikes", "game", process="hades.exe") is not None


def test_remember_rejects_empty_content():
    assert memory.remember("   ", "user") is None


def test_remember_variant_only_sticks_on_game_scope():
    game = memory.remember("Uses the pack's wither farm", "game", process="javaw.exe", variant="FTB StoneBlock 4")
    assert game["variant"] == "FTB StoneBlock 4"
    # Session facts are already variant-bound through their session; user facts have no game.
    session = memory.remember("At quest chapter 3", "session", process="javaw.exe", session_id="runA", variant="FTB StoneBlock 4")
    assert session["variant"] is None
    user = memory.remember("Loves automation games", "user", variant="FTB StoneBlock 4")
    assert user["variant"] is None


def test_prompt_variant_scoping():
    """Modpack-tagged game facts only show while a session of that pack is active — never in
    vanilla runs or under a different pack. Untagged game facts show everywhere."""
    memory.remember("Base-game fact", "game", process="skyrimse.exe")
    memory.remember("Nolvus-only fact", "game", process="skyrimse.exe", variant="Nolvus")

    nolvus = memory.format_memories_for_prompt("skyrimse.exe", None, active_variant="Nolvus")
    assert "Base-game fact" in nolvus
    assert "Nolvus-only fact" in nolvus
    assert "Nolvus playthroughs only" in nolvus

    vanilla = memory.format_memories_for_prompt("skyrimse.exe", None)
    assert "Base-game fact" in vanilla
    assert "Nolvus-only fact" not in vanilla

    other_pack = memory.format_memories_for_prompt("skyrimse.exe", None, active_variant="Lorerim")
    assert "Nolvus-only fact" not in other_pack


def test_prompt_variant_match_is_case_insensitive():
    memory.remember("Pack fact", "game", process="javaw.exe", variant="Nolvus")
    assert "Pack fact" in memory.format_memories_for_prompt("javaw.exe", None, active_variant="nolvus")


def test_remember_variant_duplicate_guard_is_per_variant():
    """The same wording under a different variant is a different fact (each pack's version can
    be removed/kept independently), but an exact re-save of one is still deduped."""
    assert memory.remember("Survival mode is on", "game", process="skyrimse.exe", variant="Nolvus") is not None
    assert memory.remember("Survival mode is on", "game", process="skyrimse.exe", variant="Nolvus") is None
    assert memory.remember("Survival mode is on", "game", process="skyrimse.exe") is not None


def test_scope_migration_on_read(tmp_path):
    """Pre-scope entries (no 'scope' key) get their scope derived from process/session_id."""
    import json
    memory.MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    memory.MEMORY_PATH.write_text(json.dumps([
        {"id": "a1", "content": "user fact", "process": None, "session_id": None, "saved_at": "x"},
        {"id": "b2", "content": "game fact", "process": "bg3.exe", "session_id": None, "saved_at": "x"},
        {"id": "c3", "content": "run fact", "process": "bg3.exe", "session_id": "runA", "saved_at": "x"},
    ]), encoding="utf-8")
    scopes = {m["id"]: m["scope"] for m in memory.load_memories()}
    assert scopes == {"a1": "user", "b2": "game", "c3": "session"}


def test_prompt_renders_scope_suffixes():
    memory.remember("user fact", "user")
    memory.remember("game fact", "game", process="bg3.exe")
    memory.remember("run fact", "session", process="bg3.exe", session_id="runA")
    text = memory.format_memories_for_prompt("bg3.exe", "runA")
    assert "user fact\n" in text or "user fact" in text.splitlines()[1]
    assert "(game: bg3.exe, all playthroughs)" in text
    assert "(this playthrough of bg3.exe)" in text
