import struct

from app.core.config import settings
from app.services.tts import chirp3, kokoro, openrouter_tts

# Providers can return audio whose first samples are non-zero (a hard cut or DC
# offset), which the ear hears as a click/pop when playback jumps from silence
# straight into speech. A fade this short is inaudible on the voice itself.
_FADE_IN_MS = 12


def _apply_fade_in(wav_bytes: bytes) -> bytes:
    """Apply a short linear fade-in to the leading samples of a 16-bit PCM WAV.

    Walks the RIFF chunks to find fmt/data rather than assuming a 44-byte
    header. Returns the input unchanged for anything unparseable or non-16-bit
    so a provider format surprise can never break narration.
    """
    try:
        if len(wav_bytes) < 12 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
            return wav_bytes
        pos = 12
        sample_rate = 0
        channels = 1
        while pos + 8 <= len(wav_bytes):
            chunk_id = wav_bytes[pos : pos + 4]
            chunk_size = struct.unpack_from("<I", wav_bytes, pos + 4)[0]
            if chunk_id == b"fmt ":
                audio_format, channels, sample_rate = struct.unpack_from("<HHI", wav_bytes, pos + 8)
                bits_per_sample = struct.unpack_from("<H", wav_bytes, pos + 22)[0]
                if audio_format != 1 or bits_per_sample != 16:
                    return wav_bytes
            elif chunk_id == b"data":
                if not sample_rate:
                    return wav_bytes
                data_start = pos + 8
                avail = min(chunk_size, len(wav_bytes) - data_start) // 2
                fade_frames = sample_rate * _FADE_IN_MS // 1000
                count = min(fade_frames * channels, avail)
                if count <= 0:
                    return wav_bytes
                out = bytearray(wav_bytes)
                samples = struct.unpack_from(f"<{count}h", out, data_start)
                faded = [s * (i // channels) // fade_frames for i, s in enumerate(samples)]
                struct.pack_into(f"<{count}h", out, data_start, *faded)
                return bytes(out)
            pos += 8 + chunk_size + (chunk_size & 1)
        return wav_bytes
    except Exception:
        return wav_bytes


async def synthesize(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    if settings.tts_provider == "openrouter":
        audio = await openrouter_tts.synthesize(text, voice, speed)
    elif settings.tts_provider == "chirp3":
        audio = await chirp3.synthesize(text, voice, speed)
    else:
        audio = await kokoro.synthesize(text, voice, speed)
    return _apply_fade_in(audio)
