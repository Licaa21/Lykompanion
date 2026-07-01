from app.services.system.app_audio import set_app_volume

APP_VOLUME_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_application_volume",
            "description": (
                "Adjust the Windows per-application mixer volume for a specific running app or game - "
                "not your own narration, use set_narration_volume for that. Call this when the user "
                "names a specific app/game that's too loud or too quiet relative to something else, e.g. "
                "'Rocket League is too loud, I can't hear my music' (lower Rocket League) or 'turn up "
                "Spotify'. Matches loosely against the app's process name, so 'rocket league' or "
                "'spotify' works fine without the .exe."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "process": {
                        "type": "string",
                        "description": "The app/game name or process to adjust, e.g. 'Rocket League' or 'spotify.exe'.",
                    },
                    "volume_percent": {
                        "type": "integer",
                        "description": "Target volume as a percentage from 0 (mute) to 100 (full volume).",
                    },
                },
                "required": ["process", "volume_percent"],
            },
        },
    },
]


def execute_set_application_volume(arguments: dict) -> str:
    process = (arguments.get("process") or "").strip()
    volume_percent = arguments.get("volume_percent")
    if not process or not isinstance(volume_percent, (int, float)):
        return "Couldn't adjust volume: missing app name or percentage."

    clamped = max(0, min(100, int(volume_percent)))
    touched = set_app_volume(process, clamped)
    if not touched:
        return f"Couldn't find an active audio session for '{process}' - it may not be playing any audio right now."
    return f"Set {', '.join(touched)} volume to {clamped}%."
