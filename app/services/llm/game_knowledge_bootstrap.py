"""One-time game knowledge bootstrap - runs in the background the first time a game is
tracked. Fetches IGDB info + Steam store info + a web search for the game, then one LLM pass
turns that into (a) game-appropriate trackers (replacing the generic RPG-flavored defaults, but
only if the user hasn't customized them) and (b) starting training data notes, so the extraction
pass doesn't face a blank document it never bothers to fill in."""

import asyncio
import logging
import re

import httpx

from app.core import game_state_trackers
from app.core import game_state_training_data
from app.core.config import settings
from app.core.game_art import APP_DETAILS_URL, STORE_SEARCH_URL, _titles_match
from app.core.prompts import load_prompt
from app.services.llm.client import chat_completion, parse_json_reply
from app.services.llm.igdb_tool import execute_lookup_game_info
from app.services.llm.web_search_tool import execute_web_search

logger = logging.getLogger(__name__)

# Fixed, non-dynamic response shape (unlike game_state_extraction's per-tracker fields, which rely
# on omitting a key to mean "carry forward" - incompatible with strict schema's "every key present
# every time") - a good candidate for response_format: json_schema instead of the looser
# json_object mode. Falls back to json_object automatically when the resolved model doesn't
# support it (see client.py's _model_supports_structured_outputs) - never used unconditionally.
_BOOTSTRAP_JSON_SCHEMA = {
    "name": "game_knowledge_bootstrap",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "trackers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["label", "description"],
                    "additionalProperties": False,
                },
            },
            "training_data": {"type": ["string", "null"]},
        },
        "required": ["trackers", "training_data"],
        "additionalProperties": False,
    },
}

# Processes a bootstrap has already been attempted for this app run - avoids re-running the
# whole gather+LLM pass every time the same game regains focus after a failed/empty attempt.
# Deliberately in-memory only: a restart gets one fresh retry, which is what you want when the
# first attempt failed because the network/search was down.
_attempted: set[str] = set()


def _guess_game_name(process: str) -> str:
    """Best-effort human name for a process: the Gaming Journal's resolved/overridden title
    when one exists (it may name the real game behind a generic host exe like javaw.exe),
    otherwise a cleaned-up exe name — IGDB's fuzzy search and the web search engine both cope
    well with that form. Late import: game_art pulls in the LLM client stack."""
    from app.core.game_art import get_display_title

    return get_display_title(process)


def _trackers_are_default(trackers: list[dict]) -> bool:
    """True when the process still has the untouched seeded defaults - the only case where the
    bootstrap is allowed to replace them. A user-customized list is never overwritten."""
    default_ids = [t["id"] for t in game_state_trackers.DEFAULT_TRACKERS]
    return [t["id"] for t in trackers] == default_ids


STEAM_ACHIEVEMENTS_URL = "https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/"
# Some AAA games ship 100+ achievements - capped to keep one game's knowledge from dominating
# the gathered text (and the doc's ongoing per-pass token cost).
_MAX_ACHIEVEMENTS = 40


