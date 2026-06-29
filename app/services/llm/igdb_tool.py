import time
from datetime import datetime

import httpx

from app.core.config import settings

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_GAMES_URL = "https://api.igdb.com/v4/games"

IGDB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_game_info",
            "description": (
                "Look up a game in the IGDB database - genre, platforms, release date, rating, and a "
                "summary. Use it for factual game info you're not confident about, rather than guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "game_name": {"type": "string", "description": "The game's title to search for."},
                },
                "required": ["game_name"],
            },
        },
    },
]

# Twitch app access tokens are valid for ~60 days; cache in-process rather than
# re-authenticating on every call. Not persisted - refetched on restart.
_cached_token: str | None = None
_cached_token_expires_at: float = 0.0


async def _get_igdb_token() -> str | None:
    global _cached_token, _cached_token_expires_at

    if not settings.igdb_client_id or not settings.igdb_client_secret:
        return None

    if _cached_token and time.monotonic() < _cached_token_expires_at:
        return _cached_token

    async with httpx.AsyncClient(timeout=10) as http_client:
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

    _cached_token = data["access_token"]
    # Refresh a minute early to avoid using a token that expires mid-request.
    _cached_token_expires_at = time.monotonic() + data.get("expires_in", 0) - 60
    return _cached_token


def _format_game(game: dict) -> str:
    parts = [game.get("name", "Unknown")]

    release_timestamp = game.get("first_release_date")
    if release_timestamp:
        parts.append(f"Released: {datetime.fromtimestamp(release_timestamp).strftime('%Y-%m-%d')}")

    genres = [g["name"] for g in game.get("genres", [])]
    if genres:
        parts.append(f"Genres: {', '.join(genres)}")

    platforms = [p["name"] for p in game.get("platforms", [])]
    if platforms:
        parts.append(f"Platforms: {', '.join(platforms)}")

    if game.get("rating"):
        parts.append(f"Rating: {round(game['rating'])}/100")

    summary = game.get("summary")
    if summary:
        parts.append(f"Summary: {summary}")

    return "\n".join(parts)


async def execute_lookup_game_info(arguments: dict) -> str:
    game_name = (arguments.get("game_name") or "").strip()
    if not game_name:
        return "No game name given."

    try:
        token = await _get_igdb_token()
    except httpx.HTTPError as exc:
        return f"IGDB authentication failed: {exc}"

    if not token:
        return "IGDB isn't configured - no Twitch Client ID/Secret set in Settings."

    query = (
        f'search "{game_name}"; '
        "fields name,summary,first_release_date,genres.name,platforms.name,rating; "
        "limit 3;"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            response = await http_client.post(
                IGDB_GAMES_URL,
                headers={"Client-ID": settings.igdb_client_id, "Authorization": f"Bearer {token}"},
                content=query,
            )
            response.raise_for_status()
            games = response.json()
    except httpx.HTTPError as exc:
        return f"IGDB lookup failed: {exc}"

    if not games:
        return f"No IGDB results found for '{game_name}'."

    return "\n\n".join(_format_game(game) for game in games)
