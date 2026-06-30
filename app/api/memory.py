from fastapi import APIRouter, HTTPException

from app.core import memory
from app.models.schemas import MemoryCreate, MemoryEntry, MemoryUpdate

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("", response_model=list[MemoryEntry])
async def list_memories() -> list[MemoryEntry]:
    return memory.load_memories()


@router.post("", response_model=MemoryEntry)
async def create_memory(payload: MemoryCreate) -> MemoryEntry:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Memory content cannot be empty.")
    process = (payload.process or "").strip() or None
    return memory.add_memory(content, process=process)


@router.put("/{memory_id}", response_model=MemoryEntry)
async def update_memory(memory_id: str, payload: MemoryUpdate) -> MemoryEntry:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Memory content cannot be empty.")
    process = (payload.process or "").strip() or None
    updated = memory.update_memory(memory_id, content, process=process)
    if not updated:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return updated


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str) -> dict:
    removed = memory.remove_memory(memory_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"ok": True}
