import json
from datetime import datetime, timezone
from pathlib import Path

USAGE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "usage.json"


def load_usage_records() -> list[dict]:
    if not USAGE_PATH.exists():
        return []
    data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def save_usage_records(records: list[dict]) -> None:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")


def record_usage(prompt_tokens: int, completion_tokens: int, cost_usd: float, source: str) -> None:
    records = load_usage_records()
    records.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost_usd,
        }
    )
    save_usage_records(records)


def clear_usage() -> None:
    save_usage_records([])
