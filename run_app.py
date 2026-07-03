"""Desktop launcher — starts the FastAPI server in a background thread, then opens a
native app window (EdgeWebView2 on Windows 11) pointed at it. Close the window to exit."""

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
    # minimize/close buttons. easy_drag=False so only the explicit .pywebview-drag-region element
    # (the titlebar) moves the window, not clicks anywhere in the body.
    win = webview.create_window(
        "Lykompanion",
        f"{URL}/?token={API_TOKEN}",
        width=1280,
        height=820,
        min_size=(800, 600),
        frameless=True,
        easy_drag=False,
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
        try:
            win.destroy()
        except Exception:
            pass

    def window_minimize() -> None:
        win.hide()

    def window_close() -> None:
        _quit()

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

    win.expose(
        _make_download_api(win), window_minimize, window_close, window_set_bounds,
    )
    win.events.loaded += lambda: _lock_down_webview(win)

    threading.Thread(target=_run_tray, args=(win, tray, _quit), daemon=True).start()

    # private_mode=False required — without it pywebview ignores storage_path and uses
    # an in-memory session, so permissions and download prefs are never written to disk.
    webview.start(icon=_build_icon(), storage_path=str(profile_dir), private_mode=False)
    _configure_profile(profile_dir)  # re-apply after close so next launch inherits the settings


if __name__ == "__main__":
    main()
