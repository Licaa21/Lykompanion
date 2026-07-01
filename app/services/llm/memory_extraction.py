import json
import logging

from app.core import game_state, game_state_processes, memory
from app.core.config import settings
from app.core.prompts import current_datetime_context, load_prompt
from app.services.llm.client import chat_completion
from app.services.system.processes import get_foreground_process_name

logger = logging.getLogger(__name__)


async def extract_and_apply_memory(user_message: str, assistant_message: str) -> None:
    """Runs a dedicated, non-conversational LLM pass to decide what to save/remove from memory.

    Proactive memory upkeep doesn't reliably emerge from asking the main chat model to call
    tools on its own initiative mid-conversation (smaller models in particular tend to only
    act on explicit instructions). Running it as its own forced task every turn, rather than
    leaving it to the chat model's discretion, makes it work regardless of which model is
    handling the conversation. Runs as a fire-and-forget background task, so failures here
    must never raise into the caller.
    """
    # Filter to current session so the extraction LLM only sees facts relevant here — without
    # this, it could see level-20 memories from another BG3 session and "fix" them based on
    # what it sees in the current conversation, corrupting the other session's facts.
    gs = game_state.get_game_state()
    tracked_process = gs["process"] if gs else None
    tracked_session = gs["session_id"] if gs else None
    known_facts = memory.format_memories_for_prompt(tracked_process, tracked_session) or "Known facts about the user: none yet."
    game_state_text = game_state.format_game_state_for_prompt()
    messages = [
        {"role": "system", "content": load_prompt("memory_extraction")},
        {
            "role": "user",
            "content": (
                f"{current_datetime_context()}\n\n{known_facts}\n\n"
                + (f"{game_state_text}\n\n" if game_state_text else "")
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

    fg_process = None
    for fact in data.get("save") or []:
        if not isinstance(fact, dict):
            continue
        content = (fact.get("content") or "").strip()
        if not content:
            continue
        scope = (fact.get("scope") or "user").lower()
        if scope in ("game", "session"):
            # Resolve the game process — prefer the tracked game over raw foreground, since
            # the user may have alt-tabbed to the companion window while still mid-session.
            if fg_process is None:
                fg_process = get_foreground_process_name() or ""
            tag = fg_process if fg_process and game_state_processes.is_likely_game(fg_process) else None
            if not tag and tracked_process:
                tag = tracked_process
            if scope == "session":
                # Session-specific: only visible in this exact playthrough.
                sess = tracked_session if (tag and tracked_process and tag.lower() == tracked_process.lower()) else None
                memory.add_memory(content, process=tag, session_id=sess)
            else:
                # Game-level: visible across all sessions of this game, not just this run.
                memory.add_memory(content, process=tag, session_id=None)
        else:
            # "user" scope — general fact about the person, no process, no session,
            # always visible regardless of what game is active.
            memory.add_memory(content)

    for memory_id in data.get("remove") or []:
        if isinstance(memory_id, str) and memory_id:
            memory.remove_memory(memory_id)
