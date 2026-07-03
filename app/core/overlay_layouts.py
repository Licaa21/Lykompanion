"""Per-game overlay element positions - data/overlay_layouts.json.

Shape: {process: {element_id: {"x": 0.0-1.0, "y": 0.0-1.0}}} where x/y are the element's
top-left corner as fractions of the overlay viewport (resolution-independent). The special
process key "default" is used when no game is being tracked, and is the fallback for games
without a saved layout of their own.
"""

import json
import threading
from pathlib import Path

LAYOUTS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "overlay_layouts.json"

DEFAULT_KEY = "default"

_lock = threading.Lock()


def _load_all() -> dict:
    if not LAYOUTS_PATH.exists():
        return {}
    try:
        return json.loads(LAYOUTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _sanitize(layout: dict) -> dict:
    clean: dict = {}
    for element_id, pos in layout.items():
        if not isinstance(element_id, str) or not isinstance(pos, dict):
            continue
        try:
            x = float(pos["x"])
            y = float(pos["y"])
        except (KeyError, TypeError, ValueError):
            continue
        clean[element_id] = {"x": min(max(x, 0.0), 1.0), "y": min(max(y, 0.0), 1.0)}
    return clean


def get_layout(process: str) -> dict:
    """Layout for a process, falling back to the default layout, then to {} (CSS defaults)."""
    layouts = _load_all()
    key = process.lower()
    return layouts.get(key) or layouts.get(DEFAULT_KEY) or {}


def save_layout(process: str, layout: dict) -> dict:
    clean = _sanitize(layout)
    with _lock:
        layouts = _load_all()
        layouts[process.lower()] = clean
        LAYOUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAYOUTS_PATH.write_text(json.dumps(layouts, indent=2), encoding="utf-8")
    return clean
