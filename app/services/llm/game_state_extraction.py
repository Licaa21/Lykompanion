import asyncio
import json
import logging
import sys
import time
from difflib import SequenceMatcher

from app.core import game_state
from app.core import game_state_processes
from app.core import game_state_trackers
from app.core import game_state_training_data
from app.core import memory as memory_store
from app.core.config import settings
from app.core.prompts import load_prompt
from app.services.llm.client import chat_completion
from app.services.ocr import windows_ocr
from app.services.screenshot.capture import capture_monitor_b64
from app.services.screenshot.wgc_capture import capture_monitor_frame
from app.services.system.processes import get_foreground_process_name, is_process_running

logger = logging.getLogger(__name__)

# A captured frame is dropped (not sent to the LLM) if its normalized text is at least this
# similar to the last kept frame - filters out an unchanging HUD/menu across consecutive
# captures while still keeping frames that show a real on-screen change.
_SIMILARITY_THRESHOLD = 0.9

# Every captured frame is downscaled to this width before OCR (independent of the screenshot
# settings used for vision LLM calls) - cuts OCR CPU time substantially on high-res captures,
# with negligible accuracy loss for HUD-sized text, reducing CPU contention with whatever game
# is running while this poller captures every tick.
_OCR_MAX_WIDTH = 1600

# Below this self-reported confidence, a training pass re-examines the frame with a vision model.
# Set higher than "clearly wrong" on purpose - self-reported LLM confidence skews high, especially
# now that OCR text itself is clean (Windows OCR vs. the old Tesseract path), which tends to read
# as "legible" even when the model is genuinely guessing at what a value means.
_TRAINING_CONFIDENCE_THRESHOLD = 0.65

# Fire-and-forget training tasks, kept around so they aren't garbage-collected mid-flight.
_training_tasks: set[asyncio.Task] = set()
# Processes (lowercased) with a training pass currently in flight - guards against two passes for
# the same process racing to read-then-write the same training data document.
_training_in_progress: set[str] = set()

_last_process: str | None = None
_last_kept_text: str | None = None
_frames: list[tuple[float, str]] = []  # (time.time() captured, raw OCR text), oldest first
_window_started_at: float | None = None

# A single empty OCR result is routine (loading screens, blank/solid-color frames, a menu with no
# text) and not worth logging every tick - only warn once capture/OCR has come back empty this
# many consecutive ticks in a row, since that's what actually indicates a persistent problem.
_EMPTY_OCR_WARN_THRESHOLD = 10
_empty_ocr_streak = 0


def _reset_window() -> None:
    """Resets the per-process OCR-batching buffers (frames collected this poll window, dedupe
    state). Doesn't touch persisted tracker values - those live independently in game_state.py,
    keyed by process, and survive a process switch or the companion restarting."""
    global _frames, _last_kept_text, _window_started_at, _empty_ocr_streak
    _frames = []
    _last_kept_text = None
    _window_started_at = None
    _empty_ocr_streak = 0


def _frames_similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= _SIMILARITY_THRESHOLD


def _format_frames(frames: list[tuple[float, str]]) -> str:
    if len(frames) == 1:
        return f"New OCR text:\n```\n{frames[0][1]}\n```"

    end = frames[-1][0]
    lines = []
    for i, (ts, text) in enumerate(frames, 1):
        label = "most recent" if i == len(frames) else f"{round(end - ts)}s before the most recent"
        lines.append(f"Screenshot {i} ({label}):\n```\n{text}\n```")
    return "New OCR text, captured in sequence during this poll window (oldest to newest):\n\n" + "\n\n".join(lines)


def _format_tracker_fields(trackers: list[dict], previous_values: dict[str, str]) -> str:
    lines = [f'- "{t["id"]}": {t["description"]}' for t in trackers]
    previous_text = json.dumps(previous_values) if previous_values else "none yet"
    return "Fields to track for this process:\n" + "\n".join(lines) + f"\n\nPrevious values: {previous_text}"


