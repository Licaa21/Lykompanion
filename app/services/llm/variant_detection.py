"""Modpack/variant detection — figures out, at game launch, whether the tracked process is
running a modpack/overhaul (Nolvus, FTB StoneBlock 4, ...) and which base game a generic host
exe (javaw.exe) actually is. One small LLM call interprets OS-level launch signals (window
title, command line, paths, parent process); the result drives:

- fixing a junk exe-derived title ("Javaw") to the real base game via game_art,
- auto-creating/switching a *session* named after the pack (a modpack playthrough IS a
  playthrough profile — session memories, tracked values, and journal history isolate for
  free), tagged with `variant` so memories/training data scope by it,
- seeding pack-specific knowledge via the variant bootstrap.

Fire-and-forget from the OCR poller's tracking-start path (same contract as the other
background passes: must never raise into the poller). The extraction pass can also feed a
pack name spotted on screen through apply_detected_variant() when launch signals weren't
enough (see game_state_extraction.py)."""

import asyncio
import logging

from app.core import game_art, game_state, memory
from app.core.config import settings
from app.core.game_art import _titles_match
from app.core.prompts import load_prompt
from app.services.llm.client import chat_completion, parse_json_reply
from app.services.system.processes import get_foreground_process_details

logger = logging.getLogger(__name__)

# Fixed response shape, safe for strict schema enforcement (see game_knowledge_bootstrap.py's
# _BOOTSTRAP_JSON_SCHEMA for why this differs from the per-tick extraction pass). Falls back to
# json_object mode automatically when the resolved model doesn't support structured_outputs.
_VARIANT_DETECTION_JSON_SCHEMA = {
    "name": "variant_detection",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "base_game_title": {"type": ["string", "null"]},
            "modded": {"type": "boolean"},
            "modpack_name": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
        },
        "required": ["base_game_title", "modded", "modpack_name", "confidence"],
        "additionalProperties": False,
    },
}

# Below this, a modpack_name is treated as a guess and ignored (the OCR fallback can still
# name the pack later from actual on-screen branding).
_MODPACK_CONFIDENCE_MIN = 0.6
# Auto-switching AWAY from a variant session to vanilla is the riskier direction (a false
# "vanilla" verdict silently moves the playthrough) — demand near-certainty.
_VANILLA_CONFIDENCE_MIN = 0.85
_TITLE_CONFIDENCE_MIN = 0.5

# signals-text -> parsed result, per app run: relaunching the same pack shouldn't re-pay the
# LLM call when nothing about how it's launched changed.
_cached_results: dict[str, dict] = {}

# Keep strong refs to fire-and-forget tasks (asyncio only holds weak ones).
_background_tasks: set[asyncio.Task] = set()


def schedule_variant_detection(process: str) -> None:
    if not settings.game_state_ocr_enabled:
        return
    task = asyncio.create_task(_detect_and_apply(process))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _format_signals(details: dict, known_title: str) -> str:
    lines = [f"Process: {details.get('process')}"]
    for label, key in (
        ("Window title", "window_title"),
        ("Command line", "cmdline"),
        ("Executable path", "exe"),
        ("Working directory", "cwd"),
        ("Parent process", "parent"),
    ):
        value = details.get(key)
        if value:
            # Command lines of heavily modded games (especially javaw) can be enormous —
            # the identifying parts (paths, pack names) are in the first stretch.
            lines.append(f"{label}: {str(value)[:2000]}")
    lines.append(f"Title currently on file for this process: {known_title}")
    return "\n".join(lines)


async def _detect_and_apply(process: str) -> None:
    try:
        details = await asyncio.to_thread(get_foreground_process_details)
        if not details or (details.get("process") or "").lower() != process.lower():
            # Focus already moved elsewhere — signals would describe the wrong process.
            return
        known_title = game_art.get_display_title(process)
        signals = _format_signals(details, known_title)

        result = _cached_results.get(signals)
        if result is None:
            raw = await chat_completion(
                [
                    {"role": "system", "content": load_prompt("variant_detection")},
                    {"role": "user", "content": signals},
                ],
                model=settings.game_state_model or None,
                json_schema=_VARIANT_DETECTION_JSON_SCHEMA,
                source="variant_detection",
                provider=settings.game_state_provider or settings.llm_provider,
            )
            result = parse_json_reply(raw)
            _cached_results[signals] = result

        confidence = result.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.0
        base_title = (result.get("base_game_title") or "").strip() or None
        modpack = (result.get("modpack_name") or "").strip() or None

        # The model occasionally names the base game itself as "the pack" (e.g. modpack_name:
        # "Minecraft" for plain Minecraft) instead of leaving it null per the prompt's own rule -
        # never coherent, since a pack can't be the same game it overhauls. A "pack" detection is
        # only trustworthy when it comes with its own base-game identification (naming a real,
        # specific pack implies knowing what it's a pack OF) - so a modpack_name with no
        # base_title alongside it is treated the same as one that's literally identical to it.
        # Checkable in code rather than trusted from prompt wording alone (same reasoning as
        # game_art.py's _titles_match guard against a wrong fuzzy Steam/IGDB match). Either way,
        # the model clearly recognized *something* - salvage it for the title fix below instead
        # of discarding it entirely.
        if modpack and (not base_title or _titles_match(modpack, base_title)):
            logger.info(
                "Variant detection for process=%r: modpack_name %r had no coherent base-game "
                "identification alongside it - treating as vanilla instead of a real pack",
                process, modpack,
            )
            base_title = base_title or modpack
            modpack = None

        logger.info(
            "Variant detection for process=%r: base=%r modded=%r modpack=%r confidence=%.2f",
            process, base_title, result.get("modded"), modpack, confidence,
        )

        if base_title and confidence >= _TITLE_CONFIDENCE_MIN:
            await _maybe_fix_title(process, base_title)

        if modpack and confidence >= _MODPACK_CONFIDENCE_MIN:
            apply_detected_variant(process, modpack, source="launch signals")
        elif result.get("modded") is False and confidence >= _VANILLA_CONFIDENCE_MIN:
            _switch_to_vanilla_session(process)
    except Exception:
        logger.exception("Variant detection failed for process=%r", process)
    finally:
        # The base-game knowledge bootstrap is triggered from HERE, not from _capture_tick
        # alongside this function - it used to be scheduled at the same instant as this
        # detection call, and bootstrap reads the display title synchronously the moment its own
        # task starts (before this coroutine's title fix could ever land), so a generic host exe
        # (javaw.exe) reliably lost the race and got bootstrapped under "Javaw" instead of the
        # corrected "Minecraft" - a weak/wrong training document. Running it in `finally` means
        # it always fires exactly once per process (regardless of whether detection above
        # succeeded, found nothing, or raised), using whatever title is resolved by now.
        from app.services.llm.game_knowledge_bootstrap import schedule_bootstrap

        schedule_bootstrap(process)


