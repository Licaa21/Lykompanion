LISTENING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "stop_listening",
            "description": (
                "Disable hands-free (live mic) listening, indefinitely, until the user says the wake "
                "phrase or manually re-enables the mic. Call it the moment the user needs to step away, "
                "is getting a call, wants quiet, directly asks you to stop listening, says any kind of "
                "sign-off/farewell ('goodbye', 'bye', 'talk to you later', 'I'm done', 'gotta go', etc.), "
                "or when hands-free is clearly picking up audio not meant for you (overheard conversation, "
                "movie/show dialogue, music, a phone call)."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def execute_stop_listening(_arguments: dict) -> str:
    return "Disabled hands-free listening."
