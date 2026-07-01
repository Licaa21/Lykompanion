from app.core import reminders
from app.services.system.processes import get_foreground_process_name

REMINDER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": (
                "Set up a recurring reminder tied to a specific game - it will nudge the player with a "
                "short spoken message, generated fresh each time, every N minutes while that game is the "
                "one they're actively playing (paused automatically while they're not playing it). Use "
                "this for repeated in-game nudges, e.g. 'remind me to save every 5 minutes' or 'tell me to "
                "drink water every 20 minutes while I play this'. If the user doesn't name the game "
                "explicitly, call fetch_active_process first and use that as the process."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message_prompt": {
                        "type": "string",
                        "description": "What the reminder is about, e.g. 'remind the player to quick-save'.",
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
                "required": ["message_prompt", "process", "interval_minutes"],
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
                "Set up a one-time alarm tied to a specific game - a single spoken message, generated at "
                "fire time, that fires once at a specific date/time while that game is the one being "
                "played (it waits, rather than firing late, if the game isn't in the foreground yet at "
                "that time). Use this for one-off reminders, e.g. 'remind me in 20 minutes to check the "
                "auction house' or 'at 9pm remind me to log off'. If the user doesn't name the game "
                "explicitly, call fetch_active_process first and use that as the process."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message_prompt": {
                        "type": "string",
                        "description": "What the alarm is about, e.g. 'remind the player to check the auction house'.",
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
                "required": ["message_prompt", "process", "fire_at"],
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
        message_prompt = (arguments.get("message_prompt") or "").strip()
        process = _resolve_process(arguments.get("process"))
        interval_minutes = int(arguments.get("interval_minutes") or 0)
        if not message_prompt or not process or interval_minutes <= 0:
            return "Couldn't set that reminder: missing message, game, or interval."
        entry = reminders.add_reminder(message_prompt, process, interval_minutes)
        return f"Reminder [{entry['id']}] set: every {interval_minutes} min while {process} is active."

    if name == "remove_reminder":
        reminder_id = arguments.get("reminder_id") or ""
        removed = reminders.remove_reminder(reminder_id)
        return "Reminder removed." if removed else "No reminder found with that id."

    if name == "add_alarm":
        message_prompt = (arguments.get("message_prompt") or "").strip()
        process = _resolve_process(arguments.get("process"))
        fire_at = (arguments.get("fire_at") or "").strip()
        if not message_prompt or not process or not fire_at:
            return "Couldn't set that alarm: missing message, game, or time."
        entry = reminders.add_alarm(message_prompt, process, fire_at)
        return f"Alarm [{entry['id']}] set for {fire_at} while {process} is active."

    if name == "cancel_alarm":
        alarm_id = arguments.get("alarm_id") or ""
        removed = reminders.cancel_alarm(alarm_id)
        return "Alarm cancelled." if removed else "No alarm found with that id."

    return f"Unknown tool: {name}"
