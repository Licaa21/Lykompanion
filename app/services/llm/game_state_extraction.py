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
from app.services.screenshot.capture import image_to_b64
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

_last_process: str | None = None
_last_kept_text: str | None = None
_frames: list[tuple[float, str]] = []  # (time.time() captured, raw OCR text), oldest first
_window_started_at: float | None = None

# Base64 JPEGs of the first and most recent *kept* frames of the current poll window, attached to
# the extraction LLM call as actual screenshots (the middle frames travel as OCR text only). The
# pixels carry what OCR structurally cannot - which dialogue/menu option is highlighted/selected,
# who's speaking, spatial layout - which is exactly where text-only extraction hallucinated.
_first_frame_b64: str | None = None
_last_frame_b64: str | None = None

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
    global _first_frame_b64, _last_frame_b64
    _frames = []
    _last_kept_text = None
    _window_started_at = None
    _empty_ocr_streak = 0
    _first_frame_b64 = None
    _last_frame_b64 = None


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


async def _call_extraction(user_content, model: str | None, provider: str) -> dict:
    raw = await chat_completion(
        [
            {"role": "system", "content": load_prompt("game_state_extraction")},
            {"role": "user", "content": user_content},
        ],
        model=model,
        response_format={"type": "json_object"},
        source="game_state_extraction",
        provider=provider,
        on_usage=lambda cost: game_state.record_extraction_call(cost),
    )
    return json.loads(raw)


def _image_part(b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


async def extract_and_apply_game_state(
    process: str,
    frames: list[tuple[float, str]],
    first_frame_b64: str | None = None,
    last_frame_b64: str | None = None,
) -> None:
    """Runs a dedicated, non-conversational LLM pass to turn one poll window's worth of raw OCR
    frames into structured game state. Fire-and-forget by design (see memory_extraction.
    extract_and_apply_memory for the same pattern) - failures here must never raise into the
    poller loop.

    When available, the first and most recent kept frames of the window are attached as actual
    screenshots so a vision-capable extraction model can ground its answers in pixels (selection/
    highlight state, layout) instead of guessing from OCR text alone. If the configured model
    turns out not to accept images, the call is retried once text-only."""
    previous = game_state.get_game_state()
    previous_values = (previous or {}).get("values", {})
    active_session_id = (previous or {}).get("session_id")
    trackers = game_state_trackers.get_trackers(process)
    known_facts = memory_store.format_memories_for_prompt(active_process=process, active_session_id=active_session_id) or "Known facts about the user: none yet."
    training_data = game_state_training_data.format_training_data_for_prompt(process)
    text_content = (
        f"Foreground process: {process}\n\n{known_facts}\n\n"
        + (f"{training_data}\n\n" if training_data else "")
        + f"{_format_tracker_fields(trackers, previous_values)}\n\n{_format_frames(frames)}"
    )

    content: list[dict] = [{"type": "text", "text": text_content}]
    if first_frame_b64 and last_frame_b64 and first_frame_b64 != last_frame_b64:
        content.append({
            "type": "text",
            "text": (
                "Attached below, in order: the FIRST kept frame of this poll window, then the "
                "MOST RECENT one, as actual screenshots. The other frames exist as OCR text only."
            ),
        })
        content.append(_image_part(first_frame_b64))
        content.append(_image_part(last_frame_b64))
    elif last_frame_b64:
        content.append({
            "type": "text",
            "text": "Attached below: the actual screenshot the most recent OCR text was read from.",
        })
        content.append(_image_part(last_frame_b64))

    model = settings.game_state_model or None
    provider = settings.game_state_provider or settings.llm_provider
    try:
        data = await _call_extraction(content, model, provider)
    except Exception:
        if len(content) == 1:
            logger.exception("Game-state extraction failed")
            return
        # Most likely a non-vision model rejecting the image parts - retry once text-only so a
        # text-only GAME_STATE_MODEL keeps working (without the screenshots' grounding benefits).
        logger.warning(
            "Game-state extraction with attached screenshots failed (model=%r) - retrying "
            "text-only; if this repeats, set GAME_STATE_MODEL to a vision-capable model",
            model,
            exc_info=True,
        )
        try:
            data = await _call_extraction([{"type": "text", "text": text_content}], model, provider)
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
            memory_store.add_memory(fact.strip(), process=process, session_id=active_session_id)

    for memory_id in data.get("remove_memory_ids") or []:
        if isinstance(memory_id, str) and memory_id:
            memory_store.remove_memory(memory_id)

    divergence = data.get("divergence_warning")
    if isinstance(divergence, str) and divergence.strip():
        logger.info("Game-state poll: divergence detected for process=%r session=%r: %s", process, active_session_id, divergence.strip())
        game_state.set_pending_divergence(process, divergence.strip())

    # Self-training: the extraction model sees the screenshots, the OCR text, and the current
    # per-process notes document, so it maintains that document itself - no separate trainer
    # model/pass anymore. Persist its revision only when enabled and actually changed.
    if settings.game_state_training_enabled:
        update = data.get("training_data_update")
        if isinstance(update, str) and update.strip():
            update = update.strip()
            if update != game_state_training_data.get_training_data(process).strip():
                logger.info("Game-state poll: extraction pass revised the training notes for process=%r", process)
                game_state_training_data.set_training_data(process, update)


async def _capture_tick() -> None:
    """Captures+OCRs one frame locally (no LLM call) and, once a full poll window's worth of
    frames has accumulated, batches them into a single structuring LLM call."""
    global _last_process, _last_kept_text, _frames, _window_started_at, _empty_ocr_streak
    global _first_frame_b64, _last_frame_b64

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
            # Keep the pixels too (downscaled per the screenshot settings) - the first and most
            # recent kept frames of the window get attached to the extraction call as images.
            frame_b64 = image_to_b64(image)
            if _first_frame_b64 is None:
                _first_frame_b64 = frame_b64
            _last_frame_b64 = frame_b64
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
    first_b64, last_b64 = _first_frame_b64, _last_frame_b64
    _frames = []
    _first_frame_b64 = None
    _last_frame_b64 = None
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
    await extract_and_apply_game_state(process, frames_to_send, first_b64, last_b64)


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
