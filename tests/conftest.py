"""Shared pytest fixtures. Autouse fixtures here apply to every test in the suite."""

from collections import deque

import pytest

from app.core import debug_log, reminders


@pytest.fixture(autouse=True)
def _isolate_reminders(tmp_path, monkeypatch):
    """Same lesson as _isolate_debug_log below: reminders_store.add_pending() persists to the
    REAL data/reminders_pending.json, and production code paths tests exercise (e.g. the
    deterministic session-switch notifying the player) call it as a side effect - isolate the
    paths and their module-level caches for every test so no test can leave phantom pending
    messages in the running app's queue."""
    monkeypatch.setattr(reminders, "REMINDERS_PATH", tmp_path / "reminders.json")
    monkeypatch.setattr(reminders, "PENDING_PATH", tmp_path / "reminders_pending.json")
    monkeypatch.setattr(reminders, "_entries_cache", None)
    monkeypatch.setattr(reminders, "_pending_cache", None)


@pytest.fixture(autouse=True)
def _isolate_debug_log(tmp_path, monkeypatch):
    """debug_log.record_request()/record_error() write to a REAL file (data/debug_log.json)
    whenever settings.debug_mode_enabled is True - which it is in this project's real .env. Any
    test that exercises the actual chat_completion()/chat_completion_message() code path (even
    with a faked HTTP client - only the network call itself is mocked) triggers this for real, and
    _save_to_disk() overwrites the real file with whatever's now in the in-memory ring buffer.
    Confirmed live: a normal working session's repeated `pytest tests` runs fully evicted every
    real entry from the 50-slot buffer, replacing them with test-sourced junk. Isolate both the
    disk path and the in-memory deque for every test, not just the ones that know to think about
    it - this must never touch the real file regardless of what any test does."""
    monkeypatch.setattr(debug_log, "_DEBUG_LOG_PATH", tmp_path / "debug_log.json")
    monkeypatch.setattr(debug_log, "_entries", deque(maxlen=debug_log._MAX_ENTRIES))
