"""Per-model OpenRouter provider-routing preferences (which underlying providers a model is
allowed to route through, sort order, fallback behavior, max price) - keyed by model id, since
the same model may be picked for multiple features (main chat, memory extraction, game-state) and
a routing preference is really a property of the model, not the feature using it.

Follows the same single-JSON-file-store pattern as app/core/reminders.py."""

import json
import threading
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent.parent / "data" / "provider_routing.json"

_lock = threading.Lock()

# In-memory mirror of the file - looked up on every chat completion call (client.py), so a disk
# read per LLM call would add latency to the reply path for no benefit (file only changes via the
# Settings UI, which already re-reads after writing).
_cache: dict[str, dict] | None = None

_DEFAULT: dict = {
    "only": [],
    "sort": None,
    "allow_fallbacks": True,
    "max_price_prompt": None,
    "max_price_completion": None,
}


def _load() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    if not PATH.exists():
        _cache = {}
        return _cache
    _cache = json.loads(PATH.read_text(encoding="utf-8"))
    return _cache


def _save(data: dict[str, dict]) -> None:
    global _cache
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _cache = data


def get_all() -> dict[str, dict]:
    return _load()


def get_for_model(model_id: str) -> dict:
    return {**_DEFAULT, **_load().get(model_id, {})}


def set_for_model(model_id: str, config: dict) -> dict:
    with _lock:
        data = _load()
        merged = {**_DEFAULT, **config}
        data[model_id] = merged
        _save(data)
        return merged