async def _maybe_fix_title(process: str, base_title: str) -> None:
    """Replace a junk exe-derived title ('Javaw') with the detected base game, and refetch art
    under the real name. Never touches a user-overridden title, and leaves an already-resolved
    Steam/IGDB title alone (those matched a real store entry)."""
    record = game_art.get_art(process) or {}
    if record.get("title_overridden") or record.get("source"):
        return
    current = (record.get("title") or "").strip()
    if current and current.lower() == base_title.lower():
        return
    logger.info("Variant detection: retitling process=%r %r -> %r", process, current or None, base_title)
    try:
        await game_art.fetch_art(process, force=True, search_term=base_title)
    except Exception:
        logger.warning("Art refetch under detected title %r failed", base_title, exc_info=True)


def _session_has_memories(process: str, session_id: str) -> bool:
    target = process.lower()
    return any(
        (m.get("process") or "").lower() == target and m.get("session_id") == session_id
        for m in memory.load_memories()
    )


def apply_detected_variant(process: str, modpack: str, source: str) -> None:
    """Point the active session at this modpack: reuse the most recent session already tagged
    with it, tag the current session in place when it's still pristine (a just-created default
    with nothing in it), or spin up a fresh session named after the pack. Also kicks off the
    pack-knowledge bootstrap. Called from launch-signal detection and from the extraction
    pass's on-screen fallback — safe to call repeatedly."""
    modpack = modpack.strip()
    if not modpack:
        return
    gs = game_state.get_game_state()
    if not gs or gs["process"].lower() != process.lower():
        return
    active_session = gs.get("session_id")
    active_variant = (gs.get("variant") or "").strip()

    if active_variant.lower() == modpack.lower():
        pass  # already on the right playthrough
    else:
        existing = game_state.find_session_by_variant(process, modpack)
        if existing:
            logger.info("Variant %r detected via %s: switching process=%r to its existing session", modpack, source, process)
            game_state.switch_session(process, existing)
        elif (
            active_session
            and not active_variant
            and game_state.session_is_pristine(process, active_session)
            and not _session_has_memories(process, active_session)
        ):
            logger.info("Variant %r detected via %s: tagging pristine session of process=%r in place", modpack, source, process)
            game_state.set_session_variant(process, active_session, modpack)
            if (game_state.get_session_name(process, active_session) or "") in ("", "Default"):
                game_state.rename_session(process, active_session, modpack)
        else:
            logger.info("Variant %r detected via %s: creating a new session for process=%r", modpack, source, process)
            game_state.create_session(process, modpack, variant=modpack)

    # Seed pack knowledge (no-op if this variant's training data already exists). Late import:
    # bootstrap imports the LLM client stack; keep this module cheap to import for tests.
    from app.services.llm.game_knowledge_bootstrap import schedule_variant_bootstrap

    schedule_variant_bootstrap(process, game_art.get_display_title(process), modpack)


def _switch_to_vanilla_session(process: str) -> None:
    """A confidently-vanilla launch while a variant session is active means the user launched
    the base game without the pack (e.g. plain Skyrim after a Nolvus stretch) — move to the
    newest variant-less session, or a fresh Default. Non-destructive: the variant session and
    everything in it stays put."""
    gs = game_state.get_game_state()
    if not gs or gs["process"].lower() != process.lower() or not (gs.get("variant") or "").strip():
        return
    plain = next(
        (s["session_id"] for s in game_state.get_sessions(process) if not (s.get("variant") or "").strip()),
        None,
    )
    if plain:
        logger.info("Vanilla launch detected for process=%r: switching off variant session to existing plain session", process)
        game_state.switch_session(process, plain)
    else:
        logger.info("Vanilla launch detected for process=%r: creating a plain Default session", process)
        game_state.create_session(process, "Default")
