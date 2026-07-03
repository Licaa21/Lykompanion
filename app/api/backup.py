import io
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import ENV_PATH

router = APIRouter(prefix="/api/backup", tags=["backup"])

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "data"

# webview_profile/ is a full EdgeWebView2 browser profile (thousands of unrelated files, no
# user data); debug_log.json and crash_log.txt are large/ephemeral diagnostics, not something
# worth carrying across machines.
_EXCLUDED = {"webview_profile", "debug_log.json", "crash_log.txt"}


@router.get("/export")
async def export_backup() -> StreamingResponse:
    """Zips data/ (chats, memories, game sessions, reminders, voice recordings, profile
    pictures, ...) plus .env (settings/API keys) for moving to another machine."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if DATA_DIR.exists():
            for path in DATA_DIR.rglob("*"):
                if path.is_dir():
                    continue
                if path.relative_to(DATA_DIR).parts[0] in _EXCLUDED:
                    continue
                zf.write(path, arcname=str(Path("data") / path.relative_to(DATA_DIR)))
        if ENV_PATH.exists():
            zf.write(ENV_PATH, arcname=".env")
    buf.seek(0)

    filename = f"lykompanion-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_backup(file: UploadFile) -> dict:
    """Restores data/ and .env from a zip produced by /export, overwriting current files.
    Settings changes only take effect after a restart (the running Settings singleton isn't
    re-read here)."""
    content = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Not a valid backup archive.") from exc

    with zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            target = (ROOT_DIR / name).resolve()
            if ROOT_DIR not in target.parents and target != ROOT_DIR:
                raise HTTPException(status_code=400, detail=f"Invalid archive entry: {name}")
            if not (name == ".env" or name.startswith("data/")):
                raise HTTPException(status_code=400, detail=f"Unexpected archive entry: {name}")

        zf.extractall(ROOT_DIR)

    return {"ok": True, "restart_required": True}
