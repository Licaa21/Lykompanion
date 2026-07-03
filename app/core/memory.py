"""Long-term memory: durable, curated facts about the user, in three explicit scopes.

- "user"    — about the person regardless of any game (process=None, session_id=None). Always
              injected into the system prompt.
- "game"    — true across all playthroughs of one game (process set, session_id=None). Injected
              only while that game is the tracked one.
- "session" — specific to one playthrough/profile (process and session_id both set). Injected
              only while that exact session is active.

`scope` is stored explicitly on every entry (older entries without it are migrated on read from
the nullable process/session_id encoding). All writers — chat tools, the memory-extraction pass,
manual UI adds — must go through remember(), which owns scope resolution and the degradation
rule: a fact whose requested scope can't be honored (e.g. "session" with no active session) is
saved at "user" scope rather than silently landing in a *different game tier*, which is how
playthrough facts used to end up as game-wide ones.
"""

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "memory.json"

SCOPES = ("user", "game", "session")

# Serializes read-modify-write cycles - two concurrent save_*_memory tool calls (they run via
# asyncio.gather) or a background extraction pass must not overwrite each other's entry.
_lock = threading.Lock()


def _derive_scope(entry: dict) -> str:
    if entry.get("session_id"):
        return "session"
    if entry.get("process"):
        return "game"
    return "user"


def load_memories() -> list[dict]:
    if not MEMORY_PATH.exists():
        return []
    memories = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    # Migrate pre-scope entries on read; the next write persists the migrated shape since every
    # mutation rewrites the whole file.
    for m in memories:
        if m.get("scope") not in SCOPES:
            m["scope"] = _derive_scope(m)
    return memories


def save_memories(memories: list[dict]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(memories, indent=2), encoding="utf-8")


def remember(content: str, scope: str, process: str | None = None, session_id: str | None = None) -> dict | None:
    """The single entry point for saving a memory. Resolves the final scope from what's actually
    available, never silently re-tiering within game scopes:

    - "game" without a process, or "session" without a process, degrades to "user".
    - "session" with a process but no session degrades to "user" (NOT "game" - a playthrough
      fact stated game-wide would contaminate every other playthrough of that game).

    Returns the saved entry (its "scope" reflects what was actually applied), or None when an
    identical fact already exists at the same placement (cheap exact-duplicate guard - semantic
    dedup stays the extraction passes' job).
    """
    content = (content or "").strip()
    if not content:
        return None
    if scope not in SCOPES:
        scope = "user"

    requested = scope
    if scope in ("game", "session") and not process:
        scope = "user"
    elif scope == "session" and not session_id:
        scope = "user"
    if scope != requested:
        logger.info("Memory scope degraded %r -> %r (process=%r, session=%r): %s",
                    requested, scope, process, session_id, content)

    if scope == "user":
        process = None
        session_id = None
    elif scope == "game":
        session_id = None

    with _lock:
        memories = load_memories()
        key = content.lower()
        for m in memories:
            if (m["content"].strip().lower() == key
                    and (m.get("process") or "").lower() == (process or "").lower()
                    and m.get("session_id") == session_id):
                return None
        entry = {
            "id": uuid.uuid4().hex[:8],
            "content": content,
            "scope": scope,
            "process": process,
            "session_id": session_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        memories.append(entry)
        save_memories(memories)
    _notify_overlay("save", entry["scope"], entry["content"])
    return entry


def _notify_overlay(action: str, scope: str, content: str) -> None:
    """Fire a memory toast into the native overlay. Lazy import keeps core free of a
    services dependency at module load; best-effort and never raises."""
    try:
        from app.services import overlay_process

        overlay_process.push_memory(action, scope, content)
    except Exception:
        pass


def add_memory(content: str, process: str | None = None, session_id: str | None = None) -> dict:
    """Legacy-shaped writer, kept for callers that already resolved placement themselves
    (rollback re-adds, tests). Prefer remember() everywhere else."""
    with _lock:
        memories = load_memories()
        entry = {
            "id": uuid.uuid4().hex[:8],
            "content": content,
            "scope": _derive_scope({"process": process, "session_id": session_id}),
            "process": process,
            "session_id": session_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        memories.append(entry)
        save_memories(memories)
        return entry


def update_memory(memory_id: str, content: str, process: str | None = None, session_id: str | None = None) -> dict | None:
    with _lock:
        memories = load_memories()
        for m in memories:
            if m["id"] == memory_id:
                m["content"] = content
                m["process"] = process
                m["session_id"] = session_id
                m["scope"] = _derive_scope(m)
                # saved_at is intentionally not updated — it marks the original creation time,
                # which is what rollback uses to find memories from a specific time window.
                save_memories(memories)
                return m
        return None


def remove_memory(memory_id: str) -> bool:
    with _lock:
        memories = load_memories()
        removed = next((m for m in memories if m["id"] == memory_id), None)
        if removed is None:
            return False
        save_memories([m for m in memories if m["id"] != memory_id])
    _notify_overlay("remove", removed.get("scope", "user"), removed.get("content", ""))
    return True


def format_memories_for_prompt(
    active_process: str | None = None,
    active_session_id: str | None = None,
    retrieval_query: str | None = None,
    game_memory_limit: int = 0,
) -> str:
    """`retrieval_query` + `game_memory_limit` enable RAG-lite injection: user-scope memories
    are always included in full, but once the matching game/session memories exceed the limit,
    only the most relevant/recent `limit` of them make the prompt (see memory_retrieval.py).
    Background extraction passes must NOT pass these - they need every fact to dedupe/remove
    correctly."""
    memories = load_memories()
    if active_process is not None:
        def _include(m: dict) -> bool:
            m_process = m.get("process")
            m_session = m.get("session_id")
            if not m_process:
                # Global memory (no process, no session) — always include
                return True
            if m_process.lower() != active_process.lower():
                # Different game — exclude
                return False
            if not m_session:
                # Process-level memory (same game, no session) — include for all sessions
                return True
            if not active_session_id:
                # No active session context — include all memories for this process
                return True
            # Session-specific — only include when the session matches
            return m_session == active_session_id
        memories = [m for m in memories if _include(m)]
    if game_memory_limit > 0 and retrieval_query is not None:
        from app.core.memory_retrieval import select_relevant

        user_scoped = [m for m in memories if m["scope"] == "user"]
        game_scoped = [m for m in memories if m["scope"] != "user"]
        if len(game_scoped) > game_memory_limit:
            selected = select_relevant(game_scoped, retrieval_query, game_memory_limit)
            kept_ids = {m["id"] for m in selected} | {m["id"] for m in user_scoped}
            memories = [m for m in memories if m["id"] in kept_ids]
    if not memories:
        return ""
    lines = []
    for m in memories:
        if m["scope"] == "session":
            suffix = f" (this playthrough of {m['process']})"
        elif m["scope"] == "game":
            suffix = f" (game: {m['process']}, all playthroughs)"
        else:
            suffix = ""
        lines.append(f"- [{m['id']}] {m['content']}{suffix}")
    return "Known facts about the user (reference the id when removing one):\n" + "\n".join(lines)
