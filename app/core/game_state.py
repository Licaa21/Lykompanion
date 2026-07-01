"""Per-process game session state (tracked field values), persisted to disk (data/
game_state_sessions.json) so stats/progress aren't lost when the game loses focus, is closed and
reopened, or the companion itself restarts. Distinct from app/core/memory.py's durable, curated
facts about the user - this is a live, continuously-superseded snapshot of each process's most
recent tracked session, not a narrative record."""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SESSIONS_PATH = DATA_DIR / "game_state_sessions.json"

# The process currently shown as "live" in the UI - runtime only, not persisted. None means
# nothing is actively being tracked right now (no approved game focused/running), even though
# earlier sessions' values remain on disk for whenever that process is tracked again.
_active_process: str | None = None

# In-memory mirror of the active process's values — avoids a disk read on every chat message
# (format_game_state_for_prompt is called per request). Invalidated by set_game_state and
# stop_tracking; populated lazily on first get_game_state call after start_tracking.
_cached_values: dict[str, str | None] | None = None

# Session-scoped LLM usage counters for the game-state pipeline - reset on restart, not tied to
# the current tracked process/game like the persisted values are.
_extraction_call_count = 0
_extraction_cost_usd = 0.0
_training_call_count = 0
_training_cost_usd = 0.0


def _load_all() -> dict[str, dict]:
    if not SESSIONS_PATH.exists():
        return {}
    return json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))


def _save_all(data: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


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


def get_values(process: str) -> dict[str, str | None]:
    return _load_all().get(process.lower(), {}).get("values", {})


def start_tracking(process: str) -> None:
    """Marks a process as the active/displayed session - called as soon as the poller recognizes
    an approved game in focus, well before the first LLM extraction pass completes, so the UI
    (tracking=True as soon as this is set) doesn't wait a full poll window to show anything. If
    this process has persisted values from an earlier session (this run or a previous one), those
    show up first instead of a blank slate, so nothing collected earlier gets lost just because
    the game lost focus, was closed, or the companion restarted."""
    global _active_process, _cached_values
    _active_process = process
    _cached_values = None  # load lazily on first get_game_state call
    data = _load_all()
    if process.lower() not in data:
        data[process.lower()] = {"values": {}, "updated_at": datetime.now(timezone.utc).isoformat()}
        _save_all(data)


def stop_tracking() -> None:
    """Stops showing an active session (the tracked process exited) without deleting its
    persisted values - they're still there next time that process is tracked."""
    global _active_process, _cached_values
    _active_process = None
    _cached_values = None


def get_game_state() -> dict | None:
    global _cached_values
    if _active_process is None:
        return None
    if _cached_values is None:
        _cached_values = get_values(_active_process)
    return {"process": _active_process, "values": _cached_values}


def set_game_state(process: str, values: dict[str, str | None]) -> None:
    """`values` maps tracker id -> extracted value, for whatever trackers are configured for this
    process (see app/core/game_state_trackers.py) - the set of keys isn't fixed. Persisted
    immediately so it survives a restart, not just an in-memory update."""
    global _cached_values
    data = _load_all()
    data[process.lower()] = {"values": values, "updated_at": datetime.now(timezone.utc).isoformat()}
    _save_all(data)
    if _active_process and process.lower() == _active_process.lower():
        _cached_values = values


def format_game_state_for_prompt() -> str:
    state = get_game_state()
    if not state:
        return ""
    from app.core import game_state_trackers

    values = state["values"]
    lines = [
        f"- {t['label']}: {values[t['id']]}"
        for t in game_state_trackers.get_trackers(state["process"])
        if values.get(t["id"])
    ]
    if not lines:
        return ""
    return f"Current game session (process: {state['process']}):\n" + "\n".join(lines)
