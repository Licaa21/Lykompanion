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
from ctypes import wintypes
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


# Recolor the Windows 11 window border/outline (default is a bright accent/white that clashes
# with the dark UI) AND disable corner rounding on the frameless window. Both are DWM attributes
# (border colour, corner preference), NOT window styles — so this is safe and can't affect the
# frameless drag the way the reverted WS_THICKFRAME hack did. Corner rounding matters here because
# Win11 rounds top-level windows by default (DWMWCP_ROUND); our own custom maximize (JS snaps the
# frameless window to fill screen.availWidth/Height, see init.js) doesn't go through
# WindowState=Maximized, so the OS never suppresses rounding the way it does for a true maximized
# window — leaving the desktop visible through the clipped corners. argtypes are set so the
# 64-bit HWND isn't truncated to a 32-bit int (which would silently no-op).
_DWMWA_BORDER_COLOR = 34          # Win11 22000+
_DWMWA_WINDOW_CORNER_PREFERENCE = 33  # Win11 22000+
_DWMWCP_DONOTROUND = 1
_BORDER_COLORREF = 0x00C75F5B  # 0x00BBGGRR form of CSS --accent (#5b5fc7)
_dwmapi = ctypes.windll.dwmapi
_user32 = ctypes.windll.user32
_dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long  # HRESULT
_dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


def _remove_window_border(hwnd: int) -> None:
    color = ctypes.c_uint(_BORDER_COLORREF)
    _dwmapi.DwmSetWindowAttribute(hwnd, _DWMWA_BORDER_COLOR, ctypes.byref(color), ctypes.sizeof(color))
    corner_pref = ctypes.c_uint(_DWMWCP_DONOTROUND)
    _dwmapi.DwmSetWindowAttribute(
        hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner_pref), ctypes.sizeof(corner_pref)
    )


# Window position/size persistence — logical px, matching create_window's units and the values
# the frontend sends via window_save_bounds.
_WINDOW_STATE_FILE = ROOT / "data" / "window_state.json"


def _primary_work_area_logical() -> tuple[int, int, int, int]:
    """Primary monitor's work area (screen minus taskbar) in logical px, matching the units
    create_window uses elsewhere. Computed fresh on every launch so it's always correct for
    whatever screen/DPI/taskbar is active right now - unlike native WindowState.Maximized, which
    on a FRAMELESS window covers the entire monitor INCLUDING the taskbar (a well-known WinForms
    borderless-window quirk, confirmed the hard way: it looked like unwanted true fullscreen
    instead of a normal maximize). A plain Normal-state window explicitly sized to this rect gets
    the "fills the screen, taskbar still visible" look without touching WindowState at all.
    """
    SPI_GETWORKAREA = 0x0030
    rect = wintypes.RECT()
    _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    scale = (_user32.GetDpiForSystem() or 96) / 96.0
    return (
        int(rect.left / scale), int(rect.top / scale),
        int((rect.right - rect.left) / scale), int((rect.bottom - rect.top) / scale),
    )


def _save_window_state(x: float, y: float, w: float, h: float) -> None:
    try:
        _WINDOW_STATE_FILE.write_text(
            json.dumps({"x": int(x), "y": int(y), "w": int(w), "h": int(h)}), encoding="utf-8"
        )
    except Exception:
        pass


