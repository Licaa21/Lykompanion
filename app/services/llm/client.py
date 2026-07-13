import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable

import httpx
import openai as openai_module
from openai import AsyncOpenAI

from app.core import debug_log
from app.core import provider_routing
from app.core.config import settings
from app.core.usage import record_usage

# Google AI Studio's OpenAI-compatibility endpoint - a Gemini API key + this base_url is all
# that's needed to reuse the same OpenAI SDK request path as OpenRouter/custom endpoints.
GOOGLE_AI_STUDIO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Asks OpenRouter to include actual generation cost (in USD) on the usage object,
# not just token counts - off by default, and OpenRouter-specific so only sent to that provider.
_USAGE_EXTRA_BODY = {"usage": {"include": True}}


def _openrouter_extra_body(model: str) -> dict:
    """Builds the extra_body sent on every OpenRouter call: usage-cost reporting plus, if the user
    has configured provider-routing preferences for this exact model (Settings > per-model
    "Providers" picker), OpenRouter's request-level `provider` routing object - see
    https://openrouter.ai/docs/guides/routing/provider-selection. Only fields the user actually
    set are included; an unconfigured model gets OpenRouter's own default routing untouched."""
    extra_body = dict(_USAGE_EXTRA_BODY)
    routing = provider_routing.get_for_model(model)
    provider_obj: dict = {}
    if routing.get("only"):
        provider_obj["only"] = routing["only"]
    if routing.get("sort"):
        provider_obj["sort"] = routing["sort"]
    if routing.get("allow_fallbacks") is False:
        provider_obj["allow_fallbacks"] = False
    max_price: dict = {}
    if routing.get("max_price_prompt"):
        max_price["prompt"] = routing["max_price_prompt"]
    if routing.get("max_price_completion"):
        max_price["completion"] = routing["max_price_completion"]
    if max_price:
        provider_obj["max_price"] = max_price
    if provider_obj:
        extra_body["provider"] = provider_obj
    return extra_body


def _provider_config(provider: str) -> tuple[str, str]:
    """Returns (api_key, base_url) for a provider name ("openrouter" | "google_ai_studio" |
    "custom"). Unrecognized/empty values fall back to openrouter, matching existing behavior."""
    if provider == "google_ai_studio":
        return settings.google_ai_studio_api_key or "unset", GOOGLE_AI_STUDIO_BASE_URL
    if provider == "custom":
        return settings.custom_openai_api_key or "unset", settings.custom_openai_base_url or "https://api.openai.com/v1"
    return settings.openrouter_api_key or "unset", settings.openrouter_base_url


# One AsyncOpenAI instance per (provider, api_key, base_url) triple - saved settings changes
# (a new key/URL via the config API) transparently get a fresh client on the next call instead
# of an existing instance silently keeping stale credentials.
_client_cache: dict[tuple[str, str, str], AsyncOpenAI] = {}


def get_client(provider: str, max_retries: int | None = None) -> AsyncOpenAI:
    """`max_retries` overrides the SDK's default retry count (2) - notably, the SDK sleeps for
    the upstream `Retry-After` value on a 429 (observed up to 60s) as part of "one retry," so a
    caller that's itself inside a loop with its own natural retry cadence (the game-state
    poller's next tick) should pass 0 to fail fast rather than stack its own cadence on top of
    the SDK's blocking sleep. None = SDK default, used by every other (interactive) call site."""
    api_key, base_url = _provider_config(provider)
    cache_key = (provider, api_key, base_url, max_retries)
    cached = _client_cache.get(cache_key)
    if cached is not None:
        return cached
    # Without an explicit timeout the SDK falls back to httpx's default (600s) - a hung/stalled
    # upstream response would hold a chat request open for minutes instead of failing fast.
    fresh = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=45.0,
        max_retries=max_retries if max_retries is not None else openai_module.DEFAULT_MAX_RETRIES,
    )
    # Evict only this provider's stale entries (credentials/URL changed) - clearing the whole
    # cache would make two configured providers evict each other on every alternating call.
    for key in [k for k in _client_cache if k[0] == provider]:
        del _client_cache[key]
    _client_cache[cache_key] = fresh
    return fresh


