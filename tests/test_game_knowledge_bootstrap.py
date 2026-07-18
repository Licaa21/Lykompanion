"""Tests for app/services/llm/game_knowledge_bootstrap.py - focused on the variant bootstrap's
web-search query construction (regression for the 2026-07-13 fix: a verbose query mixing in the
base game's name was reliably drowned out by that game's own SEO weight - official Minecraft
pages dominate almost any query containing "Minecraft" - so the modpack bootstrap silently
searched the same generic material as the base bootstrap and produced a near-duplicate document
instead of anything modpack-specific)."""

import asyncio
import json

import pytest

from app.core import game_state_trackers
from app.core import game_state_training_data
from app.core.config import settings
from app.services.llm import game_knowledge_bootstrap as bootstrap


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(game_state_training_data, "TRAINING_DATA_PATH", tmp_path / "training.json")
    monkeypatch.setattr(game_state_training_data, "_OLD_GLOSSARY_PATH", tmp_path / "glossary.json")
    monkeypatch.setattr(game_state_trackers, "TRACKERS_PATH", tmp_path / "trackers.json")
    monkeypatch.setattr(settings, "game_state_training_enabled", True)
    bootstrap._attempted.clear()


def test_variant_bootstrap_searches_modpack_name_only(monkeypatch):
    # A base document already exists (the realistic case - base bootstrap always runs first),
    # so bootstrap_variant_knowledge skips re-gathering base knowledge and the only web search
    # fired is the pack-specific one under test.
    game_state_training_data.set_training_data("javaw.exe", "existing base Minecraft notes")

    seen_queries = []

    async def fake_web_search(arguments):
        seen_queries.append(arguments["query"])
        return "No web search results for that query."

    async def fake_chat_completion(*args, **kwargs):
        # Empty gathered knowledge no longer skips the LLM call (2026-07-13 - the model may still
        # recognize the pack/game from its own knowledge) - stub it so this test never makes a
        # real network call to whatever provider/key happens to be configured.
        return "{}"

    monkeypatch.setattr(bootstrap, "execute_web_search", fake_web_search)
    monkeypatch.setattr(bootstrap, "chat_completion", fake_chat_completion)

    asyncio.run(bootstrap.bootstrap_variant_knowledge("javaw.exe", "Minecraft", "FTB StoneBlock 4"))

    assert seen_queries == ["FTB StoneBlock 4 modpack"]


def test_base_bootstrap_still_asks_model_when_nothing_gathered(monkeypatch):
    # No IGDB, no Steam, no web search hits (e.g. a small/obscure indie title) - the bootstrap
    # used to give up right here and leave the process on generic RPG-flavored trackers forever.
    # It should now still call the model, which may recognize the game from its own knowledge.
    async def fake_igdb(_arguments):
        return "IGDB isn't configured."

    async def fake_steam_knowledge(_game_name):
        return None

    async def fake_web_search(_arguments):
        return "No web search results for that query."

    async def fake_chat_completion(*args, **kwargs):
        return json.dumps({
            "trackers": [{"label": "Spins Left", "description": "Rounds remaining before the deadline."}],
            "training_data": None,
        })

    monkeypatch.setattr(bootstrap, "execute_lookup_game_info", fake_igdb)
    monkeypatch.setattr(bootstrap, "_fetch_steam_knowledge", fake_steam_knowledge)
    monkeypatch.setattr(bootstrap, "execute_web_search", fake_web_search)
    monkeypatch.setattr(bootstrap, "chat_completion", fake_chat_completion)

    asyncio.run(bootstrap.bootstrap_game_knowledge("cloverpit.exe"))

    trackers = game_state_trackers.get_trackers("cloverpit.exe")
    assert [t["label"] for t in trackers if t["id"] != "activity"] == ["Spins Left"]


def test_variant_bootstrap_seeds_trackers_scoped_to_the_variant_only(monkeypatch):
    # Regression test (2026-07-14): trackers used to be keyed by process only, so a modpack's own
    # pack-specific trackers overwrote (and stayed active for) the base/vanilla process too - a
    # vanilla session of the same process kept showing modpack-specific fields. The base process
    # already has its own customized trackers here; seeding the variant must not touch them.
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Custom Base Tracker"}])

    async def fake_web_search(_arguments):
        return "No web search results for that query."

    async def fake_chat_completion(*args, **kwargs):
        return json.dumps({
            "trackers": [{"label": "Vaults Cleared", "description": "Vaults completed this run."}],
            "training_data": "## Lore\nSome pack lore.\n\n## UI/UX\nSome pack UI notes.",
        })

    monkeypatch.setattr(bootstrap, "execute_web_search", fake_web_search)
    monkeypatch.setattr(bootstrap, "chat_completion", fake_chat_completion)

    asyncio.run(bootstrap.bootstrap_variant_knowledge("javaw.exe", "Minecraft", "FTB StoneBlock 4"))

    base_trackers = game_state_trackers.get_trackers("javaw.exe")
    assert [t["label"] for t in base_trackers if t["id"] != "activity"] == ["Custom Base Tracker"]
    variant_trackers = game_state_trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4")
    assert [t["label"] for t in variant_trackers if t["id"] != "activity"] == ["Vaults Cleared"]


