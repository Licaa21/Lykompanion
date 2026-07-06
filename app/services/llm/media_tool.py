import asyncio
import logging

import httpx

from app.core import media_state, spotify_auth
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

# Separate, shorter cap for the best-effort "Mix" playlist fetch below - it's a nice-to-have on
# top of the actual requested video, so a slow response should just fall back to playing the one
# video rather than delaying playback further.
_MIX_TIMEOUT_SECONDS = 8
_MIX_MAX_VIDEOS = 25

MEDIA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "play_on_youtube",
            "description": (
                "Search YouTube for a song OR a video (tutorial, walkthrough, guide, gameplay "
                "footage, trailer, etc.) and start playing it in the app's own built-in YouTube "
                "player - no need to also call web_search first, this opens the actual video. Use "
                "this whenever the user asks to play/watch/pull up/find something on YouTube, asks "
                "for a video guide or tutorial, or just says 'play <song>' with no platform named "
                "and Spotify isn't clearly implied. Once something is playing, use "
                "control_youtube_player to pause/resume/restart/skip it. This also auto-queues "
                "YouTube's own generated 'Mix' of similar songs/videos when one is available, so "
                "next/previous keep going - but that's an algorithmic mix, NOT the user's own "
                "named playlist. If they asked for one of their own playlists by name, use "
                "play_youtube_playlist instead (or first, if unsure it's connected)."
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
    {
        "type": "function",
        "function": {
            "name": "control_youtube_player",
            "description": (
                "Control the in-app YouTube player that play_on_youtube already opened - pause, "
                "resume, restart the current video from the beginning, skip to the next/previous "
                "video played this session, set its playback volume, or stop and close the player. "
                "Only call this after a video has actually been played this session; if nothing "
                "has played yet, use play_on_youtube instead. IMPORTANT: 'set_volume' controls the "
                "YouTube video/music playback volume - a request like 'play that quieter' or 'at "
                "half volume' when referring to a song/video means this, NOT set_narration_volume "
                "(which only ever adjusts your own spoken voice, never played media)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play", "pause", "restart", "next", "previous", "stop", "set_volume"],
                        "description": (
                            "'play' resumes a paused/stopped video, 'pause' pauses, 'restart' seeks "
                            "the current video back to 0:00, 'next'/'previous' move through this "
                            "session's play history, 'stop' closes the player entirely, 'set_volume' "
                            "sets the player's own volume (requires the volume argument)."
                        ),
                    },
                    "volume": {
                        "type": "integer",
                        "description": "0-100 - only used with action='set_volume', e.g. 50 for 'half volume'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_now_playing",
            "description": (
                "Check what's actually playing right now in the in-app YouTube player (title, "
                "channel, and what's next/previous in the queue) - use this whenever you need to "
                "answer a question like 'what song is this' or 'what are we listening to' "
                "instead of guessing from memory of what you last played, since the user may have "
                "skipped, picked something from the player's own search/playlist buttons, or the "
                "queue may have auto-advanced since then."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_PLAYER_ACTIONS = {"play", "pause", "restart", "next", "previous", "stop", "set_volume"}
_PLAYER_ACTION_MESSAGES = {
    "play": "Resuming the video.",
    "pause": "Paused.",
    "restart": "Restarting the video from the beginning.",
    "next": "Skipping to the next video.",
    "previous": "Going back to the previous video.",
    "stop": "Stopped and closed the player.",
}


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


_BROWSE_SEARCH_RESULTS = 10


def _search_youtube_multi_sync(query: str) -> list[dict]:
    """Multiple results for the in-app player's own search/browse UI (youtube_search.py's
    endpoint) - unlike _search_youtube_sync above, which only ever needs the single best match for
    a play_on_youtube tool call. extract_flat skips per-video metadata resolution (id+title only),
    same tradeoff as _fetch_mix_playlist_sync below - plenty for a picker list."""
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "default_search": f"ytsearch{_BROWSE_SEARCH_RESULTS}",
        "js_runtimes": {},
        "socket_timeout": _SEARCH_TIMEOUT_SECONDS,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(query, download=False)
    if not info:
        return []
    entries = info.get("entries") or []
    results = []
    for entry in entries:
        video_id = entry.get("id")
        if video_id:
            results.append({"video_id": video_id, "title": entry.get("title") or "Untitled"})
    return results


def _fetch_mix_playlist_sync(video_id: str) -> list[dict]:
    """YouTube auto-generates a "Mix" playlist (id `RD<video_id>`) of similar songs/videos for
    almost every video - no OAuth or curated playlist needed. extract_flat skips per-video
    metadata resolution (we only need id + title), which keeps this fast enough to run on every
    play_on_youtube call without materially delaying playback."""
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": _MIX_MAX_VIDEOS,
        "js_runtimes": {},
        "socket_timeout": _MIX_TIMEOUT_SECONDS,
    }
    url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return []
    entries = info.get("entries") or []
    videos = []
    for entry in entries:
        entry_id = entry.get("id")
        if entry_id:
            videos.append({"video_id": entry_id, "title": entry.get("title") or "Untitled"})
    return videos


def _search_youtube_music_sync(query: str) -> dict | None:
    """Top "song" match (filter="songs" scopes to YouTube Music's music catalog, which is
    generally the official-audio/topic-channel upload, as opposed to filter=None or "videos"
    which would surface the official music video instead). Anonymous - no login/API key needed
    for public search, same as yt-dlp's scrape-based approach above."""
    from ytmusicapi import YTMusic

    yt = YTMusic()
    results = yt.search(query, filter="songs", limit=1)
    return results[0] if results else None


async def _build_player_payload(video_id: str, title: str) -> dict:
    """Best-effort attaches YouTube's auto-generated "Mix" for the resolved video so
    next/previous (control_youtube_player) walk through more of the same kind of music/video
    instead of stopping after the one requested. Falls back to a single-video payload - same
    shape play_on_youtube always returned - if the mix fetch fails or comes back empty."""
    try:
        mix = await asyncio.wait_for(
            asyncio.to_thread(_fetch_mix_playlist_sync, video_id), _MIX_TIMEOUT_SECONDS
        )
    except Exception:
        logger.warning("YouTube Mix fetch failed for video %s", video_id, exc_info=True)
        mix = []

    if not mix:
        return {"video_id": video_id, "title": title}

    videos = [{"video_id": video_id, "title": title}]
    videos.extend(v for v in mix if v["video_id"] != video_id)
    return {"videos": videos, "title": title}


async def execute_play_on_youtube(arguments: dict) -> tuple[str, dict | None]:
    """Returns (tool_message, player_payload). player_payload (video_id + title, or a videos list
    when a Mix queue was attached, or None on failure) drives the in-app YouTube IFrame player -
    callers no longer open a browser tab."""
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No song/video given to play.", None

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
            song_title = song.get("title") or query
            artists = ", ".join(a.get("name", "") for a in song.get("artists", []) if a.get("name"))
            label = f"'{song_title}'" + (f" by {artists}" if artists else "")
            # YouTube Music's "songs" search returns just the bare track name in `title` (unlike a
            # regular video search, whose `title` is the uploader's own, usually "Artist - Song")
            # - without the artist stitched in here, the in-app player panel/queue only ever shows
            # the song name with no way to tell whose version is playing.
            display_title = f"{song_title} - {artists}" if artists else song_title
            payload = await _build_player_payload(song["videoId"], display_title)
            suffix = " and queued a Mix of similar songs" if "videos" in payload else ""
            return f"Now playing {label} on YouTube Music{suffix}.", payload

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_search_youtube_sync, query), _SEARCH_TIMEOUT_SECONDS
        )
    except ImportError:
        return "YouTube playback isn't available - yt-dlp isn't installed.", None
    except TimeoutError:
        logger.warning("YouTube search timed out for %r", query)
        return f"YouTube search timed out for '{query}' - try again.", None
    except Exception as exc:
        logger.exception("YouTube search failed for %r", query)
        return f"Couldn't find that on YouTube: {exc}", None

    video_id = result.get("id") if result else None
    if not video_id:
        return f"No YouTube results for '{query}'.", None

    title = result.get("title") or query
    payload = await _build_player_payload(video_id, title)
    suffix = " and queued a Mix of similar videos" if "videos" in payload else ""
    return f"Now playing '{title}' on YouTube{suffix}.", payload


async def execute_control_youtube_player(arguments: dict) -> tuple[str, str | None, int | None]:
    """Returns (tool_message, action, volume). action (or None if invalid/unrecognized) and volume
    (only for action='set_volume') are relayed to the frontend's in-app YouTube player as a side
    effect - this tool itself has no way to know the player's actual state (nothing is playing,
    queue is empty, etc.), it just forwards the request; media_state's last-pushed snapshot (see
    get_now_playing below) is used on a best-effort basis to name the next/previous track."""
    action = (arguments.get("action") or "").strip().lower()
    if action not in _PLAYER_ACTIONS:
        return f"Unknown player action '{action}'.", None, None

    if action == "set_volume":
        try:
            volume = max(0, min(100, int(arguments.get("volume"))))
        except (TypeError, ValueError):
            return "No volume level given.", None, None
        return f"Set the video's volume to {volume}%.", action, volume

    if action in ("next", "previous"):
        now_playing = media_state.get_now_playing()
        neighbor = now_playing and now_playing[action]  # "next"/"previous" key matches the action
        if neighbor:
            verb = "Skipping to" if action == "next" else "Going back to"
            return f"{verb} '{neighbor['title']}'.", action, None
        # No queue known, or already at that end - fall back to the generic message rather than
        # claiming a title we don't actually have.

    return _PLAYER_ACTION_MESSAGES[action], action, None


def execute_get_now_playing(arguments: dict) -> str:
    now_playing = media_state.get_now_playing()
    if not now_playing:
        return "Nothing is currently playing in the in-app YouTube player."

    current = now_playing["current"]
    label = f"'{current['title']}'" + (f" by {current['channel']}" if current.get("channel") else "")
    state = "playing" if now_playing["playing"] else "paused"
    position = f"{now_playing['index'] + 1} of {now_playing['queue_length']}"
    parts = [f"Now {state}: {label} (track {position} in the queue)."]

    next_entry = now_playing.get("next")
    if next_entry:
        parts.append(f"Up next: '{next_entry['title']}'.")
    previous_entry = now_playing.get("previous")
    if previous_entry:
        parts.append(f"Previous: '{previous_entry['title']}'.")

    return " ".join(parts)


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
