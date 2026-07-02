"""The voice-turn transcript extraction: the audio-capable main model prefixes its reply with
<transcript>...</transcript>, which chat.py peels off (streaming and non-streaming) instead of
paying for a dedicated STT model."""

import asyncio

from app.api.chat import _peel_transcript, _split_transcript


def _run(events):
    async def inner():
        for e in events:
            yield e

    async def collect():
        return [e async for e in _peel_transcript(inner())]

    return asyncio.run(collect())


def _deltas(*texts):
    return [{"type": "delta", "text": t} for t in texts]


def test_split_transcript_present():
    transcript, reply = _split_transcript("<transcript>hello there</transcript>\nHi! How can I help?")
    assert transcript == "hello there"
    assert reply == "Hi! How can I help?"


def test_split_transcript_absent():
    transcript, reply = _split_transcript("Just a normal reply.")
    assert transcript is None
    assert reply == "Just a normal reply."


def test_peel_transcript_split_across_deltas():
    events = _run(_deltas("<trans", "cript>ce mai ", "faci</transcri", "pt>Sunt bine!", " Tu?"))
    assert events[0] == {"type": "transcript", "text": "ce mai faci"}
    assert "".join(e["text"] for e in events[1:]) == "Sunt bine! Tu?"


def test_peel_transcript_non_compliant_reply_flushes_immediately():
    events = _run(_deltas("Hello", " world"))
    assert events == _deltas("Hello", " world")


def test_peel_transcript_unclosed_tag_flushes_at_stream_end():
    events = _run(_deltas("<transcript>never closed"))
    assert events == _deltas("<transcript>never closed")


def test_peel_transcript_every_chunking_of_a_real_reply():
    # The exact reply shape Gemini produced in the wild; every split point must yield the same
    # transcript and the full remaining text, with nothing withheld.
    reply = "<transcript>Bro, what's up!</transcript>\n\nNot much, just hanging out. What are we diving into today?"
    for split in range(1, len(reply)):
        events = _run(_deltas(reply[:split], reply[split:]))
        transcripts = [e["text"] for e in events if e["type"] == "transcript"]
        text = "".join(e["text"] for e in events if e["type"] == "delta")
        assert transcripts == ["Bro, what's up!"], f"split={split}"
        assert text == "Not much, just hanging out. What are we diving into today?", f"split={split}"


def test_peel_transcript_passes_other_events_through():
    events = _run([{"type": "volume", "value": 0.5}, {"type": "delta", "text": "hi"}])
    assert events == [{"type": "volume", "value": 0.5}, {"type": "delta", "text": "hi"}]
