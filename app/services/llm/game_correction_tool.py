"""Lets the chat model actually fix a wrong tracked-game title or modpack/variant tag when the
user corrects it in conversation, instead of only saving the correction as a memory fact (which
doesn't touch the title shown in the UI, the modpack tag, or the training-data notes/trackers
seeded under the wrong name). Both tools force a training-data + tracker refresh under the
corrected name/pack, since notes and trackers seeded under a wrong name are worse than none (see
game_knowledge_bootstrap.md)."""

from app.core import game_art, game_state
from app.services.llm import game_knowledge_bootstrap, variant_detection

GAME_CORRECTION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "correct_game_title",
            "description": (
                "Correct the currently tracked game's title when it's wrong - e.g. still showing "
                "a raw process name like 'javaw' instead of the real game. Use this INSTEAD of "
                "(or in addition to) saving the correction as a memory whenever the user tells "
                "you the game's real name: saving a memory alone does not fix the title shown in "
                "the UI, used for cover art lookup, or the training-data notes/trackers seeded "
                "under the wrong name - this tool does all of that, including regenerating "
                "trackers for the corrected game."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The game's real, official title."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correct_game_modpack",
            "description": (
                "Correct the currently tracked game's modpack/variant tag when it's wrong or "
                "missing - e.g. wrongly showing the base game's own name as if it were a pack, or "
                "missing a real pack (like 'FTB StoneBlock 4') entirely. Use this INSTEAD of (or "
                "in addition to) saving the correction as a memory whenever the user tells you "
                "the real modpack: saving a memory alone does not fix the tag shown in the UI, "
                "retarget the training-data notes, or regenerate trackers for the pack - this "
                "tool does all of that. Pass an empty string to clear the tag entirely (mark this "
                "session as vanilla, no modpack)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "modpack": {
                        "type": "string",
                        "description": "The real modpack/overhaul name, or an empty string to clear it (vanilla).",
                    },
                },
                "required": ["modpack"],
            },
        },
    },
]


async def execute_correct_game_title(arguments: dict) -> str:
    title = (arguments.get("title") or "").strip()
    if not title:
        return "No title given."
    gs = game_state.get_game_state()
    if not gs:
        return "No game is currently being tracked - nothing to correct."
    process = gs["process"]

    # A repeat correction to the same title that's already on file is a no-op, not a fresh
    # trigger - resetting trackers/training-data again here would just race whatever an earlier
    # call already has in flight, for no actual change.
    if game_art.get_display_title(process).strip().lower() == title.lower():
        return f"The title is already \"{title}\" - nothing to correct."

    game_art.set_title_override(process, title)
    variant = (gs.get("variant") or "").strip() or None
    # Trackers are shared per-process, not per-variant - when a variant is active, let its own
    # refresh below own the tracker reset/regeneration instead of also resetting it here, or the
    # two bootstrap calls race over the same tracker list (observed live: 2026-07-13).
    game_knowledge_bootstrap.force_refresh_base(process, reset_trackers=not variant)
    if variant:
        game_knowledge_bootstrap.force_refresh_variant(process, title, variant)

    return (
        f"Corrected the tracked game's title to \"{title}\" and queued a training-data + tracker "
        "refresh " + (f"for it and its \"{variant}\" modpack." if variant else "under the corrected name.")
    )


async def execute_correct_game_modpack(arguments: dict) -> str:
    if "modpack" not in arguments:
        return "No modpack value given."
    modpack = (arguments.get("modpack") or "").strip()
    gs = game_state.get_game_state()
    if not gs:
        return "No game is currently being tracked - nothing to correct."
    process = gs["process"]

    current_variant = (gs.get("variant") or "").strip()
    if not modpack:
        if not current_variant:
            return "This session is already vanilla (no modpack tag) - nothing to correct."
        session_id = gs.get("session_id")
        if session_id:
            game_state.set_session_variant(process, session_id, None)
            # The session was likely auto-named after the pack when it was first tagged
            # (variant_detection.py's apply_detected_variant) - clearing the tag without also
            # reverting a name that still literally matches it would leave a "vanilla" session
            # stuck displaying the old modpack's name.
            if (game_state.get_session_name(process, session_id) or "").strip().lower() == current_variant.lower():
                game_state.rename_session(process, session_id, "Default")
        return "Cleared the modpack tag - this session is now tracked as vanilla."

    # Same reasoning as the title's no-op guard above - a repeat correction to the same modpack
    # already active shouldn't re-trigger a reset/refresh race against whatever's in flight.
    if current_variant.lower() == modpack.lower():
        return f"The modpack is already set to \"{modpack}\" - nothing to correct."

    base_title = game_art.get_display_title(process)
    # Clear the (possibly wrong/stale) variant doc + retry-attempt cache BEFORE switching the
    # session, so apply_detected_variant's own trailing bootstrap call actually re-seeds it
    # instead of finding a document already there and no-op'ing.
    game_knowledge_bootstrap.force_refresh_variant(process, base_title, modpack)
    variant_detection.apply_detected_variant(process, modpack, source="user correction")

    return f"Set the modpack to \"{modpack}\" and queued a training-data + tracker refresh for it."
