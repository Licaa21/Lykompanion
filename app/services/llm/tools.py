from app.core import memory

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
                    }
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
        entry = memory.add_memory(content)
        return f"Saved memory [{entry['id']}]: {entry['content']}"

    if name == "remove_memory":
        memory_id = arguments.get("memory_id") or ""
        removed = memory.remove_memory(memory_id)
        return "Memory removed." if removed else "No memory found with that id."

    return f"Unknown tool: {name}"
