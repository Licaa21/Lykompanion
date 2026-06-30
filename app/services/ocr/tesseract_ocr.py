import asyncio
import logging
import shutil
import sys
from pathlib import Path

import pytesseract
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

_warned_unavailable = False

# UB-Mannheim's Windows installer always defaults here. A shell/process started before install
# won't have picked up the PATH update yet, so check this explicitly rather than relying on PATH.
_DEFAULT_WIN_INSTALL_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def _resolve_tesseract_cmd() -> str | None:
    if settings.tesseract_cmd:
        return settings.tesseract_cmd
    if shutil.which("tesseract"):
        return None  # already resolvable via PATH, no override needed
    if sys.platform == "win32" and _DEFAULT_WIN_INSTALL_PATH.exists():
        return str(_DEFAULT_WIN_INSTALL_PATH)
    return None


async def extract_text(image: Image.Image) -> str | None:
    """Recognize on-screen text via the local Tesseract OCR engine. Returns None (after logging a
    one-time warning) if the tesseract binary isn't installed/on PATH - callers should treat that
    as "OCR unavailable", not fail. pytesseract shells out synchronously, so this runs it in a
    worker thread to avoid blocking the event loop."""
    global _warned_unavailable

    cmd = _resolve_tesseract_cmd()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    try:
        return await asyncio.to_thread(pytesseract.image_to_string, image)
    except pytesseract.TesseractNotFoundError:
        if not _warned_unavailable:
            logger.warning(
                "Tesseract OCR engine not found - install it from "
                "https://github.com/UB-Mannheim/tesseract/wiki and ensure tesseract.exe is on "
                "PATH, or set TESSERACT_CMD to its full path. Game-state OCR is disabled until then."
            )
            _warned_unavailable = True
        return None
    except Exception:
        logger.exception("Tesseract OCR recognition failed")
        return None
