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
    """Best-effort human name from an exe name: 'BaldursGate3.exe' -> 'BaldursGate3'. IGDB's
    fuzzy search and the web search engine both cope well with this form."""
    name = process.rsplit(".", 1)[0]
    return name.replace("_", " ").replace("-", " ").strip() or process


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
            model=settings.game_state_model or None,
            response_format={"type": "json_object"},
            source="game_bootstrap",
            provider=settings.game_state_provider or settings.llm_provider,
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
