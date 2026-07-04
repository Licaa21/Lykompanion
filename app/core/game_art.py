"""Per-process library art: title + cover image shown as a Steam-like library grid in the
Gaming Journal's "My Games" tab. Looked up once per process and cached to disk (data/game_art.json)
so the grid loads instantly after the first visit - Steam's public store-search (no API key needed)
is tried first, falling back to IGDB (needs a configured Client ID/Secret), then SteamGridDB (also
needs a configured API key) as a last resort for whatever still has no cover art.
A user-corrected title (title_overridden=True) is never clobbered by a later re-fetch."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.services.llm.client import get_client

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
GAME_ART_PATH = DATA_DIR / "game_art.json"

STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
STEAMGRIDDB_BASE_URL = "https://www.steamgriddb.com/api/v2"


def _load_all() -> dict[str, dict]:
    if not GAME_ART_PATH.exists():
        return {}
    return json.loads(GAME_ART_PATH.read_text(encoding="utf-8"))


def _save_all(data: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GAME_ART_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_art(process: str) -> dict | None:
    """Cache-only lookup - no network call. Returns None if never fetched."""
    return _load_all().get(process.lower())


def set_title_override(process: str, title: str) -> dict:
    title = title.strip()
    data = _load_all()
    key = process.lower()
    record = data.get(key, {"cover_url": None, "source": None})
    record["title"] = title
    record["title_overridden"] = True
    record.setdefault("first_seen_at", datetime.now(timezone.utc).isoformat())
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    data[key] = record
    _save_all(data)
    return record


def delete_process(process: str) -> None:
    data = _load_all()
    if data.pop(process.lower(), None) is not None:
        _save_all(data)


def _clean_search_term(process: str) -> str:
    name = re.sub(r"\.exe$", "", process, flags=re.IGNORECASE)
    name = re.sub(r"[_\-]+", " ", name)
    # Split camelCase / PascalCase word boundaries (e.g. "EldenRing" -> "Elden Ring").
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return re.sub(r"\s+", " ", name).strip()


async def _fetch_steam(http_client: httpx.AsyncClient, term: str) -> dict | None:
    search_response = await http_client.get(STORE_SEARCH_URL, params={"term": term, "cc": "us", "l": "en"})
    search_response.raise_for_status()
    items = search_response.json().get("items") or []
    if not items:
        return None

    appid = items[0]["id"]
    title = items[0].get("name") or term

    description = None
    try:
        details_response = await http_client.get(APP_DETAILS_URL, params={"appids": appid, "l": "en"})
        details_response.raise_for_status()
        app_data = details_response.json().get(str(appid), {})
        if app_data.get("success"):
            description = app_data["data"].get("short_description") or None
    except httpx.HTTPError:
        pass

    for filename in ("library_600x900.jpg", "header.jpg"):
        url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{filename}"
        # A HEAD request isn't reliably honored by this CDN (some zones 405/misbehave on it even
        # for assets that exist) - stream a GET instead and bail before the body downloads.
        try:
            async with http_client.stream("GET", url) as response:
                if response.status_code == 200 and response.headers.get("content-type", "").startswith("image/"):
                    return {"title": title, "cover_url": url, "description": description, "source": "steam"}
        except httpx.HTTPError:
            continue
    return {"title": title, "cover_url": None, "description": description, "source": "steam"}


_cached_igdb_token: str | None = None
_cached_igdb_token_expires_at: float = 0.0


async def _get_igdb_token(http_client: httpx.AsyncClient) -> str | None:
    global _cached_igdb_token, _cached_igdb_token_expires_at
    import time

    if not settings.igdb_client_id or not settings.igdb_client_secret:
        return None

    if _cached_igdb_token and time.monotonic() < _cached_igdb_token_expires_at:
        return _cached_igdb_token

    response = await http_client.post(
        TWITCH_TOKEN_URL,
        params={
            "client_id": settings.igdb_client_id,
            "client_secret": settings.igdb_client_secret,
            "grant_type": "client_credentials",
        },
    )
    response.raise_for_status()
    data = response.json()
    _cached_igdb_token = data["access_token"]
    _cached_igdb_token_expires_at = time.monotonic() + data.get("expires_in", 0) - 60
    return _cached_igdb_token


async def _fetch_igdb(http_client: httpx.AsyncClient, term: str) -> dict | None:
    token = await _get_igdb_token(http_client)
    if not token:
        return None

    query = f'search "{term}"; fields name,summary,cover.image_id; limit 1;'
    response = await http_client.post(
        IGDB_GAMES_URL,
        headers={"Client-ID": settings.igdb_client_id, "Authorization": f"Bearer {token}"},
        content=query,
    )
    response.raise_for_status()
    games = response.json()
    if not games:
        return None

    game = games[0]
    title = game.get("name") or term
    image_id = (game.get("cover") or {}).get("image_id")
    cover_url = f"https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg" if image_id else None
    return {"title": title, "cover_url": cover_url, "description": game.get("summary"), "source": "igdb"}


async def _fetch_steamgriddb(http_client: httpx.AsyncClient, term: str) -> dict | None:
    """Last-resort art-only source: a community-run grid image database, used only when both
    Steam and IGDB have no cover for the game (e.g. an unreleased/delisted/obscure title).
    Needs a free API key (settings.steamgriddb_api_key) - see steamgriddb.com/profile/preferences/api."""
    if not settings.steamgriddb_api_key:
        return None

    headers = {"Authorization": f"Bearer {settings.steamgriddb_api_key}"}
    search_response = await http_client.get(
        f"{STEAMGRIDDB_BASE_URL}/search/autocomplete/{quote(term)}", headers=headers,
    )
    search_response.raise_for_status()
    results = search_response.json().get("data") or []
    if not results:
        return None

    game_id = results[0]["id"]
    title = results[0].get("name") or term

    # Prefer poster-shaped grids (matching the library card aspect ratio) but fall back to
    # whatever's available if that exact size isn't - SteamGridDB doesn't have every dimension
    # for every game.
    grids_response = await http_client.get(
        f"{STEAMGRIDDB_BASE_URL}/grids/game/{game_id}",
        params={"dimensions": "600x900,342x482"},
        headers=headers,
    )
    grids_response.raise_for_status()
    grids = grids_response.json().get("data") or []
    if not grids:
        return None

    return {"title": title, "cover_url": grids[0]["url"], "description": None, "source": "steamgriddb"}


async def _augment_with_cover(http_client: httpx.AsyncClient, result: dict | None, term: str) -> dict | None:
    """Fills in a missing cover (and description, if still missing) by trying IGDB then
    SteamGridDB in turn, stopping as soon as one produces an image. No-op if `result` already
    has a cover_url."""
    if result and result.get("cover_url"):
        return result

    for fetcher in (_fetch_igdb, _fetch_steamgriddb):
        try:
            extra = await fetcher(http_client, term)
        except httpx.HTTPError:
            extra = None
        if not extra:
            continue
        if result is None:
            result = extra
        else:
            result["cover_url"] = result.get("cover_url") or extra.get("cover_url")
            result["description"] = result.get("description") or extra.get("description")
        if result.get("cover_url"):
            break
    return result


async def _resolve_official_title(term: str) -> str | None:
    """Last-resort fallback when a process name doesn't match anything on Steam/IGDB (obscure,
    delisted, or a launcher/subprocess name that doesn't resemble the real title): ask an LLM
    with OpenRouter's web-search plugin for the game's exact official name, so a follow-up
    Steam/IGDB search under that name has a real shot at finding the right cover art."""
    if not settings.openrouter_api_key:
        return None
    try:
        response = await get_client("openrouter").chat.completions.create(
            model=settings.openrouter_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "The user will give you a game's process/executable name. Search the web and "
                        "reply with ONLY the exact official title of that video game - no extra words, "
                        "no punctuation, no explanation. If you can't confidently identify a real game, "
                        "reply with exactly: unknown"
                    ),
                },
                {"role": "user", "content": term},
            ],
            extra_body={"plugins": [{"id": "web", "max_results": 3}]},
        )
    except Exception:
        return None

    title = (response.choices[0].message.content or "").strip().strip('"').strip()
    if not title or title.lower() == "unknown":
        return None
    return title


async def fetch_art(process: str, force: bool = False) -> dict:
    data = _load_all()
    key = process.lower()
    existing = data.get(key)
    # A record saved before the "description" field existed, or one that matched a Steam/IGDB
    # game but came away with no cover art (e.g. the old HEAD-request check against Steam's CDN
    # produced false negatives), is treated as stale so it gets refetched once instead of being
    # permanently stuck.
    is_fresh = existing and "description" in existing and (existing.get("cover_url") or existing.get("source") is None)
    if existing and not force and is_fresh:
        return existing

    term = _clean_search_term(process)
    result: dict | None = None
    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            try:
                result = await _fetch_steam(http_client, term)
            except httpx.HTTPError:
                result = None
            # Steam matching the game doesn't mean it *has* cover art (e.g. an unreleased title
            # with a store page but no library image yet) - fall through to IGDB then
            # SteamGridDB for art in that case too, not only when Steam found nothing at all.
            result = await _augment_with_cover(http_client, result, term)
    except httpx.HTTPError:
        result = None

    official_title: str | None = None
    if result is None or not result.get("cover_url"):
        # Only worth the LLM+web-search round trip if we still don't have art (or nothing at
        # all) - a resolved official title gives every source above a better shot at matching.
        official_title = await _resolve_official_title(term)
        if official_title and official_title.lower() != term.lower():
            try:
                async with httpx.AsyncClient(timeout=10) as http_client:
                    try:
                        retried = await _fetch_steam(http_client, official_title)
                    except httpx.HTTPError:
                        retried = None
                    retried = await _augment_with_cover(http_client, retried, official_title)
            except httpx.HTTPError:
                retried = None
            if retried:
                if result is None:
                    result = retried
                else:
                    result["cover_url"] = result.get("cover_url") or retried.get("cover_url")
                    result["description"] = result.get("description") or retried.get("description")

    if result is None:
        result = {"title": official_title or term, "cover_url": None, "description": None, "source": None}

    # A user-corrected title survives a re-fetch (e.g. one triggered to retry missing art).
    if existing and existing.get("title_overridden"):
        result["title"] = existing["title"]
        result["title_overridden"] = True

    # "date added" to the journal - stamped once on first fetch, never touched by a later re-fetch.
    result["first_seen_at"] = (existing or {}).get("first_seen_at") or datetime.now(timezone.utc).isoformat()
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    data[key] = result
    _save_all(data)
    return result
