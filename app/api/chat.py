import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openai import APIError

from app.core import debug_log, game_state, memory
from app.core.config import settings
from app.core.instructions import load_custom_instructions
from app.core.prompts import current_datetime_context, load_prompt
from app.models.schemas import ChatMessage, ChatRequest, ChatResponse, ChatTitleRequest, ChatTitleResponse
from app.services.llm.client import (
    chat_completion,
    chat_completion_message,
    stream_chat_completion_deltas,
)
from app.services.llm.igdb_tool import IGDB_TOOLS, execute_lookup_game_info
from app.services.llm.listening_tool import LISTENING_TOOLS, execute_stop_listening
from app.services.llm.memory_extraction import extract_and_apply_memory
from app.services.llm.process_tool import PROCESS_TOOLS, execute_fetch_active_process
from app.services.llm.screenshot_tool import SCREENSHOT_TOOLS, execute_take_screenshot, format_monitors_for_prompt
from app.services.llm.steam_tool import STEAM_TOOLS, execute_fetch_steam_library, execute_lookup_steam_game
from app.services.llm.system_info_tool import SYSTEM_INFO_TOOLS, execute_fetch_system_info
from app.services.llm.tools import MEMORY_TOOLS, execute_tool_call
from app.services.llm.volume_tool import VOLUME_TOOLS, execute_set_narration_volume
from app.services.llm.web_search_tool import WEB_SEARCH_TOOLS, execute_web_search
from app.services.screenshot.capture import capture_primary_monitor_b64
from app.services.system.processes import get_foreground_process_name

router = APIRouter(prefix="/api/chat", tags=["chat"])

ALL_TOOLS = (
    MEMORY_TOOLS
    + SCREENSHOT_TOOLS
    + VOLUME_TOOLS
    + LISTENING_TOOLS
    + PROCESS_TOOLS
    + WEB_SEARCH_TOOLS
    + IGDB_TOOLS
    + STEAM_TOOLS
    + SYSTEM_INFO_TOOLS
)
MAX_TOOL_ITERATIONS = 8


def _build_base_messages(include_screenshot: bool) -> list[dict]:
    system_content = load_prompt("system_companion")

    custom_instructions = load_custom_instructions().strip()
    if custom_instructions:
        system_content += "\n\n" + custom_instructions

    monitors = format_monitors_for_prompt()
    if monitors:
        system_content += "\n\n" + monitors

    # Variable content last — maximises cache hits on stable prefix above.
    system_content += "\n\n" + current_datetime_context()

    active_process = get_foreground_process_name()
    memories = memory.format_memories_for_prompt(active_process)
    if memories:
        system_content += "\n\n" + memories

    game_state_text = game_state.format_game_state_for_prompt()
    if game_state_text:
        system_content += "\n\n" + game_state_text

    messages = [{"role": "system", "content": system_content}]

    if include_screenshot:
        screenshot_b64 = capture_primary_monitor_b64()
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": load_prompt("screenshot_context")},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                ],
            }
        )

    return messages


def _limit_history(messages: list[dict]) -> list[dict]:
    limit = settings.context_window_messages
    return messages[-limit:] if limit > 0 else messages


# Background memory-extraction tasks (fire-and-forget); kept here so they aren't
# garbage-collected mid-flight, and discarded once done.
_background_tasks: set[asyncio.Task] = set()


def _schedule_memory_extraction(user_message: str, assistant_message: str) -> None:
    if not assistant_message:
        return
    task = asyncio.create_task(extract_and_apply_memory(user_message, assistant_message))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _tool_calls_to_dict(tool_calls: dict[int, dict]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in tool_calls.values()
        ],
    }


