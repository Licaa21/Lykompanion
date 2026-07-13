"""One-time game knowledge bootstrap - runs in the background the first time a game is
tracked. Fetches IGDB info + a web search for the game, then one LLM pass turns that into
(a) game-appropriate trackers (replacing the generic RPG-flavored defaults, but only if the
user hasn't customized them) and (b) starting training data notes, so the extraction pass
doesn't face a blank document it never bothers to fill in."""

import asyncio
import json
import logging

from app.core import game_state_trackers
from app.core import game_state_training_data
from app.core.config import settings
from app.core.prompts import load_prompt
from app.services.llm.client import chat_completion
from app.services.llm.igdb_tool import execute_lookup_game_info
from app.services.llm.web_search_tool import execute_web_search

logger = logging.getLogger(__name__)

# Processes a bootstrap has already been attempted for this app run - avoids re-running the
# whole gather+LLM pass every time the same game regains focus after a failed/empty attempt.
# Deliberately in-memory only: a restart gets one fresh retry, which is what you want when the
# first attempt failed because the network/search was down.
_attempted: set[str] = set()


def _guess_game_name(process: str) -> str:
    """Best-effort human name for a process: the Gaming Journal's resolved/overridden title
    when one exists (it may name the real game behind a generic host exe like javaw.exe),
    otherwise a cleaned-up exe name — IGDB's fuzzy search and the web search engine both cope
    well with that form. Late import: game_art pulls in the LLM client stack."""
    from app.core.game_art import get_display_title

    return get_display_title(process)


def _trackers_are_default(trackers: list[dict]) -> bool:
    """True when the process still has the untouched seeded defaults - the only case where the
    bootstrap is allowed to replace them. A user-customized list is never overwritten."""
    default_ids = [t["id"] for t in game_state_trackers.DEFAULT_TRACKERS]
    return [t["id"] for t in trackers] == default_ids


async def _gather_game_knowledge(game_name: str) -> str:
    parts = []

    igdb_info = await execute_lookup_game_info({"game_name": game_name})
    if igdb_info and not igdb_info.startswith(("No IGDB results", "IGDB isn't configured", "IGDB authentication failed", "IGDB lookup failed")):
        parts.append(f"## IGDB database results\n{igdb_info}")

    web_results = await execute_web_search({"query": f"{game_name} game HUD UI elements explained stats screen"})
    if web_results and not web_results.startswith(("No web search results", "Web search failed")):
        parts.append(f"## Web search results\n{web_results}")

    return "\n\n".join(parts)


async def bootstrap_game_knowledge(process: str) -> None:
    """Fire-and-forget (same contract as the extraction pass): must never raise into the
    poller loop. Skips silently when there's nothing to do - trackers customized AND training
    data already present."""
    key = process.lower()
    if key in _attempted:
        return
    _attempted.add(key)

    trackers = game_state_trackers.get_trackers(process)
    want_trackers = _trackers_are_default(trackers)
    want_training = settings.game_state_training_enabled and not game_state_training_data.get_training_data(process).strip()
    if not want_trackers and not want_training:
        return

    game_name = _guess_game_name(process)
    logger.info("Game bootstrap: gathering knowledge for process=%r (guessed game name %r)", process, game_name)

    try:
        knowledge = await _gather_game_knowledge(game_name)
    except Exception:
        logger.exception("Game bootstrap: knowledge gathering failed for process=%r", process)
        return

    if not knowledge:
        logger.info("Game bootstrap: no IGDB/web results for %r, keeping defaults", game_name)
        return

    user_content = (
        f"Foreground process: {process}\n"
        f"Guessed game name: {game_name}\n\n"
        f"Gathered information:\n\n{knowledge}"
    )

    try:
        raw = await chat_completion(
            [
                {"role": "system", "content": load_prompt("game_knowledge_bootstrap")},
                {"role": "user", "content": user_content},
            ],
            model=settings.game_bootstrap_model or settings.game_state_model or None,
            response_format={"type": "json_object"},
            source="game_bootstrap",
            provider=settings.game_bootstrap_provider or settings.game_state_provider or settings.llm_provider,
        )
        data = json.loads(raw)
    except Exception:
        logger.exception("Game bootstrap: LLM pass failed for process=%r", process)
        return

    if want_trackers:
        new_trackers = [
            {"label": t.get("label", ""), "description": t.get("description", "")}
            for t in data.get("trackers") or []
            if isinstance(t, dict) and (t.get("label") or "").strip()
        ]
        if new_trackers:
            game_state_trackers.set_trackers(process, new_trackers)
            logger.info("Game bootstrap: seeded %d game-specific tracker(s) for process=%r", len(new_trackers), process)

    if want_training:
        training = data.get("training_data")
        if isinstance(training, str) and training.strip():
            game_state_training_data.set_training_data(process, training.strip())
            logger.info("Game bootstrap: seeded starting training data for process=%r", process)


