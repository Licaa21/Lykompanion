"""Per-process library art: title + cover image shown as a Steam-like library grid in the
Gaming Journal's "My Games" tab. Looked up once per process and cached to disk (data/game_art.json)
so the grid loads instantly after the first visit - Steam's public store-search (no API key needed)
is tried first, falling back to IGDB (needs a configured Client ID/Secret) for non-Steam games.
A user-corrected title (title_overridden=True) is never clobbered by a later re-fetch."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.core.config import settings
from app.services.llm.client import get_client

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
GAME_ART_PATH = DATA_DIR / "game_art.json"

STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"


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
            # with a store page but no library image yet) - try IGDB for art in that case too,
            # rather than only when Steam found nothing at all.
            if result is None or not result.get("cover_url"):
                try:
                    igdb_result = await _fetch_igdb(http_client, term)
                except httpx.HTTPError:
                    igdb_result = None
                if igdb_result:
                    if result is None:
                        result = igdb_result
                    else:
                        result["cover_url"] = result.get("cover_url") or igdb_result.get("cover_url")
                        result["description"] = result.get("description") or igdb_result.get("description")
    except httpx.HTTPError:
        result = None

    official_title: str | None = None
    if result is None:
        official_title = await _resolve_official_title(term)
        if official_title:
            try:
                async with httpx.AsyncClient(timeout=10) as http_client:
                    try:
                        result = await _fetch_steam(http_client, official_title)
                    except httpx.HTTPError:
                        result = None
                    if result is None:
                        try:
                            result = await _fetch_igdb(http_client, official_title)
                        except httpx.HTTPError:
                            result = None
            except httpx.HTTPError:
                result = None

    if result is None:
        result = {"title": official_title or term, "cover_url": None, "description": None, "source": None}

    # A user-corrected title survives a re-fetch (e.g. one triggered to retry missing art).
    if existing and existing.get("title_overridden"):
        result["title"] = existing["title"]
        result["title_overridden"] = True

    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    data[key] = result
    _save_all(data)
    return result
