"""GPU-based screen capture for the game-state OCR poller, via the Windows Graphics Capture API
(through the `windows-capture` package, a Rust-backed wrapper - hand-rolling the underlying
Direct3D11/COM interop directly from Python isn't practical). Unlike `app/services/screenshot/
capture.py` (GDI/BitBlt via `mss`, used for on-demand vision screenshots), this avoids the
desktop-compositor synchronization that BitBlt-style capture causes, which was visibly stuttering
games - especially noticeable on cursor movement - since the OCR poller captures every tick.

Only used by the OCR poller. The occasional vision-screenshot paths (take_screenshot tool, chat
image attachments, the game-state training pass) keep using the mss-based capture module, since
those aren't called often enough to cause the same contention.
"""

import asyncio
import logging

import numpy as np
from PIL import Image
from windows_capture import Frame, InternalCaptureControl, WindowsCapture

from app.services.screenshot.capture import get_active_monitor_index

logger = logging.getLogger(__name__)


def _capture_monitor_sync(monitor_index: int) -> Image.Image | None:
    """Blocking: starts a Windows Graphics Capture session for one monitor, grabs exactly one
    frame, and tears the session down. `WindowsCapture.start()` blocks the calling thread until
    `capture_control.stop()` is called from within the frame-arrived callback."""
    result: dict[str, np.ndarray] = {}
    error: dict[str, Exception] = {}

    capture = WindowsCapture(cursor_capture=False, draw_border=False, monitor_index=monitor_index)

    @capture.event
    def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl) -> None:
        try:
            result["frame"] = frame.frame_buffer.copy()
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller below
            error["exc"] = exc
        finally:
            capture_control.stop()

    @capture.event
    def on_closed() -> None:
        pass

    capture.start()

    if "exc" in error:
        raise error["exc"]
    frame = result.get("frame")
    if frame is None:
        return None

    # frame_buffer is BGRA; drop the alpha channel and reorder to RGB for PIL.
    return Image.fromarray(frame[:, :, [2, 1, 0]], "RGB")


async def capture_monitor_frame(monitor_index: int | None = None) -> Image.Image | None:
    """Capture one monitor (1-based index, same convention as app/services/screenshot/capture.py)
    off the event loop. Returns None (after logging) if the capture session fails for any reason,
    so callers can treat that the same as "no OCR text this tick" rather than crashing the poller.
    """
    if monitor_index is None or monitor_index < 1:
        monitor_index = get_active_monitor_index()
    try:
        return await asyncio.to_thread(_capture_monitor_sync, monitor_index)
    except Exception:
        logger.exception("Windows Graphics Capture failed for monitor_index=%r", monitor_index)
        return None
