import asyncio
import base64
import json
import re
import time
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openai import APIError
from pydantic import ValidationError

from app.core import debug_log, game_state, memory, observations, reminders as reminders_store
from app.core.config import settings
from app.core.instructions import load_custom_instructions
from app.core.prompts import current_datetime_context, load_prompt
from app.models.schemas import ChatMessage, ChatRequest, ChatResponse, ChatTitleRequest, ChatTitleResponse
from app.services import overlay_process
from app.services.llm.client import (
    chat_completion,
    chat_completion_message,
    stream_chat_completion_deltas,
)
from app.services.llm.app_volume_tool import APP_VOLUME_TOOLS, execute_set_application_volume
from app.services.llm.igdb_tool import IGDB_TOOLS, execute_lookup_game_info
from app.services.llm.listening_tool import LISTENING_TOOLS, execute_stop_listening
from app.services.llm.memory_extraction import extract_and_apply_memory
from app.services.llm.reminder_tool import REMINDER_TOOLS, execute_reminder_tool
from app.services.llm.screenshot_tool import SCREENSHOT_TOOLS, execute_take_screenshot, format_monitors_for_prompt
from app.services.llm.steam_tool import STEAM_TOOLS, execute_fetch_steam_library, execute_lookup_steam_game
from app.services.llm.system_info_tool import SYSTEM_INFO_TOOLS, execute_fetch_system_info
from app.services.llm.tools import MEMORY_TOOLS, execute_tool_call
from app.services.llm.transcription import transcribe_audio
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
    + WEB_SEARCH_TOOLS
    + IGDB_TOOLS
    + STEAM_TOOLS
    + SYSTEM_INFO_TOOLS
    + REMINDER_TOOLS
    + APP_VOLUME_TOOLS
)

REMINDER_TOOL_NAMES = {"add_reminder", "remove_reminder", "add_alarm", "cancel_alarm"}
MAX_TOOL_ITERATIONS = 8



def _format_reminders_for_prompt() -> str:
    """Active reminders/alarms with their ids - without this the model can't answer "what
    reminders do I have?" and can't remove/cancel one it didn't create earlier this session
    (remove_reminder/cancel_alarm take an id it would otherwise never have seen)."""
    entries = reminders_store.load_entries()
    if not entries:
        return ""
    lines = []
    for e in entries:
        if e["kind"] == "reminder":
            lines.append(f"- [{e['id']}] recurring reminder, every {e['interval_minutes']} min while {e['process']} is played: \"{e['message']}\"")
        else:
            lines.append(f"- [{e['id']}] one-time alarm at {e['fire_at']} while {e['process']} is played: \"{e['message']}\"")
    return "Active reminders/alarms the user has set (reference the id to remove/cancel one):\n" + "\n".join(lines)


def _parse_history_form(history: str) -> list[dict]:
    """Parses the voice endpoints' history form field, turning malformed input into a 400
    instead of an unhandled 500."""
    try:
        return _limit_history([ChatMessage.model_validate(m).model_dump() for m in json.loads(history)])
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid history payload: {exc}") from exc


# Raw-audio voice turns are stored in chat history as a "🎤 (voice message)" placeholder, which
# loses the user's side of every past voice exchange. Instead of paying for a dedicated STT model,
# the audio-capable main model is asked to prefix its reply with a <transcript> block; it is peeled
# off here, sent to the frontend (which rewrites the placeholder into the actual words), and fed to
# the memory extraction pass.
_TRANSCRIPT_OPEN = "<transcript>"
_TRANSCRIPT_CLOSE = "</transcript>"
_TRANSCRIPT_RE = re.compile(r"^\s*<transcript>(.*?)</transcript>\s*", re.DOTALL)
# If the model opens a transcript block but hasn't closed it after this many streamed chars,
# assume it's not going to and flush - keeps a non-compliant reply from being withheld forever.
_TRANSCRIPT_BUFFER_CAP = 600


def _voice_user_message(audio_b64: str) -> dict:
    """The raw-audio user turn, with the transcript-prefix instruction attached to it (not the
    system prompt, so text chats and transcription mode are unaffected)."""
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": load_prompt("voice_transcript")},
            {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}},
        ],
    }


