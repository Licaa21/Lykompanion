"""Desktop launcher — starts the FastAPI server in a background thread, then opens a
native app window (EdgeWebView2 on Windows 11) pointed at it. Close the window to exit."""

import ctypes
import faulthandler
import json
import os
import secrets
import socket
import sys
import time
import threading
from pathlib import Path

# The process has died silently (no traceback, straight to RUN.cmd's pause) during normal use -
# the signature of a native access violation in one of the Windows-native deps (WGC capture,
# Windows OCR winrt, pycaw, WebView2 COM). faulthandler prints a C-level traceback for every
# thread on a hard crash so the faulting module is identifiable from the console/log.
faulthandler.enable()
try:
    _crash_log = open(Path(__file__).resolve().parent / "data" / "crash_log.txt", "a")
    faulthandler.enable(file=_crash_log)
except OSError:
    pass  # console-only fallback (faulthandler.enable() above already covers stderr)

# WebView2 white-screen-on-maximize fix: the blank-out on resize/maximize comes from
# Chromium's native window occlusion tracker wrongly deciding the window is covered and
# suspending the renderer. Disabling just that tracker fixes it while keeping GPU
# compositing on — the previous fix (--disable-gpu-compositing) forced the whole UI into
# software rendering and made everything sluggish.
os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--disable-features=CalculateNativeWinOcclusion",
)

# pywebview only marks the process DPI-aware itself inside webview.start() (and only once it
# gets around to creating the "master" window) - too late for the overlay window below, which
# needs GetSystemMetrics to return real physical screen dimensions *before* start() is called
# in order to size itself to cover the whole screen. Without this, GetSystemMetrics returns
# OS-virtualized (scaled-down) values on any display with DPI scaling enabled, and the overlay
# ends up smaller than the actual screen.
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

PORT = 6692
URL = f"http://localhost:{PORT}"
ROOT = Path(__file__).resolve().parent

# Per-launch API token: the server (same process) reads it from the environment and rejects
# /api/* requests without it; the webview receives it via the initial URL. Keeps other local
# processes from silently using the unauthenticated API (chats, settings, screenshots).
API_TOKEN = secrets.token_urlsafe(32)
os.environ["LYKO_API_TOKEN"] = API_TOKEN


def _build_icon() -> str:
    """Convert logo.png to logo.ico once; pywebview on Windows requires .ico."""
    from PIL import Image
    ico = ROOT / "logo.ico"
    if not ico.exists():
        img = Image.open(ROOT / "logo.png")
        img.save(str(ico), format="ICO", sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])
    return str(ico)


