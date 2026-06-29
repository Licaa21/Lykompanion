import json
import uuid
from pathlib import Path

MEMORY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "memory.json"


def load_memories() -> list[dict]:
    if not MEMORY_PATH.exists():
        return []
    return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))


def save_memories(memories: list[dict]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(memories, indent=2), encoding="utf-8")


def add_memory(content: str) -> dict:
    memories = load_memories()
    entry = {"id": uuid.uuid4().hex[:8], "content": content}
    memories.append(entry)
    save_memories(memories)
    return entry


def update_memory(memory_id: str, content: str) -> dict | None:
    memories = load_memories()
    for memory in memories:
        if memory["id"] == memory_id:
            memory["content"] = content
            save_memories(memories)
            return memory
    return None


def remove_memory(memory_id: str) -> bool:
    memories = load_memories()
    filtered = [m for m in memories if m["id"] != memory_id]
    if len(filtered) == len(memories):
        return False
    save_memories(filtered)
    return True


def format_memories_for_prompt() -> str:
    memories = load_memories()
    if not memories:
        return ""
    lines = [f"- [{m['id']}] {m['content']}" for m in memories]
    return "Known facts about the user (reference the id when removing one):\n" + "\n".join(lines)
