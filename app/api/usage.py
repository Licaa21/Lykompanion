from fastapi import APIRouter

from app.core.usage import load_usage
from app.models.schemas import UsageStats

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("", response_model=UsageStats)
async def get_usage() -> UsageStats:
    return UsageStats(**load_usage())