async def _execute_tool(name: str, arguments: dict) -> tuple[str, list[dict] | None, dict | None]:
    """Times and debug-logs every tool dispatch, then delegates to _execute_tool_impl. A single
    choke point so no call site can forget to record it - the debug panel otherwise only sees
    that the model *asked* to call a tool (via tool_calls on the parent chat entry), never what
    the tool actually returned."""
    start = time.monotonic()
    message, extra_messages, side_effect = await _execute_tool_impl(name, arguments)
    debug_log.record_tool_call(name, arguments, message, (time.monotonic() - start) * 1000)
    return message, extra_messages, side_effect


async def _execute_tool_impl(name: str, arguments: dict) -> tuple[str, list[dict] | None, dict | None]:
    """Returns (tool_message, extra_messages, side_effect). extra_messages are appended to the
    conversation (e.g. an image for the LLM to see); side_effect is an out-of-band event the
    frontend needs to react to immediately (e.g. a volume or listening-state change)."""
    if name == "take_screenshot":
        message, extra_messages = execute_take_screenshot(arguments)
        return message, extra_messages, None
    if name == "set_narration_volume":
        message = execute_set_narration_volume(arguments)
        return message, None, {"type": "volume", "value": settings.tts_volume}
    if name == "stop_listening":
        message = execute_stop_listening(arguments)
        return message, None, {"type": "stop_listening"}
    if name == "fetch_active_process":
        return execute_fetch_active_process(arguments), None, None
    if name == "web_search":
        return await execute_web_search(arguments), None, None
    if name == "lookup_game_info":
        return await execute_lookup_game_info(arguments), None, None
    if name == "lookup_steam_game":
        return await execute_lookup_steam_game(arguments), None, None
    if name == "fetch_steam_library":
        return await execute_fetch_steam_library(arguments), None, None
    if name == "fetch_system_info":
        return execute_fetch_system_info(arguments), None, None
    return execute_tool_call(name, arguments), None, None


async def _run_tool_calls(messages: list[dict], tool_calls) -> list[dict]:
    parsed = []
    for tool_call in tool_calls:
        try:
            arguments = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        parsed.append((tool_call.id, tool_call.function.name, arguments))

    results = await asyncio.gather(*[_execute_tool(name, args) for _, name, args in parsed])

    side_effects = []
    for (tool_call_id, _, _), (result, extra_messages, side_effect) in zip(parsed, results):
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})
        if extra_messages:
            messages.extend(extra_messages)
        if side_effect:
            side_effects.append(side_effect)
    return side_effects


async def _run_chat_with_tools(messages: list[dict], model: str | None = None, source: str = "chat") -> tuple[str, bool]:
    """Returns (reply, stop_listening). stop_listening is True if a stop_listening tool call
    happened this turn, so non-streaming callers can react to it too."""
    stop_listening = False
    for _ in range(MAX_TOOL_ITERATIONS):
        message = await chat_completion_message(messages, model=model, tools=ALL_TOOLS, source=source)
        if not message.tool_calls:
            return message.content or "", stop_listening

        messages.append(message.model_dump(exclude_none=True))
        for side_effect in await _run_tool_calls(messages, message.tool_calls):
            if side_effect["type"] == "stop_listening":
                stop_listening = True

    return "Sorry, I got stuck juggling tools just now - try asking again?", stop_listening