# Re-arms on every chunk instead of capping the whole stream, so a legitimately long reply never
# trips this as long as chunks keep arriving - only a genuine stall between chunks does. The
# client's own timeout=45.0 (get_client above) covers the initial request; this is a backstop for
# mid-stream stalls, which some providers don't reliably bound on their own once the response has
# already started, unlike a plain non-streaming request.
_STREAM_CHUNK_TIMEOUT_SECONDS = 45.0


async def _iter_with_timeout(aiter, timeout: float):
    it = aiter.__aiter__()
    while True:
        try:
            item = await asyncio.wait_for(it.__anext__(), timeout)
        except StopAsyncIteration:
            return
        yield item


def _tool_names(tools: list[dict] | None) -> list[str] | None:
    if not tools:
        return None
    return [t["function"]["name"] for t in tools if "function" in t]


def _track(
    *,
    source: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    usage,
    reply: str | None,
    tool_calls: list[dict] | None,
    duration_ms: float,
) -> None:
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    cost_usd = getattr(usage, "cost", 0) or 0
    if usage:
        record_usage(prompt_tokens, completion_tokens, cost_usd, source)
    debug_log.record_request(
        source=source,
        model=model,
        messages=messages,
        tools=_tool_names(tools),
        reply=reply,
        tool_calls=tool_calls,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
    )


def _record_error(source: str, model: str, messages: list[dict], tools: list[dict] | None, exc: Exception, duration_ms: float) -> None:
    debug_log.record_error(
        source=source, model=model, messages=messages, tools=_tool_names(tools), error=str(exc), duration_ms=duration_ms
    )


def _first_choice(response, source: str, model: str, messages: list[dict], tools: list[dict] | None, duration_ms: float):
    """Guards the non-streaming `response.choices[0]` access every call site needs. A provider
    under abuse/rate-limit pressure can return an HTTP 200 with `choices: null`/`[]` instead of a
    proper error status (observed: Xiaomi's risk_control) - without this, indexing crashes with a
    bare TypeError that bypasses _record_error entirely, since the try/except around the API call
    itself doesn't cover this line. Raises a clear, recorded error instead."""
    if not response.choices:
        exc = RuntimeError(f"Provider returned no choices in the response (model={model!r})")
        _record_error(source, model, messages, tools, exc, duration_ms)
        raise exc
    return response.choices[0]


_INVALID_JSON_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrtu])')