async def _fetch_steam_knowledge(game_name: str) -> str | None:
    """Steam's public store-search + appdetails - no API key needed, and covers small/indie
    titles that IGDB's own database often has no populated (or no) entry for. Guards against
    Steam's fuzzy search substituting a different, similarly-named real game (same risk/fix as
    game_art.py's fetch_art - see its _titles_match usage) since a wrong match here would feed
    the LLM pass a confidently wrong game's description.

    Also pulls the game's achievement schema (name + description) when a Steam Web API key is
    configured (settings.steam_api_key - the same one used by steam_tool.py's owned-games tool;
    this call needs no SteamID, just the key) - achievement text routinely spells out an obscure
    mechanic in plain language better than a generic web search does."""
    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            search_response = await http_client.get(STORE_SEARCH_URL, params={"term": game_name, "cc": "us", "l": "en"})
            search_response.raise_for_status()
            items = search_response.json().get("items") or []
            if not items or not _titles_match(items[0].get("name") or "", game_name):
                return None
            appid = items[0]["id"]

            details_response = await http_client.get(APP_DETAILS_URL, params={"appids": appid, "l": "en"})
            details_response.raise_for_status()
            app_data = details_response.json().get(str(appid), {})

            achievements_text = None
            if settings.steam_api_key:
                try:
                    ach_response = await http_client.get(
                        STEAM_ACHIEVEMENTS_URL, params={"key": settings.steam_api_key, "appid": appid},
                    )
                    ach_response.raise_for_status()
                    achievements = (
                        (ach_response.json().get("game") or {}).get("availableGameStats", {}).get("achievements") or []
                    )
                    lines = [
                        f"{a['displayName']}: {a['description']}"
                        for a in achievements
                        if a.get("displayName") and a.get("description")
                    ]
                    if lines:
                        achievements_text = "\n".join(lines[:_MAX_ACHIEVEMENTS])
                except httpx.HTTPError:
                    pass  # Bad/missing key, or Steam has no achievement schema for this game.
    except httpx.HTTPError:
        return None

    # appdetails failing doesn't necessarily mean achievements did too - keep whichever succeeded
    # rather than discarding both.
    info = app_data["data"] if app_data.get("success") else {}

    parts = []
    genres = ", ".join(g["description"] for g in info.get("genres") or [] if g.get("description"))
    if genres:
        parts.append(f"Genres: {genres}")
    categories = ", ".join(c["description"] for c in info.get("categories") or [] if c.get("description"))
    if categories:
        parts.append(f"Categories: {categories}")
    description = info.get("detailed_description") or info.get("short_description")
    if description:
        # Steam's description fields are raw marketing HTML - strip tags before handing to the LLM.
        description = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", description)).strip()[:1500]
        parts.append(f"Store description: {description}")
    if achievements_text:
        parts.append(f"Achievements (often explain an obscure mechanic in plain language):\n{achievements_text}")
    return "\n".join(parts) if parts else None


async def _gather_game_knowledge(game_name: str) -> str:
    parts = []

    igdb_info = await execute_lookup_game_info({"game_name": game_name})
    if igdb_info and not igdb_info.startswith(("No IGDB results", "IGDB isn't configured", "IGDB authentication failed", "IGDB lookup failed")):
        parts.append(f"## IGDB database results\n{igdb_info}")

    steam_info = await _fetch_steam_knowledge(game_name)
    if steam_info:
        parts.append(f"## Steam store results\n{steam_info}")

    web_results = await execute_web_search({"query": f"{game_name} game HUD UI elements explained stats screen"})
    if web_results and not web_results.startswith(("No web search results", "Web search failed")):
        parts.append(f"## Web search results\n{web_results}")

    return "\n\n".join(parts)


