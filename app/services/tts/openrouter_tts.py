import re
import struct

from app.core.config import settings
from app.services.llm.client import client


def _parse_pcm_format(content_type: str) -> tuple[int, int]:
    rate_match = re.search(r"rate=(\d+)", content_type)
    channels_match = re.search(r"channels=(\d+)", content_type)
    rate = int(rate_match.group(1)) if rate_match else 24000
    channels = int(channels_match.group(1)) if channels_match else 1
    return rate, channels


def _wrap_pcm_as_wav(pcm_bytes: bytes, sample_rate: int, channels: int, bits_per_sample: int = 16) -> bytes:
    block_align = channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm_bytes)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample)
    header += b"data" + struct.pack("<I", len(pcm_bytes))
    return header + pcm_bytes


async def synthesize(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    """Synthesize speech via an OpenRouter Speech-category model (OpenAI-compatible audio.speech).

    Only models with output_modalities containing "speech" work here — models
    that merely have "audio" output (e.g. Lyria, a music generator) are a
    different category and will 400 with "Model does not exist" on this endpoint.

    Always requests response_format="pcm" rather than "mp3": some models (e.g.
    Gemini TTS) reject "mp3" outright ("only supports response_format=pcm"),
    and OpenRouter doesn't support "wav" as a response_format at all. Raw PCM
    has no container/header of its own, so the actual sample rate and channel
    count are parsed from the response's Content-Type header (e.g.
    "audio/pcm;rate=24000;channels=1") and used to wrap the bytes into a real
    WAV file — the browser can't play headerless PCM directly.

    Each Speech model has its own voice catalog (e.g. Gemini uses "Kore",
    "Puck"... while Kokoro uses "af_heart" etc.) — OpenAI's "alloy" only
    exists as a last-resort fallback and will likely error on most providers,
    hence preferring settings.openrouter_voice when no voice is explicitly given.
    """
    response = await client.audio.speech.create(
        model=settings.openrouter_tts_model,
        voice=voice or settings.openrouter_voice or "alloy",
        input=text,
        response_format="pcm",
        speed=speed or settings.tts_speed,
    )
    content_type = response.response.headers.get("content-type", "")
    sample_rate, channels = _parse_pcm_format(content_type)
    return _wrap_pcm_as_wav(response.read(), sample_rate, channels)
