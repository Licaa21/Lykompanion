import asyncio
import logging

from app.core import memory, reminders
from app.core.config import settings
from app.core.prompts import load_prompt
from app.services.llm.client import chat_completion
from app.services.system.processes import get_foreground_process_name

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 20


async def _generate_and_enqueue(entry: dict) -> None:
    """Fire-and-forget: turns a due reminder/alarm's stored intent into a fresh, spoken-style
    line and drops it into the pending queue for the frontend to pick up and inject into the
    active chat. Failures here must never raise into the poller loop."""
    known_facts = memory.format_memories_for_prompt(entry["process"]) or "Known facts about the user: none yet."
    messages = [
        {"role": "system", "content": load_prompt("reminder_message")},
        {
            "role": "user",
            "content": f"Reminder purpose: {entry['message_prompt']}\n\nGame currently being played: {entry['process']}\n\n{known_facts}",
        },
    ]
    try:
        text = await chat_completion(messages, model=settings.memory_extraction_model or None, source="reminder_message")
    except Exception:
        logger.exception("Reminder message generation failed for entry id=%r", entry["id"])
        return
    reminders.add_pending(text.strip())
    reminders.mark_fired(entry)


async def _poll_tick() -> None:
    foreground = get_foreground_process_name()
    for entry in reminders.due_entries(foreground):
        await _generate_and_enqueue(entry)


async def run_reminder_poller() -> None:
    """Long-lived background loop, started at app startup, checking for due reminders/alarms
    scoped to whatever game is currently in the foreground."""
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        try:
            await _poll_tick()
        except Exception:
            logger.exception("Reminder poller tick failed")
