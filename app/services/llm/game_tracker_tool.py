"""Lets the chat model add/remove/edit the tracked fields (see app/core/game_state_trackers.py)
for the currently tracked game in conversation ("also keep an eye on my combo count") - the same
customization the Gaming Journal's Journal Settings -> Trackers page exposes. Scoped to the
current process + active modpack variant, not the individual session/profile: trackers are shared
across every profile of that same scope (see 2026-07-14's per-variant tracker fix), so a change
here applies to every playthrough of this pack (or every vanilla playthrough, if none is active),
not just the one currently open."""

from app.core import game_state, game_state_trackers

GAME_TRACKER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_game_tracker",
            "description": (
                "Add a new field for the game-state OCR pass to watch for in the currently "
                "tracked game (e.g. \"Combo Count\", \"Ammo Left\") - use when the player asks "
                "you to start tracking something the built-in fields don't cover. Scoped to the "
                "current modpack/variant (or the base game if none is active) - shared across "
                "every profile of that same scope, not just this one playthrough."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Short human-readable name for the field."},
                    "description": {
                        "type": "string",
                        "description": "What the model should look for on screen and how to read it.",
                    },
                },
                "required": ["label", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_game_tracker",
            "description": (
                "Stop tracking a field (by its current label) for the currently tracked game - "
                "use when the player asks you to stop watching for something. The built-in "
                "\"Current Activity\" field can't be removed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "The tracked field's current label."},
                },
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_game_tracker",
            "description": (
                "Rename a tracked field and/or change what it looks for, for the currently "
                "tracked game - use when the player wants an existing field renamed or refined "
                "rather than removed and re-added (renaming keeps its accumulated value)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "The tracked field's current label."},
                    "new_label": {"type": "string", "description": "New label, or omit to keep the current one."},
                    "new_description": {
                        "type": "string",
                        "description": "New description, or omit to keep the current one.",
                    },
                },
                "required": ["label"],
            },
        },
    },
]


def _current_scope() -> tuple[str, str | None] | None:
    gs = game_state.get_game_state()
    if not gs:
        return None
    return gs["process"], gs.get("variant") or None


def _find_tracker(trackers: list[dict], label: str) -> dict | None:
    target = label.strip().lower()
    return next((t for t in trackers if t["label"].strip().lower() == target), None)


async def execute_add_game_tracker(arguments: dict) -> str:
    label = (arguments.get("label") or "").strip()
    description = (arguments.get("description") or "").strip()
    if not label:
        return "No label given."
    scope = _current_scope()
    if not scope:
        return "No game is currently being tracked - nothing to add a tracker to."
    process, variant = scope

    current = game_state_trackers.get_trackers(process, variant=variant)
    if _find_tracker(current, label):
        return f"A tracker labeled \"{label}\" already exists - nothing to add."

    non_locked = [t for t in current if not t.get("locked")]
    non_locked.append({"label": label, "description": description})
    game_state_trackers.set_trackers(process, non_locked, variant=variant)
    return f"Now tracking \"{label}\"" + (f" for the \"{variant}\" playthroughs." if variant else " for this game.")


async def execute_remove_game_tracker(arguments: dict) -> str:
    label = (arguments.get("label") or "").strip()
    if not label:
        return "No label given."
    scope = _current_scope()
    if not scope:
        return "No game is currently being tracked - nothing to remove."
    process, variant = scope

    current = game_state_trackers.get_trackers(process, variant=variant)
    target = _find_tracker(current, label)
    if not target:
        return f"No tracker labeled \"{label}\" found."
    if target.get("locked"):
        return "\"Current Activity\" is a built-in field and can't be removed."

    remaining = [t for t in current if not t.get("locked") and t is not target]
    game_state_trackers.set_trackers(process, remaining, variant=variant)
    return f"Stopped tracking \"{target['label']}\"."


async def execute_update_game_tracker(arguments: dict) -> str:
    label = (arguments.get("label") or "").strip()
    if not label:
        return "No label given."
    scope = _current_scope()
    if not scope:
        return "No game is currently being tracked - nothing to update."
    process, variant = scope

    current = game_state_trackers.get_trackers(process, variant=variant)
    target = _find_tracker(current, label)
    if not target:
        return f"No tracker labeled \"{label}\" found."
    if target.get("locked"):
        return "\"Current Activity\" is a built-in field and can't be edited."

    new_label = (arguments.get("new_label") or "").strip() or target["label"]
    new_description_raw = arguments.get("new_description")
    new_description = (
        new_description_raw.strip() if isinstance(new_description_raw, str) and new_description_raw.strip()
        else target["description"]
    )

    updated = [
        {
            "id": t["id"], "label": new_label if t is target else t["label"],
            "description": new_description if t is target else t["description"],
            "overlay": t.get("overlay", True),
        }
        for t in current if not t.get("locked")
    ]
    game_state_trackers.set_trackers(process, updated, variant=variant)
    return f"Renamed \"{label}\" to \"{new_label}\"." if new_label.lower() != label.lower() else f"Updated the \"{label}\" tracker."
