"""Tests for app/services/llm/game_knowledge_bootstrap.py - focused on the variant bootstrap's
web-search query construction (regression for the 2026-07-13 fix: a verbose query mixing in the
base game's name was reliably drowned out by that game's own SEO weight - official Minecraft
pages dominate almost any query containing "Minecraft" - so the modpack bootstrap silently
searched the same generic material as the base bootstrap and produced a near-duplicate document
instead of anything modpack-specific)."""

import asyncio

import pytest

from app.core import game_state_training_data
from app.core.config import settings
from app.services.llm import game_knowledge_bootstrap as bootstrap


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(game_state_training_data, "TRAINING_DATA_PATH", tmp_path / "training.json")
    monkeypatch.setattr(game_state_training_data, "_OLD_GLOSSARY_PATH", tmp_path / "glossary.json")
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

    monkeypatch.setattr(bootstrap, "execute_web_search", fake_web_search)

    asyncio.run(bootstrap.bootstrap_variant_knowledge("javaw.exe", "Minecraft", "FTB StoneBlock 4"))

    assert seen_queries == ["FTB StoneBlock 4 modpack"]
