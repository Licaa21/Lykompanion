import logging
import sys

logger = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    name = name.strip().lower()
    return name[:-4] if name.endswith(".exe") else name


def _find_sessions(process_query: str):
    from pycaw.pycaw import AudioUtilities

    query = _normalize(process_query)
    matches = []
    for session in AudioUtilities.GetAllSessions():
        proc = session.Process
        if not proc:
            continue
        name = _normalize(proc.name())
        if query in name or name in query:
            matches.append(session)
    return matches


def set_app_volume(process_query: str, volume_percent: int) -> list[str]:
    """Sets the Windows per-application mixer volume for every audio session whose process name
    matches process_query - case-insensitive, ".exe" suffix optional, substring match either way
    (handles the LLM naming an app loosely, e.g. "rocket league" for RocketLeague.exe). Returns
    the distinct process names that were adjusted (empty if none matched, or not on Windows)."""
    if sys.platform != "win32":
        return []
    try:
        clamped = max(0, min(100, volume_percent)) / 100
        touched = []
        for session in _find_sessions(process_query):
            session.SimpleAudioVolume.SetMasterVolume(clamped, None)
            touched.append(session.Process.name())
        return sorted(set(touched))
    except Exception:
        logger.exception("Failed to set application volume for %r", process_query)
        return []


def list_app_audio_sessions() -> list[dict]:
    """Currently active per-application audio sessions and their mixer volume - an app only shows
    up here once it has actually played something, since Windows doesn't create a session before
    that."""
    if sys.platform != "win32":
        return []
    try:
        from pycaw.pycaw import AudioUtilities

        seen = {}
        for session in AudioUtilities.GetAllSessions():
            proc = session.Process
            if not proc:
                continue
            volume = session.SimpleAudioVolume.GetMasterVolume()
            seen[proc.name().lower()] = {
                "process": proc.name(),
                "volume_percent": round(volume * 100),
            }
        return list(seen.values())
    except Exception:
        logger.exception("Failed to list application audio sessions")
        return []