def _extract_overlay_sentences(buffer: str) -> tuple[list[str], str]:
    """Linear scan pulling complete sentences off a streaming buffer so the overlay
    can show reply text sentence-by-sentence (keeping pace with the TTS narrator)
    instead of only getting the whole reply once it's done. Returns
    (complete_sentences, remainder). Deliberately NOT a regex — see the streaming
    freeze gotcha in CLAUDE.md."""
    sentences: list[str] = []
    start = 0
    i = 0
    n = len(buffer)
    while i < n:
        if buffer[i] in ".!?":
            j = i + 1
            while j < n and buffer[j] in ".!?":  # swallow "?!", "..."
                j += 1
            sentence = buffer[start:j].strip()
            if sentence:
                sentences.append(sentence)
            while j < n and buffer[j].isspace():
                j += 1
            start = j
            i = j
        else:
            i += 1
    return sentences, buffer[start:]


def _split_transcript(reply: str) -> tuple[str | None, str]:
    """Splits a leading <transcript>...</transcript> block off a complete reply. Returns
    (transcript-or-None, reply without the block)."""
    match = _TRANSCRIPT_RE.match(reply)
    if not match:
        return None, reply
    return match.group(1).strip() or None, reply[match.end():]


async def _peel_transcript(inner: AsyncIterator[dict]) -> AsyncIterator[dict]:
    """Passes the tool-loop event stream through unchanged, except a leading
    <transcript>...</transcript> block in the delta text is removed and re-emitted as a single
    {"type": "transcript", "text": ...} event. Deltas are buffered only while the text could
    still turn out to be that block, so a compliant reply loses no streaming and a
    non-compliant one is flushed as soon as the prefix stops matching."""
    buffer = ""
    buffering = True
    strip_lead = False  # eat whitespace between the close tag and the first reply text
    async for event in inner:
        if not buffering or event["type"] != "delta":
            if strip_lead and event["type"] == "delta":
                text = event["text"].lstrip()
                if not text:
                    continue
                strip_lead = False
                event = {"type": "delta", "text": text}
            yield event
            continue
        buffer += event["text"]
        stripped = buffer.lstrip()
        if not stripped:
            continue
        if stripped.startswith(_TRANSCRIPT_OPEN):
            close_idx = stripped.find(_TRANSCRIPT_CLOSE)
            if close_idx != -1:
                buffering = False
                transcript = stripped[len(_TRANSCRIPT_OPEN):close_idx].strip()
                if transcript:
                    yield {"type": "transcript", "text": transcript}
                remainder = stripped[close_idx + len(_TRANSCRIPT_CLOSE):].lstrip()
                if remainder:
                    yield {"type": "delta", "text": remainder}
                else:
                    strip_lead = True
            elif len(stripped) > _TRANSCRIPT_BUFFER_CAP:
                buffering = False
                yield {"type": "delta", "text": buffer}
        elif _TRANSCRIPT_OPEN.startswith(stripped[: len(_TRANSCRIPT_OPEN)]):
            continue  # still a prefix of the open tag - keep buffering
        else:
            buffering = False
            yield {"type": "delta", "text": buffer}
    if buffering and buffer:
        yield {"type": "delta", "text": buffer}


def _screenshot_message() -> dict:
    """A user-role message carrying a fresh screenshot. Appended adjacent to the newest message
    (not before the whole history) so the model reads it as current context, not as something
    that was on screen dozens of messages ago."""
    screenshot_b64 = capture_primary_monitor_b64()
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": load_prompt("screenshot_context")},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
        ],
    }


def _retrieval_query(history: list[dict]) -> str:
    """Text the RAG-lite memory selection ranks against: the last few text messages of the
    conversation plus the live game activity. Voice turns whose transcript never arrived carry
    only a placeholder, so this can come back empty - retrieval then falls back to recency."""
    parts = []
    for m in history[-6:]:
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content)
    gs = game_state.get_game_state()
    if gs:
        parts.extend(str(v) for v in gs["values"].values() if v)
    return "\n".join(parts)


