from fastapi import APIRouter, HTTPException

from app.core import game_state as game_state_store
from app.core import game_state_processes
from app.core import game_state_trackers
from app.core import game_state_training_data
from app.core.config import settings
from app.models.schemas import (
    AccountBalance,
    GameSession,
    GameSessionCreate,
    GameSessionRename,
    GameStateResponse,
    GameStateTracker,
    PendingProcessResponse,
    ProcessEntry,
    TrackerInput,
    TrainingDataDocument,
)

router = APIRouter(prefix="/api/game-state", tags=["game-state"])


@router.get("", response_model=GameStateResponse)
async def get_game_state() -> GameStateResponse:
    state = game_state_store.get_game_state()
    stats = game_state_store.get_session_stats()
    process = (state or {}).get("process")
    session_id = (state or {}).get("session_id")
    values = (state or {}).get("values", {})
    defs = game_state_trackers.get_trackers(process) if process else []
    trackers = [
        GameStateTracker(id=t["id"], label=t["label"], description=t["description"], locked=t["locked"], value=values.get(t["id"]))
        for t in defs
    ]
    session_name = game_state_store.get_session_name(process, session_id) if process and session_id else None
    return GameStateResponse(
        enabled=settings.game_state_ocr_enabled,
        tracking=state is not None,
        process=process,
        session_id=session_id,
        session_name=session_name,
        trackers=trackers,
        extraction_call_count=stats["extraction_call_count"],
        extraction_cost_usd=stats["extraction_cost_usd"],
    )


@router.get("/sessions/{process}", response_model=list[GameSession])
async def list_sessions(process: str) -> list[GameSession]:
    return game_state_store.get_sessions(process)


@router.post("/sessions/{process}", response_model=GameSession)
async def create_session(process: str, payload: GameSessionCreate) -> GameSession:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Session name cannot be empty.")
    return game_state_store.create_session(process, name)


@router.put("/sessions/{process}/{session_id}/active")
async def switch_session(process: str, session_id: str) -> dict:
    if not game_state_store.switch_session(process, session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"ok": True}


@router.patch("/sessions/{process}/{session_id}", response_model=GameSession)
async def rename_session(process: str, session_id: str, payload: GameSessionRename) -> GameSession:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Session name cannot be empty.")
    if not game_state_store.rename_session(process, session_id, name):
        raise HTTPException(status_code=404, detail="Session not found.")
    sessions = game_state_store.get_sessions(process)
    session = next((s for s in sessions if s["session_id"] == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


@router.get("/trackers/{process}", response_model=list[GameStateTracker])
async def get_process_trackers(process: str) -> list[GameStateTracker]:
    return game_state_trackers.get_trackers(process)


@router.put("/trackers/{process}", response_model=list[GameStateTracker])
async def update_process_trackers(process: str, trackers: list[TrackerInput]) -> list[GameStateTracker]:
    return game_state_trackers.set_trackers(process, [t.model_dump() for t in trackers])


@router.post("/trackers/{process}/reset", response_model=list[GameStateTracker])
async def reset_process_trackers(process: str) -> list[GameStateTracker]:
    return game_state_trackers.reset_trackers(process)


@router.get("/training-data/{process}", response_model=TrainingDataDocument)
async def get_process_training_data(process: str) -> TrainingDataDocument:
    return TrainingDataDocument(content=game_state_training_data.get_training_data(process))


@router.put("/training-data/{process}", response_model=TrainingDataDocument)
async def update_process_training_data(process: str, payload: TrainingDataDocument) -> TrainingDataDocument:
    return TrainingDataDocument(content=game_state_training_data.set_training_data(process, payload.content))


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
