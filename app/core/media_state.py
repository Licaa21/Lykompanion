"""In-memory 'what's currently playing' snapshot of the in-app YouTube player, pushed by the
frontend (youtube-player.js) on every queue/track change - play, skip, mix/playlist load,
client-side auto-advance on ENDED, and the pop-out window's own heartbeat. The queue itself only
ever lives in browser JS memory (ytQueue), so this is the only way the backend/LLM can know what's
actually playing; get_now_playing (media_tool.py) reads it, and control_youtube_player's
next/previous responses use it to name the resulting track instead of a generic "skipped" message.

Deliberately not persisted to disk - this is throwaway UI-session state that's meaningless after a
restart anyway (nothing is playing yet on a fresh page load until the frontend pushes again)."""

from typing import TypedDict


class QueueEntry(TypedDict):
    video_id: str
    title: str
    channel: str | None


_queue: list[QueueEntry] = []
_index: int = -1
_playing: bool = False


def set_now_playing(queue: list[QueueEntry], index: int, playing: bool) -> None:
    global _queue, _index, _playing
    _queue = queue
    _index = index
    _playing = playing


def get_now_playing() -> dict | None:
    """None if nothing is queued/playing right now, otherwise the current entry plus its
    immediate neighbors (so callers can name what 'next'/'previous' would move to)."""
    if not _queue or _index < 0 or _index >= len(_queue):
        return None
    return {
        "current": _queue[_index],
        "index": _index,
        "queue_length": len(_queue),
        "playing": _playing,
        "next": _queue[_index + 1] if _index + 1 < len(_queue) else None,
        "previous": _queue[_index - 1] if _index > 0 else None,
    }
