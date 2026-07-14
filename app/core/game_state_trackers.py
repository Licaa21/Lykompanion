"""Per-process (and per-modpack-variant), user-configurable list of fields ("trackers") the
game-state extraction LLM pass fills in for a given foreground process - e.g. Rocket League might
track "1v1 Rank" and "Goals scored this session" instead of the generic RPG-flavored defaults.
Persisted to disk (data/game_state_trackers.json) so customizations survive restarts.

Keyed like game_state_training_data.py: a plain process key for the base game, "process::variant"
for a modpack's own list (a pack's own bootstrap seeds pack-specific trackers - e.g. FTB
StoneBlock 4's Vaults/Echoes/World Engine progress - that would be meaningless noise while a
*vanilla* session of the same process is active, and vice versa). Every key gets a fresh copy of
DEFAULT_TRACKERS the first time it's looked up (a new variant never inherits the base process's
own customized/bootstrapped list - it starts exactly like a brand new process would, then its own
bootstrap is free to replace them), and the user can add/remove/edit trackers per key from there -
except "activity", which always stays present and unmodified, since the extraction pass depends on
it being refreshed every pass and the user asked for it to never be removable/editable."""

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


def _key(process: str, variant: str | None) -> str:
    """Plain process key for the base game; "process::variant" for a modpack's own list. Process
    keys never contain "::", so the namespaces can't collide (same scheme as
    game_state_training_data.py's _key)."""
    if variant and variant.strip():
        return f"{process.lower()}::{variant.strip().lower()}"
    return process.lower()


def _load_all() -> dict[str, list[dict]]:
    if not TRACKERS_PATH.exists():
        return {}
    return json.loads(TRACKERS_PATH.read_text(encoding="utf-8"))


def _save_all(data: dict[str, list[dict]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TRACKERS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_trackers(process: str, variant: str | None = None) -> list[dict]:
    """Returns the tracker list for a process (or one modpack variant of it), seeding it with a
    copy of the defaults (persisted) the first time this key is looked up."""
    data = _load_all()
    key = _key(process, variant)
    if key not in data:
        data[key] = [dict(t) for t in DEFAULT_TRACKERS]
        _save_all(data)
    return data[key]


def set_trackers(process: str, trackers: list[dict], variant: str | None = None) -> list[dict]:
    """Replaces the non-locked trackers for a process (or one modpack variant of it). Missing/
    blank ids are slugified from the label and de-duplicated. The locked "activity" tracker is
    always restored as-is and placed first, regardless of what the caller sent - it can't be
    removed or edited."""
    activity = dict(DEFAULT_TRACKERS[0])
    cleaned: list[dict] = []
    # The extraction response uses tracker ids as top-level JSON keys alongside these reserved
    # keys - a user tracker labeled e.g. "Confidence" must not collide with them (the dedupe
    # suffixing below renames it to confidence_2 instead).
    seen_ids = {
        activity["id"], "confidence", "save_memories", "remove_memory_ids", "divergence_warning",
        "observations", "training_data_update", "proactive_message", "modpack", "modpack_mismatch",
    }
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
                # Whether this tracker is drawn in the native overlay panel. Default on.
                "overlay": t.get("overlay", True) is not False,
            }
        )

    data = _load_all()
    key = _key(process, variant)
    data[key] = [activity] + cleaned
    _save_all(data)
    return data[key]


def reset_trackers(process: str, variant: str | None = None) -> list[dict]:
    data = _load_all()
    key = _key(process, variant)
    data[key] = [dict(t) for t in DEFAULT_TRACKERS]
    _save_all(data)
    return data[key]


def delete_process(process: str) -> None:
    """Drop every tracker list for a process - the base one and every modpack variant's own -
    entirely (used when a tracked game is deleted). Next lookup re-seeds the defaults, so this is
    a full reset that also forgets any customizations."""
    data = _load_all()
    prefix = process.lower()
    keys = [k for k in data if k == prefix or k.startswith(f"{prefix}::")]
    if keys:
        for k in keys:
            data.pop(k, None)
        _save_all(data)
