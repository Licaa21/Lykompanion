import httpx

from app.core.config import settings

# One persistent keep-alive client per base_url instead of a fresh connection (TCP handshake) on
# every synthesize() call - narration fires this once per sentence, so a reply with several
# sentences used to pay connection setup cost repeatedly. Re-created only if the configured
# Kokoro URL changes (e.g. via Settings), same eviction pattern as llm/client.py's get_client().
_client: httpx.AsyncClient | None = None
_client_base_url: str | None = None


def _get_client() -> httpx.AsyncClient:
    global _client, _client_base_url
    base_url = settings.kokoro_base_url
    if _client is None or _client_base_url != base_url:
        _client = httpx.AsyncClient(base_url=base_url, timeout=60)
        _client_base_url = base_url
    return _client


async def synthesize(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    """Synthesize speech via a locally running Kokoro TTS (OpenAI-compatible /v1/audio/speech)."""
    response = await _get_client().post(
        "/audio/speech",
        json={
            "model": "kokoro",
            "input": text,
            "voice": voice or settings.kokoro_voice,
            "response_format": "wav",
            "speed": speed or settings.tts_speed,
        },
    )
    response.raise_for_status()
    return response.content


async def list_voices() -> list[dict]:
    """Fetch available voices ({id, name}) from the local Kokoro server. Returns [] if unreachable."""
    try:
        response = await _get_client().get("/audio/voices")
        response.raise_for_status()
        return response.json().get("voices", [])
    except httpx.HTTPError:
        return []
