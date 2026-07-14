"""Tests for app/services/llm/game_state_extraction.py's _push_overlay_game_state - the overlay
panel's title used to be a naive cleanup of the raw process name ("javaw.exe" -> "Javaw")
regardless of what the Gaming Journal actually resolved the game's title to, and there was no
indication of the active modpack/session in the overlay at all."""

import asyncio

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


class TestNormalizeTrainingDataUpdate:
    """Regression coverage for the 2026-07-13 bug: training_data_update came back in inconsistent
    shapes (all observed live in data/debug_log.json for the same FTB StoneBlock 4 session), but
    the old code only ever accepted a plain string - every dict-shaped revision was silently
    dropped and logged as "no update this pass" instead of being applied."""

    def test_plain_string_passes_through(self):
        assert game_state_extraction._normalize_training_data_update("## UI/UX\nsome notes") == "## UI/UX\nsome notes"

    def test_blank_string_is_none(self):
        assert game_state_extraction._normalize_training_data_update("   ") is None

    def test_dict_with_list_bodies(self):
        update = {"## UI/UX": ["First bullet.", "Second bullet."]}
        assert game_state_extraction._normalize_training_data_update(update) == (
            "## UI/UX\n- First bullet.\n- Second bullet."
        )

    def test_dict_with_string_bodies_and_multiple_sections(self):
        update = {"## Lore": "Some lore text.", "## UI/UX": "Some UI text."}
        assert game_state_extraction._normalize_training_data_update(update) == (
            "## Lore\nSome lore text.\n\n## UI/UX\nSome UI text."
        )

    def test_dict_header_without_hash_prefix_gets_normalized(self):
        # Observed live: the model sometimes omits the leading "##" entirely.
        update = {" Lore": "Some lore text.", "UI/UX": "Some UI text."}
        assert game_state_extraction._normalize_training_data_update(update) == (
            "## Lore\nSome lore text.\n\n## UI/UX\nSome UI text."
        )

    def test_dict_with_empty_list_section_is_skipped(self):
        # Observed live: {"## UI/UX": [...], "## Lore": []} - an empty section contributes nothing.
        update = {"## UI/UX": ["A real bullet."], "## Lore": []}
        assert game_state_extraction._normalize_training_data_update(update) == "## UI/UX\n- A real bullet."

    def test_none_and_other_types_are_none(self):
        assert game_state_extraction._normalize_training_data_update(None) is None
        assert game_state_extraction._normalize_training_data_update(123) is None
        assert game_state_extraction._normalize_training_data_update([]) is None


class TestTrainingUpdateWouldDropLore:
    def test_true_when_current_has_lore_and_update_does_not(self):
        current = "## Lore\nRich backstory.\n\n## UI/UX\nOld notes."
        update = "## UI/UX\nA villager's profession is indicated by its clothing."
        assert game_state_extraction._training_update_would_drop_lore(current, update) is True

    def test_false_when_update_preserves_lore(self):
        current = "## Lore\nRich backstory.\n\n## UI/UX\nOld notes."
        update = "## Lore\nRich backstory.\n\n## UI/UX\nOld notes plus something new."
        assert game_state_extraction._training_update_would_drop_lore(current, update) is False

    def test_false_when_current_never_had_lore(self):
        current = "## UI/UX\nOld notes."
        update = "## UI/UX\nOld notes plus something new."
        assert game_state_extraction._training_update_would_drop_lore(current, update) is False


class TestModpackCorroborationLine:
    """Regression coverage for the 2026-07-14 bug: the on-screen modpack field (see
    _MODPACK_PROMPT_ADDON) had nothing to cross-check an on-screen guess against, and set a live
    vanilla session's modpack to "FTB Unearthed" - a single mod bundled inside the actual pack,
    "FTB StoneBlock 4" - almost certainly read off an in-world item/block name. The window title
    routinely already names the real pack (a modded launcher sets it), so it's now surfaced to the
    model as a corroborating signal."""

    def test_includes_window_title_for_the_matching_process(self, monkeypatch):
        monkeypatch.setattr(
            game_state_extraction, "get_foreground_process_details",
            lambda: {"process": "javaw.exe", "window_title": "Minecraft* 1.20.1 - FTB StoneBlock 4"},
        )
        result = asyncio.run(game_state_extraction._modpack_corroboration_line("javaw.exe"))
        assert result == "\nCurrent window title: Minecraft* 1.20.1 - FTB StoneBlock 4"

    def test_case_insensitive_process_match(self, monkeypatch):
        monkeypatch.setattr(
            game_state_extraction, "get_foreground_process_details",
            lambda: {"process": "JavaW.exe", "window_title": "Minecraft"},
        )
        result = asyncio.run(game_state_extraction._modpack_corroboration_line("javaw.exe"))
        assert result == "\nCurrent window title: Minecraft"

    def test_empty_when_focus_moved_to_a_different_process(self, monkeypatch):
        monkeypatch.setattr(
            game_state_extraction, "get_foreground_process_details",
            lambda: {"process": "discord.exe", "window_title": "Discord"},
        )
        result = asyncio.run(game_state_extraction._modpack_corroboration_line("javaw.exe"))
        assert result == ""

    def test_empty_when_no_window_title(self, monkeypatch):
        monkeypatch.setattr(
            game_state_extraction, "get_foreground_process_details",
            lambda: {"process": "javaw.exe", "window_title": None},
        )
        result = asyncio.run(game_state_extraction._modpack_corroboration_line("javaw.exe"))
        assert result == ""

    def test_empty_when_details_unavailable(self, monkeypatch):
        monkeypatch.setattr(game_state_extraction, "get_foreground_process_details", lambda: None)
        result = asyncio.run(game_state_extraction._modpack_corroboration_line("javaw.exe"))
        assert result == ""
