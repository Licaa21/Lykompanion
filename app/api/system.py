from fastapi import APIRouter

from app.services.system.app_audio import get_peak_level
from app.services.system.processes import get_foreground_process_name

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/audio-peak")
async def get_audio_peak() -> dict:
    """Live peak audio level for the foreground app, for the frontend to hold narration until a
    real gap in game audio - see get_peak_level for why this exists instead of the OCR activity
    label. None means there's nothing to gate against; the frontend should not hold back."""
    process = get_foreground_process_name()
    peak = get_peak_level(process) if process else None
    return {"peak": peak}
