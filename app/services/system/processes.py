import sys

import psutil


def get_foreground_process_name() -> str | None:
    """Name of the process behind the currently focused window - the best available proxy for
    "what the user is actively playing," since a fullscreen/focused game is almost always it.
    Returns None on non-Windows platforms or if the lookup fails for any reason."""
    if sys.platform != "win32":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None

        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        return psutil.Process(pid.value).name()
    except Exception:
        return None


def is_foreground_window_fullscreen() -> bool:
    """True if the focused window looks like a game: it covers (approximately) its whole monitor
    and has no normal windowed chrome (title bar / resize caption). This is the practical
    "smarter game detection" signal — a borderless/exclusive-fullscreen game covers the monitor
    with no caption, whereas normal apps (even maximized) keep a caption and leave the taskbar
    visible. Best-effort; False on non-Windows or any lookup failure.
    """
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False

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
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return False

        mon = mi.rcMonitor
        # Cover the full monitor (a few px of slack), not just the work area — a maximized
        # windowed app fills only rcWork (taskbar still showing), so it won't pass this.
        covers_monitor = (
            rect.left <= mon.left + 2
            and rect.top <= mon.top + 2
            and rect.right >= mon.right - 2
            and rect.bottom >= mon.bottom - 2
        )
        if not covers_monitor:
            return False

        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        return not (style & WS_CAPTION)
    except Exception:
        return False


def is_process_running(process_name: str) -> bool:
    """Whether any running process matches this name (case-insensitive) - used to tell "the
    tracked game lost focus" (keep its session alive) apart from "the tracked game closed"
    (clear it), independent of which window currently has focus."""
    name_lower = process_name.lower()
    try:
        return any((p.info["name"] or "").lower() == name_lower for p in psutil.process_iter(["name"]))
    except Exception:
        # Best-effort - a transient psutil error shouldn't look like "the game closed."
        return True
