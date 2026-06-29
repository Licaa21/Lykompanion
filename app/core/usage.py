import json
from pathlib import Path

USAGE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "usage.json"

_DEFAULT_USAGE = {
    "request_count": 0,
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "total_cost_usd": 0.0,
}


def load_usage() -> dict:
    if not USAGE_PATH.exists():
        return dict(_DEFAULT_USAGE)
    return {**_DEFAULT_USAGE, **json.loads(USAGE_PATH.read_text(encoding="utf-8"))}


def record_usage(prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
    usage = load_usage()
    usage["request_count"] += 1
    usage["total_prompt_tokens"] += prompt_tokens
    usage["total_completion_tokens"] += completion_tokens
    usage["total_cost_usd"] += cost_usd

    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_PATH.write_text(json.dumps(usage, indent=2), encoding="utf-8")
