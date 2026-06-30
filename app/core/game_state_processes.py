"""User-managed allow/deny lists and the pending-approval slot for passive game-state OCR,
persisted to disk (unlike the ephemeral live snapshot in app/core/game_state.py) so they survive
restarts and give the user visibility/control over what the poller is allowed to OCR.

Any foreground process that isn't in the hardcoded non-game denylist (see game_state_extraction.py)
and isn't already on the whitelist is treated as "pending" - the poller won't OCR it until the user
explicitly allows or blacklists it from the UI."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
BLACKLIST_PATH = DATA_DIR / "game_state_blacklist.json"
WHITELIST_PATH = DATA_DIR / "game_state_whitelist.json"

_pending_process: str | None = None


def _load(path: Path) -> list[str]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, items: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _add(path: Path, process: str) -> None:
    process = process.strip()
    if not process:
        return
    items = _load(path)
    if process.lower() not in {p.lower() for p in items}:
        items.append(process)
        _save(path, items)


def _remove(path: Path, process: str) -> None:
    items = _load(path)
    filtered = [p for p in items if p.lower() != process.lower()]
    if len(filtered) != len(items):
        _save(path, filtered)


def _contains(path: Path, process: str) -> bool:
    return process.lower() in {p.lower() for p in _load(path)}


def load_blacklist() -> list[str]:
    return _load(BLACKLIST_PATH)


def add_to_blacklist(process: str) -> None:
    _add(BLACKLIST_PATH, process)
    clear_pending_process(process)


def remove_from_blacklist(process: str) -> None:
    _remove(BLACKLIST_PATH, process)


def is_blacklisted(process: str) -> bool:
    return _contains(BLACKLIST_PATH, process)


def load_whitelist() -> list[str]:
    return _load(WHITELIST_PATH)


def add_to_whitelist(process: str) -> None:
    _add(WHITELIST_PATH, process)
    clear_pending_process(process)


def remove_from_whitelist(process: str) -> None:
    _remove(WHITELIST_PATH, process)


def is_whitelisted(process: str) -> bool:
    return _contains(WHITELIST_PATH, process)


def get_pending_process() -> str | None:
    return _pending_process


def set_pending_process(process: str) -> None:
    global _pending_process
    _pending_process = process


def clear_pending_process(process: str | None = None) -> None:
    """Clears the pending slot. If `process` is given, only clears when it matches (so resolving
    one process's approval doesn't accidentally drop an unrelated pending entry)."""
    global _pending_process
    if process is None or (_pending_process and _pending_process.lower() == process.lower()):
        _pending_process = None
