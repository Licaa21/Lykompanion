"""Thin client for the standalone native overlay (overlay/overlay.exe).

The overlay is 100% native C++ — window, rendering, input, edit mode and layout
persistence all live in the exe. Python's ONLY responsibilities are here:
  1. process lifecycle: launch the exe when game-state tracking starts, ask it
     to quit when tracking stops / the app exits;
  2. pushing content: connect to the overlay's own local API (a named pipe it
     hosts) and write newline-delimited JSON commands into it.

No overlay behaviour is implemented in Python. Every call is best-effort: if the
overlay is disabled, missing, not yet up, or not on Windows, the calls no-op
silently and never raise into a caller (they run on latency-sensitive paths).
"""

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# overlay/Lykompanion-overlay.exe sits at the repo root, next to app/.
_OVERLAY_EXE = Path(__file__).resolve().parents[2] / "overlay" / "Lykompanion-overlay.exe"
_PIPE_PATH = r"\\.\pipe\lykompanion-overlay"

_proc: subprocess.Popen | None = None
_pipe = None  # persistent write handle to the overlay's named pipe
_lock = threading.Lock()  # guards _proc and _pipe across poller/chat/reminder threads


def is_enabled() -> bool:
    return bool(getattr(settings, "overlay_enabled", False)) and sys.platform == "win32"


def _disconnect() -> None:
    global _pipe
    if _pipe is not None:
        try:
            _pipe.close()
        except OSError:
            pass
        _pipe = None


def _ensure_pipe():
    """Lazily (re)open the write handle to the overlay's pipe. Returns None if the
    overlay isn't accepting connections yet — the next push will try again."""
    global _pipe
    if _pipe is not None:
        return _pipe
    try:
        _pipe = open(_PIPE_PATH, "wb", buffering=0)
    except OSError:
        _pipe = None
    return _pipe


def _write_locked(line: bytes) -> None:
    """Write one framed line to the pipe, reconnecting once on failure. Caller holds _lock."""
    pipe = _ensure_pipe()
    if pipe is None:
        return
    try:
        pipe.write(line)
    except OSError:
        _disconnect()
        pipe = _ensure_pipe()
        if pipe is None:
            return
        try:
            pipe.write(line)
        except OSError:
            _disconnect()


def start() -> None:
    """Launch the overlay exe if enabled and not already running."""
    global _proc
    if not is_enabled():
        return
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return
        if not _OVERLAY_EXE.exists():
            logger.warning(
                "Overlay enabled but %s is missing — build it with overlay/build.cmd "
                "(or ship the prebuilt exe). Overlay disabled for this run.",
                _OVERLAY_EXE,
            )
            return
        try:
            _proc = subprocess.Popen(
                # --parent lets the overlay self-exit if we're hard-killed (its
                # graceful stop() may never run in that case).
                [str(_OVERLAY_EXE), "--parent", str(os.getpid())],
                cwd=str(_OVERLAY_EXE.parent),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            logger.info("Overlay process started (pid=%s)", _proc.pid)
        except Exception:
            logger.exception("Failed to start overlay process")
            _proc = None


def push(command: dict) -> None:
    """Send one JSON command to the overlay. No-ops unless the overlay is running."""
    if not is_enabled():
        return
    with _lock:
        if _proc is None or _proc.poll() is not None:
            return
        try:
            line = (json.dumps(command, ensure_ascii=False) + "\n").encode("utf-8")
        except (TypeError, ValueError):
            return
        _write_locked(line)


def push_toast(text: str, kind: str = "reply") -> None:
    text = (text or "").strip()
    if not text:
        return
    if len(text) > 240:  # toasts are glanceable; keep them short
        text = text[:237].rstrip() + "…"
    push({"type": "toast", "text": text, "kind": kind})


def push_game_state(title: str, rows: list[list[str]]) -> None:
    push({"type": "game_state", "title": title, "rows": rows})


def set_edit_mode(enabled: bool) -> None:
    push({"type": "edit_mode", "enabled": bool(enabled)})


def stop() -> None:
    """Ask the overlay to quit (via its pipe), then ensure the process is gone."""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            try:
                _write_locked(b'{"type":"quit"}\n')
            except Exception:
                pass
            _disconnect()
            try:
                _proc.wait(timeout=2)
            except Exception:
                try:
                    _proc.terminate()
                except Exception:
                    pass
        else:
            _disconnect()
        _proc = None
