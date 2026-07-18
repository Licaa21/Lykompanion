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


def get_foreground_window_title() -> str | None:
    """Just the focused window's title bar text - a deliberately cheap subset of
    get_foreground_process_details() (pure ctypes, no psutil process/cmdline reads), safe to call
    every capture tick. Used by the game-state poller to notice the tracked window changing
    identity (e.g. alt-tabbing between two javaw.exe instances - a modpack's and a vanilla one -
    which is invisible to the process-name check). None on non-Windows, no focused window, or an
    empty title."""
    if sys.platform != "win32":
        return None

    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value or None
    except Exception:
        return None


def get_foreground_process_details() -> dict | None:
    """Identity signals about the focused window's process, for modpack/variant detection:
    window title, command line, exe path, working directory, and parent process name. Modded
    launches leak the pack identity through these (a modded Minecraft window title names the
    pack; javaw.exe's command line contains the pack folder; Skyrim under Mod Organizer has
    ModOrganizer.exe as parent). Every field is best-effort and independently guarded — a
    denied cmdline read (some anti-cheat-protected games) must not blank the rest. Returns
    None on non-Windows or when even the process name can't be resolved."""
    if sys.platform != "win32":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        proc = psutil.Process(pid.value)
        details: dict = {"process": proc.name(), "window_title": None, "cmdline": None,
                         "exe": None, "cwd": None, "parent": None}
    except Exception:
        return None

    try:
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            details["window_title"] = buffer.value or None
    except Exception:
        pass
    for field, getter in (
        ("cmdline", lambda: " ".join(proc.cmdline()) or None),
        ("exe", proc.exe),
        ("cwd", proc.cwd),
        ("parent", lambda: proc.parent().name() if proc.parent() else None),
    ):
        try:
            details[field] = getter()
        except Exception:
            pass
    return details


def _get_foreground_window_metrics() -> dict | None:
    """hwnd + geometry + style of the focused window, shared by the fullscreen/large-window
    heuristics and the window-capture path. None on non-Windows or any lookup failure."""
    if sys.platform != "win32":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None

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
            return None

        GWL_STYLE = -16
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        return {"hwnd": hwnd, "rect": rect, "monitor": mi.rcMonitor, "work": mi.rcWork, "style": style}
    except Exception:
        return None


_WS_CAPTION = 0x00C00000


def _metrics_are_fullscreen(m: dict) -> bool:
    rect, mon = m["rect"], m["monitor"]
    # Cover the full monitor (a few px of slack), not just the work area — a maximized
    # windowed app fills only rcWork (taskbar still showing), so it won't pass this.
    covers_monitor = (
        rect.left <= mon.left + 2
        and rect.top <= mon.top + 2
        and rect.right >= mon.right - 2
        and rect.bottom >= mon.bottom - 2
    )
    return covers_monitor and not (m["style"] & _WS_CAPTION)


def is_foreground_window_fullscreen() -> bool:
    """True if the focused window looks like a game: it covers (approximately) its whole monitor
    and has no normal windowed chrome (title bar / resize caption). This is the practical
    "smarter game detection" signal — a borderless/exclusive-fullscreen game covers the monitor
    with no caption, whereas normal apps (even maximized) keep a caption and leave the taskbar
    visible. Best-effort; False on non-Windows or any lookup failure.
    """
    m = _get_foreground_window_metrics()
    return bool(m) and _metrics_are_fullscreen(m)


def is_foreground_window_large(threshold: float = 0.7) -> bool:
    """True when the focused window covers at least `threshold` of its monitor's work area —
    the "windowed game" signal (windowed Minecraft, a maximized windowed game). Softer than
    the fullscreen check, so callers should demand persistence (several consecutive ticks)
    before acting on it. Best-effort; False on non-Windows or any lookup failure."""
    m = _get_foreground_window_metrics()
    if not m:
        return False
    rect, work = m["rect"], m["work"]
    # Clip to the work area so an off-screen overhang can't inflate the coverage.
    visible_w = max(0, min(rect.right, work.right) - max(rect.left, work.left))
    visible_h = max(0, min(rect.bottom, work.bottom) - max(rect.top, work.top))
    work_area = max(1, (work.right - work.left) * (work.bottom - work.top))
    return (visible_w * visible_h) / work_area >= threshold


def get_foreground_window_if_windowed() -> int | None:
    """HWND of the focused window when it is NOT borderless/exclusive-fullscreen, else None.
    The OCR poller uses this to capture just the game window (clean frames — no desktop,
    taskbar, or other windows leaking into OCR) while windowed; fullscreen games keep the
    monitor-capture path, where window capture is unreliable anyway."""
    m = _get_foreground_window_metrics()
    if not m or _metrics_are_fullscreen(m):
        return None
    return m["hwnd"]


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
