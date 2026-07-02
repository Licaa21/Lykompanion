import time
from collections.abc import AsyncIterator, Callable

import httpx
from openai import AsyncOpenAI

from app.core import debug_log
from app.core.config import settings
from app.core.usage import record_usage

# Google AI Studio's OpenAI-compatibility endpoint - a Gemini API key + this base_url is all
# that's needed to reuse the same OpenAI SDK request path as OpenRouter/custom endpoints.
GOOGLE_AI_STUDIO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Asks OpenRouter to include actual generation cost (in USD) on the usage object,
# not just token counts - off by default, and OpenRouter-specific so only sent to that provider.
_USAGE_EXTRA_BODY = {"usage": {"include": True}}


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


def get_client(provider: str) -> AsyncOpenAI:
    api_key, base_url = _provider_config(provider)
    cache_key = (provider, api_key, base_url)
    cached = _client_cache.get(cache_key)
    if cached is not None:
        return cached
    fresh = AsyncOpenAI(api_key=api_key, base_url=base_url)
    # Evict only this provider's stale entries (credentials/URL changed) - clearing the whole
    # cache would make two configured providers evict each other on every alternating call.
    for key in [k for k in _client_cache if k[0] == provider]:
        del _client_cache[key]
    _client_cache[cache_key] = fresh
    return fresh


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


def _tool_calls_to_dicts(tool_calls) -> list[dict] | None:
    if not tool_calls:
        return None
    return [
        {"name": tc.function.name, "arguments": tc.function.arguments}
        for tc in tool_calls
        if tc.function
    ]


async def chat_completion(
    messages: list[dict],
    model: str | None = None,
    response_format: dict | None = None,
    source: str = "unknown",
    provider: str = "openrouter",
    on_usage: Callable[[float], None] | None = None,
) -> str:
    """`on_usage`, if given, is called with the call's cost in USD once usage is known - lets
    callers that care about cost (e.g. session stats) avoid re-deriving it from the debug log."""
    resolved_model = model or settings.openrouter_model
    client = get_client(provider)
    extra_body = _USAGE_EXTRA_BODY if provider == "openrouter" else None
    start = time.monotonic()
    response = await client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        response_format=response_format,
        extra_body=extra_body,
    )
    duration_ms = (time.monotonic() - start) * 1000
    content = response.choices[0].message.content or ""
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
    extra_body = _USAGE_EXTRA_BODY if provider == "openrouter" else None
    start = time.monotonic()
    stream = await client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=extra_body,
    )
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
    extra_body = _USAGE_EXTRA_BODY if provider == "openrouter" else None
    start = time.monotonic()
    response = await client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        tools=tools,
        extra_body=extra_body,
    )
    duration_ms = (time.monotonic() - start) * 1000
    message = response.choices[0].message
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
    extra_body = _USAGE_EXTRA_BODY if provider == "openrouter" else None
    start = time.monotonic()
    stream = await client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        tools=tools,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=extra_body,
    )
    full_text = ""
    tool_call_fragments: dict[int, dict] = {}
    usage = None
    async for chunk in stream:
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices:
            delta = chunk.choices[0].delta
            if delta.content:
                full_text += delta.content
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    entry = tool_call_fragments.setdefault(tc.index, {"name": "", "arguments": ""})
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
            }
        )
    _models_cache[provider] = (time.monotonic(), models)
    return models
