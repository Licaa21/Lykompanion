from fastapi import APIRouter

from app.services.screenshot.capture import capture_primary_monitor_b64

router = APIRouter(prefix="/api/screenshot", tags=["screenshot"])


@router.get("")
async def get_screenshot() -> dict:
    return {"image_base64": capture_primary_monitor_b64()}
