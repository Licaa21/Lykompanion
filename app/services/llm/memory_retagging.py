"""Memory retagging pass - runs once, the first time a game starts being actively tracked.
remember()'s degradation rule has no choice but "user" scope for a fact stated while no game
is tracked (e.g. "I've got 41 hours in Witcher 3" said before ever launching it this session) -
there's nowhere else for it to go at save time. Once the game actually starts being tracked,
this reviews standing "user" facts and moves any that turn out to be specifically about it into
game (or session) scope, so they surface only while it's relevant instead of forever."""

import asyncio
import json
import logging

from app.core import memory
from app.core.config import settings
from app.core.prompts import load_prompt
from app.services.llm.client import chat_completion

logger = logging.getLogger(__name__)

# Processes already reviewed this app run - avoids re-running the pass every time the same game
# regains focus. In-memory only, same contract as game_knowledge_bootstrap's _attempted: a
# restart gets one fresh pass.
_attempted: set[str] = set()


def _guess_game_name(process: str) -> str:
    """Best-effort human name from an exe name, same heuristic as the knowledge bootstrap."""
    name = process.rsplit(".", 1)[0]
    return name.replace("_", " ").replace("-", " ").strip() or process


async def retag_memories_for_process(process: str, session_id: str | None) -> None:
    """Fire-and-forget (same contract as the extraction pass): must never raise into the
    poller. Skips silently when there are no general facts to review."""
    key = process.lower()
    if key in _attempted:
        return
    _attempted.add(key)

    user_memories = [m for m in memory.load_memories() if m["scope"] == "user"]
    if not user_memories:
        return

    game_name = _guess_game_name(process)
    facts_text = "\n".join(f"- [{m['id']}] {m['content']}" for m in user_memories)
    messages = [
        {"role": "system", "content": load_prompt("memory_retagging")},
        {
            "role": "user",
            "content": (
                f"Game that just started being tracked (actively being played right now): "
                f"{game_name} (process: {process})\n\n"
                f"Current general facts (scope: user, shown regardless of what's being played):\n{facts_text}"
            ),
        },
    ]

    try:
        raw = await chat_completion(
            messages,
            model=settings.memory_extraction_model or None,
            response_format={"type": "json_object"},
            source="memory_retagging",
            provider=settings.memory_extraction_provider or settings.llm_provider,
        )
        data = json.loads(raw)
    except Exception:
        logger.exception("Memory retagging pass failed for process=%r", process)
        return

    by_id = {m["id"]: m for m in user_memories}
    moved = 0
    for item in data.get("retag") or []:
        if not isinstance(item, dict):
            continue
        memory_id = item.get("id")
        scope = (item.get("scope") or "").lower()
        target = by_id.get(memory_id)
        if not target or scope not in ("game", "session"):
            continue
        # A "session" retag with no active session has nowhere to land more specifically than
        # "game" - never invent a session_id, just place it at the coarser scope.
        target_session = session_id if scope == "session" and session_id else None
        if memory.update_memory(memory_id, target["content"], process=process, session_id=target_session):
            moved += 1

    if moved:
        logger.info(
            "Memory retagging: moved %d general fact(s) to game/session scope for process=%r",
            moved, process,
        )


def schedule_retagging(process: str, session_id: str | None) -> None:
    """Kicks off the retagging pass as a background task from the (async) poller without
    awaiting it - mirrors game_knowledge_bootstrap.schedule_bootstrap's contract."""
    if process.lower() in _attempted:
        return
    asyncio.get_running_loop().create_task(retag_memories_for_process(process, session_id))