def _get_downloads_dir() -> str:
    """Return the user's actual Downloads folder, respecting custom locations set via Windows."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as key:
            return winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
    except Exception:
        return str(Path.home() / "Downloads")


def _start_server() -> None:
    import uvicorn
    # localhost-only on purpose: the API has no authentication, and exposes chats, settings
    # (API keys), and live desktop screenshots - it must not be reachable from the LAN.
    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, log_level="info")


def _wait_for_server(timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", PORT), timeout=1.0):
                return True
        except KeyboardInterrupt:
            raise  # propagate Ctrl+C cleanly — don't swallow it as OSError
        except OSError:
            time.sleep(0.3)
    return False


def _configure_profile(profile_dir: Path) -> None:
    """Write EdgeWebView2 Preferences that make the app feel native from launch 1.

    Called both before and after webview.start():
    - Before: applies to this launch if the profile already exists (2nd+ run).
    - After: re-applies after EdgeWebView2 rewrites the file on clean shutdown.

    On a completely fresh install we pre-seed EBWebView/Default/Preferences so every
    setting takes effect from the very first launch with no user action required.

    Settings applied
    ----------------
    download            — auto-save to Downloads folder, no Save-As dialog (backup in case
                          the DownloadStarting handler below can't hook in)
    media_stream_mic    — pre-grant microphone for localhost so no permission popup appears
    """
    downloads_dir = _get_downloads_dir()

    def _apply(prefs: dict) -> dict:
        prefs.setdefault("download", {}).update({
            "default_directory": downloads_dir,
            "prompt_for_download": False,
            "directory_upgrade": True,
        })
        (
            prefs
            .setdefault("profile", {})
            .setdefault("content_settings", {})
            .setdefault("exceptions", {})
            .setdefault("media_stream_mic", {})
        )[f"http://localhost:{PORT},*"] = {"setting": 1}
        return prefs

    found = list(profile_dir.rglob("Preferences"))
    if found:
        for prefs_file in found:
            try:
                prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
            except Exception:
                prefs = {}
            prefs_file.write_text(json.dumps(_apply(prefs)), encoding="utf-8")
    else:
        prefs_file = profile_dir / "EBWebView" / "Default" / "Preferences"
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(json.dumps(_apply({})), encoding="utf-8")


def _lock_down_webview(window) -> None:
    """Disable browser UI that would reveal the app runs on a web engine.

    Note: AreDefaultContextMenusEnabled is intentionally left True — disabling it also
    kills the <audio> player's 3-dot menu. Right-click is blocked in JS instead, which
    only intercepts contextmenu events and leaves the audio controls untouched.
    """
    try:
        settings = window._window.browser.CoreWebView2.Settings
        settings.AreDevToolsEnabled = False    # blocks F12 / right-click Inspect
        settings.IsStatusBarEnabled = False    # hides URL tooltip on link hover
    except Exception:
        pass


def _make_download_api(win) -> object:
    """Return an object whose methods get exposed to JS via win.expose().

    Bypasses WebView2's unreliable browser download mechanism — Python copies the file
    directly from data/voice/ to the user's Downloads folder and returns the filename.
    JS shows a toast on success.
    """
    import re
    import shutil
    _UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    def download_voice(audio_id: str) -> dict:
        if not _UUID_RE.match(audio_id):
            return {"ok": False, "error": "Invalid ID"}
        src = ROOT / "data" / "voice" / f"{audio_id}.wav"
        if not src.exists():
            return {"ok": False, "error": "File not found"}
        dst_dir = Path(_get_downloads_dir())
        dst = dst_dir / f"lykompanion-voice_{audio_id[:8]}.wav"
        already = dst.exists()
        if not already:
            shutil.copy2(str(src), str(dst))
        return {"ok": True, "name": dst.name, "folder": str(dst_dir), "already": already}

    return download_voice


def _apply_overlay_base_styles(overlay_window) -> None:
    """One-time styles applied at window init, independent of click-through toggling:
    WS_EX_TOOLWINDOW (hidden from Alt-Tab/taskbar), WS_EX_NOACTIVATE (never steals focus, even
    while click-through is lifted for editing), WS_EX_LAYERED, and DwmExtendFrameIntoClientArea
    with full negative margins ("sheet of glass" over the whole client area) - the two
    independent DWM mechanisms that can make a window's alpha channel actually show the desktop
    through it, applied together since neither alone has reliably worked here across earlier
    attempts (see the CLAUDE.md writeup on this feature for the history).

    WS_EX_LAYERED must NEVER be paired with SetLayeredWindowAttributes or UpdateLayeredWindow -
    that pairing forces legacy flat, non-per-pixel alpha blending and made the whole window
    render as an opaque dark rectangle in an earlier attempt. Set bare, it instead lets DWM (on
    Windows 8+) pull per-pixel alpha directly from the window's own GPU swap chain, which is how
    WebView2 renders - but by itself this still wasn't sufficient. DwmExtendFrameIntoClientArea
    is the older, more established "Aero glass" mechanism (Vista+) for telling DWM to composite
    a region using the app's own rendered alpha instead of painting it opaque; pywebview itself
    already uses this call (see ExtendFrameIntoClientArea in winforms.py) but only for the
    window-shadow effect, with a 1px margin - never for full-window transparency."""
    import ctypes

    class _Margins(ctypes.Structure):
        _fields_ = [
            ("cxLeftWidth", ctypes.c_int),
            ("cxRightWidth", ctypes.c_int),
            ("cyTopHeight", ctypes.c_int),
            ("cyBottomHeight", ctypes.c_int),
        ]

    user32 = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x80
    WS_EX_LAYERED = 0x80000
    WS_EX_NOACTIVATE = 0x8000000
    SWP_FLAGS = 0x0002 | 0x0001 | 0x0004 | 0x0020  # NOMOVE | NOSIZE | NOZORDER | FRAMECHANGED

    try:
        hwnd = overlay_window.native.Handle.ToInt32()
    except Exception:
        return  # window already destroyed

    current = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, current | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)

    full_glass = _Margins(-1, -1, -1, -1)
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(full_glass))


def _make_overlay_click_through_setter(overlay_window):
    """Returns set_click_through(enabled): True turns the overlay window into a pure display
    surface where mouse input falls through to the game underneath; False lifts just the
    click-through bit so the layout editor's drag handles become interactive. Only touches
    WS_EX_TRANSPARENT - unrelated to the WS_EX_LAYERED transparency setup above, which is
    applied once at init by _apply_overlay_base_styles and never toggled."""
    import ctypes

    user32 = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x20
    SWP_FLAGS = 0x0002 | 0x0001 | 0x0004 | 0x0020  # NOMOVE | NOSIZE | NOZORDER | FRAMECHANGED

    def _update_styles(hwnd: int, add: int, remove: int) -> None:
        current = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (current | add) & ~remove)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)

    def set_click_through(enabled: bool) -> None:
        try:
            hwnd = overlay_window.native.Handle.ToInt32()
        except Exception:
            return  # window already destroyed

        if enabled:
            _update_styles(hwnd, WS_EX_TRANSPARENT, 0)
        else:
            _update_styles(hwnd, 0, WS_EX_TRANSPARENT)

        # WS_EX_TRANSPARENT on the top-level form alone is not enough: the WebView2 child
        # windows (Chrome_WidgetWin_*) hit-test on their own and still swallow clicks -
        # keep every descendant's bit in sync too.
        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        def _enum_child(child_hwnd, _lparam):
            if enabled:
                _update_styles(child_hwnd, WS_EX_TRANSPARENT, 0)
            else:
                _update_styles(child_hwnd, 0, WS_EX_TRANSPARENT)
            return 1

        user32.EnumChildWindows(hwnd, _enum_child, 0)

    return set_click_through


# Ctrl+Shift+O toggles the overlay layout editor - global (works while a game has focus,
# since the overlay window is non-activating/click-through and can't receive key events
# itself). "O" for Overlay; chosen to avoid common game bindings on the WASD/function-key
# side of the keyboard.
OVERLAY_HOTKEY_MODIFIERS = 0x0002 | 0x0004  # MOD_CONTROL | MOD_SHIFT
OVERLAY_HOTKEY_VK = 0x4F  # 'O'


def _start_overlay_hotkey_listener() -> None:
    """Registers a system-wide hotkey and blocks handling WM_HOTKEY messages forever - run
    this on its own daemon thread. RegisterHotKey ties the hotkey to the calling thread's
    message queue, so registration and the GetMessage loop must happen on the same thread."""
    import ctypes
    from ctypes import wintypes

    def _listen() -> None:
        from app.core import events as overlay_events

        user32 = ctypes.windll.user32
        WM_HOTKEY = 0x0312
        HOTKEY_ID = 1

        if not user32.RegisterHotKey(None, HOTKEY_ID, OVERLAY_HOTKEY_MODIFIERS, OVERLAY_HOTKEY_VK):
            # Likely already claimed by another app - the Settings "Edit layout" button still
            # works, so this is a soft failure, not fatal to the overlay feature.
            print("WARNING: could not register Ctrl+Shift+O overlay hotkey (already in use?)", file=sys.stderr)
            return
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY:
                    overlay_events.toggle_overlay_edit_mode(threadsafe=True)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    threading.Thread(target=_listen, daemon=True).start()


def main() -> None:
    import webview

    thread = threading.Thread(target=_start_server, daemon=True)
    thread.start()

    if not _wait_for_server():
        print("ERROR: server did not start within 90 seconds", file=sys.stderr)
        sys.exit(1)

    profile_dir = ROOT / "data" / "webview_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    _configure_profile(profile_dir)  # apply before start (2nd+ launch, or pre-seeds fresh install)

    win = webview.create_window(
        "Lykompanion",
        f"{URL}/?token={API_TOKEN}",
        width=1280,
        height=820,
        min_size=(800, 600),
    )
    win.expose(_make_download_api(win))
    win.events.loaded += lambda: _lock_down_webview(win)

    # In-game overlay: a second, transparent, click-through, always-on-top window covering the
    # primary screen (web/overlay.html renders replies/reminders/game state over the game).
    # The server runs in this same process, so this reads the live Settings singleton - but
    # only at launch: enabling the setting takes effect on the next start.
    from app.core.config import settings as app_settings
    if app_settings.overlay_enabled:
        overlay_win = webview.create_window(
            "Lykompanion Overlay",
            f"{URL}/overlay.html?token={API_TOKEN}",
            x=0,
            y=0,
            width=ctypes.windll.user32.GetSystemMetrics(0),
            height=ctypes.windll.user32.GetSystemMetrics(1),
            frameless=True,
            easy_drag=False,
            on_top=True,
            transparent=True,
            focus=False,
        )
        _set_overlay_click_through = _make_overlay_click_through_setter(overlay_win)

        def _init_overlay_styles() -> None:
            # WS_EX_LAYERED must be set as early as possible, ideally before WebView2's first
            # paint, so DWM engages per-pixel compositing from the start instead of showing an
            # opaque white frame first - `shown` fires as soon as the native window appears,
            # well before `loaded` (page content finished loading).
            _apply_overlay_base_styles(overlay_win)
            _set_overlay_click_through(True)
            # WebView2 spawns its Chromium child windows asynchronously - a single pass right
            # at `shown` can miss late arrivals, so sweep once more shortly after.
            threading.Timer(2.0, lambda: _set_overlay_click_through(True)).start()

        overlay_win.events.shown += _init_overlay_styles

        # Let the layout-editor endpoint (POST /api/overlay/edit-mode) lift/restore the
        # click-through styles - the server runs in this same process.
        from app.core import events as overlay_events
        overlay_events.register_overlay_click_through_setter(_set_overlay_click_through)

        _start_overlay_hotkey_listener()

        def _close_overlay() -> None:
            # Closing the main window must take the overlay with it - otherwise an invisible
            # click-through window keeps the app alive with no way to close it.
            try:
                overlay_win.destroy()
            except Exception:
                pass

        win.events.closed += _close_overlay

    # private_mode=False required — without it pywebview ignores storage_path and uses
    # an in-memory session, so permissions and download prefs are never written to disk.
    webview.start(icon=_build_icon(), storage_path=str(profile_dir), private_mode=False)
    _configure_profile(profile_dir)  # re-apply after close so next launch inherits the settings


if __name__ == "__main__":
    main()