async def bootstrap_game_knowledge(process: str) -> None:
    """Fire-and-forget (same contract as the extraction pass): must never raise into the
    poller loop. Skips silently when there's nothing to do - trackers customized AND training
    data already present."""
    key = process.lower()
    if key in _attempted:
        return
    _attempted.add(key)

    trackers = game_state_trackers.get_trackers(process)
    want_trackers = _trackers_are_default(trackers)
    want_training = settings.game_state_training_enabled and not game_state_training_data.get_training_data(process).strip()
    if not want_trackers and not want_training:
        return

    game_name = _guess_game_name(process)
    logger.info("Game bootstrap: gathering knowledge for process=%r (guessed game name %r)", process, game_name)

    try:
        knowledge = await _gather_game_knowledge(game_name)
    except Exception:
        logger.exception("Game bootstrap: knowledge gathering failed for process=%r", process)
        return

    if not knowledge:
        # No external source found anything - still worth calling the model: it may recognize
        # the game by name alone (genre, lore) and produce real trackers instead of this process
        # being stuck on generic RPG-flavored defaults forever. The prompt's own rule ("if you
        # can't identify the game, return empty trackers/null training_data") is the actual
        # safety net here, not bailing out before ever asking.
        logger.info("Game bootstrap: no IGDB/Steam/web results for %r - asking the model anyway, from its own knowledge", game_name)
        knowledge = (
            "(Nothing found via IGDB/Steam/web search. Rely only on your own existing knowledge "
            "of this exact game if you genuinely recognize it by name; otherwise follow the "
            "empty-trackers/null-training_data rule above rather than guessing.)"
        )

    user_content = (
        f"Foreground process: {process}\n"
        f"Guessed game name: {game_name}\n\n"
        f"Gathered information:\n\n{knowledge}"
    )

    try:
        raw = await chat_completion(
            [
                {"role": "system", "content": load_prompt("game_knowledge_bootstrap")},
                {"role": "user", "content": user_content},
            ],
            model=settings.game_bootstrap_model or settings.game_state_model or None,
            json_schema=_BOOTSTRAP_JSON_SCHEMA,
            source="game_bootstrap",
            provider=settings.game_bootstrap_provider or settings.game_state_provider or settings.llm_provider,
        )
        data = parse_json_reply(raw)
    except Exception:
        logger.exception("Game bootstrap: LLM pass failed for process=%r", process)
        return

    if want_trackers:
        new_trackers = [
            {"label": t.get("label", ""), "description": t.get("description", "")}
            for t in data.get("trackers") or []
            if isinstance(t, dict) and (t.get("label") or "").strip()
        ]
        if new_trackers:
            game_state_trackers.set_trackers(process, new_trackers)
            logger.info("Game bootstrap: seeded %d game-specific tracker(s) for process=%r", len(new_trackers), process)

    if want_training:
        training = data.get("training_data")
        if isinstance(training, str) and training.strip():
            game_state_training_data.set_training_data(process, training.strip())
            logger.info("Game bootstrap: seeded starting training data for process=%r", process)


def schedule_bootstrap(process: str) -> None:
    """Kicks off the bootstrap as a background task from the (async) poller without awaiting it -
    tracking and the first extraction pass proceed immediately regardless."""
    if process.lower() in _attempted:
        return
    asyncio.get_running_loop().create_task(bootstrap_game_knowledge(process))


def forget_process(process: str) -> None:
    """Drops the once-per-app-run attempt markers for a process - the base one and every
    `process::variant` one - so a deleted-then-re-approved game actually re-bootstraps. Without
    this, delete_game wiped every data file but this in-memory cache silently swallowed both
    re-scheduled bootstraps (observed live 2026-07-18: detection re-identified the pack at 0.95
    right after a delete, yet trackers stayed at freshly-seeded defaults and the training doc
    never got its Lore section - the same bug class the extraction module's
    forget_tracked_process already fixed for ITS in-memory state on 2026-07-13)."""
    prefix = process.lower()
    _attempted.difference_update({k for k in _attempted if k == prefix or k.startswith(f"{prefix}::")})


def force_refresh_base(process: str, *, reset_trackers: bool = True) -> None:
    """Wipes this process's base training-data document, resets its trackers to the untouched
    defaults, and clears the retry-attempt cache, then re-schedules the bootstrap under whatever
    title is now on file - used when a user correction (see game_correction_tool.py) reveals the
    doc/trackers were seeded under a wrong/generic name (e.g. "javaw" instead of "Minecraft").
    Both are worse than a fresh, accurately-seeded version (same "don't invent" framing as
    game_knowledge_bootstrap.md) - the tracker reset is what lets _trackers_are_default see them
    as eligible again, since otherwise a first bootstrap run under the wrong name would count as
    "already customized" and block ever replacing them. Note: this can't distinguish those from
    trackers a user genuinely hand-edited afterward - a real tradeoff of triggering this from a
    correction rather than only from a still-pristine process.

    `reset_trackers=False` when a variant is (or is about to be) active and its own
    force_refresh_variant call will own that variant's own tracker reset instead - correcting the
    title doesn't need to also touch the base (vanilla) tracker list nobody asked to change.
    (Historical note: trackers used to be keyed by process only, not process+variant, so the base
    and variant bootstraps raced over the exact same stored list - two calls landed 2 seconds apart
    live, one producing excellent modpack-specific trackers, the other generic ones, and whichever
    finished last silently won. Fixed by scoping trackers per-variant like training data already
    was; this flag is now just about not resetting a scope nothing actually changed in.)"""
    game_state_training_data.set_training_data(process, "")
    if reset_trackers:
        game_state_trackers.reset_trackers(process)
    _attempted.discard(process.lower())
    schedule_bootstrap(process)


