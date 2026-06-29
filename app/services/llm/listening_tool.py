LISTENING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "stop_listening",
            "description": (
                "Disable hands-free (live mic) listening - e.g. the user is stepping away, getting "
                "a phone call, or directly asks you to stop listening. If they mention a duration "
                "(e.g. 'for 5 minutes'), pass duration_seconds so listening resumes automatically "
                "afterward; omit it to stop indefinitely until they manually re-enable the mic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": "Seconds to pause listening before auto-resuming. Omit to stop indefinitely.",
                    }
                },
            },
        },
    },
]


def execute_stop_listening(arguments: dict) -> tuple[str, int | None]:
    duration = arguments.get("duration_seconds")
    duration = int(duration) if isinstance(duration, (int, float)) and duration > 0 else None

    if duration:
        message = f"Paused hands-free listening for {duration} seconds."
    else:
        message = "Disabled hands-free listening."

    return message, duration
