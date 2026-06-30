from fastapi import APIRouter

from app.core.chats import load_chats, save_chats
from app.models.schemas import ChatHistoryResponse, ChatHistorySaveRequest

router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("", response_model=ChatHistoryResponse)
async def get_chats() -> ChatHistoryResponse:
    return ChatHistoryResponse(chats=load_chats())


@router.put("")
async def put_chats(payload: ChatHistorySaveRequest) -> dict:
    save_chats(payload.chats)
    return {"ok": True}
