from app.services.system.processes import get_foreground_process_name

PROCESS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_active_process",
            "description": (
                "Check which application/game executable is currently focused on the user's screen, "
                "so you know what they're playing without having to ask. Call this when it's unclear "
                "what game is currently running, or to confirm before giving game-specific advice."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def execute_fetch_active_process(_arguments: dict) -> str:
    name = get_foreground_process_name()
    if not name:
        return "Couldn't determine the active process right now."
    return f"The currently focused application is: {name}"
