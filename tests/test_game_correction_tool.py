"""Tests for app/services/llm/game_correction_tool.py - the chat-facing tools that let the model
fix a wrong tracked-game title or modpack tag (and force a training-data + tracker refresh under
the corrected name), instead of only saving the correction as an inert memory fact."""

import asyncio

import pytest

from app.core import game_art, game_state, game_state_trackers, game_state_training_data, memory
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
    monkeypatch.setattr(memory, "MEMORY_PATH", tmp_path / "memory.json")
    monkeypatch.setattr(memory, "_cache", None)
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
    # The variant gets its own fresh tracker list (reset to defaults, eligible for its own
    # bootstrap to replace) - the base process's own customized trackers are untouched.
    variant_ids = [t["id"] for t in game_state_trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4")]
    assert variant_ids == [t["id"] for t in game_state_trackers.DEFAULT_TRACKERS]
    assert [t["label"] for t in game_state_trackers.get_trackers("javaw.exe")[1:]] == ["Custom Field"]
    assert ("variant", "javaw.exe", "javaw", "FTB StoneBlock 4") in isolated


def test_correct_game_modpack_empty_reverts_a_stale_name_and_clears_session_memories(isolated):
    # Regression test (2026-07-13): apply_detected_variant renames a freshly-tagged session to
    # match the modpack (variant_detection.py's "tag pristine session in place" path), but
    # clearing the tag back to vanilla never reverted that name - leaving a "vanilla" session
    # stuck displaying the old modpack's name forever. This tool is an explicit "this session's
    # tag is wrong" correction (see switch_to_vanilla_session below for the "I'm playing without
    # the pack now" case, which must NOT touch this session) - so its modpack-specific playthrough
    # memories are cleared too, since they no longer apply once the tag is gone.
    game_state.start_tracking("javaw.exe")
    session_id = game_state.get_active_session_id("javaw.exe")
    game_state.set_session_variant("javaw.exe", session_id, "FTB StoneBlock 4")
    game_state.rename_session("javaw.exe", session_id, "FTB StoneBlock 4")
    memory.remember(content="Crafted the Vault Sword", scope="session", process="javaw.exe", session_id=session_id)

    result = asyncio.run(game_correction_tool.execute_correct_game_modpack({"modpack": ""}))

    assert "vanilla" in result.lower()
    assert game_state.get_game_state()["variant"] is None
    assert game_state.get_session_name("javaw.exe", session_id) == "Default"
    assert memory.load_memories() == []


def test_correct_game_modpack_empty_leaves_a_custom_name_alone(isolated):
    # A session the player named themselves (not auto-named after the pack) shouldn't be
    # renamed just because its modpack tag gets cleared.
    game_state.start_tracking("javaw.exe")
    session_id = game_state.get_active_session_id("javaw.exe")
    game_state.set_session_variant("javaw.exe", session_id, "FTB StoneBlock 4")
    game_state.rename_session("javaw.exe", session_id, "NG+ run")

    asyncio.run(game_correction_tool.execute_correct_game_modpack({"modpack": ""}))

    assert game_state.get_session_name("javaw.exe", session_id) == "NG+ run"


def test_switch_to_vanilla_session_leaves_the_variant_session_and_its_memories_untouched(isolated):
    # The other half of the split above: "I'm now playing without the pack" (not a correction of
    # this session's own tag) should switch away instead, leaving the modpack session - tag, name,
    # and memories - exactly as it was. No plain session exists yet here, so a new "Default" one
    # is created and switched to.
    game_state.start_tracking("javaw.exe")
    variant_session_id = game_state.get_active_session_id("javaw.exe")
    game_state.set_session_variant("javaw.exe", variant_session_id, "FTB StoneBlock 4")
    game_state.rename_session("javaw.exe", variant_session_id, "FTB StoneBlock 4")
    memory.remember(content="Crafted the Vault Sword", scope="session", process="javaw.exe", session_id=variant_session_id)

    result = asyncio.run(game_correction_tool.execute_switch_to_vanilla_session({}))

    assert "vanilla" in result.lower()
    new_gs = game_state.get_game_state()
    assert new_gs["variant"] is None
    assert new_gs["session_id"] != variant_session_id
    # The original modpack session - tag, name, and memories - is completely untouched.
    assert game_state.get_session_variant("javaw.exe", variant_session_id) == "FTB StoneBlock 4"
    assert game_state.get_session_name("javaw.exe", variant_session_id) == "FTB StoneBlock 4"
    assert len(memory.load_memories()) == 1
    assert isolated == []  # no training-data/tracker refresh - nothing about the pack changed


def test_switch_to_vanilla_session_reuses_an_existing_plain_session(isolated):
    game_state.start_tracking("javaw.exe")
    plain_session_id = game_state.get_active_session_id("javaw.exe")  # created first, no variant

    variant_session_id = game_state.create_session("javaw.exe", "FTB StoneBlock 4", variant="FTB StoneBlock 4")["session_id"]
    assert game_state.get_active_session_id("javaw.exe") == variant_session_id

    asyncio.run(game_correction_tool.execute_switch_to_vanilla_session({}))

    assert game_state.get_active_session_id("javaw.exe") == plain_session_id
    # The variant session is still there, untouched.
    assert game_state.get_session_variant("javaw.exe", variant_session_id) == "FTB StoneBlock 4"


def test_switch_to_vanilla_session_noop_when_already_vanilla(isolated):
    game_state.start_tracking("javaw.exe")

    result = asyncio.run(game_correction_tool.execute_switch_to_vanilla_session({}))

    assert "already vanilla" in result.lower()


def test_switch_to_vanilla_session_no_active_game():
    result = asyncio.run(game_correction_tool.execute_switch_to_vanilla_session({}))
    assert "no game" in result.lower()


def test_correct_game_title_with_active_variant_only_resets_trackers_once(isolated, monkeypatch):
    # Regression test (2026-07-13): correcting the title while a variant is already active used to
    # reset trackers via BOTH force_refresh_base and force_refresh_variant, racing two independent
    # bootstrap calls over the same per-process tracker list. Only the variant path should reset
    # them now - verified here by making reset_trackers itself raise if called more than once.
    game_state.start_tracking("javaw.exe")
    session_id = game_state.get_active_session_id("javaw.exe")
    game_state.set_session_variant("javaw.exe", session_id, "FTB StoneBlock 4")

    calls = []
    original_reset = game_state_trackers.reset_trackers

    def counting_reset(process, variant=None):
        calls.append((process, variant))
        return original_reset(process, variant=variant)

    monkeypatch.setattr(game_state_trackers, "reset_trackers", counting_reset)

    asyncio.run(game_correction_tool.execute_correct_game_title({"title": "Minecraft"}))

    assert calls == [("javaw.exe", "FTB StoneBlock 4")]  # reset exactly once, scoped to the variant
    assert ("base", "javaw.exe") in isolated
    assert ("variant", "javaw.exe", "Minecraft", "FTB StoneBlock 4") in isolated


def test_correct_game_title_repeat_correction_is_a_noop(isolated):
    game_state.start_tracking("javaw.exe")
    game_art.set_title_override("javaw.exe", "Minecraft")

    result = asyncio.run(game_correction_tool.execute_correct_game_title({"title": "Minecraft"}))

    assert "nothing to correct" in result.lower()
    assert isolated == []


def test_correct_game_modpack_repeat_correction_is_a_noop(isolated):
    game_state.start_tracking("javaw.exe")
    session_id = game_state.get_active_session_id("javaw.exe")
    game_state.set_session_variant("javaw.exe", session_id, "FTB StoneBlock 4")

    result = asyncio.run(game_correction_tool.execute_correct_game_modpack({"modpack": "FTB StoneBlock 4"}))

    assert "nothing to correct" in result.lower()
    assert isolated == []


def test_correct_game_title_no_active_game():
    result = asyncio.run(game_correction_tool.execute_correct_game_title({"title": "Minecraft"}))
    assert "no game" in result.lower()


def test_correct_game_modpack_no_active_game():
    result = asyncio.run(game_correction_tool.execute_correct_game_modpack({"modpack": "FTB StoneBlock 4"}))
    assert "no game" in result.lower()