def _run_tray(win, tray: dict, quit_fn) -> None:
    """Run the system-tray icon loop (blocking — call in a daemon thread).

    Menu: "Open Lykompanion" (also the default action, so a double-click on the tray icon
    restores the window on Windows) and "Quit". Stores the icon in `tray["icon"]` so the
    quit path can stop it.
    """
    import pystray
    from PIL import Image

    image = Image.open(ROOT / "logo.png")

    def _open(icon, item) -> None:
        win.show()

    def _quit(icon, item) -> None:
        quit_fn()

    menu = pystray.Menu(
        pystray.MenuItem("Open Lykompanion", _open, default=True),
        pystray.MenuItem("Quit", _quit),
    )
    tray["icon"] = pystray.Icon("Lykompanion", image, "Lykompanion", menu)
    tray["icon"].run()


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

    # frameless: no native title bar — the web UI draws its own (web/index.html .titlebar) with
    # minimize/close buttons. easy_drag=False; the titlebar drives its own move/snap from JS.
    # Launches hidden at half the work-area width; init.js's setupDesktopTitlebar snaps it to full
    # (via the same JS maximize path as the titlebar's maximize button) ~100ms after the page is
    # ready, then calls window_show(). Doing the half->full resize while still hidden means the
    # whole transition happens off-screen - the window only ever appears already full-size.
    # window_state.json (still written by window_save_bounds on every drag/resize) is intentionally
    # not read for this initial size - every launch always starts at half-width, no stale saved
    # size to ever get out of sync with.
    x, y, w, h = _primary_work_area_logical()
    win = webview.create_window(
        "Lykompanion",
        f"{URL}/?token={API_TOKEN}",
        width=w // 2,
        height=h,
        x=x,
        y=y,
        min_size=(800, 600),
        frameless=True,
        easy_drag=False,
        hidden=True,
    )

    # Tray icon + clean-quit wiring. The custom titlebar's minimize hides the window to the tray
    # (window stays alive); close and the tray "Quit" item both go through _quit, which stops the
    # tray loop and destroys the window so webview.start() returns and the process exits cleanly
    # (a half-torn-down tray leaves a zombie icon that only disappears on hover).
    tray = {"icon": None}

    def _quit() -> None:
        icon = tray["icon"]
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        # Destroy every window, not just the main one - the pop-out player window
        # (open_player_window below) would otherwise keep the process alive after quit.
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass

    def window_minimize() -> None:
        win.hide()

    def window_close() -> None:
        _quit()

    # Reveals the window after init.js has driven the startup half->full resize while still
    # hidden (see create_window's hidden=True above) - called once from the same setTimeout that
    # applies the maximize, so the window only ever becomes visible already full-size.
    def window_show() -> None:
        win.show()

    # Resize + maximize are driven from JS (the frontend's resize grips and titlebar double-click)
    # through pywebview's own move/resize — NOT by mutating the window style, which fought the
    # frameless drag and broke moving the window. One combined setter keeps it to a single bridge
    # call per drag frame. min_size is enforced here as a backstop to the JS clamp.
    def window_set_bounds(x: float, y: float, w: float, h: float) -> None:
        try:
            win.move(int(x), int(y))
            win.resize(max(int(w), 800), max(int(h), 600))
        except Exception:
            pass

    # Persist logical bounds for next-launch restore. The frontend calls this only at gesture end
    # (drag/resize release, snap, maximize), not every frame, so file writes stay cheap.
    def window_save_bounds(x: float, y: float, w: float, h: float) -> None:
        _save_window_state(x, y, w, h)

    # App-wide fullscreen (F11) - pywebview's native toggle, same mechanism as the YouTube pop-out
    # window's own fullscreen button. Hiding the custom titlebar is handled purely in CSS/JS
    # (body.app-fullscreen, toggled from the same F11 handler that calls this) since pywebview's
    # fullscreen only affects the OS window frame/bounds, not our own HTML chrome.
    def window_toggle_fullscreen() -> None:
        try:
            win.toggle_fullscreen()
        except Exception:
            pass

    # Pop-out YouTube player: a real second OS window (so the OS gives dragging to another
    # monitor for free) but frameless like the main window, for visual consistency - it draws its
    # own header/controls (web/player.html) instead of a native Windows title bar. Playback state
    # travels through localStorage, which both windows share (same WebView2 profile).
    # documentPictureInPicture doesn't exist in WebView2, so this native window IS the desktop
    # app's pop-out mechanism - the frontend branches on window.pywebview. Needs the same ?token=
    # handoff as the main window (player-window.js reads/strips it the same way core.js does) -
    # its playlists/search buttons call authenticated /api/* endpoints too.
    def open_player_window() -> None:
        child = webview.create_window(
            "Lykompanion Player",
            f"{URL}/player.html?token={API_TOKEN}",
            width=640,
            height=480,
            min_size=(380, 300),
            frameless=True,
            easy_drag=False,
            background_color="#0a0a12",
        )

        # Set right before OUR OWN destroy() call below, so _on_closing (which fires as PART of
        # that same destroy(), synchronously, on the same thread - see its comment) can tell "this
        # closing event was caused by our own player_close()" apart from an external one (Alt+F4).
        # Necessary: the JS side (closeSelf() in player-window.js) already wrote the handoff via
        # writeState() BEFORE calling this, so evaluate_js-ing that same, already-torn-down-by-us
        # window for a redundant handoff here isn't just pointless - it deadlocked the whole app
        # (evaluate_js blocks waiting on the webview's message loop to answer, but that loop is
        # itself blocked inside this very closing-event callback, which destroy() is waiting on to
        # return - a real circular wait, observed as "the window never actually closes and the app
        # freezes/crashes").
        self_initiated_close = False

        # Lets the pop-out page close its own window (e.g. its own close button, or a relayed
        # "stop" command) - window.close() inside WebView2 doesn't destroy the pywebview window.
        def player_close() -> None:
            nonlocal self_initiated_close
            self_initiated_close = True
            try:
                child.destroy()
            except Exception:
                pass

        # Native OS-level fullscreen (pywebview's own toggle, not the CSS-driven approach the main
        # window needs) - this window has no frameless-titlebar/app-shell baggage to fight with,
        # so the platform's real fullscreen just works.
        def player_toggle_fullscreen() -> None:
            try:
                child.toggle_fullscreen()
            except Exception:
                pass

        # Mirrors the main window's window_set_bounds - drives the frameless drag/resize this
        # window's own JS (player-window.js) implements for itself.
        def player_set_bounds(x: float, y: float, w: float, h: float) -> None:
            try:
                child.move(int(x), int(y))
                child.resize(max(int(w), 380), max(int(h), 300))
            except Exception:
                pass

        # events.closing fires for EVERY close path, including Alt+F4 / a taskbar close that never
        # goes through our own player_close() above (where the page's own JS already wrote the
        # handoff and this would be redundant - see self_initiated_close). It's the only remaining
        # safety net for a close pywebview/WebView2 didn't reliably dispatch `pagehide` for. Two
        # precautions given the deadlock above: skip entirely when self-initiated, and run the
        # evaluate_js off-thread so even an external close can't block this event (and therefore
        # destroy()) waiting on it - best-effort, not required for the close itself to succeed.
        def _on_closing() -> None:
            if self_initiated_close:
                return

            def _try_handoff() -> None:
                try:
                    child.evaluate_js("window.__lykoPlayerHandoff && window.__lykoPlayerHandoff()")
                except Exception:
                    pass

            threading.Thread(target=_try_handoff, daemon=True).start()

        child.events.closing += _on_closing
        child.expose(player_close, player_toggle_fullscreen, player_set_bounds)

    win.expose(
        _make_download_api(win), window_minimize, window_close, window_show,
        window_set_bounds, window_save_bounds, window_toggle_fullscreen, open_player_window,
    )

    def _on_loaded() -> None:
        _lock_down_webview(win)
        try:
            _remove_window_border(int(win._window.Handle.ToInt64()))
        except Exception:
            pass

    win.events.loaded += _on_loaded

    threading.Thread(target=_run_tray, args=(win, tray, _quit), daemon=True).start()

    # private_mode=False required — without it pywebview ignores storage_path and uses
    # an in-memory session, so permissions and download prefs are never written to disk.
    webview.start(icon=_build_icon(), storage_path=str(profile_dir), private_mode=False)
    _configure_profile(profile_dir)  # re-apply after close so next launch inherits the settings


if __name__ == "__main__":
    main()