def force_refresh_trackers(process: str, base_title: str, variant: str | None = None) -> None:
    """Regenerates ONLY this scope's trackers via a fresh bootstrap pass, leaving its training-data
    document completely untouched - unlike force_refresh_base/force_refresh_variant, which reset
    both together. For when the trackers themselves went stale/generic (reverted to defaults, or
    the player just wants a re-roll) but the training notes are still good and don't need
    regenerating too. Resets to the untouched defaults so _trackers_are_default sees them as
    eligible again, clears the retry-attempt cache for this exact scope, and re-schedules the
    matching bootstrap - since want_training in bootstrap_game_knowledge/bootstrap_variant_knowledge
    is independently gated on whether a training document already exists, a non-empty one is simply
    left alone."""
    game_state_trackers.reset_trackers(process, variant=variant)
    if variant:
        _attempted.discard(f"{process.lower()}::{variant.lower()}")
        schedule_variant_bootstrap(process, base_title, variant)
    else:
        _attempted.discard(process.lower())
        schedule_bootstrap(process)


async def bootstrap_variant_knowledge(process: str, base_title: str, modpack: str) -> None:
    """Pack-specific counterpart to the base bootstrap, run when variant detection identifies
    a modpack: web-searches the pack itself (its mechanics, progression, added content) and
    seeds the *variant's own* training-data document — the extraction pass reads and revises
    that document while a session of this variant is active, so vanilla notes and pack notes
    never overwrite each other. Also replaces the trackers when they're still the untouched
    defaults (a skyblock pack tracks very different things than generic RPG fields)."""
    key = f"{process.lower()}::{modpack.lower()}"
    if key in _attempted:
        return
    _attempted.add(key)

    # Independent want-gates, mirroring bootstrap_game_knowledge - NOT a single early-exit on the
    # training doc existing. That single gate silently made force_refresh_trackers a pure
    # reset-to-defaults (observed live 2026-07-18: "regenerate trackers" left plain defaults) -
    # it deliberately keeps the variant's good training doc while resetting trackers, exactly the
    # state the old gate bailed out on, so the reseeding pass never ran at all.
    want_trackers = _trackers_are_default(game_state_trackers.get_trackers(process, variant=modpack))
    want_training = settings.game_state_training_enabled and not game_state_training_data.has_own_training_data(process, modpack)
    if not want_trackers and not want_training:
        return

    logger.info("Variant bootstrap: gathering knowledge for %r (%s)", modpack, base_title)
    try:
        base_knowledge = "" if game_state_training_data.has_own_training_data(process) else await _gather_game_knowledge(base_title)
        # Short and modpack-name-only, not "{modpack} {base_title} modpack overview features
        # progression guide" - that longer form (observed via data/debug_log.json) reliably lost
        # to the base game's own SEO weight (official minecraft.net/wiki pages dominate almost
        # any query containing "Minecraft"), so the pack bootstrap was silently searching the same
        # generic base-game material as the base bootstrap and produced a near-duplicate document.
        # Same "plain keyword queries" discipline as system_companion.md's Web Search guidance.
        pack_results = await execute_web_search({"query": f"{modpack} modpack"})
    except Exception:
        logger.exception("Variant bootstrap: knowledge gathering failed for %r", modpack)
        return
    pack_knowledge = ""
    if pack_results and not pack_results.startswith(("No web search results", "Web search failed")):
        pack_knowledge = f"## Web search results about the modpack \"{modpack}\"\n{pack_results}"
    if not pack_knowledge and not base_knowledge:
        # Same reasoning as the base bootstrap: don't give up just because search came up empty -
        # a modpack the model already recognizes by name can still get real trackers/lore from
        # its own knowledge instead of the base game's generic defaults.
        logger.info("Variant bootstrap: nothing found for %r - asking the model anyway, from its own knowledge", modpack)
        pack_knowledge = (
            f"(No web search results found for the \"{modpack}\" modpack. Rely only on your own "
            "existing knowledge of it if you genuinely recognize it by name; otherwise follow the "
            "empty-trackers/null-training_data rule above rather than guessing.)"
        )

    user_content = (
        f"Foreground process: {process}\n"
        f"Base game: {base_title}\n"
        f"The player is running the \"{modpack}\" modpack/overhaul of it — the notes you produce are for "
        f"THAT modded experience. This is a modpack/variant request: always include the \"## Lore\" section "
        f"(scoped strictly to what \"{modpack}\" itself adds, never general facts about {base_title}) and scope "
        f"\"## UI/UX\" to what the pack adds or changes only, even if you already know {base_title} well.\n\n"
        f"Gathered information:\n\n"
        + "\n\n".join(part for part in (base_knowledge, pack_knowledge) if part)
    )

    try:
        raw = await chat_completion(
            [
                {"role": "system", "content": load_prompt("game_knowledge_bootstrap")},
                {"role": "user", "content": user_content},
            ],
            model=settings.game_bootstrap_model or settings.game_state_model or None,
            json_schema=_BOOTSTRAP_JSON_SCHEMA,
            source="game_bootstrap",
            provider=settings.game_bootstrap_provider or settings.game_state_provider or settings.llm_provider,
        )
        data = parse_json_reply(raw)
    except Exception:
        logger.exception("Variant bootstrap: LLM pass failed for %r", modpack)
        return

    if want_trackers and _trackers_are_default(game_state_trackers.get_trackers(process, variant=modpack)):
        new_trackers = [
            {"label": t.get("label", ""), "description": t.get("description", "")}
            for t in data.get("trackers") or []
            if isinstance(t, dict) and (t.get("label") or "").strip()
        ]
        if new_trackers:
            game_state_trackers.set_trackers(process, new_trackers, variant=modpack)
            logger.info("Variant bootstrap: seeded %d tracker(s) for process=%r (%s)", len(new_trackers), process, modpack)

    # Gated on want_training, not just on the reply containing a document - a trackers-only
    # refresh (force_refresh_trackers) must never overwrite the variant's existing, possibly
    # extraction-pass-enriched document with a fresh generic one.
    training = data.get("training_data")
    if want_training and isinstance(training, str) and training.strip():
        game_state_training_data.set_training_data(process, training.strip(), variant=modpack)
        logger.info("Variant bootstrap: seeded training data for process=%r variant=%r", process, modpack)


def schedule_variant_bootstrap(process: str, base_title: str, modpack: str) -> None:
    if f"{process.lower()}::{modpack.lower()}" in _attempted:
        return
    asyncio.get_running_loop().create_task(bootstrap_variant_knowledge(process, base_title, modpack))


def force_refresh_variant(process: str, base_title: str, modpack: str) -> None:
    """Same idea as force_refresh_base, scoped to one modpack's own training-data document and its
    own tracker list (see game_state_trackers.py) - used when a user correction
    (game_correction_tool.py) sets/fixes a modpack name/tag. Trackers seeded for the wrong pack
    context are just as stale as ones seeded under a wrong title."""
    game_state_training_data.set_training_data(process, "", variant=modpack)
    game_state_trackers.reset_trackers(process, variant=modpack)
    _attempted.discard(f"{process.lower()}::{modpack.lower()}")
    schedule_variant_bootstrap(process, base_title, modpack)
