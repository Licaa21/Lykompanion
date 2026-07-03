"""Screen-observation journal: the game-state OCR pass's staging area (Layer 2 of the memory
redesign). The OCR pass no longer writes long-term memory directly — a single misread frame used
to carry the same authority as the user stating a fact. Instead it appends timestamped,
confidence-tagged observations here, per process+session. Promotion into real memory happens in
the conversation-side memory-extraction pass, which sees the recent observations and can confirm
them against what the user actually says (and clears the ones it handled or that turned out
stale). A recent tail is also injected into the chat system prompt so the companion is aware of
what it noticed on screen even before anything is promoted.

Capped per session; oldest entries fall off. Persisted to data/observations.json.
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

OBSERVATIONS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "observations.json"

# Per process+session cap - observations are ephemeral working notes, not an archive.
MAX_PER_SESSION = 50
# How many recent observations get surfaced in prompts (chat + memory extraction).
PROMPT_TAIL = 12

_lock = threading.Lock()


def _load() -> list[dict]:
    if not OBSERVATIONS_PATH.exists():
        return []
    return json.loads(OBSERVATIONS_PATH.read_text(encoding="utf-8"))


def _save(entries: list[dict]) -> None:
    OBSERVATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    OBSERVATIONS_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def add_observations(process: str, session_id: str | None, contents: list[tuple[str, float | None]]) -> list[dict]:
    """Appends observations for one process+session. `contents` is (text, confidence) pairs;
    exact duplicates of a still-stored observation are skipped. Returns the added entries."""
    added = []
    with _lock:
        entries = _load()
        existing = {
            e["content"].strip().lower()
            for e in entries
            if e["process"].lower() == process.lower() and e.get("session_id") == session_id
        }
        for content, confidence in contents:
            content = (content or "").strip()
            if not content or content.lower() in existing:
                continue
            existing.add(content.lower())
            entry = {
                "id": uuid.uuid4().hex[:8],
                "process": process,
                "session_id": session_id,
                "content": content,
                "confidence": confidence,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
            entries.append(entry)
            added.append(entry)
        if added:
            # Enforce the per-session cap, dropping oldest first (list is append-ordered).
            in_session = [
                e for e in entries
                if e["process"].lower() == process.lower() and e.get("session_id") == session_id
            ]
            overflow = len(in_session) - MAX_PER_SESSION
            if overflow > 0:
                drop_ids = {e["id"] for e in in_session[:overflow]}
                entries = [e for e in entries if e["id"] not in drop_ids]
            _save(entries)
    return added


def get_observations(process: str, session_id: str | None = None) -> list[dict]:
    """All stored observations for a process (optionally narrowed to one session), oldest first."""
    return [
        e for e in _load()
        if e["process"].lower() == process.lower()
        and (session_id is None or e.get("session_id") == session_id)
    ]


def remove_observations(ids: list[str]) -> int:
    with _lock:
        entries = _load()
        id_set = set(ids)
        remaining = [e for e in entries if e["id"] not in id_set]
        removed = len(entries) - len(remaining)
        if removed:
            _save(remaining)
        return removed


def clear_session(process: str, session_id: str | None) -> int:
    """Drops every observation for one process+session (e.g. profile deleted)."""
    with _lock:
        entries = _load()
        remaining = [
            e for e in entries
            if not (e["process"].lower() == process.lower() and e.get("session_id") == session_id)
        ]
        removed = len(entries) - len(remaining)
        if removed:
            _save(remaining)
        return removed


def clear_process(process: str) -> int:
    """Drops every observation for a process across all its sessions (e.g. tracked game deleted)."""
    with _lock:
        entries = _load()
        remaining = [e for e in entries if e["process"].lower() != process.lower()]
        removed = len(entries) - len(remaining)
        if removed:
            _save(remaining)
        return removed


def format_observations_for_prompt(process: str, session_id: str | None) -> str:
    """Recent tail for the active session, for the chat system prompt and the memory-extraction
    pass. Explicitly framed as unconfirmed so neither treats them as established facts."""
    observations = get_observations(process, session_id)[-PROMPT_TAIL:]
    if not observations:
        return ""
    lines = [f"- [{o['id']}] {o['content']}" for o in observations]
    return (
        "Recent screen observations (auto-read from the player's screen, UNCONFIRMED - may be "
        "misread; do not treat as established fact without corroboration):\n" + "\n".join(lines)
    )
