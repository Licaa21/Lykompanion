import asyncio
import json
import logging
import sys
import time
from difflib import SequenceMatcher

from PIL import Image, ImageChops, ImageStat

from app.core import game_art
from app.core import game_state
from app.core import game_state_processes
from app.core import game_state_trackers
from app.core import game_state_training_data
from app.core import memory as memory_store
from app.core import observations as observations_store
from app.core import reminders as reminders_store
from app.core.config import settings
from app.core.prompts import load_prompt
from app.services.llm.client import chat_completion, parse_json_reply
from app.services.llm.memory_retagging import schedule_retagging
from app.services.llm.observation_confirmation import maybe_schedule_confirmation
from app.services.llm.variant_detection import apply_detected_variant, schedule_variant_detection
from app.services import overlay_process
from app.services.ocr import windows_ocr
from app.services.screenshot.capture import image_to_b64
from app.services.screenshot.wgc_capture import capture_monitor_frame
from app.services.system.processes import (
    get_foreground_process_name,
    get_foreground_window_if_windowed,
    is_foreground_window_fullscreen,
    is_foreground_window_large,
    is_process_running,
)

logger = logging.getLogger(__name__)

_last_process: str | None = None
_last_kept_text: str | None = None

# Last non-approved foreground process the poller already logged a "skipping" message for, so
# _capture_tick (which ticks every game_state_capture_interval_seconds, default 1s) logs a
# transition once instead of flooding INFO every tick while idling on the same app/game.
_last_logged_skip: str | None = None

# Consecutive ticks an unfamiliar WINDOWED process has held focus with a large window. A
# borderless-fullscreen unknown queues for approval instantly, but plenty of games (windowed
# Minecraft, most emulators) never go borderless — a large window held for a few ticks is the
# equivalent signal, with the persistence requirement keeping briefly-focused big utility
# windows from nagging. (process_lower, streak_count).
_large_window_streak: tuple[str, int] | None = None
_LARGE_WINDOW_STREAK_TICKS = 3
_frames: list[tuple[float, str]] = []  # (time.time() captured, raw OCR text), oldest first
_window_started_at: float | None = None

# time.time() of the last tick that actually reached the capture step (i.e. the tracked process
# was foreground) - independent of _window_started_at, which keeps counting even while focus is
# elsewhere. Used to detect "regained focus after being alt-tabbed away for a while" so a window
# spanning that whole absence isn't treated as one real 5s-ish poll window (see _capture_tick).
_last_tick_at: float | None = None

# Base64 JPEGs of the first and most recent *kept* frames of the current poll window, attached to
# the extraction LLM call as actual screenshots (the middle frames travel as OCR text only). The
# pixels carry what OCR structurally cannot - which dialogue/menu option is highlighted/selected,
# who's speaking, spatial layout - which is exactly where text-only extraction hallucinated.
_first_frame_b64: str | None = None
_last_frame_b64: str | None = None

# The very first and most recently captured raw frame of the window, kept *regardless* of the OCR
# text dedupe above - used only as a visual-diff fallback (see _visual_diff_percent) so a window
# where the on-screen text never changes at all can still be recognized as having moved (camera
# panning, environment change) instead of being a genuinely frozen screen (paused/static menu).
_window_first_image: Image.Image | None = None
_window_last_image: Image.Image | None = None

_empty_ocr_streak = 0

# When the last proactive message was delivered (time.monotonic), enforcing the user-configured
# minimum interval between two of them no matter how chatty the model wants to be.
_last_proactive_at: float | None = None

# Content of the last delivered proactive message - this pass has no memory of its own past
# output (no chat history in its prompt), so a persistent on-screen fixture (an always-there NPC,
# a landmark) can keep looking like fresh news to it every time the cooldown allows another
# attempt. Backstop against the "never repeat yourself" prompt instruction being unenforceable:
# a new candidate too similar to the last one actually delivered gets suppressed here instead.
_last_proactive_content: str | None = None
_PROACTIVE_REPEAT_SIMILARITY_THRESHOLD = 0.6

