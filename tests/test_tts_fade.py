"""Tests for the TTS post-processing pipeline (app/services/tts/__init__.py) -
the fade-in/out, declick, and normalize passes applied to every synthesized WAV
before playback."""

import struct

from app.services.tts import _FADE_MS, _apply_fades, _declick, _normalize, _postprocess


def make_wav(samples: list[int], sample_rate: int = 24000, channels: int = 1) -> bytes:
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    block_align = channels * 2
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, sample_rate * block_align, block_align, 16)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def read_samples(wav: bytes) -> list[int]:
    data_at = wav.index(b"data") + 8
    count = (len(wav) - data_at) // 2
    return list(struct.unpack_from(f"<{count}h", wav, data_at))


# --- _apply_fades ---


def test_fade_in_and_out_silence_the_edges():
    sample_rate = 24000
    fade_frames = sample_rate * _FADE_MS // 1000
    samples = [10000] * (fade_frames * 4)
    _apply_fades(samples, sample_rate, channels=1)
    assert samples[0] == 0
    assert samples[-1] == 0
    assert samples[fade_frames // 2] < 10000
    assert samples[-(fade_frames // 2 + 1)] < 10000
    assert samples[len(samples) // 2] == 10000  # middle of the clip is untouched


def test_fade_is_linear_and_handles_negative_samples():
    sample_rate = 1000
    fade_frames = sample_rate * _FADE_MS // 1000  # 12 frames
    samples = [-8000] * (fade_frames * 3)
    _apply_fades(samples, sample_rate, channels=1)
    for i in range(fade_frames):
        assert samples[i] == -8000 * i // fade_frames


def test_stereo_fades_both_channels_per_frame():
    sample_rate = 1000
    fade_frames = sample_rate * _FADE_MS // 1000
    samples = [6000, -6000] * (fade_frames * 3)  # L/R interleaved
    _apply_fades(samples, sample_rate, channels=2)
    assert samples[0] == 0 and samples[1] == 0
    for frame in range(fade_frames):
        assert samples[2 * frame] == 6000 * frame // fade_frames
        assert samples[2 * frame + 1] == -6000 * frame // fade_frames


def test_clip_shorter_than_fade_window_still_ramps():
    samples = [5000] * 4
    _apply_fades(samples, sample_rate=24000, channels=1)
    assert samples[0] == 0
    assert all(abs(s) < 5000 for s in samples)


# --- _declick ---


def test_declick_smooths_isolated_spike():
    samples = [100, 120, 30000, 110, 90] + [100] * 10
    _declick(samples)
    assert samples[2] == 115  # replaced with (prev + next) // 2


def test_declick_leaves_natural_loud_attack_alone():
    # A real onset ramps up over several samples and stays up - not an isolated spike.
    samples = [0, 5000, 15000, 25000, 30000, 30000, 30000]
    before = list(samples)
    _declick(samples)
    assert samples == before


# --- _normalize ---


def test_normalize_scales_quiet_audio_up():
    samples = [1000, -1000, 500, -500]
    _normalize(samples)
    assert max(abs(s) for s in samples) > 1000


def test_normalize_scales_loud_audio_down():
    samples = [32000, -32000, 16000]
    _normalize(samples)
    assert max(abs(s) for s in samples) < 32000


def test_normalize_skips_silence():
    samples = [0, 0, 0]
    _normalize(samples)
    assert samples == [0, 0, 0]


def test_normalize_clamps_extreme_scale_up():
    # A near-silent buffer shouldn't get amplified into noise - scale is capped at 2.5x.
    samples = [1, -1, 0]
    _normalize(samples)
    assert max(abs(s) for s in samples) == 2


# --- _postprocess (full pipeline / passthrough safety) ---


def test_postprocess_fades_a_valid_wav():
    wav = make_wav([10000] * 2000, sample_rate=24000)
    out = read_samples(_postprocess(wav))
    assert out[0] == 0
    assert out[-1] == 0


def test_non_wav_bytes_pass_through_unchanged():
    mp3ish = b"ID3\x04\x00" + b"\xff" * 64
    assert _postprocess(mp3ish) == mp3ish


def test_truncated_wav_passes_through_unchanged():
    wav = make_wav([1000] * 100)
    truncated = wav[:20]
    assert _postprocess(truncated) == truncated


def test_non_16bit_wav_passes_through_unchanged():
    # 8-bit PCM fmt chunk - post-processing must not touch it
    pcm = bytes([128] * 50)
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
    header += b"data" + struct.pack("<I", len(pcm))
    wav = header + pcm
    assert _postprocess(wav) == wav
