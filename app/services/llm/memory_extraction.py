import asyncio
import json
import logging

from app.core import game_state, memory, observations
from app.core.config import settings
from app.core.prompts import current_datetime_context, load_prompt
from app.services.llm.client import chat_completion

logger = logging.getLogger(__name__)

# Chat turns fire this as a background task per turn without awaiting the previous one - two
# quick consecutive turns could otherwise both read known_facts before either had written its
# save, so both save the same fact worded slightly differently (the exact-string dedup in
# remember() only catches identical wording). Serializing here guarantees each pass's
# known_facts reflects every earlier turn's completed writes.
_lock = asyncio.Lock()


async def extract_and_apply_memory(user_message: str, assistant_message: str) -> None:
    """Runs a dedicated, non-conversational LLM pass to decide what to save/remove from memory.

    Proactive memory upkeep doesn't reliably emerge from asking the main chat model to call
    tools on its own initiative mid-conversation (smaller models in particular tend to only
    act on explicit instructions). Running it as its own forced task every turn, rather than
    leaving it to the chat model's discretion, makes it work regardless of which model is
    handling the conversation. Runs as a fire-and-forget background task, so failures here
    must never raise into the caller.
    """
    async with _lock:
        await _extract_and_apply_memory_locked(user_message, assistant_message)


async def _extract_and_apply_memory_locked(user_message: str, assistant_message: str) -> None:
    # Filter to current session so the extraction LLM only sees facts relevant here — without
    # this, it could see level-20 memories from another BG3 session and "fix" them based on
    # what it sees in the current conversation, corrupting the other session's facts.
    gs = game_state.get_game_state()
    tracked_process = gs["process"] if gs else None
    tracked_session = gs["session_id"] if gs else None
    tracked_variant = (gs.get("variant") if gs else None) or None
    known_facts = memory.format_memories_for_prompt(
        tracked_process, tracked_session, active_variant=tracked_variant
    ) or "Known facts about the user: none yet."
    game_state_text = game_state.format_game_state_for_prompt()
    observations_text = observations.format_observations_for_prompt(tracked_process, tracked_session) if tracked_process else ""
    # The scope decision ("user" vs "game"/"session") is anchored to what's actually running -
    # without stating it explicitly, the model can only infer it from the session snapshot,
    # which is absent whenever no tracker values have been extracted yet.
    tracked_line = (
        f"Currently tracked game (actively being played right now): {tracked_process}"
        if tracked_process
        else "No game is currently being tracked (nothing is actively played right now)."
    )
    if tracked_process and tracked_variant:
        tracked_line += (
            f"\nActive modpack for this playthrough: {tracked_variant} (a modded variant of the game "
            "— see the modpack tagging rule)."
        )
    messages = [
        {"role": "system", "content": load_prompt("memory_extraction")},
        {
            "role": "user",
            "content": (
                f"{current_datetime_context()}\n\n{tracked_line}\n\n{known_facts}\n\n"
                + (f"{game_state_text}\n\n" if game_state_text else "")
                + (f"{observations_text}\n\n" if observations_text else "")
                + f"Latest exchange:\nUser: {user_message}\nCompanion: {assistant_message}"
            ),
        },
    ]

    try:
        model = settings.memory_extraction_model or None
        provider = settings.memory_extraction_provider or settings.llm_provider
        raw = await chat_completion(
            messages,
            model=model,
            response_format={"type": "json_object"},
            source="memory_extraction",
            provider=provider,
        )
        data = json.loads(raw)
    except Exception:
        logger.exception("Memory extraction failed")
        return

    for fact in data.get("save") or []:
        if not isinstance(fact, dict):
            continue
        # Placement is anchored strictly to the tracked game (what the model was told about).
        # remember() owns the degradation rule - a "session" fact with no active session becomes
        # a "user" fact, never a game-wide one. The old foreground-process fallback here is gone:
        # it silently re-tiered session facts to game scope whenever tracking was off.
        memory.remember(
            (fact.get("content") or ""),
            (fact.get("scope") or "user").lower(),
            process=tracked_process,
            session_id=tracked_session,
            # Honored only on game scope (remember() drops it elsewhere); anchored to the
            # tracked variant, never to a pack name the model conjured itself.
            variant=tracked_variant if fact.get("modpack_specific") is True else None,
        )

    for memory_id in data.get("remove") or []:
        if isinstance(memory_id, str) and memory_id:
            memory.remove_memory(memory_id)

    # Observations the model confirmed (promoted into "save" above), contradicted, or judged
    # stale get cleared from the journal so they stop being re-surfaced.
    cleared = [i for i in data.get("clear_observations") or [] if isinstance(i, str) and i]
    if cleared:
        observations.remove_observations(cleared)
