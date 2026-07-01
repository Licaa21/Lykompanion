"""Ephemeral "what's happening right now" game state, kept in memory only (not persisted to
disk like app/core/memory.py) - it reflects the current session, not durable facts about the user."""

_state: dict | None = None

# Session-scoped LLM usage counters for the game-state pipeline - reset on restart, not tied to
# the current tracked process/game like _state is.
_extraction_call_count = 0
_extraction_cost_usd = 0.0
_training_call_count = 0
_training_cost_usd = 0.0


def get_game_state() -> dict | None:
    return _state


def record_extraction_call(cost_usd: float) -> None:
    global _extraction_call_count, _extraction_cost_usd
    _extraction_call_count += 1
    _extraction_cost_usd += cost_usd


def record_training_call(cost_usd: float) -> None:
    global _training_call_count, _training_cost_usd
    _training_call_count += 1
    _training_cost_usd += cost_usd


def get_session_stats() -> dict:
    return {
        "extraction_call_count": _extraction_call_count,
        "extraction_cost_usd": _extraction_cost_usd,
        "training_call_count": _training_call_count,
        "training_cost_usd": _training_cost_usd,
    }


def start_tracking(process: str) -> None:
    """Marks a process as tracked immediately, with no field values yet - called as soon as the
    poller recognizes an approved game in focus, well before the first LLM extraction pass
    completes, so the UI (tracking=True as soon as this is set) doesn't wait a full poll window
    to show anything. A no-op if this process is already the one being tracked, so it doesn't
    wipe values a poll window may have already collected."""
    global _state
    if _state is not None and _state["process"] == process:
        return
    _state = {"process": process, "values": {}}


def set_game_state(process: str, values: dict[str, str | None]) -> None:
    """`values` maps tracker id -> extracted value, for whatever trackers are configured for this
    process (see app/core/game_state_trackers.py) - the set of keys isn't fixed."""
    global _state
    _state = {"process": process, "values": values}


def clear_game_state() -> None:
    global _state
    _state = None


def format_game_state_for_prompt() -> str:
    if not _state:
        return ""
    from app.core import game_state_trackers

    values = _state["values"]
    lines = [
        f"- {t['label']}: {values[t['id']]}"
        for t in game_state_trackers.get_trackers(_state["process"])
        if values.get(t["id"])
    ]
    if not lines:
        return ""
    return f"Current game session (process: {_state['process']}):\n" + "\n".join(lines)
