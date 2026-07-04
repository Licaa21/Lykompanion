import asyncio
import logging

import httpx

from app.core import spotify_auth
from app.services.system.browser import open_url as _open

logger = logging.getLogger(__name__)

SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
SPOTIFY_PLAY_URL = "https://api.spotify.com/v1/me/player/play"

# Hard wall-clock cap on the YouTube/YouTube Music search thread, applied at the await site via
# asyncio.wait_for. Both yt-dlp and ytmusicapi make blocking network calls with no reliable
# per-call timeout of their own (yt-dlp's socket_timeout bounds the underlying request in the
# common case, but this is the backstop) - without this, a stalled request to YouTube hangs the
# whole chat turn indefinitely instead of the tool just reporting failure.
_SEARCH_TIMEOUT_SECONDS = 12

MEDIA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "play_on_youtube",
            "description": (
                "Search YouTube for a song OR a video (tutorial, walkthrough, guide, gameplay "
                "footage, trailer, etc.) and start playing it directly in the browser - no need "
                "to also call web_search first, this opens the actual video. Use this whenever the "
                "user asks to play/watch/pull up/find something on YouTube, asks for a video guide "
                "or tutorial, or just says 'play <song>' with no platform named and Spotify isn't "
                "clearly implied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for and play, e.g. 'Future - Mask Off' or 'Skyrim Bleak Falls Barrow dragon claw puzzle tutorial'.",
                    },
                    "prefer_audio": {
                        "type": "boolean",
                        "description": (
                            "true if this is a song/music request - resolves the official-audio "
                            "upload on YouTube Music instead of the official music video. false for "
                            "anything else (tutorials, guides, gameplay, trailers) - a music-catalog "
                            "search would otherwise return an irrelevant song-shaped result instead "
                            "of the actual video the user wants."
                        ),
                    },
                },
                "required": ["query", "prefer_audio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_on_spotify",
            "description": (
                "Search Spotify for a track and start it playing on the user's currently active "
                "Spotify device (phone, desktop app, web player) via their connected account - "
                "genuine remote playback, not just opening a link. Falls back to opening the "
                "track in the local Spotify app if no device is currently active. Use this when "
                "the user explicitly says 'on Spotify' or 'in Spotify'. If the tool reports "
                "Spotify isn't connected, tell the user and offer play_on_youtube instead rather "
                "than failing silently."
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


def _search_youtube_sync(query: str) -> dict | None:
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "default_search": "ytsearch1",
        "skip_download": True,
        # We only ever resolve search metadata here, never download a stream, so the JS-runtime
        # challenge solver yt-dlp defaults to (tries "deno" on PATH) is both unneeded and, on a
        # machine with an incompatible deno.exe on PATH, pops a blocking Windows "can't run this"
        # dialog when yt-dlp tries to invoke it. Equivalent to the --no-js-runtimes CLI flag - the
        # Python API takes the already-parsed form, an empty dict, not the CLI's list syntax.
        "js_runtimes": {},
        "socket_timeout": _SEARCH_TIMEOUT_SECONDS,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(query, download=False)
    if not info:
        return None
    entries = info.get("entries")
    return entries[0] if entries else info


def _search_youtube_music_sync(query: str) -> dict | None:
    """Top "song" match (filter="songs" scopes to YouTube Music's music catalog, which is
    generally the official-audio/topic-channel upload, as opposed to filter=None or "videos"
    which would surface the official music video instead). Anonymous - no login/API key needed
    for public search, same as yt-dlp's scrape-based approach above."""
    from ytmusicapi import YTMusic

    yt = YTMusic()
    results = yt.search(query, filter="songs", limit=1)
    return results[0] if results else None


async def execute_play_on_youtube(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No song/video given to play."

    # Only try YouTube Music's "song" catalog when the model has told us this is actually a music
    # request. ytmusicapi's filter="songs" doesn't return empty for a non-music query (a tutorial,
    # a walkthrough, gameplay footage) - it returns its best-guess song match regardless, which
    # would otherwise silently hijack e.g. "find me a Skyrim puzzle tutorial" into playing an
    # unrelated song. prefer_audio is required in the tool schema so the model must decide.
    prefer_audio = bool(arguments.get("prefer_audio"))
    if prefer_audio:
        try:
            song = await asyncio.wait_for(
                asyncio.to_thread(_search_youtube_music_sync, query), _SEARCH_TIMEOUT_SECONDS
            )
        except ImportError:
            song = None
        except TimeoutError:
            logger.warning("YouTube Music search timed out for %r", query)
            song = None
        except Exception:
            logger.exception("YouTube Music search failed for %r", query)
            song = None

        if song and song.get("videoId"):
            title = song.get("title") or query
            artists = ", ".join(a.get("name", "") for a in song.get("artists", []) if a.get("name"))
            _open(f"https://music.youtube.com/watch?v={song['videoId']}")
            label = f"'{title}'" + (f" by {artists}" if artists else "")
            return f"Now playing {label} on YouTube Music."

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_search_youtube_sync, query), _SEARCH_TIMEOUT_SECONDS
        )
    except ImportError:
        return "YouTube playback isn't available - yt-dlp isn't installed."
    except TimeoutError:
        logger.warning("YouTube search timed out for %r", query)
        return f"YouTube search timed out for '{query}' - try again."
    except Exception as exc:
        logger.exception("YouTube search failed for %r", query)
        return f"Couldn't find that on YouTube: {exc}"

    video_id = result.get("id") if result else None
    if not video_id:
        return f"No YouTube results for '{query}'."

    title = result.get("title") or query
    _open(f"https://www.youtube.com/watch?v={video_id}")
    return f"Now playing '{title}' on YouTube."


async def execute_play_on_spotify(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No song given to play."

    token = await spotify_auth.get_valid_access_token()
    if not token:
        return (
            "Spotify isn't connected - the user needs to connect their account in "
            "Settings → API Keys → Spotify first. Offer to play this on YouTube instead."
        )

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
    label = f"'{title}' by {artists}"

    # Try genuine remote playback on whatever device the user's Spotify is already active on
    # (requires Premium - Spotify's Web API player endpoints are Premium-only). No active device
    # (common when nothing's currently open) falls back to the old deep-link, which launches the
    # local desktop app directly - works without Premium, just can't target a specific device.
    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            play_response = await http_client.put(
                SPOTIFY_PLAY_URL,
                json={"uris": [f"spotify:track:{track_id}"]},
                headers={"Authorization": f"Bearer {token}"},
            )
        if play_response.status_code in (200, 202, 204):
            return f"Now playing {label} on Spotify."
    except httpx.HTTPError:
        pass  # fall through to the deep-link below

    _open(f"spotify:track:{track_id}")
    return f"Opened {label} in the Spotify app (no active device to play it on remotely)."
