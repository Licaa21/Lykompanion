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
