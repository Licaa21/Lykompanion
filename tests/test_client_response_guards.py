"""Tests for app/services/llm/client.py's guard against a malformed provider response.

Regression for the 2026-07-13 bug: a provider under abuse/rate-limit pressure can return an
HTTP 200 with choices: null/[] instead of a proper error status (observed: Xiaomi's risk_control
kicking in on the OpenRouter-hosted xiaomi/mimo-v2.5). response.choices[0] used to crash with a
bare, unrecorded TypeError that bypassed debug-log error tracking entirely - must now raise a
clear error that IS recorded, same as every other failure path."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.llm import client


class _FakeCompletions:
    def __init__(self, response):
        self._response = response

    async def create(self, **kwargs):
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_FakeCompletions(response))


def _patch_client(monkeypatch, response):
    monkeypatch.setattr(client, "get_client", lambda provider, max_retries=None: _FakeClient(response))
    recorded = []
    monkeypatch.setattr(client, "_record_error", lambda *args, **kwargs: recorded.append(args))
    return recorded


def test_chat_completion_raises_clear_error_on_empty_choices(monkeypatch):
    recorded = _patch_client(monkeypatch, SimpleNamespace(choices=None, usage=None))

    async def run():
        with pytest.raises(RuntimeError, match="no choices"):
            await client.chat_completion([{"role": "user", "content": "hi"}], source="test")

    asyncio.run(run())
    assert len(recorded) == 1


def test_chat_completion_message_raises_clear_error_on_empty_choices(monkeypatch):
    recorded = _patch_client(monkeypatch, SimpleNamespace(choices=[], usage=None))

    async def run():
        with pytest.raises(RuntimeError, match="no choices"):
            await client.chat_completion_message([{"role": "user", "content": "hi"}], source="test")

    asyncio.run(run())
    assert len(recorded) == 1


def test_chat_completion_returns_content_when_choices_present(monkeypatch):
    message = SimpleNamespace(content="hello there")
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
    _patch_client(monkeypatch, response)

    async def run():
        return await client.chat_completion([{"role": "user", "content": "hi"}], source="test")

    assert asyncio.run(run()) == "hello there"


class TestParseJsonReply:
    """Regression for the 2026-07-13 bug: google/gemini-2.5-flash-lite occasionally wrote an
    invalid JSON escape (e.g. "\\[item]" meaning the literal text "[item]", not an escape
    sequence) inside an otherwise well-formed response, and Python's strict json.loads rejected
    the entire reply - even though only that one backslash was actually wrong. Observed live in
    data/debug_log.json for a game_state_extraction call."""

    def test_parses_normal_json_unchanged(self):
        assert client.parse_json_reply('{"a": 1}') == {"a": 1}

    def test_recovers_from_invalid_escape_sequence(self):
        raw = r'{"note": "The text \[item] refers to a diamond variant."}'
        assert client.parse_json_reply(raw) == {"note": "The text [item] refers to a diamond variant."}

    def test_still_raises_on_genuine_truncation(self):
        # A response that just stops mid-string has nothing to salvage - must keep raising so
        # callers' existing retry/failure handling still runs, not silently return garbage.
        with pytest.raises(ValueError):
            client.parse_json_reply('{"activity": "Exploring a cave", "training_data_update": {"## UI/UX": ["The game')
