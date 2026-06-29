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
