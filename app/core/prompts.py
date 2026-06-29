from datetime import datetime
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load a prompt template by filename stem from app/prompts/."""
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").strip()


def current_datetime_context() -> str:
    """Current local date/time, for resolving relative references ("today", "tomorrow") and
    general time-of-day awareness. Computed fresh on each call - never cached like load_prompt."""
    now = datetime.now()
    return f"Current date and time: {now.strftime('%A, %B %d, %Y, %H:%M')} (user's local time)."
