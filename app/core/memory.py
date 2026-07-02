import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

MEMORY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "memory.json"

# Serializes read-modify-write cycles - two concurrent save_*_memory tool calls (they run via
# asyncio.gather) or a background extraction pass must not overwrite each other's entry.
_lock = threading.Lock()


def load_memories() -> list[dict]:
    if not MEMORY_PATH.exists():
        return []
    return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))


def save_memories(memories: list[dict]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(memories, indent=2), encoding="utf-8")


def add_memory(content: str, process: str | None = None, session_id: str | None = None) -> dict:
    with _lock:
        memories = load_memories()
        entry = {
            "id": uuid.uuid4().hex[:8],
            "content": content,
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
                # saved_at is intentionally not updated — it marks the original creation time,
                # which is what rollback uses to find memories from a specific time window.
                save_memories(memories)
                return m
        return None


def remove_memory(memory_id: str) -> bool:
    with _lock:
        memories = load_memories()
        filtered = [m for m in memories if m["id"] != memory_id]
        if len(filtered) == len(memories):
            return False
        save_memories(filtered)
        return True


def format_memories_for_prompt(active_process: str | None = None, active_session_id: str | None = None) -> str:
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
    if not memories:
        return ""
    lines = []
    for m in memories:
        suffix = f" (game: {m['process']})" if m.get("process") else ""
        lines.append(f"- [{m['id']}] {m['content']}{suffix}")
    return "Known facts about the user (reference the id when removing one):\n" + "\n".join(lines)
