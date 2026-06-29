from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.usage import record_usage

client = AsyncOpenAI(
    api_key=settings.openrouter_api_key or "unset",
    base_url=settings.openrouter_base_url,
)

# Asks OpenRouter to include actual generation cost (in USD) on the usage object,
# not just token counts - off by default.
_USAGE_EXTRA_BODY = {"usage": {"include": True}}


def _track_usage(usage) -> None:
    if not usage:
        return
    record_usage(usage.prompt_tokens, usage.completion_tokens, getattr(usage, "cost", 0) or 0)


async def chat_completion(
    messages: list[dict], model: str | None = None, response_format: dict | None = None
) -> str:
    response = await client.chat.completions.create(
        model=model or settings.openrouter_model,
        messages=messages,
        response_format=response_format,
        extra_body=_USAGE_EXTRA_BODY,
    )
    _track_usage(response.usage)
    return response.choices[0].message.content or ""


async def chat_completion_stream(messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
    stream = await client.chat.completions.create(
        model=model or settings.openrouter_model,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=_USAGE_EXTRA_BODY,
    )
    async for chunk in stream:
        _track_usage(chunk.usage)
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


async def chat_completion_message(messages: list[dict], model: str | None = None, tools: list[dict] | None = None):
    """Returns the raw assistant message, which may carry tool_calls instead of (or alongside) content."""
    response = await client.chat.completions.create(
        model=model or settings.openrouter_model,
        messages=messages,
        tools=tools,
        extra_body=_USAGE_EXTRA_BODY,
    )
    _track_usage(response.usage)
    return response.choices[0].message


async def stream_chat_completion_deltas(messages: list[dict], model: str | None = None, tools: list[dict] | None = None):
    """Yields raw delta objects (not just text) so callers can also observe streamed tool_calls."""
    stream = await client.chat.completions.create(
        model=model or settings.openrouter_model,
        messages=messages,
        tools=tools,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=_USAGE_EXTRA_BODY,
    )
    async for chunk in stream:
        _track_usage(chunk.usage)
        if chunk.choices:
            yield chunk.choices[0].delta


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
