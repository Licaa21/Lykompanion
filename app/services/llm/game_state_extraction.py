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
from app.core import observations as observations_store
from app.core import reminders as reminders_store
from app.core.config import settings
from app.core.prompts import load_prompt
from app.services.llm.client import chat_completion
from app.services.llm.game_knowledge_bootstrap import schedule_bootstrap
from app.services.llm.memory_retagging import schedule_retagging
from app.services.llm.observation_confirmation import maybe_schedule_confirmation
from app.services.llm.web_search_tool import execute_web_search
from app.services import overlay_process
from app.services.ocr import windows_ocr
from app.services.screenshot.capture import image_to_b64
from app.services.screenshot.wgc_capture import capture_monitor_frame
from app.services.system.processes import (
    get_foreground_process_name,
    is_foreground_window_fullscreen,
    is_process_running,
)

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
_last_kept_at: float | None = None
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

# When the last proactive message was delivered (time.monotonic), enforcing the user-configured
# minimum interval between two of them no matter how chatty the model wants to be.
_last_proactive_at: float | None = None


def _proactive_allowed() -> bool:
    if not settings.proactive_messages_enabled:
        return False
    if _last_proactive_at is None:
        return True
    return time.monotonic() - _last_proactive_at >= settings.proactive_min_interval_minutes * 60


# Appended to the extraction system prompt only when a proactive message is actually allowed
# right now - offering the field while it would be dropped just trains the model to waste it.
_PROACTIVE_PROMPT_ADDON = """
Additionally, you MAY include a **"proactive_message"** field: a short, natural, spoken-style message from the companion to the player, delivered unprompted into their chat (and read aloud). Use it ONLY when you have something genuinely worth interrupting the player for - a relevant tip for exactly the situation on screen, a warning about something they seem to have missed, or a brief comment on a real milestone. It must feel like a friend watching over their shoulder speaking up at the right moment, not a narrator or a coach spamming advice. The bar is high: most windows deserve none - set it to null unless the moment truly calls for it. Never use it to describe what's on screen back to the player (they can see it), never repeat something you (or the chat) already told them, and keep it to one or two conversational sentences.
"""


def _reset_window() -> None:
    """Resets the per-process OCR-batching buffers (frames collected this poll window, dedupe
    state). Doesn't touch persisted tracker values - those live independently in game_state.py,
    keyed by process, and survive a process switch or the companion restarting."""
    global _frames, _last_kept_text, _last_kept_at, _window_started_at, _empty_ocr_streak
    global _first_frame_b64, _last_frame_b64
    _frames = []
    _last_kept_text = None
    _last_kept_at = None
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


