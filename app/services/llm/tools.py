from datetime import datetime, timedelta, timezone

from app.core import game_state, memory

MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_user_memory",
            "description": (
                "Save a fact about the user that is true regardless of any game: their name/nickname, "
                "age, life context, how they want to be addressed, recurring cross-game preferences "
                "(genres, playstyle patterns, difficulty habits), or anything they reveal about themselves "
                "as a person. These facts are always shown to you no matter what game is running. "
                "Call this proactively and silently the moment you learn something worth remembering."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact to remember, written as a short standalone sentence.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_game_memory",
            "description": (
                "Save a fact that is specific to the game currently being tracked, but true across ALL "
                "playthroughs of it — e.g. the user's preferred class type for this game, how they "
                "typically approach it, game-wide meta-preferences. Ask yourself: would this still be true "
                "if they wiped their save and started a new game? If yes, use this tool. "
                "Only call this when a game is actively being tracked right now."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact to remember, written as a short standalone sentence.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_session_memory",
            "description": (
                "Save a fact specific to the user's current playthrough only — character level, quest "
                "progress, decisions made this run, in-game relationships built so far. These facts belong "
                "to this session and would NOT carry over to a new playthrough. "
                "Only call this when a game and session are actively being tracked right now."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact to remember, written as a short standalone sentence.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_memory",
            "description": (
                "Remove a previously saved fact by its id (from the 'Known facts about the user' list). "
                "Call this proactively whenever the latest message invalidates a known fact - e.g. the user "
                "quit, finished, or uninstalled a game you have facts about, corrected something you saved, "
                "or moved past a point you'd noted as their current obstacle - even if they didn't ask you "
                "to 'remove' or 'forget' anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The id of the memory entry to remove, shown in the known facts list.",
                    }
                },
                "required": ["memory_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rollback_session_memories",
            "description": (
                "Remove session memories saved during a recent time window, after the player confirms they "
                "experienced a game crash or loaded an older save and lost progress. Only removes memories "
                "from the current active game session — general user facts and other game sessions are "
                "never touched. Call this only AFTER the player has confirmed what happened and how much "
                "progress they lost. Do NOT call it preemptively just because a divergence was detected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_lost": {
                        "type": "number",
                        "description": (
                            "How many hours of progress the player lost. Derive from what they said "
                            "(e.g. '2 hours', 'since this afternoon' → approximate hours elapsed). "
                            "When uncertain, ask before calling."
                        ),
                    }
                },
                "required": ["hours_lost"],
            },
        },
    },
]


def _resolve_tracked_game() -> tuple[str | None, str | None]:
    """Returns (process, session_id) from the currently tracked game state, or (None, None)."""
    gs = game_state.get_game_state()
    if not gs:
        return None, None
    return gs["process"], gs.get("session_id")


def _save_via_remember(content: str, scope: str) -> str:
    """Shared handler for the three save tools - all placement decisions live in
    memory.remember(), including the degradation rule when the requested scope can't be
    honored (no tracked game / no active session)."""
    content = (content or "").strip()
    if not content:
        return "Nothing to save: content was empty."
    process, session_id = _resolve_tracked_game()
    entry = memory.remember(content, scope, process=process, session_id=session_id)
    if entry is None:
        return "Not saved: an identical fact is already in memory."
    applied = entry["scope"]
    if applied == "session":
        label = f"session memory (this playthrough of {entry['process']})"
    elif applied == "game":
        label = f"game memory (game: {entry['process']}, all playthroughs)"
    else:
        label = "user memory"
    note = "" if applied == scope else f" (no game/session was being tracked, so this was saved as a general user fact instead - this is final, do not retry or remove it)"
    return f"Saved {label} [{entry['id']}]: {entry['content']}{note}"


def execute_tool_call(name: str, arguments: dict) -> str:
    if name == "save_user_memory":
        return _save_via_remember(arguments.get("content"), "user")

    if name == "save_game_memory":
        return _save_via_remember(arguments.get("content"), "game")

    if name == "save_session_memory":
        return _save_via_remember(arguments.get("content"), "session")

    if name == "remove_memory":
        memory_id = arguments.get("memory_id") or ""
        removed = memory.remove_memory(memory_id)
        return "Memory removed." if removed else "No memory found with that id."

    if name == "rollback_session_memories":
        hours_lost = arguments.get("hours_lost")
        if not isinstance(hours_lost, (int, float)) or hours_lost <= 0:
            return "Invalid hours_lost value — must be a positive number."
        gs = game_state.get_game_state()
        if not gs:
            return "No game is currently being tracked — nothing to roll back."
        process = gs["process"]
        session_id = gs.get("session_id")
        if not session_id:
            return "No active session found for the current game."
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_lost)
        all_memories = memory.load_memories()
        to_remove = []
        for m in all_memories:
            if ((m.get("process") or "").lower() == process.lower()
                    and m.get("session_id") == session_id
                    and m.get("saved_at")):
                try:
                    if datetime.fromisoformat(m["saved_at"]) >= cutoff:
                        to_remove.append(m)
                except (ValueError, TypeError):
                    pass
        if not to_remove:
            return f"No session memories found from the last {hours_lost:.1f} hour(s) — nothing removed."
        for m in to_remove:
            memory.remove_memory(m["id"])
        removed_list = "\n".join(f"- {m['content']}" for m in to_remove)
        n = len(to_remove)
        return f"Rolled back {n} session {'memory' if n == 1 else 'memories'} from the last {hours_lost:.1f} hour(s):\n{removed_list}"

    return f"Unknown tool: {name}"