def parse_json_reply(raw: str) -> dict:
    """Parses a JSON-mode model reply, tolerating one specific, observed LLM slip: a backslash
    that isn't a legal JSON escape (e.g. a model writing "\\[item]" meaning the literal text
    "[item]", not an escape sequence - "\\[" isn't valid JSON, so a strict parser rejects the
    whole response even though everything else about it is fine). Falls back to stripping exactly
    those invalid backslashes and re-parsing once; a genuinely incomplete/truncated response still
    raises after that - correctly, since there's nothing to salvage from one that just stops
    mid-string, and callers already handle that failure (retry, log-and-skip, etc.)."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_INVALID_JSON_ESCAPE_RE.sub("", raw))


def _tool_calls_to_dicts(tool_calls) -> list[dict] | None:
    if not tool_calls:
        return None
    return [
        {"name": tc.function.name, "arguments": tc.function.arguments}
        for tc in tool_calls
        if tc.function
    ]


_FALLBACK_MAX_TOKENS = 8192


async def _resolve_max_tokens(model: str, provider: str) -> int:
    """The model's own real max output tokens when the catalog exposes it (OpenRouter's /models
    endpoint reports this per model, e.g. 65535 for google/gemini-2.5-flash-lite, 16384 for
    meta-llama/llama-4-maverick - see list_models()), so a shared default doesn't needlessly cap
    a model that can actually output far more. Falls back to a safe generous constant for a
    non-OpenRouter provider (Google AI Studio/custom endpoints don't report this), a model not
    found in the catalog (a typo, or one too new for the cached fetch), or if the lookup itself
    fails for any reason - this must never be the reason a real completion call fails."""
    if provider == "openrouter":
        try:
            for entry in await list_models(provider):
                if entry["id"] == model:
                    cap = entry.get("max_completion_tokens")
                    if cap:
                        return cap
                    break
        except Exception:
            pass
    return _FALLBACK_MAX_TOKENS


async def chat_completion(
    messages: list[dict],
    model: str | None = None,
    response_format: dict | None = None,
    source: str = "unknown",
    provider: str = "openrouter",
    on_usage: Callable[[float], None] | None = None,
    max_retries: int | None = None,
    max_tokens: int | None = None,
) -> str:
    """`on_usage`, if given, is called with the call's cost in USD once usage is known - lets
    callers that care about cost (e.g. session stats) avoid re-deriving it from the debug log.
    `max_retries` - see get_client(). `max_tokens` left as None (every caller here does) resolves
    to the model's own real output-token ceiling via _resolve_max_tokens, rather than a fixed
    number - every caller of this function is a background/structured-JSON pass (bootstrap,
    game-state extraction, memory/observation passes, chat title generation), several of which
    combine multiple fields plus a document into one response; some providers otherwise fall back
    to a much smaller default, silently truncating mid-JSON (observed live: a game_bootstrap
    reply combining trackers + a training-data document cut off mid-sentence, and the whole
    result - including trackers that would have worked fine alone - got discarded when it failed
    to parse). Pass an explicit value to deliberately force a smaller cap instead of resolving
    the model's real one."""
    resolved_model = model or settings.openrouter_model
    resolved_max_tokens = max_tokens if max_tokens is not None else await _resolve_max_tokens(resolved_model, provider)
    client = get_client(provider, max_retries=max_retries)
    extra_body = _openrouter_extra_body(resolved_model) if provider == "openrouter" else None
    start = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            response_format=response_format,
            max_tokens=resolved_max_tokens,
            extra_body=extra_body,
        )
    except Exception as exc:
        _record_error(source, resolved_model, messages, None, exc, (time.monotonic() - start) * 1000)
        raise
    duration_ms = (time.monotonic() - start) * 1000
    content = _first_choice(response, source, resolved_model, messages, None, duration_ms).message.content or ""
    _track(
        source=source,
        model=resolved_model,
        messages=messages,
        tools=None,
        usage=response.usage,
        reply=content,
        tool_calls=None,
        duration_ms=duration_ms,
    )
    if on_usage:
        on_usage(getattr(response.usage, "cost", 0) or 0)
    return content


async def chat_completion_stream(
    messages: list[dict], model: str | None = None, source: str = "unknown", provider: str = "openrouter"
) -> AsyncIterator[str]:
    resolved_model = model or settings.openrouter_model
    client = get_client(provider)
    extra_body = _openrouter_extra_body(resolved_model) if provider == "openrouter" else None
    start = time.monotonic()
    try:
        stream = await client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=extra_body,
        )
    except Exception as exc:
        _record_error(source, resolved_model, messages, None, exc, (time.monotonic() - start) * 1000)
        raise
    full_text = ""
    usage = None
    async for chunk in stream:
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                full_text += delta
                yield delta
    duration_ms = (time.monotonic() - start) * 1000
    _track(
        source=source,
        model=resolved_model,
        messages=messages,
        tools=None,
        usage=usage,
        reply=full_text,
        tool_calls=None,
        duration_ms=duration_ms,
    )


async def chat_completion_message(
    messages: list[dict],
    model: str | None = None,
    tools: list[dict] | None = None,
    source: str = "unknown",
    provider: str = "openrouter",
):
    """Returns the raw assistant message, which may carry tool_calls instead of (or alongside) content."""
    resolved_model = model or settings.openrouter_model
    client = get_client(provider)
    extra_body = _openrouter_extra_body(resolved_model) if provider == "openrouter" else None
    start = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            tools=tools,
            extra_body=extra_body,
        )
    except Exception as exc:
        _record_error(source, resolved_model, messages, tools, exc, (time.monotonic() - start) * 1000)
        raise
    duration_ms = (time.monotonic() - start) * 1000
    message = _first_choice(response, source, resolved_model, messages, tools, duration_ms).message
    _track(
        source=source,
        model=resolved_model,
        messages=messages,
        tools=tools,
        usage=response.usage,
        reply=message.content,
        tool_calls=_tool_calls_to_dicts(message.tool_calls),
        duration_ms=duration_ms,
    )
    return message


