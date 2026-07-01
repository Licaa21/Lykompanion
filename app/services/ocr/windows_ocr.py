"""Text recognition for the game-state OCR poller, via the built-in Windows OCR engine
(Windows.Media.Ocr, through the `winrt` WinRT projection packages) - no external OCR binary to
install separately (unlike the old Tesseract-based approach), and generally more reliable on
stylized/low-contrast in-game UI text since it's the same engine Windows itself uses for Snipping
Tool's text actions etc."""

import logging

from PIL import Image
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

logger = logging.getLogger(__name__)

_engine = None
_engine_resolved = False
_warned_unavailable = False


def _get_engine() -> OcrEngine | None:
    """Lazily resolves (and caches) the OCR engine for the user's profile languages. Cached as
    None too if unavailable, so we don't retry the lookup on every single capture tick."""
    global _engine, _engine_resolved
    if not _engine_resolved:
        _engine = OcrEngine.try_create_from_user_profile_languages()
        _engine_resolved = True
    return _engine


def _image_to_software_bitmap(image: Image.Image) -> SoftwareBitmap:
    rgba = image.convert("RGBA")
    # SoftwareBitmap's Bgra8 format expects B, G, R, A byte order per pixel - PIL gives R, G, B, A.
    r, g, b, a = rgba.split()
    bgra_bytes = Image.merge("RGBA", (b, g, r, a)).tobytes()

    writer = DataWriter()
    writer.write_bytes(bgra_bytes)
    buffer = writer.detach_buffer()
    # Alpha is irrelevant for OCR (PIL fills it uniformly opaque on RGB->RGBA conversion, and
    # text recognition only looks at RGB anyway) - use the base overload, no alpha mode argument,
    # since this winrt package build doesn't expose the 5-arg alpha-mode overload.
    return SoftwareBitmap.create_copy_from_buffer(buffer, BitmapPixelFormat.BGRA8, rgba.width, rgba.height)


async def extract_text(image: Image.Image) -> str | None:
    """Recognize on-screen text via the built-in Windows OCR engine. Returns None (after logging
    a one-time warning) if no OCR-capable language pack is installed for the user's profile -
    callers should treat that as "OCR unavailable", not fail."""
    global _warned_unavailable

    engine = _get_engine()
    if engine is None:
        if not _warned_unavailable:
            logger.warning(
                "Windows OCR engine unavailable - no OCR-capable language pack installed for "
                "your profile languages. Install one via Settings > Time & Language > Language & "
                "region > Add a language (make sure 'Optical character recognition' is included "
                "for it). Game-state OCR is disabled until then."
            )
            _warned_unavailable = True
        return None

    try:
        bitmap = _image_to_software_bitmap(image)
        result = await engine.recognize_async(bitmap)
        return result.text
    except Exception:
        logger.exception("Windows OCR recognition failed")
        return None
