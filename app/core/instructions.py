from pathlib import Path

INSTRUCTIONS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "custom_instructions.txt"


def load_custom_instructions() -> str:
    if not INSTRUCTIONS_PATH.exists():
        return ""
    return INSTRUCTIONS_PATH.read_text(encoding="utf-8")


def save_custom_instructions(text: str) -> None:
    INSTRUCTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    INSTRUCTIONS_PATH.write_text(text, encoding="utf-8")
