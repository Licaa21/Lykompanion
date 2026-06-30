from fastapi import APIRouter

from app.core.debug_log import get_requests
from app.models.schemas import DebugRequestEntry

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/requests", response_model=list[DebugRequestEntry])
async def get_debug_requests() -> list[DebugRequestEntry]:
    return get_requests()
