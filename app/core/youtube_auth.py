"""Per-user YouTube (Google) OAuth token storage (Authorization Code + PKCE grant - see
app/api/youtube_oauth.py) - separate from the app-level Client ID/Secret in Settings/.env, which
only register the OAuth app with Google and are never sufficient on their own to act on a user's
account. data/youtube_auth.json holds the refresh token (long-lived) plus a cached access token
(short-lived, auto-refreshed here). Runtime state, not static config, so it lives in data/ like
reminders/chats rather than .env.

Unlike Spotify, Google's token endpoint still expects the (non-secret, per Google's own docs for
installed apps) Client Secret alongside the PKCE verifier on both the initial exchange and every
refresh - so, unlike spotify_auth.py, this module needs settings.youtube_client_secret too."""

import json
import time
from pathlib import Path

import httpx

from app.core.config import settings

AUTH_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "youtube_auth.json"
TOKEN_URL = "https://oauth2.googleapis.com/token"


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


def get_channel_title() -> str | None:
    return _load().get("channel_title")


def disconnect() -> None:
    if AUTH_PATH.exists():
        AUTH_PATH.unlink()


def save_from_authorization(
    access_token: str, refresh_token: str, expires_in: int, channel_title: str | None
) -> None:
    """Called once by the OAuth callback route right after the user approves access."""
    _save(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + expires_in - 60,
            "channel_title": channel_title,
        }
    )


async def get_valid_access_token() -> str | None:
    """Returns a currently-valid user access token, refreshing via the stored refresh_token if
    expired. None if never connected, the Client ID/Secret aren't set, or the refresh itself fails
    (e.g. the user revoked access from Google's side) - in that last case the stored tokens are
    cleared so Settings correctly reverts to "not connected" instead of a stale, permanently broken
    state that never surfaces to the user."""
    data = _load()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return None

    if data.get("access_token") and time.time() < data.get("expires_at", 0):
        return data["access_token"]

    if not settings.youtube_client_id or not settings.youtube_client_secret:
        return None

    async with httpx.AsyncClient(timeout=10) as http_client:
        response = await http_client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
            },
        )
    if response.status_code != 200:
        disconnect()
        return None

    payload = response.json()
    access_token = payload["access_token"]
    # Google doesn't always rotate the refresh token on refresh - keep the old one if absent.
    data["access_token"] = access_token
    data["refresh_token"] = payload.get("refresh_token", refresh_token)
    data["expires_at"] = time.time() + payload.get("expires_in", 3600) - 60
    _save(data)
    return access_token
