import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/voice", tags=["voice"])

VOICE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "voice"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _validate_id(audio_id: str) -> None:
    if not _UUID_RE.match(audio_id):
        raise HTTPException(status_code=400, detail="Invalid audio ID.")


@router.post("/{audio_id}")
async def upload_voice(audio_id: str, audio: UploadFile) -> dict:
    _validate_id(audio_id)
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    (VOICE_DIR / f"{audio_id}.wav").write_bytes(await audio.read())
    return {"ok": True}


@router.get("/{audio_id}")
async def get_voice(audio_id: str) -> FileResponse:
    _validate_id(audio_id)
    path = VOICE_DIR / f"{audio_id}.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Voice file not found.")
    return FileResponse(path, media_type="audio/wav", filename=f"lykompanion-voice_{audio_id[:8]}.wav")


@router.delete("/{audio_id}")
async def delete_voice(audio_id: str) -> dict:
    _validate_id(audio_id)
    path = VOICE_DIR / f"{audio_id}.wav"
    if path.exists():
        path.unlink()
    return {"ok": True}
