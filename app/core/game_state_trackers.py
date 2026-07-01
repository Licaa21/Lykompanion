"""Per-process, user-configurable list of fields ("trackers") the game-state extraction LLM pass
fills in for a given foreground process - e.g. Rocket League might track "1v1 Rank" and "Goals
scored this session" instead of the generic RPG-flavored defaults. Persisted to disk (unlike the
ephemeral live snapshot in app/core/game_state.py) so customizations survive restarts.

Every process gets a copy of DEFAULT_TRACKERS the first time it's looked up, and the user can
add/remove/edit trackers per process from there - except "activity", which always stays present
and unmodified, since the extraction pass depends on it being refreshed every pass and the user
asked for it to never be removable/editable."""

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TRACKERS_PATH = DATA_DIR / "game_state_trackers.json"

ACTIVITY_TRACKER_ID = "activity"

DEFAULT_TRACKERS: list[dict] = [
    {
        "id": ACTIVITY_TRACKER_ID,
        "label": "Current Activity",
        "description": (
            "A few words, not a sentence - what's currently on screen/what the user is doing right "
            "now, not just combat/quest stuff. Covers menus, settings, loading screens, cutscenes, "
            "dialogue with a specific character, multiplayer matches, character creation, inventory "
            'management, anything. E.g. "Navigating the settings menu", "In dialogue with '
            'Shadowheart", "Playing a ranked match", "Browsing the in-game shop". Never a remark '
            "about OCR quality/readability - that never belongs here."
        ),
        "locked": True,
    },
    {
        "id": "location",
        "label": "Location",
        "description": "Where the character currently is (zone/area/room name), if visible.",
        "locked": False,
    },
    {
        "id": "quest",
        "label": "Quest",
        "description": "The current active quest/objective text, if visible.",
        "locked": False,
    },
    {
        "id": "character",
        "label": "Character",
        "description": "The player's character name/class/race, if visible.",
        "locked": False,
    },
    {
        "id": "notable_choice",
        "label": "Recent Choice",
        "description": (
            "A significant dialogue choice or decision the user just made, if the OCR text shows one "
            "(e.g. a dialogue option that was just selected)."
        ),
        "locked": False,
    },
    {
        "id": "known_stats",
        "label": "Known Stats",
        "description": (
            "Notable stats/currency/resources/ranks visible on screen (e.g. gold, ammo, resources, "
            "rank/tier, level)."
        ),
        "locked": False,
    },
    {
        "id": "game_completion",
        "label": "Game Completion (estimated)",
        "description": (
            'A rough estimate of how far through the game/campaign the player is, if inferable '
            '(e.g. "~40%, mid-game").'
        ),
        "locked": False,
    },
]


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return slug or "tracker"


def _load_all() -> dict[str, list[dict]]:
    if not TRACKERS_PATH.exists():
        return {}
    return json.loads(TRACKERS_PATH.read_text(encoding="utf-8"))


def _save_all(data: dict[str, list[dict]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TRACKERS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_trackers(process: str) -> list[dict]:
    """Returns the tracker list for a process, seeding it with a copy of the defaults (persisted)
    the first time this process is looked up."""
    data = _load_all()
    key = process.lower()
    if key not in data:
        data[key] = [dict(t) for t in DEFAULT_TRACKERS]
        _save_all(data)
    return data[key]


def set_trackers(process: str, trackers: list[dict]) -> list[dict]:
    """Replaces the non-locked trackers for a process. Missing/blank ids are slugified from the
    label and de-duplicated. The locked "activity" tracker is always restored as-is and placed
    first, regardless of what the caller sent - it can't be removed or edited."""
    activity = dict(DEFAULT_TRACKERS[0])
    cleaned: list[dict] = []
    seen_ids = {activity["id"]}
    for t in trackers:
        label = (t.get("label") or "").strip()
        if not label:
            continue
        tid = (t.get("id") or "").strip() or _slugify(label)
        if tid in seen_ids:
            base, n = tid, 2
            while f"{base}_{n}" in seen_ids:
                n += 1
            tid = f"{base}_{n}"
        seen_ids.add(tid)
        cleaned.append(
            {
                "id": tid,
                "label": label,
                "description": (t.get("description") or "").strip(),
                "locked": False,
            }
        )

    data = _load_all()
    data[process.lower()] = [activity] + cleaned
    _save_all(data)
    return data[process.lower()]


def reset_trackers(process: str) -> list[dict]:
    data = _load_all()
    data[process.lower()] = [dict(t) for t in DEFAULT_TRACKERS]
    _save_all(data)
    return data[process.lower()]
