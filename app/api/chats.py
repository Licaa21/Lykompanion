from fastapi import APIRouter, Body, HTTPException

from app.core.chats import delete_chat, load_chats, save_chats, upsert_chat
from app.models.schemas import ChatHistoryResponse, ChatHistorySaveRequest

router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("", response_model=ChatHistoryResponse)
async def get_chats() -> ChatHistoryResponse:
    return ChatHistoryResponse(chats=load_chats())


@router.put("")
async def put_chats(payload: ChatHistorySaveRequest) -> dict:
    """Bulk replace - legacy path, kept for full-list operations (e.g. reordering)."""
    save_chats(payload.chats)
    return {"ok": True}


@router.put("/{chat_id}")
async def put_chat(chat_id: str, chat: dict = Body(...)) -> dict:
    """Upsert a single chat - the normal save path, so one message doesn't re-upload the
    entire history of every conversation."""
    if chat.get("id") != chat_id:
        raise HTTPException(status_code=400, detail="Chat id in body doesn't match the URL.")
    upsert_chat(chat)
    return {"ok": True}


@router.delete("/{chat_id}")
async def remove_chat(chat_id: str) -> dict:
    delete_chat(chat_id)
    return {"ok": True}
