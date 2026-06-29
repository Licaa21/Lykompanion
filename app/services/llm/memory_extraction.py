import json
import logging

from app.core import memory
from app.core.prompts import current_datetime_context, load_prompt
from app.services.llm.client import chat_completion

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
    known_facts = memory.format_memories_for_prompt() or "Known facts about the user: none yet."
    messages = [
        {"role": "system", "content": load_prompt("memory_extraction")},
        {
            "role": "user",
            "content": (
                f"{current_datetime_context()}\n\n{known_facts}\n\n"
                f"Latest exchange:\nUser: {user_message}\nCompanion: {assistant_message}"
            ),
        },
    ]

    try:
        raw = await chat_completion(messages, response_format={"type": "json_object"})
        data = json.loads(raw)
    except Exception:
        logger.exception("Memory extraction failed")
        return

    for fact in data.get("save") or []:
        if isinstance(fact, str) and fact.strip():
            memory.add_memory(fact.strip())

    for memory_id in data.get("remove") or []:
        if isinstance(memory_id, str) and memory_id:
            memory.remove_memory(memory_id)
