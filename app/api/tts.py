from fastapi import APIRouter, Response

from app.models.schemas import TTSRequest
from app.services import tts

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.post("")
async def synthesize(request: TTSRequest) -> Response:
    audio = await tts.synthesize(request.text, request.voice, request.speed)
    return Response(content=audio, media_type="audio/wav")
