"""Pure-logic tests for app/core/reminders.py - due-entry datetime handling in particular,
since the naive-vs-aware comparison bug silently killed all alarms once."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core import reminders


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(reminders, "REMINDERS_PATH", tmp_path / "reminders.json")
    monkeypatch.setattr(reminders, "PENDING_PATH", tmp_path / "reminders_pending.json")
    monkeypatch.setattr(reminders, "_entries_cache", None)
    monkeypatch.setattr(reminders, "_pending_cache", None)


def test_add_and_remove_reminder_roundtrip():
    entry = reminders.add_reminder("Save your game!", "game.exe", 5)
    assert reminders.list_reminders() == [entry]
    assert reminders.remove_reminder(entry["id"]) is True
    assert reminders.list_reminders() == []
    assert reminders.remove_reminder("nonexistent") is False


def test_reminder_not_due_until_interval_elapses():
    reminders.add_reminder("msg", "game.exe", 5)
    assert reminders.due_entries("game.exe") == []


def test_naive_local_alarm_in_past_is_due():
    past_local = (datetime.now() - timedelta(minutes=1)).isoformat()
    entry = reminders.add_alarm("Time to log off!", "game.exe", past_local)
    due = reminders.due_entries("game.exe")
    assert [e["id"] for e in due] == [entry["id"]]


def test_naive_local_alarm_in_future_is_not_due():
    future_local = (datetime.now() + timedelta(hours=1)).isoformat()
    reminders.add_alarm("later", "game.exe", future_local)
    assert reminders.due_entries("game.exe") == []


def test_aware_utc_alarm_is_handled():
    past_utc = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    entry = reminders.add_alarm("utc alarm", "game.exe", past_utc)
    assert [e["id"] for e in reminders.due_entries("game.exe")] == [entry["id"]]


def test_malformed_fire_at_is_skipped_without_stalling_others():
    reminders.add_alarm("broken", "game.exe", "not-a-datetime")
    good = reminders.add_alarm("good", "game.exe", (datetime.now() - timedelta(minutes=1)).isoformat())
    due = reminders.due_entries("game.exe")
    assert [e["id"] for e in due] == [good["id"]]


def test_due_entries_respects_foreground_process():
    reminders.add_alarm("msg", "game.exe", (datetime.now() - timedelta(minutes=1)).isoformat())
    assert reminders.due_entries("other.exe") == []
    assert reminders.due_entries(None) == []
    # Case-insensitive match
    assert len(reminders.due_entries("GAME.EXE")) == 1


def test_mark_fired_removes_alarm_but_reschedules_reminder():
    alarm = reminders.add_alarm("once", "game.exe", (datetime.now() - timedelta(minutes=1)).isoformat())
    reminders.mark_fired(alarm)
    assert reminders.list_alarms() == []

    reminder = reminders.add_reminder("again", "game.exe", 10)
    before = reminder["next_fire_at"]
    reminders.mark_fired(reminder)
    after = reminders.list_reminders()[0]["next_fire_at"]
    assert after >= before  # pushed into the future, entry still present


def test_pending_queue_roundtrip():
    entry = reminders.add_pending("Drink water!")
    assert reminders.load_pending() == [entry]
    assert reminders.remove_pending(entry["id"]) is True
    assert reminders.load_pending() == []
