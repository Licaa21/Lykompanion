import asyncio
import base64
import logging
import struct

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://texttospeech.googleapis.com"
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Cached credentials — google-auth refreshes the token automatically when it expires.
_credentials = None

# One persistent keep-alive client instead of a fresh connection (TCP+TLS handshake) on every
# synthesize() call - narration fires this once per sentence, so a multi-sentence reply used to
# pay handshake cost repeatedly for a fixed remote host.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=_BASE_URL, timeout=30)
    return _client


def _get_access_token() -> str:
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as exc:
        raise RuntimeError(
            "google-auth is not installed. Run: pip install google-auth[requests]"
        ) from exc

    global _credentials
    if _credentials is None:
        _credentials, _ = google.auth.default(scopes=_SCOPES)
    if not _credentials.valid:
        _credentials.refresh(google.auth.transport.requests.Request())
    return _credentials.token


async def _request_auth() -> tuple[dict, dict]:
    """Returns (headers, params) for the chosen auth method.

    API key takes priority when set — no google-auth dependency needed.
    Falls back to Application Default Credentials otherwise.

    User OAuth2 credentials (from gcloud auth application-default login) require
    x-goog-user-project so Google knows which project to bill — this isn't added
    automatically when we build the Bearer header ourselves.
    """
    if settings.google_tts_api_key:
        return {}, {"key": settings.google_tts_api_key}
    token = await asyncio.to_thread(_get_access_token)
    headers = {"Authorization": f"Bearer {token}"}
    quota_project = getattr(_credentials, "quota_project_id", None)
    if quota_project:
        headers["x-goog-user-project"] = quota_project
    return headers, {}


def _wrap_pcm_as_wav(pcm_bytes: bytes, sample_rate: int, channels: int, bits_per_sample: int = 16) -> bytes:
    block_align = channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm_bytes)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample)
    header += b"data" + struct.pack("<I", len(pcm_bytes))
    return header + pcm_bytes


async def synthesize(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    """Synthesize speech via Google Cloud Text-to-Speech (Chirp 3 HD) using Application Default Credentials."""
    voice_name = voice or settings.google_tts_voice
    parts = voice_name.split("-")
    language_code = f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else "en-US"

    headers, params = await _request_auth()
    response = await _get_client().post(
        "/v1/text:synthesize",
        headers=headers,
        params=params,
        json={
            "input": {"text": text},
            "voice": {"languageCode": language_code, "name": voice_name},
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "speakingRate": speed or settings.tts_speed,
            },
        },
    )
    response.raise_for_status()

    pcm_bytes = base64.b64decode(response.json()["audioContent"])
    # Chirp 3 HD returns 24000 Hz mono 16-bit PCM.
    return _wrap_pcm_as_wav(pcm_bytes, sample_rate=24000, channels=1)


async def list_voices() -> list[dict]:
    """Fetch Chirp 3 HD voices. Returns [] if neither API key nor ADC credentials are configured."""
    try:
        headers, params = await _request_auth()
    except Exception as exc:
        logger.info("[chirp3] list_voices auth failed: %r", exc)
        return []
    try:
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=10) as client:
            response = await client.get("/v1/voices", headers=headers, params=params)
            response.raise_for_status()
            voices = response.json().get("voices", [])
        logger.info(
            "[chirp3] fetched %d total voices, %d Chirp3-HD",
            len(voices),
            sum(1 for v in voices if "Chirp3-HD" in v["name"]),
        )
        return [{"id": v["name"], "name": v["name"]} for v in voices if "Chirp3-HD" in v["name"]]
    except Exception as exc:
        body = getattr(getattr(exc, "response", None), "text", "")
        logger.info("[chirp3] list_voices request failed: %r – %s", exc, body)
        return []