# How many consecutive poll windows in a row have been skipped (no genuine OCR/visual change) for
# the tracked process since the last real structuring pass. See game_state_max_consecutive_skips -
# window-to-window comparisons only ever look at diffs *within* one poll window, so a state that
# became static entirely inside a single window (e.g. a death screen reached mid-window) would
# otherwise be skipped forever, since every later window also compares that same static screen
# against itself and finds nothing new. Reset to 0 whenever a genuine change is detected.
_consecutive_skips = 0

# Lowercased process names with a structuring pass currently in flight in the background (see
# _run_structuring_pass) - real calls have been observed taking anywhere from ~3s to 70+s
# (data/debug_log.json), so this is what lets capture/OCR keep ticking at full cadence instead of
# the whole poller stalling for that call's duration, while still refusing to stack a second call
# for the SAME process on top of one already running (a slower older call finishing after a newer
# one could otherwise overwrite fresher tracked values with stale ones). A different process is
# free to run concurrently - independent game_state entries, no race.
_extraction_in_progress: set[str] = set()

# Strong refs to the background structuring-pass tasks (asyncio only holds weak ones) - same
# pattern as variant_detection.py's _background_tasks.
_background_tasks: set[asyncio.Task] = set()


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

Lean on whatever real context you have about this exact situation - training data notes, known facts about the player - instead of a generic reaction. "That's the Ashen Idol, it opens with a poison cloud - don't stand still" beats "careful, tough-looking boss" every time. If you don't actually know anything specific about what's on screen, don't manufacture false confidence - say nothing this window.
"""

# Appended only while the tracked session has no known modpack/variant yet — once one is set,
# offering the field would just invite churn.
_MODPACK_PROMPT_ADDON = """
Additionally, include a **"modpack"** field: normally **null**. Set it to the modpack/overhaul/modlist's name ONLY when the screen itself names one unmistakably — a pack name in the main menu or window chrome ("FTB StoneBlock 4"), a branded splash/loading screen (e.g. Nolvus), a pack-specific quest book title. It must be a real pack identity read off the screen, never an inference from modded-looking content, and never a guess. Leave it null on every ordinary window.
"""


def _coerce_tracker_value(value) -> str | None:
    """Tracker values are always text downstream (the REST API's `str | None` schema, the chat
    prompt, the overlay panel) - a model that returns a bare JSON number/bool for a field despite
    the prompt asking for short text (observed: a numeric-looking custom tracker coming back as
    a raw int) would otherwise persist as that raw type and 500 every later /api/game-state read.
    Coerces to a string; None/empty stays None (a real "no value," not the string "None")."""
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else str(value)


def _reset_window() -> None:
    """Resets the per-process OCR-batching buffers (frames collected this poll window, dedupe
    state). Doesn't touch persisted tracker values - those live independently in game_state.py,
    keyed by process, and survive a process switch or the companion restarting."""
    global _frames, _last_kept_text, _window_started_at, _empty_ocr_streak
    global _first_frame_b64, _last_frame_b64, _window_first_image, _window_last_image, _consecutive_skips
    _frames = []
    _last_kept_text = None
    _window_started_at = None
    _empty_ocr_streak = 0
    _first_frame_b64 = None
    _last_frame_b64 = None
    _window_first_image = None
    _window_last_image = None
    _consecutive_skips = 0


def forget_tracked_process(process: str) -> None:
    """Resets the poller's own in-memory "what am I currently tracking" state for one process -
    call this when a tracked game is deleted via the API (DELETE /api/game-state/games/{process})
    while it's still the focused/running process. Without this, _capture_tick's `process !=
    _last_process` check - the only thing that triggers game_state.start_tracking(),
    schedule_variant_detection(), and schedule_bootstrap() - never fires again, since the
    foreground process name is identical before and after the delete. The freshly-wiped process
    would otherwise keep polling under stale state forever, never re-seeding trackers/training
    data or re-detecting a modpack, even though its underlying data was just cleared."""
    global _last_process
    if _last_process and _last_process.lower() == process.lower():
        _last_process = None
        _reset_window()


def _frames_similar(a: str, b: str) -> tuple[bool, float]:
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= settings.game_state_ocr_similarity_threshold, ratio


def _visual_diff_percent_sync(a: Image.Image, b: Image.Image) -> float:
    """Coarse whole-frame visual difference between two images, as a 0-100 percent: both are
    downscaled to a tiny grayscale thumbnail (cheap, and blurs out compression/OCR-irrelevant
    noise) and compared via mean absolute pixel difference. Used only as a fallback signal when
    OCR text found nothing to distinguish the window's frames - real camera movement/environment
    change registers here even when no on-screen text changed at all."""
    size = (settings.game_state_visual_diff_thumbnail_size, settings.game_state_visual_diff_thumbnail_size)
    a_thumb = a.convert("L").resize(size)
    b_thumb = b.convert("L").resize(size)
    diff = ImageChops.difference(a_thumb, b_thumb)
    return (ImageStat.Stat(diff).mean[0] / 255) * 100


async def _visual_diff_percent(a: Image.Image, b: Image.Image) -> float:
    # Off the event loop - this poller ticks every capture interval while a game is tracked, and
    # PIL's resize/diff work is pure CPU, so running it inline would stall every other coroutine
    # (including a concurrent foreground chat request) for its duration.
    return await asyncio.to_thread(_visual_diff_percent_sync, a, b)


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


async def _call_extraction(
    user_content, model: str | None, provider: str,
    allow_proactive: bool = False, allow_modpack: bool = False,
) -> dict:
    system = load_prompt("game_state_extraction")
    if allow_proactive:
        system += "\n" + _PROACTIVE_PROMPT_ADDON.strip()
    if allow_modpack:
        system += "\n" + _MODPACK_PROMPT_ADDON.strip()
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
        # Used to fail fast (max_retries=0) because this call was awaited directly inside the
        # poller's own tick loop - the SDK's retry sleeps for the upstream Retry-After value on a
        # 429 (observed up to 60s per attempt), which would have frozen capture/OCR for minutes.
        # As of 2026-07-13 this call runs backgrounded (_run_structuring_pass), no longer blocking
        # the tick loop, so that risk is gone - the SDK default retries (2, see get_client) apply
        # like every other call site, giving a rate-limited window a real chance to complete
        # instead of failing outright on the first 429 and waiting for a later window's luck.
    )
    return parse_json_reply(raw)


def _image_part(b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def _normalize_training_data_update(update) -> str | None:
    """`training_data_update` comes back in inconsistent shapes despite the prompt asking for one
    markdown string - sometimes a dict keyed by header (with or without the leading "##", value a
    string or a list of bullet lines), sometimes a plain string with "## Header" lines embedded.
    Observed live (2026-07-13): the dict shape was the *common* case, but the code only ever
    accepted a plain string - every dict-shaped revision (often the richer one, extending an
    existing document) was silently dropped and logged as "no update this pass," while the rare
    plain-string reply instead wholesale replaced the document, since nothing here ever merged
    partial updates in. Normalizing the shape at least stops genuinely-shaped revisions from being
    thrown away outright."""
    if isinstance(update, str):
        return update.strip() or None
    if isinstance(update, dict):
        lines = []
        for header, body in update.items():
            header = header.strip()
            if header and not header.startswith("#"):
                header = f"## {header}"
            if isinstance(body, list):
                body_text = "\n".join(f"- {item.strip()}" for item in body if isinstance(item, str) and item.strip())
            elif isinstance(body, str):
                body_text = body.strip()
            else:
                continue
            if header and body_text:
                lines.append(f"{header}\n{body_text}")
        return "\n\n".join(lines) if lines else None
    return None


def _training_update_would_drop_lore(current: str, update: str) -> bool:
    """True when `current` has a "## Lore" section but `update` doesn't - the prompt tells the
    model to keep an existing Lore section untouched, so a revision that drops it entirely almost
    certainly means the model replaced the whole document with just this window's observations
    instead of actually revising it (observed live: a rich bootstrap-seeded Lore+UI/UX doc for FTB
    StoneBlock 4 reduced to three generic sentences in one pass)."""
    return "## lore" in current.lower() and "## lore" not in update.lower()


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
    active_variant = (previous or {}).get("variant") or None
    trackers = game_state_trackers.get_trackers(process)
    known_facts = memory_store.format_memories_for_prompt(
        active_process=process, active_session_id=active_session_id, active_variant=active_variant
    ) or "Known facts about the user: none yet."
    training_data = game_state_training_data.format_training_data_for_prompt(process, active_variant)
    if not training_data and settings.game_state_training_enabled:
        # A blank document reads as "don't build one" to most models - be explicit that this
        # game has no notes yet and the pass is expected to start the document itself.
        training_data = (
            "There is no training data document for this game yet. You are expected to START one "
            "via \"training_data_update\" as soon as this window teaches you anything about how to "
            "decode this game's UI/HUD/terms - don't wait for a complete picture."
        )
    variant_line = f"\nActive modpack for this playthrough: {active_variant}" if active_variant else ""
    text_content = (
        f"Foreground process: {process}{variant_line}\n\n{known_facts}\n\n"
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
    allow_modpack = not active_variant  # launch signals didn't name one — the screen might
    try:
        data = await _call_extraction(content, model, provider, allow_proactive, allow_modpack)
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
            data = await _call_extraction([{"type": "text", "text": text_content}], model, provider, allow_proactive, allow_modpack)
        except Exception:
            logger.exception("Game-state extraction failed")
            return

    # On-screen modpack identification (offered only while no variant is known): points the
    # active session at the pack's playthrough profile BEFORE the values below are persisted,
    # so this window's state lands in the right session.
    modpack = data.get("modpack") if allow_modpack else None
    if isinstance(modpack, str) and modpack.strip():
        apply_detected_variant(process, modpack.strip(), source="on-screen text")
        refreshed = game_state.get_game_state()
        if refreshed and refreshed["process"].lower() == process.lower():
            active_session_id = refreshed.get("session_id")
            active_variant = refreshed.get("variant") or None

    new_values = {}
    for tracker in trackers:
        tid = tracker["id"]
        if tid == game_state_trackers.ACTIVITY_TRACKER_ID:
            new_values[tid] = _coerce_tracker_value(data.get(tid))
        elif tid in data:
            # Key present: an explicit null/"" clears the field (the model retracting a value it
            # now believes is wrong), any other value replaces it. Without this, one bad guess
            # was sticky forever - null used to fall back to the previous value.
            new_values[tid] = _coerce_tracker_value(data[tid])
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
    global _last_proactive_at, _last_proactive_content
    proactive = data.get("proactive_message")
    if isinstance(proactive, str) and proactive.strip():
        proactive = proactive.strip()
        if not (allow_proactive and _proactive_allowed()):
            pass
        elif (
            _last_proactive_content is not None
            and SequenceMatcher(None, proactive, _last_proactive_content).ratio() >= _PROACTIVE_REPEAT_SIMILARITY_THRESHOLD
        ):
            # Same underlying "news" as last time (e.g. a persistent NPC/landmark re-noticed each
            # window it's on screen) - the model has no memory of its own past proactive messages
            # to catch this itself, so it's enforced here instead of trusting the prompt alone.
            logger.info(
                "Game-state poll: suppressing proactive message for process=%r - too similar to "
                "the last one delivered (%r)",
                process, _last_proactive_content,
            )
        else:
            _last_proactive_at = time.monotonic()
            _last_proactive_content = proactive
            reminders_store.add_pending(proactive)
            logger.info("Game-state poll: proactive message queued for process=%r", process)

    divergence = data.get("divergence_warning")
    if isinstance(divergence, str) and divergence.strip():
        logger.info("Game-state poll: divergence detected for process=%r session=%r: %s", process, active_session_id, divergence.strip())
        game_state.set_pending_divergence(process, divergence.strip())
    else:
        # Null is the overwhelmingly common, correct result here (only an unambiguous stat
        # regression - a crash/reverted save - should ever set this) - logged anyway so it's
        # visible from the logs alone that this pass actually evaluated the field, rather than
        # silence being ambiguous between "checked, nothing to report" and "never checked."
        logger.info("Game-state poll: no divergence this pass for process=%r", process)

    # Self-training: the extraction model sees the screenshots, the OCR text, and the current
    # per-process notes document, so it maintains that document itself - no separate trainer
    # model/pass anymore. Persist its revision only when enabled and actually changed.
    if settings.game_state_training_enabled:
        update = _normalize_training_data_update(data.get("training_data_update"))
        if update:
            current = game_state_training_data.get_training_data(process, active_variant).strip()
            if update == current:
                logger.info("Game-state poll: training_data_update matched existing notes for process=%r (no-op)", process)
            elif _training_update_would_drop_lore(current, update):
                # Refuse it rather than silently destroying everything earlier passes (and the
                # bootstrap) built up.
                logger.warning(
                    "Game-state poll: discarding training_data_update for process=%r variant=%r - "
                    "it drops the existing '## Lore' section instead of preserving it",
                    process, active_variant,
                )
            else:
                logger.info("Game-state poll: extraction pass revised the training notes for process=%r variant=%r", process, active_variant)
                game_state_training_data.set_training_data(process, update, variant=active_variant)
        else:
            # Same rationale as the divergence branch above - most passes SHOULD be null once a
            # document exists (see game_state_extraction.md), but this makes that visible instead
            # of indistinguishable from the check never running.
            logger.info("Game-state poll: no training-data update this pass for process=%r", process)
    else:
        logger.debug("Game-state poll: self-training disabled, skipping training_data_update for process=%r", process)


async def _capture_tick() -> None:
    """Captures+OCRs one frame locally (no LLM call) and, once a full poll window's worth of
    frames has accumulated, batches them into a single structuring LLM call - dispatched as a
    background task (see _run_structuring_pass), never awaited here, so a slow call can't stall
    this function's own capture/OCR work on later ticks."""
    global _last_process, _last_kept_text, _frames, _window_started_at, _empty_ocr_streak
    global _first_frame_b64, _last_frame_b64, _window_first_image, _window_last_image, _consecutive_skips
    global _last_logged_skip, _large_window_streak, _last_tick_at

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
            # Smarter detection: nag for approval when the window actually looks like a game —
            # borderless/fullscreen queues instantly; a windowed app whose window covers most of
            # the work area queues after holding focus a few consecutive ticks (windowed
            # Minecraft, emulators). Small/briefly-focused windows never nag; anything can still
            # be approved manually from the UI.
            if is_foreground_window_fullscreen():
                _large_window_streak = None
                if game_state_processes.add_pending_process(foreground):
                    logger.info("Game-state poll: unfamiliar fullscreen process=%r queued for user approval", foreground)
            elif is_foreground_window_large():
                streak = _large_window_streak[1] + 1 if _large_window_streak and _large_window_streak[0] == foreground.lower() else 1
                _large_window_streak = (foreground.lower(), streak)
                if streak >= _LARGE_WINDOW_STREAK_TICKS:
                    _large_window_streak = None
                    if game_state_processes.add_pending_process(foreground):
                        logger.info("Game-state poll: unfamiliar large-windowed process=%r queued for user approval", foreground)
            else:
                _large_window_streak = None
                if _last_logged_skip != foreground:
                    logger.info("Game-state poll: skipping unfamiliar windowed process=%r (window too small to look like a game, not queued for approval)", foreground)
                    _last_logged_skip = foreground
        elif not is_game:
            if _last_logged_skip != foreground:
                logger.info("Game-state poll: skipping foreground process=%r (not recognized as a game / blacklisted)", foreground)
                _last_logged_skip = foreground

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
        # Modpack/variant detection from launch signals (window title, cmdline, parent process)
        # — figures out what a generic host exe (javaw.exe) really is and whether this launch is
        # a modpack, auto-pointing the active session at the right playthrough profile. The base
        # game knowledge bootstrap (IGDB/web search -> trackers + starting training data) is
        # triggered from INSIDE this call (after any title fix), not scheduled here directly - see
        # the comment in variant_detection.py's _detect_and_apply for why (a race that gave
        # generic host exes like javaw.exe a bootstrap seeded under the wrong/junk name).
        schedule_variant_detection(process)
        # Also review standing "user"-scope facts for anything that's actually about this game -
        # facts stated before it was ever tracked (e.g. playtime mentioned in passing) had
        # nowhere more specific to land at save time and default to general scope.
        gs = game_state.get_game_state()
        schedule_retagging(process, gs["session_id"] if gs else None)
    elif _last_tick_at is not None and time.time() - _last_tick_at > settings.game_state_poll_interval_seconds:
        # Same process, but it's been longer than a full poll interval since the last tick that
        # actually reached here - focus was elsewhere for a while (alt-tabbed away), not just
        # normal capture-interval jitter, since ticks this function never even runs for an
        # unfocused process. The buffered window spans that whole absence and would produce a
        # meaningless diff against an ancient frame, or close instantly and chain into a second
        # window right behind it - observed tripping a provider's own high-frequency abuse
        # detection (2026-07-13). Start the window clean instead of closing a stale one.
        logger.info(
            "Game-state poll: process=%r regained focus after %.1fs away - resetting the poll "
            "window instead of closing one that spans the whole absence",
            process, time.time() - _last_tick_at,
        )
        _reset_window()

    _last_tick_at = time.time()

    if _window_started_at is None:
        _window_started_at = time.time()

    # Windowed game: capture just its window so desktop/taskbar/other windows never reach OCR.
    # Fullscreen (hwnd None): monitor capture, as before.
    image = await capture_monitor_frame(window_hwnd=get_foreground_window_if_windowed())
    ocr_text = None
    if image is None:
        logger.info("Game-state poll: screen capture returned no frame for process=%r this tick", process)
    else:
        ocr_max_width = settings.game_state_ocr_max_width
        if image.width > ocr_max_width:
            ratio = ocr_max_width / image.width
            # Off the event loop - PIL resize is pure CPU and this runs every capture tick.
            image = await asyncio.to_thread(image.resize, (ocr_max_width, int(image.height * ratio)))
        # Tracked regardless of the OCR-text dedupe below - the visual-diff fallback needs the
        # window's true first/last frame, not just its first/last *kept* one.
        if _window_first_image is None:
            _window_first_image = image
        _window_last_image = image
        ocr_text = await windows_ocr.extract_text(image)
    if ocr_text and ocr_text.strip():
        ocr_text = ocr_text.strip()
        _empty_ocr_streak = 0
        normalized = " ".join(ocr_text.split())
        if _last_kept_text is None:
            similar, ratio = False, 0.0
        else:
            similar, ratio = _frames_similar(normalized, _last_kept_text)
        if not similar:
            _frames.append((time.time(), ocr_text))
            _last_kept_text = normalized
            # Keep the pixels too (downscaled per the screenshot settings) - the first and most
            # recent kept frames of the window get attached to the extraction call as images.
            frame_b64 = await asyncio.to_thread(image_to_b64, image)
            if _first_frame_b64 is None:
                _first_frame_b64 = frame_b64
            _last_frame_b64 = frame_b64
            logger.info(
                "Game-state poll: kept new OCR frame for process=%r (similarity=%.2f vs last kept, "
                "chars=%d, %d frame(s) buffered this window)",
                process,
                ratio,
                len(ocr_text),
                len(_frames),
            )
        else:
            logger.info(
                "Game-state poll: skipping near-duplicate OCR frame for process=%r (similarity=%.2f >= "
                "threshold=%.2f)",
                process,
                ratio,
                settings.game_state_ocr_similarity_threshold,
            )
    else:
        _empty_ocr_streak += 1
        if _empty_ocr_streak == settings.game_state_empty_ocr_warn_threshold:
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
            logger.info(
                "Game-state poll: capture/OCR returned no text for process=%r (empty streak=%d)",
                process,
                _empty_ocr_streak,
            )

    elapsed = time.time() - _window_started_at
    if elapsed < settings.game_state_poll_interval_seconds:
        return

    if process.lower() in _extraction_in_progress:
        # A structuring pass for this same process is still running in the background - don't
        # stack a second one or reset the window early. Keep accumulating into the same window;
        # the next tick re-checks and closes it (now spanning more time, which is fine) once the
        # in-flight call clears.
        logger.debug(
            "Game-state poll: structuring pass still in flight for process=%r, extending window "
            "(elapsed=%.1fs)",
            process,
            elapsed,
        )
        return

    logger.info(
        "Game-state poll: poll window closed for process=%r (elapsed=%.1fs >= interval=%ds, "
        "%d OCR frame(s) buffered)",
        process,
        elapsed,
        settings.game_state_poll_interval_seconds,
        len(_frames),
    )

    frames_to_send = _frames
    first_b64, last_b64 = _first_frame_b64, _last_frame_b64
    window_first_image, window_last_image = _window_first_image, _window_last_image
    _frames = []
    _first_frame_b64 = None
    _last_frame_b64 = None
    _window_started_at = None
    _window_first_image = None
    _window_last_image = None
    diff_percent = None
    if (
        window_first_image is not None
        and window_last_image is not None
        and window_first_image is not window_last_image
    ):
        diff_percent = await _visual_diff_percent(window_first_image, window_last_image)
    logger.info(
        "Game-state poll: visual diff for process=%r this window: %s (threshold=%.1f%%)",
        process,
        f"{diff_percent:.1f}%" if diff_percent is not None else "n/a - no distinct first/last frame",
        settings.game_state_visual_diff_threshold_percent,
    )

    if (
        frames_to_send
        and diff_percent is not None
        and settings.game_state_visual_diff_noise_floor_percent > 0
        and diff_percent < settings.game_state_visual_diff_noise_floor_percent
    ):
        # OCR flagged text as "changed," but the pixels barely moved at all - almost certainly OCR
        # misread jitter on an otherwise static screen (a stable HUD number/glyph read slightly
        # differently between ticks), not a real on-screen change. Discard it and fall through to
        # the same "nothing changed" handling below rather than wasting an LLM call on noise.
        logger.info(
            "Game-state poll: OCR flagged %d changed frame(s) for process=%r but pixel diff is "
            "only %.1f%% (below noise floor=%.1f%%) - treating as OCR noise, not a real change",
            len(frames_to_send),
            process,
            diff_percent,
            settings.game_state_visual_diff_noise_floor_percent,
        )
        frames_to_send = []
        first_b64 = None
        last_b64 = None

    genuine_change = bool(frames_to_send)

    if not frames_to_send:
        if diff_percent is not None and diff_percent >= settings.game_state_visual_diff_threshold_percent:
            # OCR text never changed all window, but the actual pixels did (camera movement,
            # environment change) - a minimal-UI/textless gameplay moment, not a frozen screen.
            # Force the window through on the last OCR reading + these two raw frames so the
            # extraction pass still gets a look, driven entirely by pixels since there's no new text.
            logger.info(
                "Game-state poll: OCR text unchanged but frames differ %.1f%% for process=%r, "
                "running structuring pass on visual diff alone",
                diff_percent,
                process,
            )
            frames_to_send = [(time.time(), _last_kept_text or "(no on-screen text detected)")]
            first_b64 = first_b64 or await asyncio.to_thread(image_to_b64, window_first_image)
            last_b64 = await asyncio.to_thread(image_to_b64, window_last_image)
            genuine_change = True
        else:
            # Neither signal saw a change *within this window*. That comparison is blind to a state
            # that became static entirely inside one window (e.g. a death screen reached mid-window,
            # or one that started already on it) - every later window compares that same static
            # screen against itself and would find nothing new forever. The first "should skip"
            # window in a streak is therefore ALWAYS let through, unconditionally - this is the
            # actual fix and isn't gated by the setting below. game_state_max_consecutive_skips only
            # controls an optional periodic re-check after that: 0 (default) means skip indefinitely
            # for the rest of the streak once the single guaranteed look has happened (no recurring
            # cost); a positive value re-forces one through every N skips as extra insurance.
            max_skips = settings.game_state_max_consecutive_skips
            if _consecutive_skips == 0 or (max_skips > 0 and _consecutive_skips >= max_skips):
                logger.info(
                    "Game-state poll: no OCR/visual change detected for process=%r (consecutive "
                    "skips=%d, budget=%d) - forcing one through anyway (%s)",
                    process,
                    _consecutive_skips,
                    max_skips,
                    "first skip in this streak" if _consecutive_skips == 0 else "skip budget exhausted",
                )
                frames_to_send = [(time.time(), _last_kept_text or "(no on-screen text detected)")]
                if window_first_image is not None:
                    first_b64 = first_b64 or await asyncio.to_thread(image_to_b64, window_first_image)
                if window_last_image is not None:
                    last_b64 = await asyncio.to_thread(image_to_b64, window_last_image)
                _consecutive_skips = 1
            else:
                _consecutive_skips += 1
                logger.info(
                    "Game-state poll: no changed OCR frames and no meaningful visual diff (%s) for "
                    "process=%r this window, skipping LLM pass entirely (consecutive skips=%d/%d)",
                    f"{diff_percent:.1f}%" if diff_percent is not None else "n/a - no prior frame to diff",
                    process,
                    _consecutive_skips,
                    max_skips,
                )
                return

    if genuine_change:
        _consecutive_skips = 0

    image_count = sum(1 for b64 in (first_b64, last_b64) if b64) if first_b64 != last_b64 else (1 if last_b64 else 0)
    logger.info(
        "Game-state poll: %d changed OCR frame(s) + %d screenshot(s) for process=%r, running "
        "structuring pass (model=%r, provider=%r)",
        len(frames_to_send),
        image_count,
        process,
        settings.game_state_model or "(default)",
        settings.game_state_provider or settings.llm_provider,
    )
    # Backgrounded rather than awaited here - this call has been observed taking anywhere from
    # ~3s to 70+s, and awaiting it inline would freeze the capture loop (no new frames, no OCR,
    # no overlay updates) for the whole duration. _extraction_in_progress (checked above) keeps
    # this process from stacking a second call while this one's still running.
    _extraction_in_progress.add(process.lower())
    task = asyncio.create_task(_run_structuring_pass(process, frames_to_send, first_b64, last_b64))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_structuring_pass(
    process: str,
    frames: list[tuple[float, str]],
    first_b64: str | None,
    last_b64: str | None,
) -> None:
    """Runs the structuring LLM call + overlay push off the poller's own tick loop (see the
    dispatch comment above). extract_and_apply_game_state and _push_overlay_game_state already
    swallow their own exceptions (fire-and-forget by design); the try/finally here only exists
    to guarantee _extraction_in_progress always clears, even on something unexpected (e.g. task
    cancellation), so a stuck flag can't permanently block this process's future windows."""
    try:
        await extract_and_apply_game_state(process, frames, first_b64, last_b64)
        # Only push if this is still the actively tracked process - the user may have switched
        # games while this call was in flight, and pushing now would flicker the overlay with
        # this (now stale) game's data over whatever's actually being tracked.
        if process == _last_process:
            _push_overlay_game_state(process)
    finally:
        _extraction_in_progress.discard(process.lower())


