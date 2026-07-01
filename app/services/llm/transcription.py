import time

import httpx

from app.core import debug_log
from app.core.config import settings
from app.core.usage import record_usage


async def transcribe_audio(audio_b64: str, audio_format: str) -> str:
    """Sends audio to the dedicated transcription model and returns the plain-text transcript.

    Dedicated ASR models (Whisper, Chirp, Parakeet, ...) aren't invocable through the regular
    chat-completions API used elsewhere in this app - OpenRouter serves them through a separate
    /audio/transcriptions endpoint with its own JSON request/response shape, so this bypasses the
    shared AsyncOpenAI client in app/services/llm/client.py and hits it directly. OpenRouter-only:
    Google AI Studio and custom OpenAI-compatible endpoints have no equivalent.
    """
    if not settings.transcription_model:
        raise ValueError("Transcription mode is enabled but no transcription model is configured.")

    start = time.monotonic()
    async with httpx.AsyncClient(base_url=settings.openrouter_base_url, timeout=60) as http_client:
        response = await http_client.post(
            "/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={
                "model": settings.transcription_model,
                "input_audio": {"data": audio_b64, "format": audio_format},
            },
        )
        response.raise_for_status()
        data = response.json()
    duration_ms = (time.monotonic() - start) * 1000

    transcript = (data.get("text") or "").strip()
    usage = data.get("usage") or {}
    cost_usd = usage.get("cost", 0) or 0
    record_usage(usage.get("input_tokens", 0) or 0, usage.get("output_tokens", 0) or 0, cost_usd, "transcription")
    debug_log.record_request(
        source="transcription",
        model=settings.transcription_model,
        messages=[{"role": "user", "content": "(audio)"}],
        tools=None,
        reply=transcript,
        tool_calls=None,
        prompt_tokens=usage.get("input_tokens", 0) or 0,
        completion_tokens=usage.get("output_tokens", 0) or 0,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
    )
    return transcript
