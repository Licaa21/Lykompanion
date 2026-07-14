"""Per-process game session state (tracked field values), persisted to disk (data/
game_state_sessions.json) so stats/progress aren't lost when the game loses focus, is closed and
reopened, or the companion itself restarts. Distinct from app/core/memory.py's durable, curated
facts about the user - this is a live, continuously-superseded snapshot of each process's most
recent tracked session, not a narrative record.

Sessions allow multiple named playthroughs per process (e.g. "Dark Urge run", "NG+"). Keys in
game_state_sessions.json are "process::session_id". The active session per process is tracked in
data/active_sessions.json. Trackers stay per-process (shared across sessions); training data is
per-variant when a variant is set (a modpack overhauls the UI) and per-process otherwise.

A session may carry a "variant" — the modpack/overhaul it runs (e.g. "Nolvus" for a modded
Skyrim playthrough), auto-detected by app/services/llm/variant_detection.py or set by the user
from the Gaming Journal. None/absent = vanilla. The variant scopes what the companion knows:
variant-tagged game memories and training data are only surfaced while a session of that
variant is active."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SESSIONS_PATH = DATA_DIR / "game_state_sessions.json"
ACTIVE_SESSIONS_PATH = DATA_DIR / "active_sessions.json"

# The process currently shown as "live" in the UI - runtime only, not persisted.
_active_process: str | None = None

# Active session id for the tracked process - runtime only. Set by start_tracking, cleared by
# stop_tracking. Switches immediately when the user picks a different session from the UI.
_active_session: str | None = None

# When the current game became the tracked one (this launch/switch) - runtime only, for a
# rough "how long they've been playing this session" signal in the chat prompt. Only reset on a
# genuine game switch or exit, NOT on alt-tabbing to the companion, so it reflects the real
# gaming session, not the current focus window.
_tracking_started_at: datetime | None = None

# In-memory mirror of the active session's values — avoids a disk read on every chat message.
# Invalidated by set_game_state, stop_tracking, and switch_session.
_cached_values: dict[str, str | None] | None = None

# In-memory mirror of the active session's variant (modpack), same lifecycle as _cached_values.
# None is a valid cached value (vanilla), so a separate loaded-flag marks cache validity.
_cached_variant: str | None = None
_cached_variant_loaded = False

_extraction_call_count = 0
_extraction_cost_usd = 0.0

# Divergence warnings set by the extraction pass when OCR contradicts session memory (e.g. level
# regressed). Keyed by lowercased process name. Consumed once — popped on the next chat turn so
# the companion raises it naturally without repeating on every subsequent message.
_pending_divergence: dict[str, str] = {}


def _load_all() -> dict[str, dict]:
    if not SESSIONS_PATH.exists():
        return {}
    data = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    # Migrate old format { "process.exe": { values, updated_at } } → { "process.exe::default": {...} }
    if any("::" not in k for k in data):
        migrated = {}
        for k, v in data.items():
            if "::" not in k:
                migrated[f"{k}::default"] = {
                    "name": "Default",
                    "session_id": "default",
                    "process": k,
                    "values": v.get("values", {}),
                    "updated_at": v.get("updated_at", datetime.now(timezone.utc).isoformat()),
                }
            else:
                migrated[k] = v
        _save_all(migrated)
        return migrated
    return data


def _save_all(data: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_active_sessions() -> dict[str, str]:
    if not ACTIVE_SESSIONS_PATH.exists():
        return {}
    return json.loads(ACTIVE_SESSIONS_PATH.read_text(encoding="utf-8"))


def _save_active_sessions(data: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_SESSIONS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _session_key(process: str, session_id: str) -> str:
    return f"{process.lower()}::{session_id}"


def record_extraction_call(cost_usd: float) -> None:
    global _extraction_call_count, _extraction_cost_usd
    _extraction_call_count += 1
    _extraction_cost_usd += cost_usd


def get_session_stats() -> dict:
    return {
        "extraction_call_count": _extraction_call_count,
        "extraction_cost_usd": _extraction_cost_usd,
    }


def get_sessions(process: str) -> list[dict]:
    """All sessions for a process, sorted by updated_at descending."""
    data = _load_all()
    prefix = process.lower() + "::"
    sessions = [
        {
            "session_id": v["session_id"],
            "name": v["name"],
            "updated_at": v.get("updated_at"),
            "variant": v.get("variant"),
        }
        for k, v in data.items()
        if k.startswith(prefix)
    ]
    return sorted(sessions, key=lambda s: s.get("updated_at") or "", reverse=True)


def get_active_session_id(process: str) -> str:
    """Returns the active session_id for a process. Creates 'default' if no sessions exist yet."""
    active = _load_active_sessions()
    session_id = active.get(process.lower())
    if session_id and _session_key(process, session_id) in _load_all():
        return session_id
    # Fall back to most recently updated session, or create default
    sessions = get_sessions(process)
    if sessions:
        session_id = sessions[0]["session_id"]
    else:
        session_id = "default"
        data = _load_all()
        data[_session_key(process, session_id)] = {
            "name": "Default",
            "session_id": session_id,
            "process": process.lower(),
            "values": {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_all(data)
    active[process.lower()] = session_id
    _save_active_sessions(active)
    return session_id


def peek_active_session_id(process: str) -> str | None:
    """Read-only variant of get_active_session_id - never creates a default session, so listing
    endpoints (Gaming Journal) don't mutate state as a side effect of being viewed."""
    session_id = _load_active_sessions().get(process.lower())
    if session_id and _session_key(process, session_id) in _load_all():
        return session_id
    sessions = get_sessions(process)
    return sessions[0]["session_id"] if sessions else None


