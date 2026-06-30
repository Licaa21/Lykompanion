from fastapi import APIRouter

from app.core.usage import clear_usage, load_usage_records
from app.models.schemas import AccountBalance, UsageRecord
from app.services.llm.client import fetch_account_balance

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/records", response_model=list[UsageRecord])
async def get_usage_records() -> list[UsageRecord]:
    return load_usage_records()


@router.get("/balance", response_model=AccountBalance)
async def get_account_balance() -> AccountBalance:
    return await fetch_account_balance()


@router.delete("")
async def delete_usage() -> dict:
    clear_usage()
    return {"ok": True}