def _build_base_messages(history: list[dict] | None = None) -> list[dict]:
    system_content = load_prompt("system_companion")

    custom_instructions = load_custom_instructions().strip()
    if custom_instructions:
        system_content += "\n\n" + custom_instructions

    monitors = format_monitors_for_prompt()
    if monitors:
        system_content += "\n\n" + monitors

    # Variable content last — maximises cache hits on stable prefix above.
    system_content += "\n\n" + current_datetime_context()

    # Injected instead of a fetch_active_process tool call — it's a few tokens, and having the
    # model fetch it doubled the LLM cost of every conversation that needed it.
    foreground = get_foreground_process_name()
    if foreground:
        system_content += (
            f"\n\n[Active application] The currently focused application is: {foreground} "
            "(if this is a browser or the companion app itself (python.exe), the player likely alt-tabbed away from their game or haven't started playing yet)."
        )

    # Use the tracked game's process + session for memory filtering. The foreground process
    # would be the companion window when the user alt-tabs to chat — game_state gives us the
    # game that's actually being tracked regardless of what's in focus right now.
    gs = game_state.get_game_state()
    tracked_process = gs["process"] if gs else None
    tracked_session = gs["session_id"] if gs else None
    memories = memory.format_memories_for_prompt(
        tracked_process,
        tracked_session,
        retrieval_query=_retrieval_query(history or []),
        game_memory_limit=settings.memory_rag_limit,
    )
    if memories:
        system_content += "\n\n" + memories

    reminders_text = _format_reminders_for_prompt()
    if reminders_text:
        system_content += "\n\n" + reminders_text

    game_state_text = game_state.format_game_state_for_prompt()
    divergence_warning = game_state.pop_pending_divergence(tracked_process) if tracked_process else None
    if game_state_text:
        system_content += "\n\n" + game_state_text
    if tracked_process:
        observations_text = observations.format_observations_for_prompt(tracked_process, tracked_session)
        if observations_text:
            system_content += "\n\n" + observations_text
    if divergence_warning:
        system_content += f"\n\n[Game state divergence detected] {divergence_warning} — mention this naturally in your next response and ask the player what happened (crash? loaded an older save? switched character?). Don't be alarmist, keep it conversational."

    return [{"role": "system", "content": system_content}]


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


def _tool_calls_to_dict(tool_calls: dict[int, dict], content: str | None = None) -> dict:
    return {
        "role": "assistant",
        "content": content,
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
    if name == "set_application_volume":
        return execute_set_application_volume(arguments), None, None
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
    if name in REMINDER_TOOL_NAMES:
        return execute_reminder_tool(name, arguments), None, None
    return execute_tool_call(name, arguments), None, None


def _safe_json_args(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


async def _execute_parsed_tool_calls(messages: list[dict], parsed: list[tuple[str, str, dict]]) -> list[dict]:
    """Executes (id, name, args) tool calls in parallel, appends their tool messages (and any
    extra messages, e.g. screenshots) to the conversation, and returns collected side effects.
    Shared by the streaming and non-streaming paths - keep them behaviorally identical."""
    results = await asyncio.gather(*[_execute_tool(name, args) for _, name, args in parsed])

    side_effects = []
    for (tool_call_id, name, _), (result, extra_messages, side_effect) in zip(parsed, results):
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})
        if extra_messages:
            messages.extend(extra_messages)
        if side_effect:
            side_effects.append(side_effect)
        # Lets the frontend play a per-tool sound effect the moment it runs, not just for the
        # handful of tools with a "real" side effect above.
        side_effects.append({"type": "tool_sfx", "name": name})
    return side_effects


async def _run_tool_calls(messages: list[dict], tool_calls) -> list[dict]:
    parsed = [(tc.id, tc.function.name, _safe_json_args(tc.function.arguments)) for tc in tool_calls]
    return await _execute_parsed_tool_calls(messages, parsed)