def get_processes_with_sessions() -> list[str]:
    """Distinct process names that have at least one stored session."""
    seen: dict[str, str] = {}
    for entry in _load_all().values():
        proc = entry.get("process")
        if proc and proc.lower() not in seen:
            seen[proc.lower()] = proc
    return list(seen.values())


def get_session_name(process: str, session_id: str) -> str | None:
    entry = _load_all().get(_session_key(process, session_id))
    return entry.get("name") if entry else None


def create_session(process: str, name: str, variant: str | None = None) -> dict:
    """Creates a named session with a unique id, makes it active, returns the session dict."""
    session_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    data = _load_all()
    data[_session_key(process, session_id)] = {
        "name": name,
        "session_id": session_id,
        "process": process.lower(),
        "values": {},
        "updated_at": now,
        "variant": variant,
    }
    _save_all(data)
    active = _load_active_sessions()
    active[process.lower()] = session_id
    _save_active_sessions(active)
    # If this is the currently tracked process, switch runtime state immediately
    global _active_session, _cached_values, _cached_variant_loaded
    if _active_process and process.lower() == _active_process.lower():
        _active_session = session_id
        _cached_values = None
        _cached_variant_loaded = False
    return {"session_id": session_id, "name": name, "updated_at": now, "variant": variant}


def rename_session(process: str, session_id: str, name: str) -> bool:
    data = _load_all()
    key = _session_key(process, session_id)
    if key not in data:
        return False
    data[key]["name"] = name
    _save_all(data)
    return True


def set_session_variant(process: str, session_id: str, variant: str | None) -> bool:
    """Sets (or clears, with None) the modpack/variant of a session. Returns False if the
    session doesn't exist."""
    data = _load_all()
    key = _session_key(process, session_id)
    if key not in data:
        return False
    data[key]["variant"] = (variant or "").strip() or None
    _save_all(data)
    global _cached_variant_loaded
    if _active_process and process.lower() == _active_process.lower() and _active_session == session_id:
        _cached_variant_loaded = False
    return True


def get_session_variant(process: str, session_id: str) -> str | None:
    entry = _load_all().get(_session_key(process, session_id))
    return (entry or {}).get("variant")


def find_session_by_variant(process: str, variant: str) -> str | None:
    """Most recently updated session of a process tagged with this variant (case-insensitive),
    or None."""
    target = variant.strip().lower()
    for s in get_sessions(process):  # newest first
        if (s.get("variant") or "").strip().lower() == target:
            return s["session_id"]
    return None


def session_is_pristine(process: str, session_id: str) -> bool:
    """True when a session has no tracked values yet — used by variant detection to decide
    whether a just-created default session can simply be tagged/renamed in place instead of
    spawning a second one. (Session memories are checked separately by the caller.)"""
    entry = _load_all().get(_session_key(process, session_id))
    return bool(entry) and not (entry.get("values") or {})


def switch_session(process: str, session_id: str) -> bool:
    """Makes an existing session active. Returns False if the session doesn't exist."""
    data = _load_all()
    if _session_key(process, session_id) not in data:
        return False
    active = _load_active_sessions()
    active[process.lower()] = session_id
    _save_active_sessions(active)
    global _active_session, _cached_values, _cached_variant_loaded
    if _active_process and process.lower() == _active_process.lower():
        _active_session = session_id
        _cached_values = None
        _cached_variant_loaded = False
    return True


