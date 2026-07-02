"""Per-process training data: a single living reference document self-maintained by the
game-state extraction pass via its "training_data_update" output (see app/services/llm/
game_state_extraction.py) - e.g. explaining that a string like 0-2:12-2 on a Rocket League HUD
means home score - time left - away score. The pass revises the whole document rather than
appending a new entry, so it stays a coherent reference instead of a growing pile of notes. Persisted to disk (data/
game_state_training_data.json) so it accumulates across sessions and gets fed into every future
extraction pass for that process. User-editable from Settings > Game Awareness > Training Data."""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TRAINING_DATA_PATH = DATA_DIR / "game_state_training_data.json"
# Earlier filename/shape, kept only as a one-time read fallback for existing installs.
_OLD_GLOSSARY_PATH = DATA_DIR / "game_state_glossary.json"


def _load_all() -> dict[str, dict]:
    path = TRAINING_DATA_PATH if TRAINING_DATA_PATH.exists() else _OLD_GLOSSARY_PATH
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))

    # One-time migration from the oldest format (a list of {"id", "note"} entries per process,
    # appended to over time) to a single document per process - merges old notes into one
    # document instead of discarding them. Only touches processes still in that old shape; a
    # process already migrated (a dict) is left as-is. Not written back here - callers that
    # mutate (set_training_data) persist the migrated shape (and the new filename) as a side
    # effect of their own save.
    migrated = {}
    for process, value in raw.items():
        if isinstance(value, list):
            content = "\n\n".join(entry.get("note", "") for entry in value if isinstance(entry, dict))
            migrated[process] = {"content": content, "updated_at": datetime.now(timezone.utc).isoformat()}
        elif isinstance(value, dict):
            migrated[process] = value
    return migrated


def _save_all(data: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TRAINING_DATA_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_training_data(process: str) -> str:
    return _load_all().get(process.lower(), {}).get("content", "")


def set_training_data(process: str, content: str) -> str:
    content = content.strip()
    data = _load_all()
    data[process.lower()] = {"content": content, "updated_at": datetime.now(timezone.utc).isoformat()}
    _save_all(data)
    return content


def format_training_data_for_prompt(process: str) -> str:
    content = get_training_data(process)
    if not content:
        return ""
    return f"Known training data for this game (notes from your own earlier passes - trust this over guesses when it applies):\n{content}"