async def _run_chat_with_tools(messages: list[dict], model: str | None = None, source: str = "chat") -> tuple[str, bool]:
    """Returns (reply, stop_listening). stop_listening is True if a stop_listening tool call
    happened this turn, so non-streaming callers can react to it too."""
    stop_listening = False
    for _ in range(MAX_TOOL_ITERATIONS):
        message = await chat_completion_message(
            messages, model=model, tools=ALL_TOOLS, source=source, provider=settings.llm_provider
        )
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
        round_text = ""

        async for delta in stream_chat_completion_deltas(
            messages, model=model, tools=ALL_TOOLS, source=source, provider=settings.llm_provider
        ):
            if delta.content:
                round_text += delta.content
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

        if round_text:
            # Flush the boundary the moment this round ends, rather than waiting for the next
            # round's first delta - a slow tool call (e.g. a Steam API fetch) could otherwise
            # leave this round's trailing sentence stuck in the frontend's sentence buffer,
            # un-narrated, for as long as the tool takes (or forever, if the next round is
            # tool-only and never emits text at all).
            yield {"type": "delta", "text": "\n\n"}
        # Keep the text the model emitted alongside its tool calls - dropping it means the next
        # iteration can't see what it already said and restarts the reply from scratch, so the
        # user gets the same greeting stacked 3-4 times in one bubble.
        messages.append(_tool_calls_to_dict(tool_calls, round_text or None))
        parsed = [(tc["id"], tc["name"], _safe_json_args(tc["arguments"])) for tc in tool_calls.values()]
        for side_effect in await _execute_parsed_tool_calls(messages, parsed):
            yield side_effect

    # Mirror the non-streaming fallback - without this, exhausting the tool budget ends the
    # stream silently and the user is left staring at an empty bubble.
    yield {"type": "delta", "text": "Sorry, I got stuck juggling tools just now - try asking again?"}


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    history = _limit_history([m.model_dump() for m in request.messages])
    messages = _build_base_messages(history)
    messages.extend(history)
    if request.include_screenshot:
        # Right before the newest user message: screenshot as context, then the question.
        messages.insert(max(1, len(messages) - 1), _screenshot_message())
    last_user_message = request.messages[-1].content if request.messages else ""

    try:
        reply, stop_listening = await _run_chat_with_tools(messages, source="chat")
    except APIError as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc

    # Typed turns carry no transcript instruction, but a model that just did voice turns in the
    # same conversation sometimes emits the block out of habit - strip it, never surface it.
    _, reply = _split_transcript(reply)

    _schedule_memory_extraction(last_user_message, reply)

    return ChatResponse(
        reply=reply,
        narration_volume=settings.tts_volume,
        stop_listening=stop_listening,
    )


