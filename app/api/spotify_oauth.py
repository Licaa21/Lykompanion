import base64
import hashlib
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

# Single pending CSRF state + PKCE verifier - fine for a single-user desktop app where only one
# Settings window can be running this flow at a time. Cleared after the callback consumes them
# (one-shot). PKCE (RFC 7636) is what lets this whole flow skip a Client Secret entirely - it's
# designed for exactly this case, a public client that can't keep a secret confidential.
_pending_state: str | None = None
_pending_verifier: str | None = None


def _redirect_uri(request: Request) -> str:
    """Built from the incoming request's own host/port rather than hardcoded, so it matches
    whatever port the server actually runs on - it must be registered byte-for-byte in the
    Spotify app's dashboard settings, though (Settings tells the user the default)."""
    return str(request.base_url).rstrip("/") + "/api/spotify/oauth/callback"


def _new_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge). The verifier is a random string we keep secret
    server-side; the challenge (its SHA-256, base64url-encoded) is what we send Spotify up front -
    at token-exchange time we prove we're the same client that started the flow by revealing the
    verifier, without ever needing a pre-shared Client Secret."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@router.post("/start")
async def start_oauth(request: Request) -> dict:
    """"Connect to Spotify" button: opens the user's real default browser (never the embedded
    desktop webview - Spotify's login page refuses to load in most in-app webviews) to Spotify's
    consent screen. Best-effort; reports back if a Client ID isn't set yet."""
    global _pending_state, _pending_verifier
    if not settings.spotify_client_id:
        return {"ok": False, "error": "Set a Spotify Client ID first."}

    _pending_state = secrets.token_urlsafe(16)
    _pending_verifier, code_challenge = _new_pkce_pair()
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(request),
        "scope": SCOPES,
        "state": _pending_state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
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
    global _pending_state, _pending_verifier
    if error or not code or not state or not _pending_state or state != _pending_state:
        return HTMLResponse(
            "<h2>Spotify connection failed or was cancelled.</h2>"
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
                "client_id": settings.spotify_client_id,
                "code_verifier": verifier,
            },
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
