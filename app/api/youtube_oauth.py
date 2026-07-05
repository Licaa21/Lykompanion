import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.core import youtube_auth
from app.core.config import settings
from app.services.llm.youtube_playlist_tool import _fetch_playlist_videos, _fetch_playlists
from app.services.system.browser import open_url

router = APIRouter(prefix="/api/youtube/oauth", tags=["youtube"])

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
# Read-only: enough to list the user's own playlists and their contents. No playback-control
# scope exists for YouTube the way it does for Spotify's Web API - see media_tool.py.
SCOPES = "https://www.googleapis.com/auth/youtube.readonly"

# Single pending CSRF state + PKCE verifier - fine for a single-user desktop app where only one
# Settings window can be running this flow at a time. Cleared after the callback consumes them
# (one-shot). Mirrors app/api/spotify_oauth.py's PKCE flow, except Google's token endpoint still
# wants the (non-secret, per Google's own installed-app docs) Client Secret alongside it.
_pending_state: str | None = None
_pending_verifier: str | None = None


def _redirect_uri(request: Request) -> str:
    """Built from the incoming request's own host/port rather than hardcoded, so it matches
    whatever port the server actually runs on - it must be registered byte-for-byte as an
    Authorized redirect URI in the Google Cloud OAuth client, though (Settings tells the user the
    default)."""
    return str(request.base_url).rstrip("/") + "/api/youtube/oauth/callback"


def _new_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) - see spotify_oauth.py's identical helper for the
    full explanation of RFC 7636 PKCE."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@router.post("/start")
async def start_oauth(request: Request) -> dict:
    """"Connect to YouTube" button: opens the user's real default browser (never the embedded
    desktop webview - Google's login page refuses to load in most in-app webviews) to Google's
    consent screen. Best-effort; reports back if a Client ID/Secret aren't set yet."""
    global _pending_state, _pending_verifier
    if not settings.youtube_client_id or not settings.youtube_client_secret:
        return {"ok": False, "error": "Set a YouTube Client ID and Client Secret first."}

    _pending_state = secrets.token_urlsafe(16)
    _pending_verifier, code_challenge = _new_pkce_pair()
    params = {
        "client_id": settings.youtube_client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(request),
        "scope": SCOPES,
        "state": _pending_state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        # Without these, Google only issues a refresh_token on a user's very first-ever consent
        # for this Client ID - a re-connect after Settings→Disconnect would silently get an
        # access-only grant and break get_valid_access_token()'s refresh path.
        "access_type": "offline",
        "prompt": "consent",
    }
    open_url(f"{AUTHORIZE_URL}?{urlencode(params)}")
    return {"ok": True}


@router.get("/callback")
async def oauth_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> HTMLResponse:
    """Google redirects the user's browser here after they approve/deny access. Exempted from the
    desktop app's x-lyko-token middleware (see app/main.py) - the browser has no way to carry that
    header on this external redirect - so the state check below is what actually prevents an
    unrelated request from completing someone else's pending authorization."""
    global _pending_state, _pending_verifier
    if error or not code or not state or not _pending_state or state != _pending_state:
        return HTMLResponse(
            "<h2>YouTube connection failed or was cancelled.</h2>"
            "<p>You can close this tab and try again from Settings.</p>",
            status_code=400,
        )
    verifier = _pending_verifier
    _pending_state = None
    _pending_verifier = None

    async with httpx.AsyncClient(timeout=10) as http_client:
        token_response = await http_client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(request),
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "code_verifier": verifier,
            },
        )
    if token_response.status_code != 200:
        return HTMLResponse(
            f"<h2>YouTube token exchange failed.</h2><p>{token_response.text}</p>", status_code=502
        )
    payload = token_response.json()

    channel_title = None
    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            channels_response = await http_client.get(
                CHANNELS_URL,
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {payload['access_token']}"},
            )
        if channels_response.status_code == 200:
            items = channels_response.json().get("items", [])
            if items:
                channel_title = items[0].get("snippet", {}).get("title")
    except httpx.HTTPError:
        pass  # cosmetic only - the connection itself already succeeded above

    if "refresh_token" not in payload:
        return HTMLResponse(
            "<h2>YouTube connected, but Google didn't return a refresh token.</h2>"
            "<p>This can happen on a re-connect - go to "
            "<a href=\"https://myaccount.google.com/permissions\" target=\"_blank\">"
            "Google Account → Security → Third-party access</a>, remove this app's access, "
            "then try Connect again.</p>",
            status_code=502,
        )

    youtube_auth.save_from_authorization(
        payload["access_token"], payload["refresh_token"], payload.get("expires_in", 3600), channel_title
    )
    return HTMLResponse(
        f"<h2>Connected to YouTube{f' as {channel_title}' if channel_title else ''}!</h2>"
        "<p>You can close this tab and return to Lykompanion.</p>"
    )


@router.post("/disconnect")
async def disconnect_oauth() -> dict:
    youtube_auth.disconnect()
    return {"ok": True}


# --- Direct playlist listing for the in-app player's "Playlists" button (as opposed to the LLM
# tool calls in youtube_playlist_tool.py, which drive the same data through chat) ---

@router.get("/playlists")
async def list_playlists() -> dict:
    access_token = await youtube_auth.get_valid_access_token()
    if not access_token:
        raise HTTPException(status_code=400, detail="YouTube isn't connected.")
    try:
        playlists = await _fetch_playlists(access_token)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't fetch YouTube playlists: {exc}")
    return {
        "playlists": [
            {"id": p.get("id"), "title": (p.get("snippet") or {}).get("title", "Untitled")}
            for p in playlists
        ]
    }


@router.get("/playlists/{playlist_id}/videos")
async def list_playlist_videos(playlist_id: str) -> dict:
    access_token = await youtube_auth.get_valid_access_token()
    if not access_token:
        raise HTTPException(status_code=400, detail="YouTube isn't connected.")
    try:
        videos = await _fetch_playlist_videos(access_token, playlist_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't fetch playlist videos: {exc}")
    return {"videos": videos}
