from fastapi import APIRouter
from pydantic import BaseModel

from app.core import game_state
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


@router.post("/design-mode/start")
async def start_design_mode() -> dict:
    """Settings-triggered "customize overlay layout" button: spawns the overlay (or reuses one
    already running for a tracked game) purely so its edit mode can be used from the desktop, with
    no game in the loop - the whole point being that no other app is reading the gamepad, so pad
    presses inside the overlay's editor can never double-fire into a game. Best-effort; no-ops if
    the overlay is disabled/missing/non-Windows.

    full=True (delete/create presets, appearance) is only safe when no game is actually being
    tracked - if the user opens this while a game session is live (e.g. tabbed out, game still
    running/reading the gamepad in the background), we fall back to the same restricted session a
    real in-game hotkey/voice trigger would get, instead of trusting the button click blindly."""
    overlay_process.start()
    full = game_state.get_game_state() is None
    overlay_process.push_retry({"type": "edit_mode", "enabled": True, "full": full})
    return {"ok": True, "full": full}


@router.post("/design-mode/stop")
async def stop_design_mode() -> dict:
    """Ends a design-mode session started above. Only fully quits the overlay process if no game
    is currently being tracked - otherwise a real in-game overlay session is relying on it staying
    up, and this would tear it down from under the player."""
    overlay_process.set_edit_mode(False)
    if game_state.get_game_state() is None:
        overlay_process.stop()
    return {"ok": True}


@router.get("/design-mode/status")
async def design_mode_status() -> dict:
    """Polled by the Settings "Customize Overlay Layout" button while it thinks a session is open.
    The pipe is write-only, so pressing Save/Discard/B inside the overlay itself ends edit mode
    with no message back to the app - without this, the button would stay stuck on "Stop Editing"
    forever after the user exits from inside the overlay. active=False tells the frontend to revert
    the button (and call design-mode/stop to actually release the process, if appropriate)."""
    return {"active": overlay_process.is_design_mode_active()}
