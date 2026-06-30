from fastapi import APIRouter

from app.core.usage import clear_usage, load_usage_records
from app.models.schemas import UsageRecord

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/records", response_model=list[UsageRecord])
async def get_usage_records() -> list[UsageRecord]:
    return load_usage_records()


@router.delete("")
async def delete_usage() -> dict:
    clear_usage()
    return {"ok": True}
