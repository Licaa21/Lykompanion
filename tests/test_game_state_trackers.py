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


def test_variant_trackers_are_independent_of_the_base_process(isolated_store):
    # Regression test (2026-07-14): trackers used to be keyed by process only, so a modpack's
    # own pack-specific trackers (e.g. FTB StoneBlock 4's Vaults/Echoes) stayed active even while
    # a *vanilla* session of the same process was the one being tracked. Now keyed like training
    # data: base and each variant get their own independent list.
    trackers.set_trackers("javaw.exe", [{"label": "Vaults Cleared"}], variant="FTB StoneBlock 4")

    assert trackers.get_trackers("javaw.exe") == trackers.DEFAULT_TRACKERS  # base untouched
    variant_result = trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4")
    assert [t["label"] for t in variant_result[1:]] == ["Vaults Cleared"]


def test_variant_trackers_seed_fresh_defaults_not_the_bases_own_customization(isolated_store):
    # A new variant should start clean, not inherit whatever the base process was already
    # customized/bootstrapped to - it gets its own bootstrap pass to replace them.
    trackers.set_trackers("javaw.exe", [{"label": "Custom Base Tracker"}])

    variant_result = trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4")

    assert variant_result == trackers.DEFAULT_TRACKERS


def test_reset_trackers_variant_only_resets_that_variant(isolated_store):
    trackers.set_trackers("javaw.exe", [{"label": "Custom Base"}])
    trackers.set_trackers("javaw.exe", [{"label": "Custom Variant"}], variant="FTB StoneBlock 4")

    trackers.reset_trackers("javaw.exe", variant="FTB StoneBlock 4")

    assert [t["label"] for t in trackers.get_trackers("javaw.exe")[1:]] == ["Custom Base"]
    assert trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4") == trackers.DEFAULT_TRACKERS


def test_delete_process_removes_base_and_every_variant(isolated_store):
    trackers.set_trackers("javaw.exe", [{"label": "Custom Base"}])
    trackers.set_trackers("javaw.exe", [{"label": "Custom Variant"}], variant="FTB StoneBlock 4")

    trackers.delete_process("javaw.exe")

    assert trackers.get_trackers("javaw.exe") == trackers.DEFAULT_TRACKERS
    assert trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4") == trackers.DEFAULT_TRACKERS
