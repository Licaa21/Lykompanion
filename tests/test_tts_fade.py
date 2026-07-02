"""Tests for the TTS fade-in (_apply_fade_in in app/services/tts/__init__.py) -
the anti-click pass applied to every synthesized WAV before playback."""

import struct

from app.services.tts import _FADE_IN_MS, _apply_fade_in


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


def test_fade_silences_first_sample_and_ramps_up():
    sample_rate = 24000
    fade_frames = sample_rate * _FADE_IN_MS // 1000
    samples = [10000] * (fade_frames * 2)
    out = read_samples(_apply_fade_in(make_wav(samples, sample_rate)))

    assert out[0] == 0  # a hard 10000-amplitude onset would click
    assert out[fade_frames // 2] < 10000  # mid-fade is attenuated
    assert out[fade_frames:] == samples[fade_frames:]  # audio past the fade is untouched


def test_fade_is_linear_and_handles_negative_samples():
    sample_rate = 1000
    fade_frames = sample_rate * _FADE_IN_MS // 1000  # 12 frames
    samples = [-8000] * fade_frames + [-8000] * 5
    out = read_samples(_apply_fade_in(make_wav(samples, sample_rate)))
    for i in range(fade_frames):
        assert out[i] == -8000 * i // fade_frames
    assert out[fade_frames:] == [-8000] * 5


def test_stereo_fades_both_channels_per_frame():
    sample_rate = 1000
    fade_frames = sample_rate * _FADE_IN_MS // 1000
    frames = fade_frames + 3
    samples = [6000, -6000] * frames  # L/R interleaved
    out = read_samples(_apply_fade_in(make_wav(samples, sample_rate, channels=2)))
    assert out[0] == 0 and out[1] == 0
    # both channels of the same frame get the same gain
    for frame in range(fade_frames):
        assert out[2 * frame] == 6000 * frame // fade_frames
        assert out[2 * frame + 1] == -6000 * frame // fade_frames
    assert out[2 * fade_frames :] == [6000, -6000] * 3


def test_clip_shorter_than_fade_window_still_ramps():
    wav = make_wav([5000] * 4, sample_rate=24000)
    out = read_samples(_apply_fade_in(wav))
    assert out[0] == 0
    assert all(abs(s) < 5000 for s in out)


def test_non_wav_bytes_pass_through_unchanged():
    mp3ish = b"ID3\x04\x00" + b"\xff" * 64
    assert _apply_fade_in(mp3ish) == mp3ish


def test_truncated_wav_passes_through_unchanged():
    wav = make_wav([1000] * 100)
    truncated = wav[:20]
    assert _apply_fade_in(truncated) == truncated


def test_non_16bit_wav_passes_through_unchanged():
    # 8-bit PCM fmt chunk - fade must not touch it
    pcm = bytes([128] * 50)
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
    header += b"data" + struct.pack("<I", len(pcm))
    wav = header + pcm
    assert _apply_fade_in(wav) == wav
