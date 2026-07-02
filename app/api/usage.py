from fastapi import APIRouter

from app.core.config import settings
from app.core.usage import clear_usage, load_usage_records
from app.models.schemas import AccountBalance, UsageRecord
from app.services.llm.client import fetch_account_balance

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/records", response_model=list[UsageRecord])
async def get_usage_records() -> list[UsageRecord]:
    return load_usage_records()


@router.get("/balance", response_model=list[AccountBalance])
async def get_account_balance() -> list[AccountBalance]:
    """One balance per distinct provider currently configured across LLM features - e.g.
    OpenRouter for memory/game-state and Google AI Studio for the main chat model both show up
    here. Providers with no balance API (anything but OpenRouter) or with fetch errors are
    silently omitted rather than shown as an error."""
    providers = {
        settings.llm_provider,
        settings.memory_extraction_provider or settings.llm_provider,
        settings.game_state_provider or settings.llm_provider,
    }
    balances = [await fetch_account_balance(provider) for provider in providers]
    return [b for b in balances if b.available]


@router.delete("")
async def delete_usage() -> dict:
    clear_usage()
    return {"ok": True}
