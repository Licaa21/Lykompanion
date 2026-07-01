import time
from collections.abc import AsyncIterator, Callable

import httpx
from openai import AsyncOpenAI

from app.core import debug_log
from app.core.config import settings
from app.core.usage import record_usage

client = AsyncOpenAI(
    api_key=settings.openrouter_api_key or "unset",
    base_url=settings.openrouter_base_url,
)

# Asks OpenRouter to include actual generation cost (in USD) on the usage object,
# not just token counts - off by default.
_USAGE_EXTRA_BODY = {"usage": {"include": True}}


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
    on_usage: Callable[[float], None] | None = None,
) -> str:
    """`on_usage`, if given, is called with the call's cost in USD once usage is known - lets
    callers that care about cost (e.g. session stats) avoid re-deriving it from the debug log."""
    resolved_model = model or settings.openrouter_model
    start = time.monotonic()
    response = await client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        response_format=response_format,
        extra_body=_USAGE_EXTRA_BODY,
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
    messages: list[dict], model: str | None = None, source: str = "unknown"
) -> AsyncIterator[str]:
    resolved_model = model or settings.openrouter_model
    start = time.monotonic()
    stream = await client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=_USAGE_EXTRA_BODY,
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
    messages: list[dict], model: str | None = None, tools: list[dict] | None = None, source: str = "unknown"
):
    """Returns the raw assistant message, which may carry tool_calls instead of (or alongside) content."""
    resolved_model = model or settings.openrouter_model
    start = time.monotonic()
    response = await client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        tools=tools,
        extra_body=_USAGE_EXTRA_BODY,
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
    messages: list[dict], model: str | None = None, tools: list[dict] | None = None, source: str = "unknown"
):
    """Yields raw delta objects (not just text) so callers can also observe streamed tool_calls."""
    resolved_model = model or settings.openrouter_model
    start = time.monotonic()
    stream = await client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        tools=tools,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=_USAGE_EXTRA_BODY,
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


async def fetch_account_balance() -> "AccountBalance":
    from app.models.schemas import AccountBalance

    if not settings.openrouter_management_key:
        return AccountBalance(available=False, reason="No management key configured")
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
            spent_usd=used,
            limit_usd=total,
            remaining_usd=remaining,
        )
    except Exception as exc:
        return AccountBalance(available=False, reason=str(exc))


async def list_models() -> list[dict]:
    """Fetch all models available on OpenRouter, with input/output modality info.

    OpenRouter's /models endpoint only returns chat-completion-style models by
    default — dedicated Speech/Transcription-category models (e.g. Kokoro,
    Voxtral Mini TTS) are omitted unless output_modalities=all is passed.
    """
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
    return models
