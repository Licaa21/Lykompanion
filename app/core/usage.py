import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
# Append-only JSONL (one record per line) - the old usage.json format re-read and re-wrote the
# entire (unbounded) array on every single LLM call, which got slower with every request.
USAGE_PATH = _DATA_DIR / "usage.jsonl"
_LEGACY_USAGE_PATH = _DATA_DIR / "usage.json"

_lock = threading.Lock()


def _migrate_legacy() -> None:
    """One-time conversion of the old usage.json array into usage.jsonl."""
    if USAGE_PATH.exists() or not _LEGACY_USAGE_PATH.exists():
        return
    try:
        data = json.loads(_LEGACY_USAGE_PATH.read_text(encoding="utf-8"))
        records = data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        records = []
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_PATH.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    _LEGACY_USAGE_PATH.unlink(missing_ok=True)


def load_usage_records() -> list[dict]:
    with _lock:
        _migrate_legacy()
        if not USAGE_PATH.exists():
            return []
        records = []
        for line in USAGE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn/corrupt line must not take down the whole history
        return records


def record_usage(prompt_tokens: int, completion_tokens: int, cost_usd: float, source: str) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
    }
    with _lock:
        _migrate_legacy()
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with USAGE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def clear_usage() -> None:
    with _lock:
        _LEGACY_USAGE_PATH.unlink(missing_ok=True)
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text("", encoding="utf-8")