def test_variant_bootstrap_reseeds_trackers_even_when_training_data_already_exists(monkeypatch):
    # Regression test (2026-07-18): bootstrap_variant_knowledge used to early-exit entirely
    # whenever the variant already had its own training document - which is exactly the state
    # force_refresh_trackers creates on purpose (reset trackers to defaults, keep the good doc).
    # Net effect observed live: "regenerate trackers" was a pure reset-to-defaults, the reseeding
    # pass never ran. The existing doc must also survive untouched (a trackers-only refresh must
    # never replace a possibly extraction-pass-enriched document with a fresh generic one).
    game_state_training_data.set_training_data(
        "javaw.exe", "## Lore\nExisting enriched pack lore.", variant="FTB StoneBlock 4"
    )
    game_state_trackers.reset_trackers("javaw.exe", variant="FTB StoneBlock 4")

    async def fake_web_search(_arguments):
        return "No web search results for that query."

    async def fake_chat_completion(*args, **kwargs):
        return json.dumps({
            "trackers": [{"label": "Vaults Cleared", "description": "Vaults completed this run."}],
            "training_data": "## Lore\nFresh generic lore that must NOT overwrite the existing doc.",
        })

    monkeypatch.setattr(bootstrap, "execute_web_search", fake_web_search)
    monkeypatch.setattr(bootstrap, "chat_completion", fake_chat_completion)

    asyncio.run(bootstrap.bootstrap_variant_knowledge("javaw.exe", "Minecraft", "FTB StoneBlock 4"))

    variant_trackers = game_state_trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4")
    assert [t["label"] for t in variant_trackers if t["id"] != "activity"] == ["Vaults Cleared"]
    assert game_state_training_data.get_training_data("javaw.exe", "FTB StoneBlock 4") == "## Lore\nExisting enriched pack lore."


def test_variant_bootstrap_still_skips_when_nothing_is_wanted(monkeypatch):
    # Customized trackers + an existing doc = nothing to do; the gather/LLM pass must not fire.
    game_state_training_data.set_training_data("javaw.exe", "existing doc", variant="FTB StoneBlock 4")
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Custom"}], variant="FTB StoneBlock 4")

    async def exploding_web_search(_arguments):
        raise AssertionError("gather must not run when neither trackers nor training are wanted")

    monkeypatch.setattr(bootstrap, "execute_web_search", exploding_web_search)
    monkeypatch.setattr(bootstrap, "chat_completion", exploding_web_search)

    asyncio.run(bootstrap.bootstrap_variant_knowledge("javaw.exe", "Minecraft", "FTB StoneBlock 4"))


def test_forget_process_drops_base_and_variant_attempt_markers():
    # Regression test (2026-07-18): delete_game wiped every data file but not this in-memory
    # once-per-run cache, so a deleted-then-re-approved game's re-scheduled bootstraps silently
    # no-op'd - trackers stayed at freshly-seeded defaults and the variant doc never got its Lore.
    bootstrap._attempted.update({"javaw.exe", "javaw.exe::ftb stoneblock 4", "otherqgame.exe"})

    bootstrap.forget_process("javaw.exe")

    assert bootstrap._attempted == {"otherqgame.exe"}


def test_force_refresh_trackers_regenerates_only_the_trackers_for_a_variant(monkeypatch):
    # force_refresh_trackers is the "just regenerate my trackers" action (2026-07-14) - unlike
    # force_refresh_variant, it must NOT touch the training-data document, since the user's
    # complaint was specifically that trackers reverted to generic defaults, not that the notes
    # were wrong.
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Stale Tracker"}], variant="FTB StoneBlock 4")
    game_state_training_data.set_training_data("javaw.exe", "## Lore\nGood existing lore.", variant="FTB StoneBlock 4")
    bootstrap._attempted.add("javaw.exe::ftb stoneblock 4")

    scheduled = []
    monkeypatch.setattr(
        bootstrap, "schedule_variant_bootstrap",
        lambda process, base_title, modpack: scheduled.append((process, base_title, modpack)),
    )

    bootstrap.force_refresh_trackers("javaw.exe", "Minecraft", "FTB StoneBlock 4")

    variant_trackers = game_state_trackers.get_trackers("javaw.exe", variant="FTB StoneBlock 4")
    assert variant_trackers == game_state_trackers.DEFAULT_TRACKERS
    assert game_state_training_data.get_training_data("javaw.exe", "FTB StoneBlock 4") == "## Lore\nGood existing lore."
    assert "javaw.exe::ftb stoneblock 4" not in bootstrap._attempted
    assert scheduled == [("javaw.exe", "Minecraft", "FTB StoneBlock 4")]


def test_force_refresh_trackers_regenerates_the_base_scope_when_no_variant(monkeypatch):
    game_state_trackers.set_trackers("javaw.exe", [{"label": "Stale Tracker"}])
    game_state_training_data.set_training_data("javaw.exe", "existing base notes")
    bootstrap._attempted.add("javaw.exe")

    scheduled = []
    monkeypatch.setattr(bootstrap, "schedule_bootstrap", lambda process: scheduled.append(process))

    bootstrap.force_refresh_trackers("javaw.exe", "Minecraft")

    assert game_state_trackers.get_trackers("javaw.exe") == game_state_trackers.DEFAULT_TRACKERS
    assert game_state_training_data.get_training_data("javaw.exe") == "existing base notes"
    assert "javaw.exe" not in bootstrap._attempted
    assert scheduled == ["javaw.exe"]
