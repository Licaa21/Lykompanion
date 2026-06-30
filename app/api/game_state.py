from fastapi import APIRouter

from app.core import game_state as game_state_store
from app.core.config import settings
from app.models.schemas import GameStateResponse

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
