"""SSE feed for the in-game overlay window (web/overlay.html).

The overlay is a separate browser context from the main app window, so it can't see the
chat stream directly - instead it subscribes here and receives events published to
app.core.events: finished companion replies and fired reminders. Game state is not
relayed through this bus - the overlay polls /api/game-state for it like the main UI does.
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import events, overlay_layouts

router = APIRouter(prefix="/api/overlay", tags=["overlay"])


class OverlayLayout(BaseModel):
    layout: dict


class OverlayEditMode(BaseModel):
    enabled: bool


@router.get("/events")
async def overlay_events() -> StreamingResponse:
    async def event_generator():
        queue = events.subscribe()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    # Keepalive comment: forces a write so a vanished client surfaces as a
                    # send error and the subscription gets reaped instead of leaking.
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            events.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/layout/{process}")
async def get_layout(process: str) -> dict:
    """Element positions for a game ("default" = no game tracked), with default-layout
    fallback handled server-side - the overlay just asks for its current process."""
    return {"layout": overlay_layouts.get_layout(process)}


@router.put("/layout/{process}")
async def put_layout(process: str, payload: OverlayLayout) -> dict:
    return {"layout": overlay_layouts.save_layout(process, payload.layout)}


@router.post("/edit-mode")
async def set_edit_mode(payload: OverlayEditMode) -> dict:
    """Set the overlay's layout editor on/off (the Settings button always sends True; the
    overlay's own Save/Cancel send False). Lifts/restores the native click-through styles
    on the overlay window and tells the overlay page over the SSE bus to enter/leave editing
    UI. Same state a global hotkey can toggle from run_app.py - see app.core.events."""
    return {"enabled": events.set_overlay_edit_mode(payload.enabled)}
