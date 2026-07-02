import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REMINDERS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "reminders.json"
PENDING_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "reminders_pending.json"

# Serializes read-modify-write cycles - concurrent tool calls (asyncio.gather) or the poller
# firing while a tool mutates the same file must not overwrite each other's changes.
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_entries() -> list[dict]:
    if not REMINDERS_PATH.exists():
        return []
    return json.loads(REMINDERS_PATH.read_text(encoding="utf-8"))


def save_entries(entries: list[dict]) -> None:
    REMINDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REMINDERS_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def list_reminders() -> list[dict]:
    return [e for e in load_entries() if e["kind"] == "reminder"]


def list_alarms() -> list[dict]:
    return [e for e in load_entries() if e["kind"] == "alarm"]


def add_reminder(message: str, process: str, interval_minutes: int) -> dict:
    with _lock:
        entries = load_entries()
        entry = {
            "id": uuid.uuid4().hex[:8],
            "kind": "reminder",
            "message": message,
            "process": process,
            "interval_minutes": interval_minutes,
            "next_fire_at": (_now() + timedelta(minutes=interval_minutes)).isoformat(),
        }
        entries.append(entry)
        save_entries(entries)
        return entry


def remove_reminder(reminder_id: str) -> bool:
    with _lock:
        entries = load_entries()
        filtered = [e for e in entries if not (e["kind"] == "reminder" and e["id"] == reminder_id)]
        if len(filtered) == len(entries):
            return False
        save_entries(filtered)
        return True


def add_alarm(message: str, process: str, fire_at: str) -> dict:
    with _lock:
        entries = load_entries()
        entry = {
            "id": uuid.uuid4().hex[:8],
            "kind": "alarm",
            "message": message,
            "process": process,
            "fire_at": fire_at,
        }
        entries.append(entry)
        save_entries(entries)
        return entry


def cancel_alarm(alarm_id: str) -> bool:
    with _lock:
        entries = load_entries()
        filtered = [e for e in entries if not (e["kind"] == "alarm" and e["id"] == alarm_id)]
        if len(filtered) == len(entries):
            return False
        save_entries(filtered)
        return True


def _parse_when(value: str) -> datetime:
    """Alarm fire_at strings come from the LLM as naive local datetimes ('2026-07-01T21:00:00'),
    while reminder next_fire_at strings are UTC-aware - naive values are interpreted as local
    time so the two can be compared against the same aware 'now'."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def due_entries(foreground_process: str | None) -> list[dict]:
    """Entries whose scheduled time has passed AND whose process filter matches the game
    currently in the foreground - a reminder/alarm never fires while its game isn't being
    played, and simply waits (no catch-up burst) until that game is foreground again."""
    if not foreground_process:
        return []
    now = _now()
    due = []
    for entry in load_entries():
        if entry["process"].lower() != foreground_process.lower():
            continue
        when = entry["next_fire_at"] if entry["kind"] == "reminder" else entry["fire_at"]
        try:
            if _parse_when(when) <= now:
                due.append(entry)
        except ValueError:
            # One malformed timestamp must not stall every other reminder/alarm forever.
            continue
    return due


def mark_fired(entry: dict) -> None:
    """Reschedules a fired reminder for its next interval, or removes a one-time alarm."""
    with _lock:
        entries = load_entries()
        if entry["kind"] == "alarm":
            entries = [e for e in entries if e["id"] != entry["id"]]
        else:
            for e in entries:
                if e["id"] == entry["id"]:
                    e["next_fire_at"] = (_now() + timedelta(minutes=e["interval_minutes"])).isoformat()
                    break
        save_entries(entries)


def load_pending() -> list[dict]:
    if not PENDING_PATH.exists():
        return []
    return json.loads(PENDING_PATH.read_text(encoding="utf-8"))


def save_pending(pending: list[dict]) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps(pending, indent=2), encoding="utf-8")


def add_pending(text: str) -> dict:
    with _lock:
        pending = load_pending()
        entry = {"id": uuid.uuid4().hex[:8], "text": text}
        pending.append(entry)
        save_pending(pending)
        return entry


def remove_pending(pending_id: str) -> bool:
    with _lock:
        pending = load_pending()
        filtered = [p for p in pending if p["id"] != pending_id]
        if len(filtered) == len(pending):
            return False
        save_pending(filtered)
        return True
