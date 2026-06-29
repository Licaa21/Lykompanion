from app.services.system.system_info import get_system_info

SYSTEM_INFO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_system_info",
            "description": (
                "Check the user's PC specs - OS, CPU, RAM total/available. Use it if they ask "
                "whether their system can run a game, or for troubleshooting performance issues."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def execute_fetch_system_info(_arguments: dict) -> str:
    info = get_system_info()
    return (
        f"OS: {info['os']}\n"
        f"CPU: {info['cpu']} ({info['cpu_cores']} logical cores)\n"
        f"RAM: {info['ram_available_gb']} GB available of {info['ram_total_gb']} GB total"
    )
