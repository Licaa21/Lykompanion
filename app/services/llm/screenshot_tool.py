from app.services.screenshot.capture import capture_monitor_b64, get_active_monitor_index, list_monitors

SCREENSHOT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": (
                "Capture a screenshot of one of the user's monitors to see what's currently on their "
                "screen. Defaults to their active/focused monitor if no monitor is given. If the user "
                "has more than one monitor, they're listed in the system prompt as 'Available monitors' "
                "- call this again with a different monitor index if the active one turns out to not "
                "show anything game-relevant (e.g. it's a browser, or the game is on another screen)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "monitor": {
                        "type": "integer",
                        "description": "1-based monitor index to capture. Omit to use the active monitor.",
                    }
                },
            },
        },
    },
]


def format_monitors_for_prompt() -> str:
    monitors = list_monitors()
    if len(monitors) <= 1:
        return ""
    active = get_active_monitor_index()
    lines = [f"- {m['index']}: {m['width']}x{m['height']}{' (active)' if m['index'] == active else ''}" for m in monitors]
    return "Available monitors:\n" + "\n".join(lines)


def execute_take_screenshot(arguments: dict) -> tuple[str, list[dict]]:
    monitor = arguments.get("monitor")
    monitor_index = monitor if isinstance(monitor, int) and monitor > 0 else None

    image_b64 = capture_monitor_b64(monitor_index)
    resolved_index = monitor_index or get_active_monitor_index()

    tool_message = f"Captured screenshot from monitor {resolved_index}."
    image_message = {
        "role": "user",
        "content": [
            {"type": "text", "text": f"Screenshot from monitor {resolved_index}:"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ],
    }
    return tool_message, [image_message]
