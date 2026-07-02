"""Observation confirmation pass - promotes accumulated screen observations into scoped
memories without waiting for the user to say something. The conversation-side memory
extraction pass still corroborates observations against the user's own words when a chat
happens, but during long silent play sessions observations used to just pile up and fall off
the journal's cap without ever becoming memories. This pass runs whenever a session has
accumulated enough pending observations, reviews the whole journal for that session against
the known facts, and sorts the keepers into the right scope (user / game / session)."""

import asyncio
import json
import logging

from app.core import game_state, memory, observations
from app.core.config import settings
from app.core.prompts import current_datetime_context, load_prompt
from app.services.llm.client import chat_completion

logger = logging.getLogger(__name__)

# Run the pass once this many observations are pending for the active process+session. High
# enough that single-frame misreads usually get contradicted/superseded before review, low
# enough that entries are reviewed well before the journal cap (50) starts dropping them.
CONFIRMATION_THRESHOLD = 12

# One pass at a time per process+session - the poller can hit the threshold again while a
# previous pass is still running.
_in_flight: set[tuple[str, str | None]] = set()


def _format_observations_full(entries: list[dict]) -> str:
    lines = []
    for o in entries:
        conf = o.get("confidence")
        conf_text = f", confidence {conf:.2f}" if isinstance(conf, (int, float)) else ""
        lines.append(f"- [{o['id']}] ({o['observed_at']}{conf_text}) {o['content']}")
    return "Pending screen observations for this session (oldest first):\n" + "\n".join(lines)


async def confirm_observations(process: str, session_id: str | None) -> None:
    """Fire-and-forget: must never raise into the poller. Reviews every pending observation for
    one process+session and either promotes it to a scoped memory, discards it, or leaves it
    pending for more evidence."""
    key = (process.lower(), session_id)
    if key in _in_flight:
        return
    _in_flight.add(key)
    try:
        entries = observations.get_observations(process, session_id)
        if len(entries) < CONFIRMATION_THRESHOLD:
            return

        known_facts = memory.format_memories_for_prompt(process, session_id) or "Known facts about the user: none yet."
        game_state_text = game_state.format_game_state_for_prompt()
        messages = [
            {"role": "system", "content": load_prompt("observation_confirmation")},
            {
                "role": "user",
                "content": (
                    f"{current_datetime_context()}\n\nTracked game process: {process}\n\n{known_facts}\n\n"
                    + (f"{game_state_text}\n\n" if game_state_text else "")
                    + _format_observations_full(entries)
                ),
            },
        ]

        try:
            raw = await chat_completion(
                messages,
                model=settings.memory_extraction_model or None,
                response_format={"type": "json_object"},
                source="observation_confirmation",
                provider=settings.memory_extraction_provider or settings.llm_provider,
            )
            data = json.loads(raw)
        except Exception:
            logger.exception("Observation confirmation pass failed for process=%r", process)
            return

        saved = 0
        for fact in data.get("save") or []:
            if not isinstance(fact, dict):
                continue
            if memory.remember(
                (fact.get("content") or ""),
                (fact.get("scope") or "game").lower(),
                process=process,
                session_id=session_id,
            ):
                saved += 1

        for memory_id in data.get("remove") or []:
            if isinstance(memory_id, str) and memory_id:
                memory.remove_memory(memory_id)

        cleared = [i for i in data.get("clear_observations") or [] if isinstance(i, str) and i]
        if cleared:
            observations.remove_observations(cleared)
        logger.info(
            "Observation confirmation: process=%r session=%r - %d promoted, %d observation(s) cleared, %d still pending",
            process, session_id, saved, len(cleared), len(entries) - len(cleared),
        )
    finally:
        _in_flight.discard(key)


def maybe_schedule_confirmation(process: str, session_id: str | None) -> None:
    """Called from the game-state poller after new observations land - kicks off a confirmation
    pass in the background once enough are pending. Cheap when below threshold (one file read)."""
    if (process.lower(), session_id) in _in_flight:
        return
    if len(observations.get_observations(process, session_id)) < CONFIRMATION_THRESHOLD:
        return
    asyncio.get_running_loop().create_task(confirm_observations(process, session_id))