def _push_overlay_game_state(process: str, prime: bool = False) -> None:
    """Build the overlay panel (label/value rows) from the tracked values and push it.
    `prime=True` retries across the overlay's boot window (first push on start).
    Best-effort — never let an overlay hiccup disturb the poll loop."""
    try:
        trackers = game_state_trackers.get_trackers(process)
        values = game_state.get_values(process)
        rows: list[list[str]] = []

        # Modpack/session identity, shown as a plain row above the trackers (same smaller row
        # styling the overlay already uses - no native rendering changes needed). Only shown when
        # there's something non-obvious to say: a modpack, or a non-default named profile - the
        # common single vanilla "Default" session would just be clutter every single game.
        gs = game_state.get_game_state()
        if gs and gs.get("process", "").lower() == process.lower():
            variant = gs.get("variant")
            if variant:
                rows.append(["Modpack", variant])
            else:
                session_id = gs.get("session_id")
                session_name = session_id and game_state.get_session_name(process, session_id)
                if session_name and session_name != "Default":
                    rows.append(["Session", session_name])

        for tracker in trackers:
            if not tracker.get("overlay", True):  # per-tracker "show in overlay" toggle
                continue
            value = values.get(tracker["id"])
            # Mirror the web panel: show every overlay-enabled tracker, empty ones
            # included (the overlay renders "(not seen yet)" for a blank value).
            rows.append([tracker["label"], str(value) if value else ""])
        # The resolved/corrected display title (Steam/IGDB match or a user correction via
        # correct_game_title), not a naive cleanup of the raw process name - this used to show
        # "Javaw" forever regardless of what the Gaming Journal/My Games title actually resolved to.
        title = game_art.get_display_title(process)
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
    picks up live settings changes without a restart since both intervals are re-read each tick.
    The structuring LLM call itself runs as a background task (_run_structuring_pass), not
    awaited by this loop, so this loop's own cadence never drifts with that call's latency."""
    while True:
        await asyncio.sleep(settings.game_state_capture_interval_seconds)
        try:
            await _capture_tick()
        except Exception:
            logger.exception("Game-state poller tick failed")
