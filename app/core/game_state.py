"""Ephemeral "what's happening right now" game state, kept in memory only (not persisted to
disk like app/core/memory.py) - it reflects the current session, not durable facts about the user."""

_state: dict | None = None


def get_game_state() -> dict | None:
    return _state


def set_game_state(
    process: str,
    activity: str | None,
    location: str | None,
    quest: str | None,
    character: str | None,
    notable_choice: str | None,
) -> None:
    global _state
    _state = {
        "process": process,
        "activity": activity,
        "location": location,
        "quest": quest,
        "character": character,
        "notable_choice": notable_choice,
    }


def clear_game_state() -> None:
    global _state
    _state = None


def format_game_state_for_prompt() -> str:
    if not _state:
        return ""
    lines = []
    if _state.get("activity"):
        lines.append(f"- Currently: {_state['activity']}")
    if _state.get("location"):
        lines.append(f"- Location: {_state['location']}")
    if _state.get("quest"):
        lines.append(f"- Quest: {_state['quest']}")
    if _state.get("character"):
        lines.append(f"- Character: {_state['character']}")
    if _state.get("notable_choice"):
        lines.append(f"- Recent choice: {_state['notable_choice']}")
    if not lines:
        return ""
    return f"Current game session (process: {_state['process']}):\n" + "\n".join(lines)
