import asyncio
import logging
import os
import sys
import time
import webbrowser

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"

MEDIA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "play_on_youtube",
            "description": (
                "Search YouTube for a song/video and start playing it in the browser. Use this "
                "when the user asks to play something 'on YouTube', or just says 'play <song>' "
                "with no platform named and Spotify isn't clearly implied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for and play, e.g. 'Future - Mask Off'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_on_spotify",
            "description": (
                "Search Spotify for a track and start playing it in the user's local Spotify app. "
                "Use this when the user explicitly says 'on Spotify' or 'in Spotify'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for and play, e.g. 'Future Mask Off'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def _open(url: str) -> None:
    """Best-effort local launch. os.startfile handles custom protocol handlers (spotify:)
    registered with Windows, which webbrowser.open cannot invoke reliably there; everywhere
    else falls back to the standard library's browser opener."""
    if sys.platform == "win32":
        try:
            os.startfile(url)
            return
        except OSError:
            pass
    webbrowser.open(url)


def _search_youtube_sync(query: str) -> dict | None:
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "default_search": "ytsearch1",
        "skip_download": True,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(query, download=False)
    if not info:
        return None
    entries = info.get("entries")
    return entries[0] if entries else info


async def execute_play_on_youtube(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No song/video given to play."

    try:
        result = await asyncio.to_thread(_search_youtube_sync, query)
    except ImportError:
        return "YouTube playback isn't available - yt-dlp isn't installed."
    except Exception as exc:
        logger.exception("YouTube search failed for %r", query)
        return f"Couldn't find that on YouTube: {exc}"

    video_id = result.get("id") if result else None
    if not video_id:
        return f"No YouTube results for '{query}'."

    title = result.get("title") or query
    _open(f"https://www.youtube.com/watch?v={video_id}")
    return f"Now playing '{title}' on YouTube."


# Spotify app access tokens (Client Credentials grant - no user login/Premium needed, only lets us
# search the public catalog) are valid for 1 hour; cached in-process rather than
# re-authenticating on every call. Not persisted - refetched on restart. Mirrors igdb_tool.py's
# Twitch token caching, the same OAuth shape.
_cached_spotify_token: str | None = None
_cached_spotify_token_expires_at: float = 0.0


async def _get_spotify_token() -> str | None:
    global _cached_spotify_token, _cached_spotify_token_expires_at

    if not settings.spotify_client_id or not settings.spotify_client_secret:
        return None

    if _cached_spotify_token and time.monotonic() < _cached_spotify_token_expires_at:
        return _cached_spotify_token

    async with httpx.AsyncClient(timeout=10) as http_client:
        response = await http_client.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(settings.spotify_client_id, settings.spotify_client_secret),
        )
        response.raise_for_status()
        data = response.json()

    _cached_spotify_token = data["access_token"]
    # Refresh a minute early to avoid using a token that expires mid-request.
    _cached_spotify_token_expires_at = time.monotonic() + data.get("expires_in", 0) - 60
    return _cached_spotify_token


async def execute_play_on_spotify(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No song given to play."

    try:
        token = await _get_spotify_token()
    except httpx.HTTPError as exc:
        return f"Spotify authentication failed: {exc}"

    if not token:
        return "Spotify playback isn't configured - no Client ID/Secret set in Settings."

    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            response = await http_client.get(
                SPOTIFY_SEARCH_URL,
                params={"q": query, "type": "track", "limit": 1},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            items = response.json().get("tracks", {}).get("items", [])
    except httpx.HTTPError as exc:
        return f"Spotify search failed: {exc}"

    if not items:
        return f"No Spotify results for '{query}'."

    track = items[0]
    track_id = track.get("id")
    if not track_id:
        return f"No Spotify results for '{query}'."

    title = track.get("name") or query
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
    _open(f"spotify:track:{track_id}")
    return f"Now playing '{title}' by {artists} on Spotify."