async def _stream_chat_with_tools(
    messages: list[dict], model: str | None = None, source: str = "chat_stream"
) -> AsyncIterator[dict]:
    """Yields tagged events: {"type": "delta", "text": ...} for reply text, plus out-of-band
    events like {"type": "volume", ...} or {"type": "stop_listening", ...} the moment a tool
    changes something the frontend needs to react to immediately, rather than only after the
    full reply finishes."""
    for _ in range(MAX_TOOL_ITERATIONS):
        tool_calls: dict[int, dict] = {}

        async for delta in stream_chat_completion_deltas(messages, model=model, tools=ALL_TOOLS, source=source):
            if delta.content:
                yield {"type": "delta", "text": delta.content}
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    entry = tool_calls.setdefault(tc.index, {"id": tc.id, "name": "", "arguments": ""})
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function and tc.function.name:
                        entry["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        entry["arguments"] += tc.function.arguments

        if not tool_calls:
            return

        messages.append(_tool_calls_to_dict(tool_calls))
        tc_list = list(tool_calls.values())
        parsed = []
        for tc in tc_list:
            try:
                arguments = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            parsed.append((tc["id"], tc["name"], arguments))

        results = await asyncio.gather(*[_execute_tool(name, args) for _, name, args in parsed])

        for (tool_call_id, _, _), (result, extra_messages, side_effect) in zip(parsed, results):
            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})
            if extra_messages:
                messages.extend(extra_messages)
            if side_effect:
                yield side_effect


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    messages = _build_base_messages(request.include_screenshot)
    messages.extend(_limit_history([m.model_dump() for m in request.messages]))

    try:
        reply, stop_listening = await _run_chat_with_tools(messages, source="chat")
    except APIError as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc

    last_user_message = request.messages[-1].content if request.messages else ""
    _schedule_memory_extraction(last_user_message, reply)

    return ChatResponse(
        reply=reply,
        narration_volume=settings.tts_volume,
        stop_listening=stop_listening,
    )


@router.post("/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    messages = _build_base_messages(request.include_screenshot)
    messages.extend(_limit_history([m.model_dump() for m in request.messages]))

    last_user_message = request.messages[-1].content if request.messages else ""

    async def event_generator():
        full_reply = ""
        try:
            async for event in _stream_chat_with_tools(messages, source="chat_stream"):
                if event["type"] == "delta":
                    full_reply += event["text"]
                    yield f"data: {json.dumps({'delta': event['text']})}\n\n"
                elif event["type"] == "volume":
                    yield f"data: {json.dumps({'volume': event['value']})}\n\n"
                elif event["type"] == "stop_listening":
                    yield f"data: {json.dumps({'stop_listening': True})}\n\n"
        except APIError as exc:
            yield f"data: {json.dumps({'error': f'LLM request failed: {exc}'})}\n\n"
            return
        _schedule_memory_extraction(last_user_message, full_reply)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/voice", response_model=ChatResponse)
async def chat_voice(
    audio: UploadFile,
    history: str = Form("[]"),
    include_screenshot: bool = Form(False),
) -> ChatResponse:
    """Send raw audio directly to an audio-capable OpenRouter model, skipping local STT."""
    messages = _build_base_messages(include_screenshot)
    messages.extend(_limit_history([ChatMessage.model_validate(m).model_dump() for m in json.loads(history)]))

    audio_b64 = base64.b64encode(await audio.read()).decode("ascii")
    messages.append(
        {
            "role": "user",
            "content": [{"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}}],
        }
    )

    model = settings.openrouter_voice_model or settings.openrouter_model
    try:
        reply, stop_listening = await _run_chat_with_tools(messages, model=model, source="chat_voice")
    except APIError as exc:
        raise HTTPException(status_code=502, detail=f"Voice LLM request failed: {exc}") from exc

    # No transcript of the spoken message is available (audio is sent straight to the LLM,
    # skipping local STT), so there's nothing meaningful to feed the memory extraction pass.
    return ChatResponse(
        reply=reply,
        narration_volume=settings.tts_volume,
        stop_listening=stop_listening,
    )


@router.post("/title", response_model=ChatTitleResponse)
async def chat_title(request: ChatTitleRequest) -> ChatTitleResponse:
    """Generate a short chat title from the first exchange (voice messages have no useful text to title from)."""
    messages = [
        {"role": "system", "content": load_prompt("chat_title")},
        {"role": "user", "content": f"User: {request.user_message}\nCompanion: {request.assistant_message}"},
    ]

    try:
        title = await chat_completion(messages, source="chat_title")
    except APIError as exc:
        raise HTTPException(status_code=502, detail=f"Title generation failed: {exc}") from exc

    return ChatTitleResponse(title=title.strip().strip('"'))
