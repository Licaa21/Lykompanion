import asyncio
import logging

from app.core import reminders
from app.services.system.processes import get_foreground_process_name

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 20


def _fire(entry: dict) -> None:
    """The message was already composed by the LLM at add_reminder/add_alarm time (see
    reminder_tool.py) - firing just replays it verbatim into the pending queue, with no LLM call
    of its own, so a recurring reminder never costs more than the one tool call that created it."""
    reminders.add_pending(entry["message"])
    reminders.mark_fired(entry)


async def _poll_tick() -> None:
    foreground = get_foreground_process_name()
    for entry in reminders.due_entries(foreground):
        try:
            _fire(entry)
        except Exception:
            logger.exception("Failed to fire reminder/alarm entry id=%r", entry["id"])


async def run_reminder_poller() -> None:
    """Long-lived background loop, started at app startup, checking for due reminders/alarms
    scoped to whatever game is currently in the foreground."""
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        try:
            await _poll_tick()
        except Exception:
            logger.exception("Reminder poller tick failed")
