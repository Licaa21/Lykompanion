import base64
import io
import sys

import mss
from PIL import Image

from app.core.config import settings


def list_monitors() -> list[dict]:
    """All individual monitors (mss.monitors[0] is the combined virtual screen, skipped here)."""
    with mss.mss() as sct:
        monitors = sct.monitors[1:]
    return [
        {"index": i + 1, "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
        for i, m in enumerate(monitors)
    ]


def get_active_monitor_index() -> int:
    """Best-effort guess at which monitor the user is currently focused on, via cursor position.

    There's no cross-platform notion of "active display," so this uses the Windows cursor
    position as a proxy (where the mouse is tends to track where the user is looking/playing).
    Falls back to monitor 1 on non-Windows platforms or if the lookup fails for any reason.
    """
    monitors = list_monitors()
    if not monitors:
        return 1

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            point = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            for m in monitors:
                if m["left"] <= point.x < m["left"] + m["width"] and m["top"] <= point.y < m["top"] + m["height"]:
                    return m["index"]
        except Exception:
            pass

    return 1


def get_foreground_monitor_index() -> int:
    """Monitor index (1-based) that the focused/foreground window sits on.

    Used by the OCR poller so it captures the monitor the game is actually on, rather than
    wherever the mouse happens to be — e.g. a controller player whose cursor is parked on a
    second monitor. Falls back to the cursor-based guess, then monitor 1.
    """
    monitors = list_monitors()
    if not monitors:
        return 1

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                MONITOR_DEFAULTTONEAREST = 2
                hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD),
                    ]

                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(MONITORINFO)
                if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                    # Match the Win32 monitor to an mss monitor by its top-left origin.
                    for m in monitors:
                        if m["left"] == mi.rcMonitor.left and m["top"] == mi.rcMonitor.top:
                            return m["index"]
        except Exception:
            pass

    # Foreground lookup failed (or coordinate spaces didn't line up) — fall back to the
    # cursor-based heuristic, then monitor 1.
    return get_active_monitor_index()


def capture_monitor_image(monitor_index: int | None = None) -> Image.Image:
    """Capture one monitor (1-based index) and return the raw, full-resolution PIL image."""
    if monitor_index is None or monitor_index < 1:
        monitor_index = get_active_monitor_index()

    with mss.mss() as sct:
        monitors = sct.monitors[1:]
        if monitor_index > len(monitors):
            monitor_index = 1
        raw = sct.grab(monitors[monitor_index - 1])

    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def image_to_b64(image: Image.Image, max_width: int | None = None, quality: int | None = None) -> str:
    """Downscale and JPEG-encode an already-captured PIL image to base64 for LLM context,
    using the configured screenshot settings by default."""
    max_width = max_width or settings.screenshot_max_width
    quality = quality or settings.screenshot_jpeg_quality

    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def capture_monitor_b64(monitor_index: int | None = None, max_width: int | None = None, quality: int | None = None) -> str:
    """Capture one monitor (1-based index) and return a base64-encoded JPEG, downscaled for LLM context."""
    return image_to_b64(capture_monitor_image(monitor_index), max_width, quality)


def capture_primary_monitor_b64(max_width: int | None = None, quality: int | None = None) -> str:
    """Capture the user's active/focused monitor (kept for backward-compat call sites)."""
    return capture_monitor_b64(get_active_monitor_index(), max_width, quality)


def resize_uploaded_image_b64(data_url: str) -> str:
    """Downscale/re-encode a user-uploaded image (data URL or raw base64) using the same
    settings as screenshots, so a large browsed/dropped/pasted image doesn't blow up
    image-token cost before it reaches the LLM."""
    raw_b64 = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
    image = Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGB")
    return image_to_b64(image)
