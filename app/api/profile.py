import io
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

router = APIRouter(prefix="/api/profile", tags=["profile"])

PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "profile"
AVATAR_PATH = PROFILE_DIR / "avatar.png"

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_AVATAR_DIMENSION = 512


@router.get("/avatar/status")
async def avatar_status() -> dict:
    return {"has_avatar": AVATAR_PATH.exists()}


@router.get("/avatar")
async def get_avatar() -> FileResponse:
    if not AVATAR_PATH.exists():
        raise HTTPException(status_code=404, detail="No avatar set.")
    return FileResponse(AVATAR_PATH, media_type="image/png")


@router.post("/avatar")
async def upload_avatar(avatar: UploadFile) -> dict:
    if avatar.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported image type — use PNG, JPEG, or WebP.")

    raw = await avatar.read()
    if len(raw) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 5MB).")

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Invalid image file.") from exc

    img = img.convert("RGBA")
    img.thumbnail((MAX_AVATAR_DIMENSION, MAX_AVATAR_DIMENSION))
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    img.save(AVATAR_PATH, format="PNG")
    return {"ok": True}


@router.delete("/avatar")
async def delete_avatar() -> dict:
    if AVATAR_PATH.exists():
        AVATAR_PATH.unlink()
    return {"ok": True}
