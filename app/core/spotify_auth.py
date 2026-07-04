"""Per-user Spotify OAuth token storage (Authorization Code + PKCE grant - see
app/api/spotify_oauth.py) - separate from the app-level Client ID in Settings/.env, which only
registers the OAuth app with Spotify and is never sufficient on its own to act on a user's
account. data/spotify_auth.json holds the refresh token (long-lived) plus a cached access token
(short-lived, auto-refreshed here). Runtime state, not static config, so it lives in data/ like
reminders/chats rather than .env. PKCE means no Client Secret is needed anywhere in this flow,
including here on refresh - just the (non-secret) Client ID."""

import json
import time
from pathlib import Path

import httpx

from app.core.config import settings

AUTH_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "spotify_auth.json"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def _load() -> dict:
    if not AUTH_PATH.exists():
        return {}
    try:
        return json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_PATH.write_text(json.dumps(data), encoding="utf-8")


def is_connected() -> bool:
    return bool(_load().get("refresh_token"))


def get_display_name() -> str | None:
    return _load().get("display_name")


def disconnect() -> None:
    if AUTH_PATH.exists():
        AUTH_PATH.unlink()


def save_from_authorization(
    access_token: str, refresh_token: str, expires_in: int, display_name: str | None
) -> None:
    """Called once by the OAuth callback route right after the user approves access."""
    _save(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + expires_in - 60,
            "display_name": display_name,
        }
    )


async def get_valid_access_token() -> str | None:
    """Returns a currently-valid user access token, refreshing via the stored refresh_token if
    expired. None if never connected, the Client ID isn't set, or the refresh itself fails (e.g.
    the user revoked access from Spotify's side) - in that last case the stored tokens are cleared
    so Settings correctly reverts to "not connected" instead of a stale, permanently broken state
    that never surfaces to the user."""
    data = _load()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return None

    if data.get("access_token") and time.time() < data.get("expires_at", 0):
        return data["access_token"]

    if not settings.spotify_client_id:
        return None

    # PKCE: no Client Secret / Basic auth here - client_id travels in the body instead, same as
    # the original authorization_code exchange in app/api/spotify_oauth.py.
    async with httpx.AsyncClient(timeout=10) as http_client:
        response = await http_client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.spotify_client_id,
            },
        )
    if response.status_code != 200:
        disconnect()
        return None

    payload = response.json()
    access_token = payload["access_token"]
    # Spotify doesn't always rotate the refresh token on refresh - keep the old one if absent.
    data["access_token"] = access_token
    data["refresh_token"] = payload.get("refresh_token", refresh_token)
    data["expires_at"] = time.time() + payload.get("expires_in", 3600) - 60
    _save(data)
    return access_token
