"""Tests for app/core/game_art.py's disk-backed store and search-term cleanup - the
network-dependent Steam/IGDB/LLM fetch paths aren't covered here (no live calls in tests)."""

import asyncio

import httpx
import pytest

from app.core import game_art
from app.core.config import settings


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(game_art, "DATA_DIR", tmp_path)
    monkeypatch.setattr(game_art, "GAME_ART_PATH", tmp_path / "game_art.json")


def test_get_art_returns_none_when_never_fetched():
    assert game_art.get_art("unknown.exe") is None


def test_set_title_override_persists_and_is_flagged():
    record = game_art.set_title_override("EldenRing.exe", "Elden Ring")
    assert record["title"] == "Elden Ring"
    assert record["title_overridden"] is True
    assert game_art.get_art("eldenring.exe")["title"] == "Elden Ring"


def test_set_title_override_is_case_insensitive_key():
    game_art.set_title_override("Game.exe", "My Game")
    assert game_art.get_art("game.exe")["title"] == "My Game"
    assert game_art.get_art("GAME.EXE")["title"] == "My Game"


def test_delete_process_removes_entry():
    game_art.set_title_override("game.exe", "My Game")
    game_art.delete_process("game.exe")
    assert game_art.get_art("game.exe") is None


def test_delete_process_is_a_no_op_when_absent():
    game_art.delete_process("never-fetched.exe")  # should not raise


@pytest.mark.parametrize("process,expected", [
    ("EldenRing.exe", "Elden Ring"),
    ("baldurs_gate_3.exe", "baldurs gate 3"),
    ("Rocket-League.exe", "Rocket League"),
    ("witcher3.exe", "witcher3"),
])
def test_clean_search_term(process, expected):
    assert game_art._clean_search_term(process) == expected


def test_fetch_steamgriddb_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "steamgriddb_api_key", "")

    async def run():
        async with httpx.AsyncClient() as client:
            return await game_art._fetch_steamgriddb(client, "some game")

    assert asyncio.run(run()) is None


def test_augment_with_cover_is_a_noop_when_already_covered():
    existing = {"title": "Some Game", "cover_url": "https://example.com/cover.jpg", "source": "steam"}

    async def run():
        async with httpx.AsyncClient() as client:
            return await game_art._augment_with_cover(client, existing, "some game")

    assert asyncio.run(run()) is existing


def test_fetch_art_rejects_fuzzy_title_mismatch_on_official_title_retry(monkeypatch):
    """Regression for the 2026-07-13 bug: a process name that doesn't match anything on its own
    (e.g. javaw.exe) resolves to an official title via LLM ("Minecraft"), but a Steam search for
    that resolved title can still fuzzy-match a different real game ("Minecraft Dungeons" - a
    real Steam listing, since Minecraft Java itself isn't sold on Steam). That mismatch must be
    rejected, not silently accepted as this process's art - it used to be, since this retry path
    had no title-match validation of its own (unlike the sibling `search_term`-trusted path)."""
    async def fake_fetch_steam(http_client, term):
        if term == "Minecraft":
            return {"title": "Minecraft Dungeons", "cover_url": "https://example.com/dungeons.jpg",
                     "description": "wrong game", "source": "steam"}
        return None

    async def fake_augment(http_client, result, term):
        return result  # no-op - isolates the test to the Steam-search mismatch path

    async def fake_resolve_official_title(term):
        return "Minecraft"

    monkeypatch.setattr(game_art, "_fetch_steam", fake_fetch_steam)
    monkeypatch.setattr(game_art, "_augment_with_cover", fake_augment)
    monkeypatch.setattr(game_art, "_resolve_official_title", fake_resolve_official_title)

    result = asyncio.run(game_art.fetch_art("javaw.exe"))

    assert result["title"] == "Minecraft"
    assert result["cover_url"] is None
