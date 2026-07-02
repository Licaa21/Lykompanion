"""Tests for app/core/usage.py - JSONL append semantics and legacy usage.json migration."""

import json

import pytest

from app.core import usage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "USAGE_PATH", tmp_path / "usage.jsonl")
    monkeypatch.setattr(usage, "_LEGACY_USAGE_PATH", tmp_path / "usage.json")


def test_record_and_load_roundtrip():
    usage.record_usage(100, 20, 0.003, "chat")
    usage.record_usage(50, 10, 0.001, "memory_extraction")
    records = usage.load_usage_records()
    assert len(records) == 2
    assert records[0]["source"] == "chat"
    assert records[1]["prompt_tokens"] == 50


def test_legacy_json_is_migrated_once():
    legacy = [{"timestamp": "t", "source": "chat", "prompt_tokens": 1, "completion_tokens": 2, "cost_usd": 0.0}]
    usage._LEGACY_USAGE_PATH.write_text(json.dumps(legacy), encoding="utf-8")

    records = usage.load_usage_records()
    assert records == legacy
    assert not usage._LEGACY_USAGE_PATH.exists()
    assert usage.USAGE_PATH.exists()

    # New records append after the migrated ones.
    usage.record_usage(9, 9, 0.0, "chat")
    assert len(usage.load_usage_records()) == 2


def test_corrupt_line_is_skipped():
    usage.record_usage(1, 1, 0.0, "chat")
    with usage.USAGE_PATH.open("a", encoding="utf-8") as f:
        f.write("{torn line\n")
    usage.record_usage(2, 2, 0.0, "chat")
    records = usage.load_usage_records()
    assert [r["prompt_tokens"] for r in records] == [1, 2]


def test_clear_empties_history():
    usage.record_usage(1, 1, 0.0, "chat")
    usage.clear_usage()
    assert usage.load_usage_records() == []
