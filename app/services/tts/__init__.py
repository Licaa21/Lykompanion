from app.core.config import settings
from app.services.tts import kokoro, openrouter_tts


async def synthesize(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    if settings.tts_provider == "openrouter":
        return await openrouter_tts.synthesize(text, voice, speed)
    return await kokoro.synthesize(text, voice, speed)
