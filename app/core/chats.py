import json
import threading
from pathlib import Path

CHATS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "chats.json"

# Serializes read-modify-write cycles on chats.json (per-chat upserts/deletes).
_lock = threading.Lock()


def load_chats() -> list[dict]:
    if not CHATS_PATH.exists():
        return []
    return json.loads(CHATS_PATH.read_text(encoding="utf-8"))


def save_chats(chats: list[dict]) -> None:
    CHATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHATS_PATH.write_text(json.dumps(chats, indent=2), encoding="utf-8")


def upsert_chat(chat: dict) -> None:
    """Replaces the stored chat with the same id, or inserts a new one at the front (newest
    first, matching the sidebar order). Lets the frontend save one chat per message instead of
    re-uploading the entire history of every conversation on every message."""
    chat_id = chat.get("id")
    with _lock:
        chats = load_chats()
        for i, existing in enumerate(chats):
            if existing.get("id") == chat_id:
                chats[i] = chat
                break
        else:
            chats.insert(0, chat)
        save_chats(chats)


def delete_chat(chat_id: str) -> bool:
    with _lock:
        chats = load_chats()
        filtered = [c for c in chats if c.get("id") != chat_id]
        if len(filtered) == len(chats):
            return False
        save_chats(filtered)
        return True
