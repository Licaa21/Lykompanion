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
    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    async def create(self, **kwargs):
        self._calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response, calls):
        self.chat = SimpleNamespace(completions=_FakeCompletions(response, calls))


def _patch_client(monkeypatch, response):
    calls: list[dict] = []
    monkeypatch.setattr(client, "get_client", lambda provider, max_retries=None: _FakeClient(response, calls))
    recorded = []
    monkeypatch.setattr(client, "_record_error", lambda *args, **kwargs: recorded.append(args))
    return recorded, calls


def test_chat_completion_raises_clear_error_on_empty_choices(monkeypatch):
    recorded, _calls = _patch_client(monkeypatch, SimpleNamespace(choices=None, usage=None))

    async def run():
        with pytest.raises(RuntimeError, match="no choices"):
            await client.chat_completion([{"role": "user", "content": "hi"}], source="test")

    asyncio.run(run())
    assert len(recorded) == 1


def test_chat_completion_message_raises_clear_error_on_empty_choices(monkeypatch):
    recorded, _calls = _patch_client(monkeypatch, SimpleNamespace(choices=[], usage=None))

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


class TestResolveMaxTokens:
    """Regression coverage for the 2026-07-13 max_tokens fix: chat_completion() now resolves
    max_tokens to the actual configured model's own real output-token ceiling (via OpenRouter's
    /models catalog, cached by list_models()) instead of one hardcoded number shared by every
    model. Verified against the real, live-checked values: 65535 for
    google/gemini-2.5-flash-lite, 16384 for meta-llama/llama-4-maverick."""

    def test_uses_the_models_own_cap(self, monkeypatch):
        async def fake_list_models(provider):
            return [
                {"id": "google/gemini-2.5-flash-lite", "max_completion_tokens": 65535},
                {"id": "meta-llama/llama-4-maverick", "max_completion_tokens": 16384},
            ]

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            gemini = await client._resolve_max_tokens("google/gemini-2.5-flash-lite", "openrouter")
            llama = await client._resolve_max_tokens("meta-llama/llama-4-maverick", "openrouter")
            return gemini, llama

        assert asyncio.run(run()) == (65535, 16384)

    def test_falls_back_when_model_not_in_catalog(self, monkeypatch):
        async def fake_list_models(provider):
            return [{"id": "some/other-model", "max_completion_tokens": 4096}]

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            return await client._resolve_max_tokens("unknown/model", "openrouter")

        assert asyncio.run(run()) == client._FALLBACK_MAX_TOKENS

    def test_falls_back_for_non_openrouter_provider_without_calling_list_models(self, monkeypatch):
        async def fake_list_models(provider):
            raise AssertionError("list_models should not be called for a non-openrouter provider")

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            return await client._resolve_max_tokens("gemini-2.5-flash", "google_ai_studio")

        assert asyncio.run(run()) == client._FALLBACK_MAX_TOKENS

    def test_falls_back_if_catalog_lookup_raises(self, monkeypatch):
        async def fake_list_models(provider):
            raise RuntimeError("network error")

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            return await client._resolve_max_tokens("google/gemini-2.5-flash-lite", "openrouter")

        assert asyncio.run(run()) == client._FALLBACK_MAX_TOKENS

    def test_chat_completion_passes_the_resolved_cap_to_the_real_call(self, monkeypatch):
        # Guards against the exact mistake made while wiring this up: the resolved value was
        # computed but the actual create() call kept passing the original (always-None) argument.
        message = SimpleNamespace(content="ok")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        _recorded, calls = _patch_client(monkeypatch, response)

        async def fake_list_models(provider):
            return [{"id": "google/gemini-2.5-flash-lite", "max_completion_tokens": 65535}]

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            return await client.chat_completion(
                [{"role": "user", "content": "hi"}],
                model="google/gemini-2.5-flash-lite",
                source="test",
            )

        asyncio.run(run())
        assert calls[0]["max_tokens"] == 65535

    def test_chat_completion_respects_an_explicit_override(self, monkeypatch):
        message = SimpleNamespace(content="ok")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        _recorded, calls = _patch_client(monkeypatch, response)

        async def fake_list_models(provider):
            raise AssertionError("an explicit max_tokens must skip catalog auto-detection entirely")

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            return await client.chat_completion(
                [{"role": "user", "content": "hi"}], source="test", max_tokens=256,
            )

        asyncio.run(run())
        assert calls[0]["max_tokens"] == 256


