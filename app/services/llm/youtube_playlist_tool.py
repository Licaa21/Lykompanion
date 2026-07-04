import httpx

from app.core import youtube_auth

PLAYLISTS_URL = "https://www.googleapis.com/youtube/v3/playlists"
PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
_REQUEST_TIMEOUT_SECONDS = 10
# One page is plenty for a personal account and keeps this to a single request each - a heavy
# user with more playlists/items than this can still ask for a differently-worded playlist name.
_MAX_RESULTS = 50

YOUTUBE_PLAYLIST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_youtube_playlists",
            "description": (
                "List the names of the user's own YouTube playlists (requires their YouTube "
                "account to be connected in Settings). Use this when they ask what playlists "
                "they have, or you need to see the exact name before calling play_youtube_playlist."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_youtube_playlist",
            "description": (
                "Find one of the user's own YouTube playlists by name (fuzzy match - doesn't need "
                "to be exact) and start playing it in the app's in-app YouTube player, queuing "
                "every video in it so next/previous (control_youtube_player) walk through the "
                "whole playlist. Requires their YouTube account to be connected in Settings - if "
                "not connected, tell them and offer play_on_youtube instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "playlist_name": {
                        "type": "string",
                        "description": "The playlist to play, e.g. 'road trip' or 'Chill Vibes'.",
                    },
                },
                "required": ["playlist_name"],
            },
        },
    },
]


async def _fetch_playlists(access_token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as http_client:
        response = await http_client.get(
            PLAYLISTS_URL,
            params={"part": "snippet", "mine": "true", "maxResults": _MAX_RESULTS},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    response.raise_for_status()
    return response.json().get("items", [])


async def _fetch_playlist_videos(access_token: str, playlist_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as http_client:
        response = await http_client.get(
            PLAYLIST_ITEMS_URL,
            params={"part": "snippet", "playlistId": playlist_id, "maxResults": _MAX_RESULTS},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    response.raise_for_status()
    videos = []
    for item in response.json().get("items", []):
        snippet = item.get("snippet", {})
        video_id = (snippet.get("resourceId") or {}).get("videoId")
        # Deleted/private videos still show up as a placeholder item with no resolvable video id.
        if video_id:
            videos.append({"video_id": video_id, "title": snippet.get("title") or "Untitled"})
    return videos


def _find_best_match(playlists: list[dict], query: str) -> dict | None:
    """Case-insensitive substring match first (either direction), falling back to the first
    playlist sharing any whole word with the query - good enough for "play my road trip playlist"
    against a "Road Trip 2024" title without needing a real fuzzy-matching dependency."""
    query_lower = query.strip().lower()
    if not query_lower:
        return None

    for playlist in playlists:
        title = (playlist.get("snippet") or {}).get("title", "")
        title_lower = title.lower()
        if query_lower in title_lower or title_lower in query_lower:
            return playlist

    query_words = set(query_lower.split())
    for playlist in playlists:
        title_lower = (playlist.get("snippet") or {}).get("title", "").lower()
        if query_words & set(title_lower.split()):
            return playlist

    return None


async def execute_list_youtube_playlists(arguments: dict) -> str:
    access_token = await youtube_auth.get_valid_access_token()
    if not access_token:
        return "YouTube isn't connected - the user needs to connect their account in Settings → API Keys → YouTube first."

    try:
        playlists = await _fetch_playlists(access_token)
    except httpx.HTTPError as exc:
        return f"Couldn't fetch YouTube playlists: {exc}"

    if not playlists:
        return "The user has no YouTube playlists."

    names = [(p.get("snippet") or {}).get("title", "Untitled") for p in playlists]
    return "The user's YouTube playlists: " + ", ".join(names)


async def execute_play_youtube_playlist(arguments: dict) -> tuple[str, dict | None]:
    """Returns (tool_message, player_payload). player_payload ({"videos": [...], "title": ...},
    or None on failure) is queued into the in-app player the same way a single play_on_youtube
    video is, just with every playlist video pre-loaded so next/previous walk the real playlist."""
    playlist_name = (arguments.get("playlist_name") or "").strip()
    if not playlist_name:
        return "No playlist name given.", None

    access_token = await youtube_auth.get_valid_access_token()
    if not access_token:
        return (
            "YouTube isn't connected - the user needs to connect their account in "
            "Settings → API Keys → YouTube first. Offer play_on_youtube instead.",
            None,
        )

    try:
        playlists = await _fetch_playlists(access_token)
    except httpx.HTTPError as exc:
        return f"Couldn't fetch YouTube playlists: {exc}", None

    match = _find_best_match(playlists, playlist_name)
    if not match:
        return f"No playlist matching '{playlist_name}' was found.", None

    title = (match.get("snippet") or {}).get("title", playlist_name)
    playlist_id = match.get("id")
    try:
        videos = await _fetch_playlist_videos(access_token, playlist_id)
    except httpx.HTTPError as exc:
        return f"Couldn't fetch videos in '{title}': {exc}", None

    if not videos:
        return f"'{title}' has no playable videos.", None

    return f"Now playing your '{title}' playlist ({len(videos)} videos).", {"videos": videos, "title": title}