async def _run_training_pass(process: str, ocr_text: str) -> None:
    """Fire-and-forget: re-examines a low-confidence OCR frame with a vision-capable "trainer"
    model (a fresh screenshot + the OCR text + the training data document as it stands) and saves
    its revised document as the new persistent per-process training data, so future extraction
    passes for this game understand its HUD/UI layout without needing another training pass.
    Never raises into the caller - training is best-effort. Always releases the in-flight guard
    for this process, even on failure, so a later low-confidence frame can retry."""
    try:
        current_doc = game_state_training_data.get_training_data(process)
        doc_text = current_doc or "(empty - no training data yet for this process)"
        screenshot_b64 = capture_monitor_b64()
        messages = [
            {"role": "system", "content": load_prompt("game_state_training")},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Foreground process: {process}\n\n"
                            f"Current training data document:\n{doc_text}\n\n"
                            f"New OCR text:\n```\n{ocr_text}\n```"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                ],
            },
        ]
        model = settings.game_state_training_model or None
        cost_holder = {"cost": 0.0}
        revised_doc = await chat_completion(
            messages,
            model=model,
            source="game_state_training",
            on_usage=lambda cost: cost_holder.__setitem__("cost", cost),
        )
        game_state.record_training_call(cost_holder["cost"])
        game_state_training_data.set_training_data(process, revised_doc)
    except Exception:
        logger.exception("Game-state training pass failed")
    finally:
        _training_in_progress.discard(process.lower())


def _maybe_start_training_pass(process: str, confidence, ocr_text: str) -> None:
    if not settings.game_state_training_enabled:
        return
    no_training_data = not game_state_training_data.get_training_data(process)
    confidence_low = isinstance(confidence, (int, float)) and confidence < _TRAINING_CONFIDENCE_THRESHOLD
    # Fire when confidence is low OR when this process has never been trained before — the
    # chicken-and-egg guard: the first extraction for a new game has no training data to lean on,
    # and might produce a confidently-wrong result that never triggers a normal training pass.
    if not confidence_low and not no_training_data:
        return
    if process.lower() in _training_in_progress:
        logger.debug("Game-state poll: training already in flight for process=%r, skipping", process)
        return
    if no_training_data:
        logger.info("Game-state poll: no training data yet for process=%r, bootstrapping", process)
    else:
        logger.info("Game-state poll: low confidence (%.2f) for process=%r, starting training pass", confidence, process)
    _training_in_progress.add(process.lower())
    task = asyncio.create_task(_run_training_pass(process, ocr_text))
    _training_tasks.add(task)
    task.add_done_callback(_training_tasks.discard)


async def extract_and_apply_game_state(process: str, frames: list[tuple[float, str]]) -> None:
    """Runs a dedicated, non-conversational LLM pass to turn one poll window's worth of raw OCR
    frames into structured game state. Fire-and-forget by design (see memory_extraction.
    extract_and_apply_memory for the same pattern) - failures here must never raise into the
    poller loop."""
    previous = game_state.get_game_state()
    previous_values = (previous or {}).get("values", {})
    trackers = game_state_trackers.get_trackers(process)
    known_facts = memory_store.format_memories_for_prompt() or "Known facts about the user: none yet."
    training_data = game_state_training_data.format_training_data_for_prompt(process)
    messages = [
        {"role": "system", "content": load_prompt("game_state_extraction")},
        {
            "role": "user",
            "content": (
                f"Foreground process: {process}\n\n{known_facts}\n\n"
                + (f"{training_data}\n\n" if training_data else "")
                + f"{_format_tracker_fields(trackers, previous_values)}\n\n{_format_frames(frames)}"
            ),
        },
    ]

    try:
        model = settings.game_state_model or None
        cost_holder = {"cost": 0.0}
        raw = await chat_completion(
            messages,
            model=model,
            response_format={"type": "json_object"},
            source="game_state_extraction",
            on_usage=lambda cost: cost_holder.__setitem__("cost", cost),
        )
        game_state.record_extraction_call(cost_holder["cost"])
        data = json.loads(raw)
    except Exception:
        logger.exception("Game-state extraction failed")
        return

    new_values = {}
    for tracker in trackers:
        tid = tracker["id"]
        if tid == game_state_trackers.ACTIVITY_TRACKER_ID:
            new_values[tid] = data.get(tid)
        else:
            new_values[tid] = data.get(tid) or previous_values.get(tid)
    game_state.set_game_state(process, new_values)

    for fact in data.get("save_memories") or []:
        if isinstance(fact, str) and fact.strip():
            memory_store.add_memory(fact.strip(), process=process)

    for memory_id in data.get("remove_memory_ids") or []:
        if isinstance(memory_id, str) and memory_id:
            memory_store.remove_memory(memory_id)

    _maybe_start_training_pass(process, data.get("confidence"), frames[-1][1])


