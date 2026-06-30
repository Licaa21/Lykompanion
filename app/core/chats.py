import json
from pathlib import Path

CHATS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "chats.json"


def load_chats() -> list[dict]:
    if not CHATS_PATH.exists():
        return []
    return json.loads(CHATS_PATH.read_text(encoding="utf-8"))


def save_chats(chats: list[dict]) -> None:
    CHATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHATS_PATH.write_text(json.dumps(chats, indent=2), encoding="utf-8")