def delete_session(process: str, session_id: str) -> bool:
    """Delete one session (profile) of a process. Returns False if it didn't exist. If it was the
    active session, the active pointer moves to the most-recently-updated remaining session (or is
    dropped if none remain). Only touches this store — callers also clear the profile's session
    memories/observations."""
    data = _load_all()
    key = _session_key(process, session_id)
    if key not in data:
        return False
    del data[key]
    _save_all(data)

    active = _load_active_sessions()
    proc = process.lower()
    if active.get(proc) == session_id:
        remaining = get_sessions(process)  # sorted newest-first, already excludes the deleted one
        if remaining:
            active[proc] = remaining[0]["session_id"]
        else:
            active.pop(proc, None)
        _save_active_sessions(active)

    # If this process is the one being tracked, refresh runtime state to a valid session.
    global _active_session, _cached_values, _cached_variant_loaded
    if _active_process and proc == _active_process.lower() and _active_session == session_id:
        _active_session = active.get(proc)
        _cached_values = None
        _cached_variant_loaded = False
    return True


def delete_process(process: str) -> None:
    """Delete every session of a process plus its active-session pointer (used when a tracked game
    is removed from the Gaming Journal). Only touches this store — callers also clear the game's
    trackers, training data, memories, observations, and whitelist entry."""
    proc = process.lower()
    data = _load_all()
    prefix = proc + "::"
    kept = {k: v for k, v in data.items() if not k.startswith(prefix)}
    if len(kept) != len(data):
        _save_all(kept)
    active = _load_active_sessions()
    if active.pop(proc, None) is not None:
        _save_active_sessions(active)

    global _active_process, _active_session, _cached_values, _cached_variant_loaded
    if _active_process and proc == _active_process.lower():
        _active_process = None
        _active_session = None
        _cached_values = None
        _cached_variant_loaded = False


def get_values(process: str, session_id: str | None = None) -> dict[str, str | None]:
    if session_id is None:
        session_id = get_active_session_id(process)
    return _load_all().get(_session_key(process, session_id), {}).get("values", {})


def start_tracking(process: str) -> None:
    """Marks a process as the active/displayed session. Called as soon as an approved game enters
    focus — the UI shows previous session values immediately instead of waiting for the first LLM
    pass to populate anything."""
    global _active_process, _active_session, _cached_values, _tracking_started_at, _cached_variant_loaded
    _active_process = process
    _active_session = get_active_session_id(process)  # creates default session if none exists
    _cached_values = None
    _cached_variant_loaded = False
    _tracking_started_at = datetime.now(timezone.utc)


def stop_tracking() -> None:
    """Stops showing an active session (the tracked process exited) without deleting its
    persisted data — it's all still there next time that process is tracked."""
    global _active_process, _active_session, _cached_values, _tracking_started_at, _cached_variant_loaded
    _active_process = None
    _active_session = None
    _cached_values = None
    _cached_variant_loaded = False
    _tracking_started_at = None


def get_tracking_started_at() -> datetime | None:
    """When the currently tracked game became the active one (UTC), or None if nothing tracked."""
    return _tracking_started_at


def get_game_state() -> dict | None:
    global _cached_values, _cached_variant, _cached_variant_loaded
    if _active_process is None:
        return None
    if _cached_values is None:
        _cached_values = get_values(_active_process, _active_session)
    if not _cached_variant_loaded:
        _cached_variant = get_session_variant(_active_process, _active_session) if _active_session else None
        _cached_variant_loaded = True
    return {
        "process": _active_process,
        "session_id": _active_session,
        "values": _cached_values,
        "variant": _cached_variant,
    }


def set_game_state(process: str, values: dict[str, str | None]) -> None:
    """`values` maps tracker id -> extracted value. Persisted immediately so it survives a restart."""
    global _cached_values
    # Use the runtime-tracked session when available (normal path); fall back to disk lookup.
    if _active_process and process.lower() == _active_process.lower() and _active_session:
        session_id = _active_session
    else:
        session_id = _load_active_sessions().get(process.lower(), "default")

    data = _load_all()
    key = _session_key(process, session_id)
    if key not in data:
        data[key] = {
            "name": "Default",
            "session_id": session_id,
            "process": process.lower(),
            "values": {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    data[key]["values"] = values
    data[key]["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_all(data)
    if _active_process and process.lower() == _active_process.lower():
        _cached_values = values


def set_pending_divergence(process: str, warning: str) -> None:
    _pending_divergence[process.lower()] = warning


def pop_pending_divergence(process: str) -> str | None:
    """Returns and removes the pending divergence warning for a process, or None if none."""
    return _pending_divergence.pop(process.lower(), None)


def format_game_state_for_prompt() -> str:
    state = get_game_state()
    if not state:
        return ""
    from app.core import game_state_trackers

    values = state["values"]
    lines = [
        f"- {t['label']}: {values[t['id']]}"
        for t in game_state_trackers.get_trackers(state["process"], variant=state.get("variant"))
        if values.get(t["id"])
    ]
    if not lines:
        return ""
    return "Current game session (live, read from the player's screen):\n" + "\n".join(lines)
