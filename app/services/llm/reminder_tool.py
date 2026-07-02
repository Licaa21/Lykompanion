from datetime import datetime

from app.core import reminders
from app.services.system.processes import get_foreground_process_name

REMINDER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": (
                "Set up a recurring reminder tied to a specific game - it repeats the exact message you "
                "give it, spoken every N minutes while that game is the one being actively played (paused "
                "automatically while they're not playing it). Use this for repeated in-game nudges, e.g. "
                "'remind me to save every 5 minutes' or 'tell me to drink water every 20 minutes while I "
                "play this'. If the user doesn't name the game explicitly, call fetch_active_process first "
                "and use that as the process."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": (
                            "The exact line to speak when this reminder fires, written in-character as if "
                            "you're saying it directly to the player right now (not a description of what "
                            "it's about) - e.g. 'Remember to quick-save, that boss hits hard!'. This is "
                            "spoken verbatim every time it fires, with no further LLM call, so word it "
                            "generically enough to still make sense on repeat - avoid anything time-specific."
                        ),
                    },
                    "process": {
                        "type": "string",
                        "description": "The game executable this reminder is scoped to, e.g. 'eldenring.exe'.",
                    },
                    "interval_minutes": {
                        "type": "integer",
                        "description": "How often, in minutes, to repeat this reminder while the game is active.",
                    },
                },
                "required": ["message", "process", "interval_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_reminder",
            "description": "Cancel a previously set recurring reminder by its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {"type": "string", "description": "The id of the reminder to remove."}
                },
                "required": ["reminder_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_alarm",
            "description": (
                "Set up a one-time alarm tied to a specific game - it speaks the exact message you give it, "
                "once, at a specific date/time while that game is the one being played (it waits, rather "
                "than firing late, if the game isn't in the foreground yet at that time). Use this for "
                "one-off reminders, e.g. 'remind me in 20 minutes to check the auction house' or 'at 9pm "
                "remind me to log off'. If the user doesn't name the game explicitly, call "
                "fetch_active_process first and use that as the process."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": (
                            "The exact line to speak when this alarm fires, written in-character as if "
                            "you're saying it directly to the player right now (not a description of what "
                            "it's about) - e.g. 'Time to check the auction house before you log off!'. This "
                            "is spoken verbatim when it fires, with no further LLM call."
                        ),
                    },
                    "process": {
                        "type": "string",
                        "description": "The game executable this alarm is scoped to, e.g. 'wow.exe'.",
                    },
                    "fire_at": {
                        "type": "string",
                        "description": (
                            "ISO 8601 local datetime this alarm should fire at, e.g. '2026-07-01T21:00:00'. "
                            "Resolve any relative time ('in 20 minutes', 'at 9pm') against the current "
                            "date/time you were given."
                        ),
                    },
                },
                "required": ["message", "process", "fire_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_alarm",
            "description": "Cancel a previously set one-time alarm by its id.",
            "parameters": {
                "type": "object",
                "properties": {"alarm_id": {"type": "string", "description": "The id of the alarm to cancel."}},
                "required": ["alarm_id"],
            },
        },
    },
]


def _resolve_process(process: str) -> str:
    process = (process or "").strip()
    return process or (get_foreground_process_name() or "")


def execute_reminder_tool(name: str, arguments: dict) -> str:
    if name == "add_reminder":
        message = (arguments.get("message") or "").strip()
        process = _resolve_process(arguments.get("process"))
        interval_minutes = int(arguments.get("interval_minutes") or 0)
        if not message or not process or interval_minutes <= 0:
            return "Couldn't set that reminder: missing message, game, or interval."
        entry = reminders.add_reminder(message, process, interval_minutes)
        return f"Reminder [{entry['id']}] set: every {interval_minutes} min while {process} is active."

    if name == "remove_reminder":
        reminder_id = arguments.get("reminder_id") or ""
        removed = reminders.remove_reminder(reminder_id)
        return "Reminder removed." if removed else "No reminder found with that id."

    if name == "add_alarm":
        message = (arguments.get("message") or "").strip()
        process = _resolve_process(arguments.get("process"))
        fire_at = (arguments.get("fire_at") or "").strip()
        if not message or not process or not fire_at:
            return "Couldn't set that alarm: missing message, game, or time."
        try:
            datetime.fromisoformat(fire_at)
        except ValueError:
            return (
                f"Couldn't set that alarm: '{fire_at}' isn't a valid ISO 8601 datetime. "
                "Use e.g. '2026-07-01T21:00:00' and try again."
            )
        entry = reminders.add_alarm(message, process, fire_at)
        return f"Alarm [{entry['id']}] set for {fire_at} while {process} is active."

    if name == "cancel_alarm":
        alarm_id = arguments.get("alarm_id") or ""
        removed = reminders.cancel_alarm(alarm_id)
        return "Alarm cancelled." if removed else "No alarm found with that id."

    return f"Unknown tool: {name}"
