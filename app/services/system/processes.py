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
