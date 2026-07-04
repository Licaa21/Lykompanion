import struct

from app.core.config import settings
from app.services.tts import chirp3, kokoro, openrouter_tts

# Providers can return audio whose first/last samples are non-zero (a hard cut or DC
# offset), which the ear hears as a click/pop when playback jumps from/to silence.
# A fade this short is inaudible on the voice itself.
_FADE_MS = 12

# A "click" is one sample that jumps far from its neighbors and back again within a
# couple samples - a decoder/concatenation glitch, not a natural loud attack (which
# ramps over many samples). Threshold is in raw int16 units.
_CLICK_JUMP_THRESHOLD = 6000
_CLICK_NEIGHBOR_THRESHOLD = 2000

# Peak-normalize toward this fraction of full scale (~-1 dBFS) so volume is consistent
# across providers/voices that synthesize at different natural loudness. Scale factor is
# clamped so a corrupt/near-silent buffer can't get amplified into noise.
_NORMALIZE_TARGET_PEAK = 0.89
_NORMALIZE_MIN_SCALE = 0.4
_NORMALIZE_MAX_SCALE = 2.5


def _find_pcm16_data(wav_bytes: bytes) -> tuple[int, int, int, int] | None:
    """Walk RIFF chunks to locate a 16-bit PCM data chunk.

    Returns (data_start, sample_count, sample_rate, channels), or None if the
    input isn't parseable / isn't 16-bit PCM - callers should return the input
    unchanged in that case so a provider format surprise can never break narration.
    """
    if len(wav_bytes) < 12 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        return None
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
                return None
        elif chunk_id == b"data":
            if not sample_rate:
                return None
            data_start = pos + 8
            count = min(chunk_size, len(wav_bytes) - data_start) // 2
            return data_start, count, sample_rate, channels
        pos += 8 + chunk_size + (chunk_size & 1)
    return None


def _declick(samples: list[int]) -> None:
    """Smooth isolated single-sample spikes in place (decoder/concatenation glitches).

    A real click stands out on both sides (jumps away from its neighbor and the
    neighbor beyond it jumps back), whereas a genuine loud attack ramps up and stays
    up. Only that narrow signature gets replaced, via linear interpolation across it.
    """
    n = len(samples)
    for i in range(1, n - 1):
        prev_s, cur, next_s = samples[i - 1], samples[i], samples[i + 1]
        if (
            abs(cur - prev_s) > _CLICK_JUMP_THRESHOLD
            and abs(cur - next_s) > _CLICK_JUMP_THRESHOLD
            and abs(next_s - prev_s) < _CLICK_NEIGHBOR_THRESHOLD
        ):
            samples[i] = (prev_s + next_s) // 2


def _normalize(samples: list[int]) -> None:
    """Scale samples in place toward a consistent peak level, clamped to a safe range."""
    peak = max((abs(s) for s in samples), default=0)
    if peak == 0:
        return
    scale = (_NORMALIZE_TARGET_PEAK * 32767) / peak
    scale = max(_NORMALIZE_MIN_SCALE, min(_NORMALIZE_MAX_SCALE, scale))
    if abs(scale - 1.0) < 0.01:
        return
    for i, s in enumerate(samples):
        v = int(s * scale)
        samples[i] = 32767 if v > 32767 else -32768 if v < -32768 else v


def _apply_fades(samples: list[int], sample_rate: int, channels: int) -> None:
    """Short linear fade-in/out at the edges, in place, so playback never jumps
    straight from/to silence (a click at the very start or end of the clip)."""
    fade_frames = sample_rate * _FADE_MS // 1000
    count = min(fade_frames * channels, len(samples))
    for i in range(count):
        samples[i] = samples[i] * (i // channels) // fade_frames
    for i in range(count):
        j = len(samples) - 1 - i
        samples[j] = samples[j] * (i // channels) // fade_frames


def _postprocess(wav_bytes: bytes) -> bytes:
    try:
        parsed = _find_pcm16_data(wav_bytes)
        if parsed is None:
            return wav_bytes
        data_start, count, sample_rate, channels = parsed
        if count <= 0:
            return wav_bytes
        out = bytearray(wav_bytes)
        samples = list(struct.unpack_from(f"<{count}h", out, data_start))
        _declick(samples)
        _normalize(samples)
        _apply_fades(samples, sample_rate, channels)
        struct.pack_into(f"<{count}h", out, data_start, *samples)
        return bytes(out)
    except Exception:
        return wav_bytes


async def synthesize(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    if settings.tts_provider == "openrouter":
        audio = await openrouter_tts.synthesize(text, voice, speed)
    elif settings.tts_provider == "chirp3":
        audio = await chirp3.synthesize(text, voice, speed)
    else:
        audio = await kokoro.synthesize(text, voice, speed)
    return _postprocess(audio)
