from fastapi import APIRouter
from pydantic import BaseModel

from app.services import overlay_process

router = APIRouter(prefix="/api/overlay", tags=["overlay"])


class HandsFreeState(BaseModel):
    active: bool


@router.post("/handsfree")
async def set_handsfree(state: HandsFreeState) -> dict:
    """The frontend owns hands-free (live-mic) state; forward it to the native
    overlay so it can show/hide its persistent listening indicator."""
    overlay_process.set_handsfree(state.active)
    return {"active": state.active}