def schedule_bootstrap(process: str) -> None:
    """Kicks off the bootstrap as a background task from the (async) poller without awaiting it -
    tracking and the first extraction pass proceed immediately regardless."""
    if process.lower() in _attempted:
        return
    asyncio.get_running_loop().create_task(bootstrap_game_knowledge(process))


async def bootstrap_variant_knowledge(process: str, base_title: str, modpack: str) -> None:
    """Pack-specific counterpart to the base bootstrap, run when variant detection identifies
    a modpack: web-searches the pack itself (its mechanics, progression, added content) and
    seeds the *variant's own* training-data document — the extraction pass reads and revises
    that document while a session of this variant is active, so vanilla notes and pack notes
    never overwrite each other. Also replaces the trackers when they're still the untouched
    defaults (a skyblock pack tracks very different things than generic RPG fields)."""
    key = f"{process.lower()}::{modpack.lower()}"
    if key in _attempted:
        return
    _attempted.add(key)

    if not settings.game_state_training_enabled or game_state_training_data.has_own_training_data(process, modpack):
        return

    logger.info("Variant bootstrap: gathering knowledge for %r (%s)", modpack, base_title)
    try:
        base_knowledge = "" if game_state_training_data.has_own_training_data(process) else await _gather_game_knowledge(base_title)
        # Short and modpack-name-only, not "{modpack} {base_title} modpack overview features
        # progression guide" - that longer form (observed via data/debug_log.json) reliably lost
        # to the base game's own SEO weight (official minecraft.net/wiki pages dominate almost
        # any query containing "Minecraft"), so the pack bootstrap was silently searching the same
        # generic base-game material as the base bootstrap and produced a near-duplicate document.
        # Same "plain keyword queries" discipline as system_companion.md's Web Search guidance.
        pack_results = await execute_web_search({"query": f"{modpack} modpack"})
    except Exception:
        logger.exception("Variant bootstrap: knowledge gathering failed for %r", modpack)
        return
    pack_knowledge = ""
    if pack_results and not pack_results.startswith(("No web search results", "Web search failed")):
        pack_knowledge = f"## Web search results about the modpack \"{modpack}\"\n{pack_results}"
    if not pack_knowledge and not base_knowledge:
        logger.info("Variant bootstrap: nothing found for %r, skipping", modpack)
        return

    user_content = (
        f"Foreground process: {process}\n"
        f"Base game: {base_title}\n"
        f"The player is running the \"{modpack}\" modpack/overhaul of it — the notes you produce are for "
        f"THAT modded experience. This is a modpack/variant request: always include the \"## Lore\" section "
        f"(scoped strictly to what \"{modpack}\" itself adds, never general facts about {base_title}) and scope "
        f"\"## UI/UX\" to what the pack adds or changes only, even if you already know {base_title} well.\n\n"
        f"Gathered information:\n\n"
        + "\n\n".join(part for part in (base_knowledge, pack_knowledge) if part)
    )

    try:
        raw = await chat_completion(
            [
                {"role": "system", "content": load_prompt("game_knowledge_bootstrap")},
                {"role": "user", "content": user_content},
            ],
            model=settings.game_bootstrap_model or settings.game_state_model or None,
            response_format={"type": "json_object"},
            source="game_bootstrap",
            provider=settings.game_bootstrap_provider or settings.game_state_provider or settings.llm_provider,
        )
        data = json.loads(raw)
    except Exception:
        logger.exception("Variant bootstrap: LLM pass failed for %r", modpack)
        return

    if _trackers_are_default(game_state_trackers.get_trackers(process)):
        new_trackers = [
            {"label": t.get("label", ""), "description": t.get("description", "")}
            for t in data.get("trackers") or []
            if isinstance(t, dict) and (t.get("label") or "").strip()
        ]
        if new_trackers:
            game_state_trackers.set_trackers(process, new_trackers)
            logger.info("Variant bootstrap: seeded %d tracker(s) for process=%r (%s)", len(new_trackers), process, modpack)

    training = data.get("training_data")
    if isinstance(training, str) and training.strip():
        game_state_training_data.set_training_data(process, training.strip(), variant=modpack)
        logger.info("Variant bootstrap: seeded training data for process=%r variant=%r", process, modpack)


def schedule_variant_bootstrap(process: str, base_title: str, modpack: str) -> None:
    if f"{process.lower()}::{modpack.lower()}" in _attempted:
        return
    asyncio.get_running_loop().create_task(bootstrap_variant_knowledge(process, base_title, modpack))
