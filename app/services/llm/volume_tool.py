from app.core.config import persist_env_value, settings

VOLUME_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_narration_volume",
            "description": (
                "Adjust your own narration (text-to-speech) volume. Call this when the user says "
                "you're too loud, too quiet, asks you to turn it up/down, or gives an explicit volume."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "volume_percent": {
                        "type": "integer",
                        "description": "Target narration volume as a percentage from 0 (mute) to 100 (full volume).",
                    }
                },
                "required": ["volume_percent"],
            },
        },
    },
]


def execute_set_narration_volume(arguments: dict) -> str:
    volume_percent = arguments.get("volume_percent")
    if not isinstance(volume_percent, (int, float)):
        return "Couldn't set volume: no valid percentage given."

    clamped = max(0, min(100, int(volume_percent)))
    settings.tts_volume = clamped / 100
    persist_env_value("TTS_VOLUME", str(settings.tts_volume))

    return f"Narration volume set to {clamped}%."
