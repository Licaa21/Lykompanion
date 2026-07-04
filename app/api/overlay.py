from fastapi import APIRouter
from pydantic import BaseModel

from app.services import overlay_process

router = APIRouter(prefix="/api/overlay", tags=["overlay"])


class HandsFreeState(BaseModel):
    active: bool


class OverlayToast(BaseModel):
    text: str
    kind: str = "reply"
    duration_ms: int | None = None


@router.post("/handsfree")
async def set_handsfree(state: HandsFreeState) -> dict:
    """The frontend owns hands-free (live-mic) state; forward it to the native
    overlay so it can show/hide its persistent listening indicator."""
    overlay_process.set_handsfree(state.active)
    return {"active": state.active}


@router.post("/toast")
async def push_toast(toast: OverlayToast) -> dict:
    """Frontend-driven overlay toast. When narration is on, the client pushes each reply sentence
    here as it starts speaking, with duration_ms set to how long that sentence takes to narrate at
    the current TTS speed — so the toast stays up for the whole spoken sentence instead of a fixed
    timer. Best-effort; no-ops if the overlay isn't running."""
    overlay_process.push_toast(toast.text, toast.kind, toast.duration_ms)
    return {"ok": True}


@router.post("/edit-mode")
async def enter_edit_mode() -> dict:
    """Voice-triggered entry into overlay edit mode — the "edit overlay" phrase is detected
    client-side by wake-word.js, independent of the LLM/chat pipeline. Best-effort; no-ops if the
    overlay isn't running."""
    overlay_process.set_edit_mode(True)
    return {"ok": True}
