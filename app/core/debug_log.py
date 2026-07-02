"""In-memory ring buffer of the last 50 individual LLM API calls and tool executions, for live
debugging via the Debug panel. When debug mode is enabled, entries are also persisted to
data/debug_log.json (capped at 10 MB) so the history survives restarts for post-mortem debugging.

Recording is gated behind settings.debug_mode_enabled (off by default) - entries are kept
full/untruncated, which can be large, so this is opt-in rather than always-on."""

import json
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings

_MAX_ENTRIES = 50
_DISK_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
_DEBUG_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "debug_log.json"

_entries: deque[dict] = deque(maxlen=_MAX_ENTRIES)


def _load_from_disk() -> None:
    if not settings.debug_mode_enabled or not _DEBUG_LOG_PATH.exists():
        return
    try:
        data = json.loads(_DEBUG_LOG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            # Entries on disk are newest-first; appendleft from oldest so newest stays at front.
            for entry in reversed(data):
                _entries.appendleft(entry)
    except Exception:
        pass


def _save_to_disk() -> None:
    try:
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entries = list(_entries)
        content = json.dumps(entries, ensure_ascii=False)
        # Trim oldest entries (tail of the newest-first list) until under the size cap.
        while entries and len(content.encode()) > _DISK_MAX_SIZE_BYTES:
            entries.pop()
            content = json.dumps(entries, ensure_ascii=False)
        _DEBUG_LOG_PATH.write_text(content, encoding="utf-8")
    except Exception:
        pass


_load_from_disk()


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
    if not settings.debug_mode_enabled:
        return
    entry = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "model": model,
        "messages": messages,
        "tools": tools,
        "reply": reply,
        "tool_calls": tool_calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
    }
    _entries.appendleft(entry)
    _save_to_disk()


def record_tool_call(name: str, arguments: dict, result: str, duration_ms: float) -> None:
    """Records a local tool execution (not an LLM API call) so it shows up in the same timeline -
    the chat entry's tool_calls only shows what the model asked for, not what the tool returned."""
    record_request(
        source=f"tool:{name}",
        model="(tool call)",
        messages=[{"role": "tool", "content": f"{name}({json.dumps(arguments)})"}],
        tools=None,
        reply=result,
        tool_calls=None,
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=0.0,
        duration_ms=duration_ms,
    )


def get_requests() -> list[dict]:
    return list(_entries)
