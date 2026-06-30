"""Desktop launcher — starts the FastAPI server in a background thread, then opens a
native app window (EdgeWebView2 on Windows 11) pointed at it. Close the window to exit."""

import json
import socket
import sys
import time
import threading
from pathlib import Path

PORT = 6692
URL = f"http://localhost:{PORT}"
ROOT = Path(__file__).resolve().parent


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
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, log_level="info")


def _wait_for_server(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
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


def main() -> None:
    import webview

    thread = threading.Thread(target=_start_server, daemon=True)
    thread.start()

    if not _wait_for_server():
        print("ERROR: server did not start within 20 seconds", file=sys.stderr)
        sys.exit(1)

    profile_dir = ROOT / "data" / "webview_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    _configure_profile(profile_dir)  # apply before start (2nd+ launch, or pre-seeds fresh install)

    win = webview.create_window(
        "Lykompanion",
        URL,
        width=1280,
        height=820,
        min_size=(800, 600),
    )
    win.expose(_make_download_api(win))
    win.events.loaded += lambda: _lock_down_webview(win)

    # private_mode=False required — without it pywebview ignores storage_path and uses
    # an in-memory session, so permissions and download prefs are never written to disk.
    webview.start(icon=_build_icon(), storage_path=str(profile_dir), private_mode=False)
    _configure_profile(profile_dir)  # re-apply after close so next launch inherits the settings


if __name__ == "__main__":
    main()
