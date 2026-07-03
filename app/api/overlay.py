"""SSE feed + layout API for the in-game overlay (web/overlay.html, one small native window
per widget - see CLAUDE.md's in-game overlay section).

The overlay windows are a separate browser context from the main app window, so they can't
see the chat stream directly - instead each subscribes here and receives events published to
app.core.events: finished companion replies, fired reminders, and edit-mode toggles. Game
state is not relayed through this bus - the overlay polls /api/game-state for it like the
main UI does.
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import events, overlay_layouts

router = APIRouter(prefix="/api/overlay", tags=["overlay"])


class OverlayElementPosition(BaseModel):
    x: float
    y: float


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


@router.put("/layout/{process}/{element_id}")
async def put_element_position(process: str, element_id: str, payload: OverlayElementPosition) -> dict:
    """Saves one widget's position, merged into the process's stored layout - each widget
    window (toasts, game-state panel) drags and saves independently, so this must not clobber
    another widget's concurrently-saved position (see overlay_layouts.save_element_position)."""
    return {"layout": overlay_layouts.save_element_position(process, element_id, payload.x, payload.y)}


@router.post("/edit-mode")
async def set_edit_mode(payload: OverlayEditMode) -> dict:
    """Set the overlay's layout editor on/off (the Settings button always sends True; the
    overlay's own Save/Cancel send False). Lifts/restores the native click-through styles
    on the overlay window and tells the overlay page over the SSE bus to enter/leave editing
    UI. Same state a global hotkey can toggle from run_app.py - see app.core.events."""
    return {"enabled": events.set_overlay_edit_mode(payload.enabled)}