async def stream_chat_completion_deltas(
    messages: list[dict],
    model: str | None = None,
    tools: list[dict] | None = None,
    source: str = "unknown",
    provider: str = "openrouter",
):
    """Yields raw delta objects (not just text) so callers can also observe streamed tool_calls."""
    resolved_model = model or settings.openrouter_model
    client = get_client(provider)
    extra_body = _openrouter_extra_body(resolved_model) if provider == "openrouter" else None
    start = time.monotonic()
    try:
        stream = await client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            tools=tools,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=extra_body,
        )
    except Exception as exc:
        _record_error(source, resolved_model, messages, tools, exc, (time.monotonic() - start) * 1000)
        raise
    full_text = ""
    tool_call_fragments: dict[int | str, dict] = {}
    usage = None
    async for chunk in _iter_with_timeout(stream, _STREAM_CHUNK_TIMEOUT_SECONDS):
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices:
            delta = chunk.choices[0].delta
            if delta.content:
                full_text += delta.content
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    # See the matching comment in chat.py's _stream_chat_with_tools: some providers
                    # reuse the same delta.index for distinct parallel tool calls, so fall back to id.
                    key = tc.index
                    existing = tool_call_fragments.get(key)
                    if tc.id and existing and existing.get("id") and existing["id"] != tc.id:
                        key = tc.id
                    entry = tool_call_fragments.setdefault(key, {"id": tc.id, "name": "", "arguments": ""})
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function and tc.function.name:
                        entry["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        entry["arguments"] += tc.function.arguments
            yield delta
    duration_ms = (time.monotonic() - start) * 1000
    _track(
        source=source,
        model=resolved_model,
        messages=messages,
        tools=tools,
        usage=usage,
        reply=full_text or None,
        tool_calls=list(tool_call_fragments.values()) or None,
        duration_ms=duration_ms,
    )


async def fetch_account_balance(provider: str = "openrouter") -> "AccountBalance":
    from app.models.schemas import AccountBalance

    if provider != "openrouter":
        # Neither Google AI Studio nor a generic custom OpenAI-compatible endpoint expose a
        # standard balance/credits API - nothing to fetch, so report unavailable rather than
        # guessing at a provider-specific endpoint that may not exist.
        return AccountBalance(available=False, provider=provider, reason="No balance API for this provider")

    if not settings.openrouter_management_key:
        return AccountBalance(available=False, provider=provider, reason="No management key configured")
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.get(
                f"{settings.openrouter_base_url}/credits",
                headers={"Authorization": f"Bearer {settings.openrouter_management_key}"},
            )
            response.raise_for_status()
            data = response.json().get("data", {})
        total = float(data.get("total_credits") or 0)
        used = float(data.get("total_usage") or 0)
        remaining = total - used
        return AccountBalance(
            available=True,
            provider=provider,
            spent_usd=used,
            limit_usd=total,
            remaining_usd=remaining,
        )
    except Exception as exc:
        return AccountBalance(available=False, provider=provider, reason=str(exc))


# Short-TTL catalog cache - the Settings modal fires half a dozen model-list fetches per open,
# and the catalogs barely change. "Refresh Model Lists" bypasses with force=True.
_MODELS_CACHE_TTL_SECONDS = 300
_models_cache: dict[str, tuple[float, list[dict]]] = {}


async def list_models(provider: str = "openrouter", force: bool = False) -> list[dict]:
    """Fetch models available for a provider, with input/output modality info where the provider
    exposes it. Results are cached for a few minutes per provider unless force=True.

    OpenRouter's /models endpoint only returns chat-completion-style models by default -
    dedicated Speech/Transcription-category models (e.g. Kokoro, Voxtral Mini TTS) are omitted
    unless output_modalities=all is passed. Other OpenAI-compatible providers (Google AI Studio,
    a custom endpoint) only expose a bare model id via the standard SDK model-list call, with no
    modality metadata - callers should not modality-filter those results.
    """
    cached = _models_cache.get(provider)
    if not force and cached and time.monotonic() - cached[0] < _MODELS_CACHE_TTL_SECONDS:
        return cached[1]

    if provider != "openrouter":
        try:
            response = await get_client(provider).models.list()
        except Exception:
            return []
        models = [
            {
                "id": m.id,
                "name": m.id,
                "context_length": None,
                "input_modalities": [],
                "output_modalities": [],
                "supported_voices": [],
                "supported_parameters": [],
            }
            for m in response.data
        ]
        _models_cache[provider] = (time.monotonic(), models)
        return models

    async with httpx.AsyncClient(base_url=settings.openrouter_base_url, timeout=15) as http_client:
        response = await http_client.get("/models", params={"output_modalities": "all"})
        response.raise_for_status()
        data = response.json().get("data", [])

    models = []
    for model in data:
        architecture = model.get("architecture", {})
        models.append(
            {
                "id": model["id"],
                "name": model.get("name", model["id"]),
                "context_length": model.get("context_length"),
                "input_modalities": architecture.get("input_modalities", []),
                "output_modalities": architecture.get("output_modalities", []),
                "supported_voices": model.get("supported_voices") or [],
                "supported_parameters": model.get("supported_parameters") or [],
                # The model's own real output-token ceiling (e.g. 65535 for gemini-2.5-flash-lite,
                # 16384 for llama-4-maverick) - used by chat_completion()'s max_tokens
                # auto-detection so a shared conservative default doesn't needlessly cap a model
                # that can actually output far more. None when OpenRouter doesn't report one.
                "max_completion_tokens": (model.get("top_provider") or {}).get("max_completion_tokens"),
            }
        )
    _models_cache[provider] = (time.monotonic(), models)
    return models


# Separate cache from _models_cache (keyed by model id, not provider) since this is a distinct
# OpenRouter endpoint (per-model provider breakdown, not the model catalog).
_endpoints_cache: dict[str, tuple[float, list[dict]]] = {}


async def list_model_endpoints(model_id: str, force: bool = False) -> list[dict]:
    """Real providers OpenRouter currently routes a given model through, with per-provider price/
    context/quantization/reliability - powers the Settings "Providers" picker next to each model
    dropdown. OpenRouter-only (this is an OpenRouter-specific API with no equivalent on Google AI
    Studio/custom endpoints)."""
    cached = _endpoints_cache.get(model_id)
    if not force and cached and time.monotonic() - cached[0] < _MODELS_CACHE_TTL_SECONDS:
        return cached[1]

    async with httpx.AsyncClient(base_url=settings.openrouter_base_url, timeout=15) as http_client:
        response = await http_client.get(f"/models/{model_id}/endpoints")
        response.raise_for_status()
        data = response.json().get("data", {})

    endpoints = []
    for ep in data.get("endpoints", []):
        pricing = ep.get("pricing") or {}
        endpoints.append(
            {
                "tag": ep.get("tag", ep.get("provider_name", "")),
                "provider_name": ep.get("provider_name", ""),
                "pricing_prompt": float(pricing["prompt"]) if pricing.get("prompt") is not None else None,
                "pricing_completion": float(pricing["completion"]) if pricing.get("completion") is not None else None,
                "context_length": ep.get("context_length"),
                "quantization": ep.get("quantization"),
                "uptime_last_30m": ep.get("uptime_last_30m"),
                "latency_last_30m": ep.get("latency_last_30m"),
                "throughput_last_30m": ep.get("throughput_last_30m"),
            }
        )
    _endpoints_cache[model_id] = (time.monotonic(), endpoints)
    return endpoints
