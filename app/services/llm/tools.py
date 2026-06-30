from app.core import memory
from app.services.system.processes import get_foreground_process_name

MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "Save a fact to long-term memory, persisted across sessions and shown back to you as "
                "'Known facts about the user' in future conversations. Call this proactively and silently "
                "the moment you learn something worth remembering, without being asked to 'remember' or "
                "'save' it - e.g. how the user wants to be addressed (name/nickname), what game they're "
                "playing, their build/class/progress, or any stated preference (difficulty, spoilers, tone)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact to remember, written as a short standalone sentence.",
                    },
                    "game_specific": {
                        "type": "boolean",
                        "description": (
                            "True if this fact is specific to the game currently being played (e.g. character "
                            "build, quest progress, in-game relationships) and should only resurface while that "
                            "same game is active. False/omitted for general facts (name, life context, "
                            "preferences that hold across games)."
                        ),
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
]


def execute_tool_call(name: str, arguments: dict) -> str:
    if name == "save_memory":
        content = (arguments.get("content") or "").strip()
        if not content:
            return "Nothing to save: content was empty."
        process = get_foreground_process_name() if arguments.get("game_specific") else None
        entry = memory.add_memory(content, process=process)
        return f"Saved memory [{entry['id']}]: {entry['content']}"

    if name == "remove_memory":
        memory_id = arguments.get("memory_id") or ""
        removed = memory.remove_memory(memory_id)
        return "Memory removed." if removed else "No memory found with that id."

    return f"Unknown tool: {name}"