async def _call_extraction(user_content, model: str | None, provider: str, allow_proactive: bool = False) -> dict:
    system = load_prompt("game_state_extraction")
    if allow_proactive:
        system += "\n" + _PROACTIVE_PROMPT_ADDON.strip()
    raw = await chat_completion(
        [
            {"role": "system", "content": system},
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
    if not training_data and settings.game_state_training_enabled:
        # A blank document reads as "don't build one" to most models - be explicit that this
        # game has no notes yet and the pass is expected to start the document itself.
        training_data = (
            "There is no training data document for this game yet. You are expected to START one "
            "via \"training_data_update\" as soon as this window teaches you anything about how to "
            "decode this game's UI/HUD/terms - don't wait for a complete picture."
        )
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
    allow_proactive = _proactive_allowed()
    try:
        data = await _call_extraction(content, model, provider, allow_proactive)
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
            data = await _call_extraction([{"type": "text", "text": text_content}], model, provider, allow_proactive)
        except Exception:
            logger.exception("Game-state extraction failed")
            return

    # One research round: the model flagged something on screen it can't decode (an unknown
    # game-specific term/stat/UI element) - run the search and re-call with the results so it can
    # interpret correctly and bank what it learned into the training data.
    search_query = data.get("web_search_query")
    if isinstance(search_query, str) and search_query.strip():
        search_query = search_query.strip()
        logger.info("Game-state poll: extraction pass requested web search %r for process=%r", search_query, process)
        try:
            results = await execute_web_search({"query": search_query})
            enriched = content + [{
                "type": "text",
                "text": (
                    f"Web search results for your query \"{search_query}\" (requested by your own "
                    f"previous pass - use them to interpret the screen and update the training "
                    f"data; do not request another search):\n{results}"
                ),
            }]
            data = await _call_extraction(enriched, model, provider, allow_proactive)
        except Exception:
            # Keep the first pass's output - a failed search must not cost us the whole window.
            logger.exception("Game-state extraction web-search round failed for process=%r", process)

    new_values = {}
    for tracker in trackers:
        tid = tracker["id"]
        if tid == game_state_trackers.ACTIVITY_TRACKER_ID:
            new_values[tid] = data.get(tid)
        elif tid in data:
            # Key present: an explicit null/"" clears the field (the model retracting a value it
            # now believes is wrong), any other value replaces it. Without this, one bad guess
            # was sticky forever - null used to fall back to the previous value.
            new_values[tid] = data[tid] or None
        else:
            # Key omitted: not visible this window, keep what we had.
            new_values[tid] = previous_values.get(tid)
    game_state.set_game_state(process, new_values)

    # The OCR pass observes; it does not write long-term memory. Candidate facts go to the
    # observations journal, where the conversation-side memory-extraction pass promotes the ones
    # that hold up (see app/core/observations.py).
    confidence = data.get("confidence")
    confidence = confidence if isinstance(confidence, (int, float)) else None
    observed = [
        (fact.strip(), confidence)
        for fact in data.get("observations") or []
        if isinstance(fact, str) and fact.strip()
    ]
    if observed:
        added = observations_store.add_observations(process, active_session_id, observed)
        if added:
            logger.info("Game-state poll: recorded %d observation(s) for process=%r", len(added), process)
            # Enough pending observations -> background confirmation pass promotes the ones that
            # hold up into scoped memories, so silent play sessions still build memory.
            maybe_schedule_confirmation(process, active_session_id)

    # Proactive companion message: delivered through the reminders pending queue, which the
    # frontend already polls and injects into the active chat (and narrates) like a normal
    # unprompted assistant message. Re-check the gate at delivery time - a slow LLM call could
    # otherwise let two overlapping passes both deliver.
    proactive = data.get("proactive_message")
    if allow_proactive and isinstance(proactive, str) and proactive.strip() and _proactive_allowed():
        global _last_proactive_at
        _last_proactive_at = time.monotonic()
        reminders_store.add_pending(proactive.strip())
        logger.info("Game-state poll: proactive message queued for process=%r", process)

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
    global _last_process, _last_kept_text, _last_kept_at, _frames, _window_started_at, _empty_ocr_streak
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
            # Smarter detection: only nag for approval when the window actually looks like a game
            # (covers the whole monitor, no title bar) — a borderless/fullscreen app. This stops
            # every random windowed app that grabs focus from queuing as a "pending game." A
            # windowed game can still be approved manually from the UI.
            if is_foreground_window_fullscreen():
                if game_state_processes.add_pending_process(foreground):
                    logger.info("Game-state poll: unfamiliar fullscreen process=%r queued for user approval", foreground)
            else:
                logger.debug("Game-state poll: unfamiliar windowed process=%r not queued (not fullscreen)", foreground)
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
            overlay_process.stop()
        return

    process = foreground
    if process != _last_process:
        _last_process = process
        _reset_window()
        # Flips tracking=True immediately (previous session's values for this process show up
        # right away if any exist, empty otherwise) instead of waiting a full poll window for the
        # first LLM call to populate anything.
        game_state.start_tracking(process)
        # Spawn the native overlay for this session and show whatever we already have
        # from prior sessions immediately (retry across the exe's brief boot window
        # instead of waiting for the first OCR extraction pass).
        overlay_process.start()
        _push_overlay_game_state(process, prime=True)
        # First time this game is ever tracked: fetch IGDB/web knowledge in the background to
        # seed game-specific trackers + starting training data (no-op if already done/customized).
        schedule_bootstrap(process)
        # Also review standing "user"-scope facts for anything that's actually about this game -
        # facts stated before it was ever tracked (e.g. playtime mentioned in passing) had
        # nowhere more specific to land at save time and default to general scope.
        gs = game_state.get_game_state()
        schedule_retagging(process, gs["session_id"] if gs else None)

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
        now = time.time()
        normalized = " ".join(ocr_text.split())
        changed = _last_kept_text is None or not _frames_similar(normalized, _last_kept_text)
        heartbeat_due = (
            not changed
            and settings.game_state_heartbeat_minutes > 0
            and _last_kept_at is not None
            and now - _last_kept_at >= settings.game_state_heartbeat_minutes * 60
        )
        if changed or heartbeat_due:
            if heartbeat_due:
                logger.info(
                    "Game-state poll: text unchanged for %.0fs, force-keeping frame for process=%r "
                    "(heartbeat)",
                    now - _last_kept_at,
                    process,
                )
            _frames.append((now, ocr_text))
            _last_kept_text = normalized
            _last_kept_at = now
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
    _push_overlay_game_state(process)


def _push_overlay_game_state(process: str, prime: bool = False) -> None:
    """Build the overlay panel (label/value rows) from the tracked values and push it.
    `prime=True` retries across the overlay's boot window (first push on start).
    Best-effort — never let an overlay hiccup disturb the poll loop."""
    try:
        trackers = game_state_trackers.get_trackers(process)
        values = game_state.get_values(process)
        rows: list[list[str]] = []
        for tracker in trackers:
            if not tracker.get("overlay", True):  # per-tracker "show in overlay" toggle
                continue
            value = values.get(tracker["id"])
            # Mirror the web panel: show every overlay-enabled tracker, empty ones
            # included (the overlay renders "(not seen yet)" for a blank value).
            rows.append([tracker["label"], str(value) if value else ""])
        title = process.rsplit(".", 1)[0].replace("_", " ").title()
        command = {"type": "game_state", "title": title, "rows": rows}
        if prime:
            overlay_process.push_retry(command)
        else:
            overlay_process.push(command)
    except Exception:
        logger.debug("Overlay game-state push failed", exc_info=True)


def apply_overlay_enabled(enabled: bool) -> None:
    """React to the OVERLAY_ENABLED setting being toggled at runtime so it takes
    effect live (no app restart): tear the overlay down when disabled; when enabled,
    spawn it and push the panel we already have if a game is currently tracked.
    Best-effort — never raises into the settings handler."""
    try:
        if not enabled:
            overlay_process.stop()
            return
        if _last_process:
            overlay_process.start()
            _push_overlay_game_state(_last_process, prime=True)
    except Exception:
        logger.debug("apply_overlay_enabled failed", exc_info=True)


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
