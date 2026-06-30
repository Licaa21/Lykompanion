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
PENDING_PATH = DATA_DIR / "game_state_pending.json"


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


# Pending processes are persisted so they survive server restarts.
_pending_processes: list[str] = _load(PENDING_PATH)


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


def get_pending_processes() -> list[str]:
    return list(_pending_processes)


def add_pending_process(process: str) -> bool:
    """Adds process to the pending queue if not already present. Returns True if newly added."""
    global _pending_processes
    if process.lower() in {p.lower() for p in _pending_processes}:
        return False
    _pending_processes.append(process)
    _save(PENDING_PATH, _pending_processes)
    return True


def clear_pending_process(process: str | None = None) -> None:
    """Removes a specific process from the pending queue, or clears all if process is None."""
    global _pending_processes
    if process is None:
        _pending_processes = []
    else:
        _pending_processes = [p for p in _pending_processes if p.lower() != process.lower()]
    _save(PENDING_PATH, _pending_processes)