class TestStructuredOutputs:
    """Coverage for the 2026-07-13 structured-outputs support: chat_completion(json_schema=...)
    should only actually request response_format: json_schema when the resolved model reports
    "structured_outputs" in its supported_parameters - forcing it on an unsupported model/provider
    would fail the request outright, which is worse than the plain json_object mode this upgrades."""

    _SCHEMA = {"name": "test_schema", "strict": True, "schema": {"type": "object", "properties": {}}}

    def test_uses_json_schema_when_model_supports_it(self, monkeypatch):
        message = SimpleNamespace(content="{}")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        _recorded, calls = _patch_client(monkeypatch, response)

        async def fake_list_models(provider):
            return [{"id": "google/gemini-2.5-flash-lite", "supported_parameters": ["structured_outputs", "max_tokens"]}]

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            return await client.chat_completion(
                [{"role": "user", "content": "hi"}],
                model="google/gemini-2.5-flash-lite",
                source="test",
                json_schema=self._SCHEMA,
            )

        asyncio.run(run())
        assert calls[0]["response_format"] == {"type": "json_schema", "json_schema": self._SCHEMA}

    def test_falls_back_to_json_object_when_model_lacks_support(self, monkeypatch):
        message = SimpleNamespace(content="{}")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        _recorded, calls = _patch_client(monkeypatch, response)

        async def fake_list_models(provider):
            return [{"id": "some/older-model", "supported_parameters": ["max_tokens"]}]

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            return await client.chat_completion(
                [{"role": "user", "content": "hi"}],
                model="some/older-model",
                source="test",
                response_format={"type": "json_object"},
                json_schema=self._SCHEMA,
            )

        asyncio.run(run())
        assert calls[0]["response_format"] == {"type": "json_object"}

    def test_falls_back_for_non_openrouter_provider_without_calling_list_models(self, monkeypatch):
        message = SimpleNamespace(content="{}")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        _recorded, calls = _patch_client(monkeypatch, response)

        async def fake_list_models(provider):
            raise AssertionError("list_models should not be called for a non-openrouter provider")

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            return await client.chat_completion(
                [{"role": "user", "content": "hi"}],
                source="test",
                provider="google_ai_studio",
                json_schema=self._SCHEMA,
            )

        asyncio.run(run())
        assert calls[0]["response_format"] is None

    def test_no_json_schema_argument_leaves_response_format_untouched(self, monkeypatch):
        message = SimpleNamespace(content="{}")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        _recorded, calls = _patch_client(monkeypatch, response)

        async def fake_list_models(provider):
            raise AssertionError("no capability lookup should happen when json_schema isn't used")

        monkeypatch.setattr(client, "list_models", fake_list_models)

        async def run():
            return await client.chat_completion(
                [{"role": "user", "content": "hi"}], source="test", response_format={"type": "json_object"},
            )

        asyncio.run(run())
        assert calls[0]["response_format"] == {"type": "json_object"}


class TestFinishReasonDiagnostics:
    """Coverage for the 2026-07-13 diagnostic addition: finish_reason was never inspected
    anywhere, so a genuine token-limit cutoff ("length") looked identical to a content-safety
    intervention ("content_filter") or anything else a provider might mean by "not stop" - all
    indistinguishable from the outside as just "the JSON failed to parse". Now logged and stored
    in debug_log.json so a future incident can tell which one actually happened."""

    def test_warns_on_non_stop_finish_reason(self, monkeypatch, caplog):
        message = SimpleNamespace(content="{}")
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="length")],
            usage=SimpleNamespace(completion_tokens=42, prompt_tokens=10, cost=0),
        )
        _patch_client(monkeypatch, response)

        async def run():
            return await client.chat_completion([{"role": "user", "content": "hi"}], source="test")

        with caplog.at_level("WARNING"):
            asyncio.run(run())

        assert any("length" in r.message for r in caplog.records)

    def test_no_warning_on_normal_stop(self, monkeypatch, caplog):
        message = SimpleNamespace(content="{}")
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(completion_tokens=42, prompt_tokens=10, cost=0),
        )
        _patch_client(monkeypatch, response)

        async def run():
            return await client.chat_completion([{"role": "user", "content": "hi"}], source="test")

        with caplog.at_level("WARNING"):
            asyncio.run(run())

        assert caplog.records == []

    def test_finish_reason_reaches_debug_log(self, monkeypatch):
        message = SimpleNamespace(content="{}")
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="content_filter")],
            usage=SimpleNamespace(completion_tokens=5, prompt_tokens=10, cost=0),
        )
        _patch_client(monkeypatch, response)
        recorded_calls = []
        monkeypatch.setattr(
            client.debug_log, "record_request",
            lambda **kwargs: recorded_calls.append(kwargs),
        )

        async def run():
            return await client.chat_completion([{"role": "user", "content": "hi"}], source="test")

        asyncio.run(run())
        assert recorded_calls[0]["finish_reason"] == "content_filter"


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

    def test_recovers_from_trailing_extra_data(self):
        # Regression for the 2026-07-13 "Extra data" bug (observation_confirmation.py): the model
        # produced a complete, valid JSON value and then kept talking past it (repeating itself /
        # adding commentary despite being told not to). Recover the first complete value and
        # discard the trailing noise, rather than failing on an otherwise-good answer.
        raw = '{"activity": "mining"}\nSorry, I should also mention the player found a diamond.'
        assert client.parse_json_reply(raw) == {"activity": "mining"}

    def test_recovers_when_both_invalid_escape_and_extra_data_are_present(self):
        raw = r'{"note": "The text \[item] variant."}' + "\nextra trailing commentary"
        assert client.parse_json_reply(raw) == {"note": "The text [item] variant."}
