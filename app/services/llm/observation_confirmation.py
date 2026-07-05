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

from app.core import game_state, game_state_training_data, memory, observations
from app.core.config import settings
from app.core.prompts import current_datetime_context, load_prompt
from app.services.llm.client import chat_completion
from app.services.llm.web_search_tool import execute_web_search

logger = logging.getLogger(__name__)

# Run the pass once this many observations are pending for the active process+session. High
# enough that single-frame misreads usually get contradicted/superseded before review, low
# enough that entries are reviewed well before the journal cap (50) starts dropping them.
CONFIRMATION_THRESHOLD = 12

# One pass at a time per process+session - the poller can hit the threshold again while a
# previous pass is still running.
_in_flight: set[tuple[str, str | None]] = set()

# This pass runs rarely (once per 12+ pending observations) compared to the extraction pass
# (every poll window), so it can afford to actually dig - allow several search rounds in the same
# review instead of just one, so it can e.g. look up an unfamiliar quest name, then follow up on
# what that search turns up, before finalizing its save/remove/clear decisions.
MAX_SEARCH_ROUNDS = 4


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
        training_data = game_state_training_data.format_training_data_for_prompt(process)
        user_content = (
            f"{current_datetime_context()}\n\nTracked game process: {process}\n\n{known_facts}\n\n"
            + (f"{game_state_text}\n\n" if game_state_text else "")
            + (f"{training_data}\n\n" if training_data else "")
            + _format_observations_full(entries)
        )
        messages = [
            {"role": "system", "content": load_prompt("observation_confirmation")},
            {"role": "user", "content": user_content},
        ]
        model = settings.memory_extraction_model or None
        provider = settings.memory_extraction_provider or settings.llm_provider

        try:
            raw = await chat_completion(
                messages,
                model=model,
                response_format={"type": "json_object"},
                source="observation_confirmation",
                provider=provider,
            )
            data = json.loads(raw)
        except Exception:
            logger.exception("Observation confirmation pass failed for process=%r", process)
            return

        # Research loop: this pass runs rarely, so unlike the extraction pass it can afford to dig
        # across several rounds - each one may flag something new to check (a search result that
        # raises a follow-up question, a second unfamiliar name) before finalizing its decisions.
        for round_num in range(1, MAX_SEARCH_ROUNDS + 1):
            search_query = data.get("web_search_query")
            if not (isinstance(search_query, str) and search_query.strip()):
                break
            search_query = search_query.strip()
            logger.info(
                "Observation confirmation: requested web search %r for process=%r (round %d/%d)",
                search_query, process, round_num, MAX_SEARCH_ROUNDS,
            )
            try:
                results = await execute_web_search({"query": search_query})
            except Exception:
                # Keep the current output - a failed search must not cost us the whole review.
                logger.exception("Observation confirmation web-search round failed for process=%r", process)
                break
            last_round = round_num == MAX_SEARCH_ROUNDS
            followup = (
                f"Web search results for your query \"{search_query}\":\n{results}\n\n"
                + (
                    "Use them, together with anything you've already learned this review, to finalize "
                    "your save/remove/clear decisions - you've used your last search round, so do not "
                    "request another."
                    if last_round else
                    "Use them to refine your understanding. If you now need to check something else "
                    "that would change a decision, set \"web_search_query\" again; otherwise set it to "
                    "null and finalize your save/remove/clear decisions."
                )
            )
            messages = messages + [{"role": "assistant", "content": raw}, {"role": "user", "content": followup}]
            try:
                raw = await chat_completion(
                    messages,
                    model=model,
                    response_format={"type": "json_object"},
                    source="observation_confirmation",
                    provider=provider,
                )
                data = json.loads(raw)
            except Exception:
                logger.exception("Observation confirmation pass failed for process=%r on search round %d", process, round_num)
                break

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
