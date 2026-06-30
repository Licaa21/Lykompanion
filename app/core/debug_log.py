"""In-memory ring buffer of the last 10 individual LLM API calls, for live debugging via the
Debug panel. Restart-scoped on purpose (not persisted) - this reflects "what just happened",
not a historical record (that's what app/core/usage.py is for)."""

import uuid
from collections import deque
from datetime import datetime, timezone

_MAX_ENTRIES = 10
_MAX_FIELD_LEN = 2000

_entries: deque[dict] = deque(maxlen=_MAX_ENTRIES)


def _truncate(value):
    if isinstance(value, str) and len(value) > _MAX_FIELD_LEN:
        return value[:_MAX_FIELD_LEN] + f"... [truncated, {len(value)} chars total]"
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value]
    return value


def record_request(
    *,
    source: str,
    model: str,
    messages: list[dict],
    tools: list[str] | None,
    reply: str | None,
    tool_calls: list[dict] | None,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    duration_ms: float | None,
) -> None:
    entry = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "model": model,
        "messages": _truncate(messages),
        "tools": tools,
        "reply": _truncate(reply),
        "tool_calls": _truncate(tool_calls),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
    }
    _entries.appendleft(entry)


def get_requests() -> list[dict]:
    return list(_entries)
