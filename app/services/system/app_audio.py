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


def get_peak_level(process_query: str) -> float | None:
    """Instantaneous peak audio level (0.0-1.0) for process_query's audio session(s) right now -
    the loudest matching session if there are several. This reads Windows' live per-session peak
    meter (no polling delay of its own), unlike the OCR-derived game-state "activity" label which
    lags ~15s behind reality - the intended use is gating narration playback against real game
    audio (e.g. waiting for a gap between dialogue lines) rather than a stale text classification.
    None if there's no matching session, on non-Windows, or the read failed - callers should treat
    that as "nothing to gate against," not as silence."""
    if sys.platform != "win32":
        return None
    try:
        # Moved from pycaw.api.audioclient to pycaw.api.endpointvolume at some point after this
        # was written (observed: pycaw 20251023 no longer has it under audioclient) - same COM
        # interface (IID C02216F6-...), just relocated.
        from pycaw.api.endpointvolume import IAudioMeterInformation

        peaks = [session._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue() for session in _find_sessions(process_query)]
        return max(peaks) if peaks else None
    except Exception:
        logger.exception("Failed to read peak audio level for %r", process_query)
        return None


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
