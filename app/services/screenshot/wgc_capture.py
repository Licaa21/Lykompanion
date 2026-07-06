"""GPU-based screen capture for the game-state OCR poller, via the Windows Graphics Capture API
(through the `windows-capture` package, a Rust-backed wrapper - hand-rolling the underlying
Direct3D11/COM interop directly from Python isn't practical). Unlike `app/services/screenshot/
capture.py` (GDI/BitBlt via `mss`, used for on-demand vision screenshots), this avoids the
desktop-compositor synchronization that BitBlt-style capture causes, which was visibly stuttering
games - especially noticeable on cursor movement - since the OCR poller captures every tick.

Only used by the OCR poller (which also reuses these frames as the screenshots attached to the
game-state extraction pass). The occasional vision-screenshot paths (take_screenshot tool, chat
image attachments) keep using the mss-based capture module, since those aren't called often
enough to cause the same contention.
"""

import asyncio
import logging
import threading

import numpy as np
from PIL import Image
from windows_capture import Frame, InternalCaptureControl, WindowsCapture

from app.core.config import settings
from app.services.screenshot.capture import get_foreground_monitor_index

logger = logging.getLogger(__name__)


def _noop_frame_handler(frame: Frame, capture_control: InternalCaptureControl) -> None:
    # Late frame after we've already detached (see the finally block below) — just keep asking
    # the session to stop. Must exist: the wrapper raises if frame_handler is unset/None.
    capture_control.stop()


def _noop_closed_handler() -> None:
    pass


def _capture_sync(monitor_index: int | None, window_hwnd: int | None) -> Image.Image | None:
    """Starts a Windows Graphics Capture session for one monitor OR one window (window_hwnd
    wins when given — used for windowed games so only the game's own pixels reach OCR), grabs
    exactly one frame, and tears it down. Uses `start_free_threaded()` (capture runs on its own
    thread) so we can bound the wait: if no frame arrives within
    game_state_capture_frame_timeout_seconds we stop the session and return None instead of
    blocking indefinitely (the old blocking `start()` had no escape hatch)."""
    result: dict[str, np.ndarray] = {}
    error: dict[str, Exception] = {}
    done = threading.Event()
    frame_timeout = settings.game_state_capture_frame_timeout_seconds

    capture = WindowsCapture(
        cursor_capture=settings.game_state_capture_cursor_enabled,
        draw_border=False,
        monitor_index=monitor_index,
        window_hwnd=window_hwnd,
    )

    @capture.event
    def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl) -> None:
        try:
            result["frame"] = frame.frame_buffer.copy()
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller below
            error["exc"] = exc
        finally:
            done.set()
            capture_control.stop()

    @capture.event
    def on_closed() -> None:
        done.set()

    control = capture.start_free_threaded()

    try:
        if not done.wait(frame_timeout):
            # No frame in time — stop the session so its thread can unwind, and treat this tick as
            # "no capture" (same as a failed capture). We deliberately don't join here: if the native
            # thread is genuinely stuck, joining would just move the hang back onto the poller.
            try:
                control.stop()
            except Exception:
                logger.debug("Failed to stop timed-out capture session", exc_info=True)
            logger.warning(
                "Windows Graphics Capture timed out after %.1fs for %s (no frame arrived "
                "— exclusive-fullscreen or protected content? try borderless/windowed mode)",
                frame_timeout,
                f"window_hwnd={window_hwnd}" if window_hwnd else f"monitor_index={monitor_index!r}",
            )
            return None

        if "exc" in error:
            raise error["exc"]
        frame = result.pop("frame", None)
        if frame is None:
            return None

        # frame_buffer is BGRA; drop the alpha channel and reorder to RGB for PIL.
        return Image.fromarray(frame[:, :, [2, 1, 0]], "RGB")
    finally:
        # Every WindowsCapture leaks: its __init__ hands bound methods to the Rust-side
        # NativeWindowsCapture, creating a capture -> native -> bound-methods -> capture cycle
        # that CPython's GC can't traverse (the native object has no tp_traverse), so it is never
        # collected. We can't break the cycle itself, but we CAN evict the expensive cargo it
        # would otherwise pin: the closures above and, through them, the full-resolution frame
        # copy in `result` (~33 MB/tick at 4K — this was a multi-GB-per-hour leak). Swap in
        # module-level no-op handlers (never None: a late frame delivery would raise in the
        # wrapper) and empty the dicts, so each leaked session retains only a few small objects.
        capture.frame_handler = _noop_frame_handler
        capture.closed_handler = _noop_closed_handler
        result.clear()
        error.clear()


# HWNDs whose window capture already failed once this app run — go straight to monitor capture
# for them instead of re-paying the frame timeout on every single tick.
_broken_window_captures: set[int] = set()


async def capture_monitor_frame(monitor_index: int | None = None, window_hwnd: int | None = None) -> Image.Image | None:
    """Capture one frame off the event loop: the given window when `window_hwnd` is set (clean
    frames for windowed games — no desktop/taskbar/other windows in the OCR), else a monitor
    (1-based index, same convention as app/services/screenshot/capture.py). A window capture
    that fails falls back to monitor capture the same tick, and that HWND is skipped for the
    rest of the app run. Returns None (after logging) if capture fails entirely, so callers can
    treat it as "no OCR text this tick" rather than crashing the poller."""
    if window_hwnd and window_hwnd not in _broken_window_captures:
        try:
            image = await asyncio.to_thread(_capture_sync, None, window_hwnd)
        except Exception:
            logger.exception("Windows Graphics Capture failed for window_hwnd=%r", window_hwnd)
            image = None
        if image is not None:
            return image
        if len(_broken_window_captures) > 64:  # HWNDs get recycled; don't grow unbounded
            _broken_window_captures.clear()
        _broken_window_captures.add(window_hwnd)
        logger.warning(
            "Window capture produced no frame for hwnd=%r — using monitor capture for this window from now on",
            window_hwnd,
        )

    if monitor_index is None or monitor_index < 1:
        # The OCR poller (this module's only caller) should follow the focused game window's
        # monitor, not the mouse cursor's — controller players often leave the cursor elsewhere.
        monitor_index = get_foreground_monitor_index()
    try:
        return await asyncio.to_thread(_capture_sync, monitor_index, None)
    except Exception:
        logger.exception("Windows Graphics Capture failed for monitor_index=%r", monitor_index)
        return None