@router.post("/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    history = _limit_history([m.model_dump() for m in request.messages])
    messages = _build_base_messages(history)
    messages.extend(history)
    if request.include_screenshot:
        messages.insert(max(1, len(messages) - 1), _screenshot_message())
    last_user_message = request.messages[-1].content if request.messages else ""

    async def event_generator():
        full_reply = ""
        overlay_buf = ""
        try:
            # Typed turns carry no transcript instruction, but a model that just did voice turns
            # in the same conversation sometimes emits the block out of habit - peel and drop it.
            async for event in _peel_transcript(_stream_chat_with_tools(messages, source="chat_stream")):
                if event["type"] == "transcript":
                    continue
                if event["type"] == "delta":
                    full_reply += event["text"]
                    # Push completed sentences to the overlay as they stream (even mid
                    # tool-loop, before the full reply lands) so it keeps pace with TTS.
                    overlay_buf += event["text"]
                    sentences, overlay_buf = _extract_overlay_sentences(overlay_buf)
                    for sentence in sentences:
                        overlay_process.push_toast(sentence, "reply")
                    yield f"data: {json.dumps({'delta': event['text']})}\n\n"
                elif event["type"] == "volume":
                    yield f"data: {json.dumps({'volume': event['value']})}\n\n"
                elif event["type"] == "stop_listening":
                    yield f"data: {json.dumps({'stop_listening': True})}\n\n"
                elif event["type"] == "tool_sfx":
                    yield f"data: {json.dumps({'tool_sfx': event['name']})}\n\n"
        except APIError as exc:
            yield f"data: {json.dumps({'error': f'LLM request failed: {exc}'})}\n\n"
            return
        if overlay_buf.strip():  # flush any trailing partial sentence
            overlay_process.push_toast(overlay_buf, "reply")
        _schedule_memory_extraction(last_user_message, full_reply)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/voice", response_model=ChatResponse)
async def chat_voice(
    audio: UploadFile,
    history: str = Form("[]"),
    include_screenshot: bool = Form(False),
) -> ChatResponse:
    """Sends voice audio to the LLM. In transcription mode, a dedicated audio-input model
    transcribes it first and the main model only ever sees text; otherwise the raw audio goes
    straight to the (audio-capable) main model, skipping local STT."""
    history_messages = _parse_history_form(history)
    messages = _build_base_messages(history_messages)
    messages.extend(history_messages)
    if include_screenshot:
        # Right before the voice message it accompanies: screenshot as context, then the question.
        messages.append(_screenshot_message())

    audio_b64 = base64.b64encode(await audio.read()).decode("ascii")
    transcript: str | None = None

    if settings.transcription_enabled:
        try:
            transcript = await transcribe_audio(audio_b64, "wav")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Transcription request failed: {exc}") from exc
        messages.append({"role": "user", "content": transcript})
    else:
        messages.append(_voice_user_message(audio_b64))

    model = None if settings.transcription_enabled else settings.openrouter_model
    try:
        reply, stop_listening = await _run_chat_with_tools(messages, model=model, source="chat_voice")
    except APIError as exc:
        raise HTTPException(status_code=502, detail=f"Voice LLM request failed: {exc}") from exc

    # Raw-audio turns get their transcript from the model's own <transcript> reply prefix.
    if transcript is None:
        transcript, reply = _split_transcript(reply)

    if transcript:
        _schedule_memory_extraction(transcript, reply)

    return ChatResponse(
        reply=reply,
        narration_volume=settings.tts_volume,
        stop_listening=stop_listening,
        transcript=transcript,
    )


@router.post("/voice/stream")
async def chat_voice_stream(
    audio: UploadFile,
    history: str = Form("[]"),
    include_screenshot: bool = Form(False),
) -> StreamingResponse:
    """Streaming variant of chat_voice — same audio-to-LLM flow (including transcription mode)
    but emits SSE deltas so the reply types in live rather than appearing all at once."""
    history_messages = _parse_history_form(history)
    messages = _build_base_messages(history_messages)
    messages.extend(history_messages)
    if include_screenshot:
        messages.append(_screenshot_message())

    audio_b64 = base64.b64encode(await audio.read()).decode("ascii")
    transcript: str | None = None

    if settings.transcription_enabled:
        try:
            transcript = await transcribe_audio(audio_b64, "wav")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Transcription request failed: {exc}") from exc
        messages.append({"role": "user", "content": transcript})
    else:
        messages.append(_voice_user_message(audio_b64))

    model = None if settings.transcription_enabled else settings.openrouter_model

    async def event_generator():
        nonlocal transcript
        full_reply = ""
        overlay_buf = ""
        stop_listening = False
        try:
            async for event in _peel_transcript(_stream_chat_with_tools(messages, model=model, source="chat_voice_stream")):
                if event["type"] == "delta":
                    full_reply += event["text"]
                    overlay_buf += event["text"]
                    sentences, overlay_buf = _extract_overlay_sentences(overlay_buf)
                    for sentence in sentences:
                        overlay_process.push_toast(sentence, "reply")
                    yield f"data: {json.dumps({'delta': event['text']})}\n\n"
                elif event["type"] == "transcript":
                    # Raw-audio turns: peeled from the model's reply prefix. Delivered only in
                    # the final done payload - no mid-stream event - so the UI updates the
                    # 🎤 placeholder once, after the reply is finished.
                    transcript = event["text"]
                elif event["type"] == "volume":
                    yield f"data: {json.dumps({'volume': event['value']})}\n\n"
                elif event["type"] == "stop_listening":
                    stop_listening = True
                    yield f"data: {json.dumps({'stop_listening': True})}\n\n"
                elif event["type"] == "tool_sfx":
                    yield f"data: {json.dumps({'tool_sfx': event['name']})}\n\n"
        except APIError as exc:
            yield f"data: {json.dumps({'error': f'Voice LLM request failed: {exc}'})}\n\n"
            return

        # Transcription mode transcribes up front; raw-audio turns get theirs from the model's
        # <transcript> reply prefix - either way the extraction pass now has real user text.
        if overlay_buf.strip():
            overlay_process.push_toast(overlay_buf, "reply")
        if transcript:
            _schedule_memory_extraction(transcript, full_reply)

        yield f"data: {json.dumps({'done': True, 'narration_volume': settings.tts_volume, 'stop_listening': stop_listening, 'transcript': transcript})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/title", response_model=ChatTitleResponse)
async def chat_title(request: ChatTitleRequest) -> ChatTitleResponse:
    """Generate a short chat title from the first exchange (voice messages have no useful text to title from)."""
    messages = [
        {"role": "system", "content": load_prompt("chat_title")},
        {"role": "user", "content": f"User: {request.user_message}\nCompanion: {request.assistant_message}"},
    ]

    try:
        title = await chat_completion(messages, source="chat_title", provider=settings.llm_provider)
    except APIError as exc:
        raise HTTPException(status_code=502, detail=f"Title generation failed: {exc}") from exc

    return ChatTitleResponse(title=title.strip().strip('"'))
