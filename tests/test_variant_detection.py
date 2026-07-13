"""Tests for variant_detection.py's guard against a self-referential modpack_name (the model
naming the base game itself as "the pack", e.g. modpack_name: "Minecraft" for plain Minecraft -
observed live 2026-07-13: the session got tagged with a "Minecraft" variant and the process's
title never got fixed from the raw exe name)."""

import asyncio
import json

import pytest

from app.core import game_art
from app.services.llm import game_knowledge_bootstrap, variant_detection


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    variant_detection._cached_results.clear()
    # The real bootstrap pass does network/LLM work - never let it actually fire from these tests.
    monkeypatch.setattr(game_knowledge_bootstrap, "schedule_bootstrap", lambda process: None)


def _run_detection(monkeypatch, *, base_game_title, modpack_name, known_title="javaw"):
    monkeypatch.setattr(
        variant_detection,
        "get_foreground_process_details",
        lambda: {"process": "javaw.exe", "window_title": "Minecraft* 1.20.1"},
    )
    monkeypatch.setattr(game_art, "get_display_title", lambda process: known_title)

    async def fake_chat_completion(*args, **kwargs):
        return json.dumps({
            "base_game_title": base_game_title,
            "modded": True,
            "modpack_name": modpack_name,
            "confidence": 0.8,
        })

    monkeypatch.setattr(variant_detection, "chat_completion", fake_chat_completion)

    applied = []
    fixed_titles = []
    monkeypatch.setattr(
        variant_detection, "apply_detected_variant",
        lambda process, modpack, source: applied.append((process, modpack, source)),
    )

    async def fake_maybe_fix_title(process, base_title):
        fixed_titles.append((process, base_title))

    monkeypatch.setattr(variant_detection, "_maybe_fix_title", fake_maybe_fix_title)

    asyncio.run(variant_detection._detect_and_apply("javaw.exe"))
    return applied, fixed_titles


def test_modpack_name_identical_to_base_title_is_treated_as_vanilla(monkeypatch):
    applied, fixed_titles = _run_detection(monkeypatch, base_game_title="Minecraft", modpack_name="Minecraft")

    assert applied == []
    assert fixed_titles == [("javaw.exe", "Minecraft")]


def test_modpack_name_with_no_base_title_is_treated_as_vanilla(monkeypatch):
    # The observed real case: the model recognized "Minecraft" but only wrote it into
    # modpack_name, leaving base_game_title null - just as incoherent, and salvaged the same way.
    applied, fixed_titles = _run_detection(monkeypatch, base_game_title=None, modpack_name="Minecraft")

    assert applied == []
    assert fixed_titles == [("javaw.exe", "Minecraft")]


def test_real_modpack_with_base_title_still_applies(monkeypatch):
    applied, fixed_titles = _run_detection(monkeypatch, base_game_title="Minecraft", modpack_name="FTB StoneBlock 4")

    assert applied == [("javaw.exe", "FTB StoneBlock 4", "launch signals")]
    assert fixed_titles == [("javaw.exe", "Minecraft")]
