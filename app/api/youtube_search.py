import asyncio
import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core import media_state
from app.services.llm.media_tool import (
    _SEARCH_TIMEOUT_SECONDS,
    _build_player_payload,
    _search_youtube_multi_sync,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/youtube", tags=["youtube"])


class NowPlayingEntry(BaseModel):
    video_id: str
    title: str
    channel: str | None = None


class NowPlayingUpdate(BaseModel):
    queue: list[NowPlayingEntry]
    index: int
    playing: bool = True


@router.post("/now-playing")
async def set_now_playing(update: NowPlayingUpdate) -> dict:
    """Pushed by youtube-player.js on every track change (play, skip, mix/playlist load,
    auto-advance, or the pop-out window's own heartbeat) - see app/core/media_state.py."""
    media_state.set_now_playing([e.model_dump() for e in update.queue], update.index, update.playing)
    return {"ok": True}


@router.get("/search")
async def search_youtube(q: str = Query(..., min_length=1)) -> dict:
    """Backs the in-app player's own search/browse UI (opened from the chat toolbar) - lets the
    user find and play a video directly, without going through the LLM/play_on_youtube tool call."""
    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_search_youtube_multi_sync, q), _SEARCH_TIMEOUT_SECONDS
        )
    except ImportError:
        return {"results": []}
    except Exception:
        logger.exception("YouTube browse search failed for %r", q)
        return {"results": []}
    return {"results": results}


@router.get("/mix")
async def get_mix(video_id: str = Query(..., min_length=1), title: str = Query(...)) -> dict:
    """Reuses the same best-effort Mix lookup play_on_youtube already does server-side, so picking
    a video from the in-app search/browse UI also auto-queues similar videos instead of stopping
    after the one the user picked. Falls back to a single-video payload internally on failure."""
    return await _build_player_payload(video_id, title)
