from fastapi import APIRouter, HTTPException

from app.core import game_state as game_state_store
from app.core import game_state_processes
from app.core.config import settings
from app.models.schemas import AccountBalance, GameStateResponse, PendingProcessResponse, ProcessEntry

router = APIRouter(prefix="/api/game-state", tags=["game-state"])


@router.get("", response_model=GameStateResponse)
async def get_game_state() -> GameStateResponse:
    state = game_state_store.get_game_state()
    return GameStateResponse(
        enabled=settings.game_state_ocr_enabled,
        tracking=state is not None,
        process=(state or {}).get("process"),
        activity=(state or {}).get("activity"),
        location=(state or {}).get("location"),
        quest=(state or {}).get("quest"),
        character=(state or {}).get("character"),
        notable_choice=(state or {}).get("notable_choice"),
    )


@router.get("/pending", response_model=PendingProcessResponse)
async def get_pending_processes() -> PendingProcessResponse:
    return PendingProcessResponse(processes=game_state_processes.get_pending_processes())


@router.get("/blacklist", response_model=list[str])
async def list_blacklist() -> list[str]:
    return game_state_processes.load_blacklist()


@router.post("/blacklist", response_model=list[str])
async def add_blacklist_entry(payload: ProcessEntry) -> list[str]:
    process = payload.process.strip()
    if not process:
        raise HTTPException(status_code=400, detail="Process name cannot be empty.")
    game_state_processes.add_to_blacklist(process)
    return game_state_processes.load_blacklist()


@router.delete("/blacklist/{process}", response_model=list[str])
async def remove_blacklist_entry(process: str) -> list[str]:
    game_state_processes.remove_from_blacklist(process)
    return game_state_processes.load_blacklist()


@router.get("/whitelist", response_model=list[str])
async def list_whitelist() -> list[str]:
    return game_state_processes.load_whitelist()


@router.post("/whitelist", response_model=list[str])
async def add_whitelist_entry(payload: ProcessEntry) -> list[str]:
    process = payload.process.strip()
    if not process:
        raise HTTPException(status_code=400, detail="Process name cannot be empty.")
    game_state_processes.add_to_whitelist(process)
    return game_state_processes.load_whitelist()


@router.delete("/whitelist/{process}", response_model=list[str])
async def remove_whitelist_entry(process: str) -> list[str]:
    game_state_processes.remove_from_whitelist(process)
    return game_state_processes.load_whitelist()
