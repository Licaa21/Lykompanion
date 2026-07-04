import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core import spotify_auth
from app.core.config import settings
from app.services.system.browser import open_url

router = APIRouter(prefix="/api/spotify/oauth", tags=["spotify"])

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
ME_URL = "https://api.spotify.com/v1/me"
# Enough to search, see what's playing, and start/control playback on the user's active device.
SCOPES = "user-read-playback-state user-modify-playback-state user-read-email user-read-private"

# Single pending CSRF state - fine for a single-user desktop app where only one Settings window
# can be running this flow at a time. Cleared after the callback consumes it (one-shot).
_pending_state: str | None = None


def _redirect_uri(request: Request) -> str:
    """Built from the incoming request's own host/port rather than hardcoded, so it matches
    whatever port the server actually runs on - it must be registered byte-for-byte in the
    Spotify app's dashboard settings, though (Settings tells the user the default)."""
    return str(request.base_url).rstrip("/") + "/api/spotify/oauth/callback"


@router.post("/start")
async def start_oauth(request: Request) -> dict:
    """"Connect to Spotify" button: opens the user's real default browser (never the embedded
    desktop webview - Spotify's login page refuses to load in most in-app webviews) to Spotify's
    consent screen. Best-effort; reports back if Client ID/Secret aren't set yet."""
    global _pending_state
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        return {"ok": False, "error": "Set a Spotify Client ID/Secret first."}

    _pending_state = secrets.token_urlsafe(16)
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(request),
        "scope": SCOPES,
        "state": _pending_state,
    }
    open_url(f"{AUTHORIZE_URL}?{urlencode(params)}")
    return {"ok": True}


@router.get("/callback")
async def oauth_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> HTMLResponse:
    """Spotify redirects the user's browser here after they approve/deny access. Exempted from
    the desktop app's x-lyko-token middleware (see app/main.py) - the browser has no way to carry
    that header on this external redirect - so the state check below is what actually prevents an
    unrelated request from completing someone else's pending authorization."""
    global _pending_state
    if error or not code or not state or not _pending_state or state != _pending_state:
        return HTMLResponse(
            "<h2>Spotify connection failed or was cancelled.</h2>"
            "<p>You can close this tab and try again from Settings.</p>",
            status_code=400,
        )
    _pending_state = None

    async with httpx.AsyncClient(timeout=10) as http_client:
        token_response = await http_client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(request),
            },
            auth=(settings.spotify_client_id, settings.spotify_client_secret),
        )
    if token_response.status_code != 200:
        return HTMLResponse(
            f"<h2>Spotify token exchange failed.</h2><p>{token_response.text}</p>", status_code=502
        )
    payload = token_response.json()

    display_name = None
    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            me_response = await http_client.get(
                ME_URL, headers={"Authorization": f"Bearer {payload['access_token']}"}
            )
        if me_response.status_code == 200:
            display_name = me_response.json().get("display_name")
    except httpx.HTTPError:
        pass  # cosmetic only - the connection itself already succeeded above

    spotify_auth.save_from_authorization(
        payload["access_token"], payload["refresh_token"], payload.get("expires_in", 3600), display_name
    )
    return HTMLResponse(
        f"<h2>Connected to Spotify{f' as {display_name}' if display_name else ''}!</h2>"
        "<p>You can close this tab and return to Lykompanion.</p>"
    )


@router.post("/disconnect")
async def disconnect_oauth() -> dict:
    spotify_auth.disconnect()
    return {"ok": True}
