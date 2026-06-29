import httpx

from app.core.config import settings


async def synthesize(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    """Synthesize speech via a locally running Kokoro TTS (OpenAI-compatible /v1/audio/speech)."""
    async with httpx.AsyncClient(base_url=settings.kokoro_base_url, timeout=60) as client:
        response = await client.post(
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
        async with httpx.AsyncClient(base_url=settings.kokoro_base_url, timeout=5) as client:
            response = await client.get("/audio/voices")
            response.raise_for_status()
            return response.json().get("voices", [])
    except httpx.HTTPError:
        return []
