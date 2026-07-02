from fastapi import APIRouter, HTTPException

from app.core import game_state, memory
from app.models.schemas import MemoryCreate, MemoryEntry, MemoryUpdate

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _derive_session_id(process: str | None) -> str | None:
    """Returns the active session_id when process matches the currently tracked game, else None.
    Means manually-added/edited memories get session-tagged the same way auto-extracted ones do."""
    if not process:
        return None
    gs = game_state.get_game_state()
    if gs and gs["process"].lower() == process.lower():
        return gs.get("session_id")
    return None


@router.get("", response_model=list[MemoryEntry])
async def list_memories() -> list[MemoryEntry]:
    return memory.load_memories()


@router.post("", response_model=MemoryEntry)
async def create_memory(payload: MemoryCreate) -> MemoryEntry:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Memory content cannot be empty.")
    process = (payload.process or "").strip() or None
    # Explicit session_id (Gaming Journal adds a fact to a specific profile) wins; otherwise
    # fall back to tagging with the active session the way auto-extracted memories are.
    session_id = (payload.session_id or "").strip() or _derive_session_id(process)
    if payload.scope:
        scope = payload.scope
    elif session_id:
        scope = "session"
    elif process:
        scope = "game"
    else:
        scope = "user"
    entry = memory.remember(content, scope, process=process, session_id=session_id)
    if entry is None:
        raise HTTPException(status_code=409, detail="An identical memory already exists.")
    return entry


@router.put("/{memory_id}", response_model=MemoryEntry)
async def update_memory(memory_id: str, payload: MemoryUpdate) -> MemoryEntry:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Memory content cannot be empty.")
    process = (payload.process or "").strip() or None
    session_id = (payload.session_id or "").strip() or _derive_session_id(process)
    updated = memory.update_memory(memory_id, content, process=process, session_id=session_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return updated


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str) -> dict:
    removed = memory.remove_memory(memory_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"ok": True}
