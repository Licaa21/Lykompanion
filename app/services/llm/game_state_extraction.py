import asyncio
import json
import logging
import sys

from app.core import game_state
from app.core import game_state_processes
from app.core import memory as memory_store
from app.core.config import settings
from app.core.prompts import load_prompt
from app.services.llm.client import chat_completion
from app.services.ocr import tesseract_ocr
from app.services.screenshot.capture import capture_monitor_image
from app.services.system.processes import get_foreground_process_name

logger = logging.getLogger(__name__)

# Foreground processes that are clearly not games, so the poller skips OCR/LLM work for them.
# Best-effort heuristic, not exhaustive - see TODO.md for smarter detection ideas.
_NON_GAME_PROCESSES = {
    "explorer.exe",
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "discord.exe",
    "code.exe",
    "windowsterminal.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "python.exe",
    "pythonw.exe",
    "spotify.exe",
    "slack.exe",
    "steam.exe",
    "steamwebhelper.exe",
}

_last_process: str | None = None
_last_ocr_text: str | None = None


async def extract_and_apply_game_state(process: str, ocr_text: str) -> None:
    """Runs a dedicated, non-conversational LLM pass to turn raw OCR text into structured game
    state. Fire-and-forget by design (see memory_extraction.extract_and_apply_memory for the
    same pattern) - failures here must never raise into the poller loop."""
    previous = game_state.get_game_state()
    previous_text = json.dumps(previous) if previous else "none yet"
    known_facts = memory_store.format_memories_for_prompt() or "Known facts about the user: none yet."
    messages = [
        {"role": "system", "content": load_prompt("game_state_extraction")},
        {
            "role": "user",
            "content": (
                f"Foreground process: {process}\n\n{known_facts}\n\n"
                f"Previous state: {previous_text}\n\nNew OCR text:\n{ocr_text}"
            ),
        },
    ]

    try:
        model = settings.game_state_model or None
        raw = await chat_completion(
            messages, model=model, response_format={"type": "json_object"}, source="game_state_extraction"
        )
        data = json.loads(raw)
    except Exception:
        logger.exception("Game-state extraction failed")
        return

    game_state.set_game_state(
        process=process,
        activity=data.get("activity"),
        location=data.get("location") or (previous or {}).get("location"),
        quest=data.get("quest") or (previous or {}).get("quest"),
        character=data.get("character") or (previous or {}).get("character"),
        notable_choice=data.get("notable_choice"),
    )

    for fact in data.get("save_memories") or []:
        if isinstance(fact, str) and fact.strip():
            memory_store.add_memory(fact.strip(), process=process)

    for memory_id in data.get("remove_memory_ids") or []:
        if isinstance(memory_id, str) and memory_id:
            memory_store.remove_memory(memory_id)


async def _poll_once() -> None:
    global _last_process, _last_ocr_text

    if not settings.game_state_ocr_enabled or sys.platform != "win32":
        return

    process = get_foreground_process_name()
    if not process or process.lower() in _NON_GAME_PROCESSES or game_state_processes.is_blacklisted(process):
        logger.debug("Game-state poll: skipping non-game/blacklisted/unknown foreground process=%r", process)
        if _last_process is not None:
            _last_process = None
            _last_ocr_text = None
            game_state.clear_game_state()
        return

    if not game_state_processes.is_whitelisted(process):
        if game_state_processes.get_pending_process() != process:
            logger.info("Game-state poll: unfamiliar process=%r detected, awaiting user approval", process)
            game_state_processes.set_pending_process(process)
        return

    if process != _last_process:
        _last_process = process
        _last_ocr_text = None
        game_state.clear_game_state()

    image = capture_monitor_image()
    ocr_text = await tesseract_ocr.extract_text(image)
    if not ocr_text or not ocr_text.strip():
        logger.warning(
            "Game-state poll: OCR returned no text for process=%r. If this persists, the game may be "
            "running in exclusive fullscreen, which screen capture can't read - try borderless/windowed mode.",
            process,
        )
        return

    normalized = " ".join(ocr_text.split())
    if normalized == _last_ocr_text:
        logger.debug("Game-state poll: OCR text unchanged for process=%r, skipping LLM pass", process)
        return
    _last_ocr_text = normalized

    logger.info(
        "Game-state poll: OCR text changed for process=%r (%d chars), running structuring pass",
        process,
        len(ocr_text),
    )
    await extract_and_apply_game_state(process, ocr_text)


async def run_game_state_poller() -> None:
    """Long-lived background loop, started at app startup. Sleeps for the configured interval
    between ticks so the poll cadence picks up live settings changes without a restart."""
    while True:
        await asyncio.sleep(settings.game_state_poll_interval_seconds)
        try:
            await _poll_once()
        except Exception:
            logger.exception("Game-state poller tick failed")