async def _capture_tick() -> None:
    """Captures+OCRs one frame locally (no LLM call) and, once a full poll window's worth of
    frames has accumulated, batches them into a single structuring LLM call."""
    global _last_process, _last_kept_text, _frames, _window_started_at, _empty_ocr_streak

    if not settings.game_state_ocr_enabled or sys.platform != "win32":
        return

    foreground = get_foreground_process_name()
    is_game = game_state_processes.is_likely_game(foreground)
    is_approved = is_game and game_state_processes.is_whitelisted(foreground)

    if not is_approved:
        # Foreground isn't an approved game right now - either something unwhitelisted just got
        # focus, or the user tabbed away to something else entirely (browser, IDE...). Don't touch
        # an in-progress tracked session just because focus moved elsewhere: players routinely
        # alt-tab away mid-session (checking a guide, browsing) and shouldn't lose their snapshot
        # for it - only clear tracking once the tracked process actually exits.
        if is_game and not game_state_processes.is_whitelisted(foreground):
            if game_state_processes.add_pending_process(foreground):
                logger.info("Game-state poll: unfamiliar process=%r queued for user approval", foreground)
        elif not is_game:
            logger.debug("Game-state poll: skipping non-game/blacklisted/unknown foreground process=%r", foreground)

        if _last_process is not None and not is_process_running(_last_process):
            logger.info(
                "Game-state poll: tracked process=%r no longer running, hiding panel (its data "
                "stays saved for next time)",
                _last_process,
            )
            _last_process = None
            _reset_window()
            game_state.stop_tracking()
        return

    process = foreground
    if process != _last_process:
        _last_process = process
        _reset_window()
        # Flips tracking=True immediately (previous session's values for this process show up
        # right away if any exist, empty otherwise) instead of waiting a full poll window for the
        # first LLM call to populate anything.
        game_state.start_tracking(process)

    if _window_started_at is None:
        _window_started_at = time.time()

    image = await capture_monitor_frame()
    ocr_text = None
    if image is not None:
        if image.width > _OCR_MAX_WIDTH:
            ratio = _OCR_MAX_WIDTH / image.width
            image = image.resize((_OCR_MAX_WIDTH, int(image.height * ratio)))
        ocr_text = await windows_ocr.extract_text(image)
    if ocr_text and ocr_text.strip():
        ocr_text = ocr_text.strip()
        _empty_ocr_streak = 0
        normalized = " ".join(ocr_text.split())
        if _last_kept_text is None or not _frames_similar(normalized, _last_kept_text):
            _frames.append((time.time(), ocr_text))
            _last_kept_text = normalized
        else:
            logger.debug("Game-state poll: skipping near-duplicate OCR frame for process=%r", process)
    else:
        _empty_ocr_streak += 1
        if _empty_ocr_streak == _EMPTY_OCR_WARN_THRESHOLD:
            logger.warning(
                "Game-state poll: capture/OCR has returned no text for %d consecutive ticks for "
                "process=%r. Either screen capture is failing for this window (some exclusive-"
                "fullscreen/protected-content cases aren't capturable even with Windows Graphics "
                "Capture - try borderless/windowed mode) or no OCR-capable language pack is "
                "installed (see the earlier warning, if any).",
                _empty_ocr_streak,
                process,
            )
        else:
            logger.debug("Game-state poll: capture/OCR returned no text for process=%r", process)

    elapsed = time.time() - _window_started_at
    if elapsed < settings.game_state_poll_interval_seconds:
        return

    frames_to_send = _frames
    _frames = []
    _window_started_at = None
    if not frames_to_send:
        logger.debug(
            "Game-state poll: no changed frames captured for process=%r this window, skipping LLM pass", process
        )
        return

    logger.info(
        "Game-state poll: %d changed frame(s) captured for process=%r, running structuring pass",
        len(frames_to_send),
        process,
    )
    await extract_and_apply_game_state(process, frames_to_send)


async def run_game_state_poller() -> None:
    """Long-lived background loop, started at app startup. Captures+OCRs a frame locally every
    capture interval (cheap, no LLM call), then once a full poll interval's worth of frames has
    built up, sends them to the LLM together in one batch instead of one screenshot at a time -
    picks up live settings changes without a restart since both intervals are re-read each tick."""
    while True:
        await asyncio.sleep(settings.game_state_capture_interval_seconds)
        try:
            await _capture_tick()
        except Exception:
            logger.exception("Game-state poller tick failed")
