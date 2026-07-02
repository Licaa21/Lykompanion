# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Standing workflow (user-mandated)

1. Read this file first before any task, regardless of the task.
2. Any mistake or user correction must be captured here immediately so it can't recur.
3. Keep this file current toward its two goals: no re-exploration after context loss, minimal token consumption.
4. After finishing a task, update `TODO.md` — remove completed items, add newly found ones.
5. After each task, commit and push to `main` (pre-authorized — don't ask).
6. Never start servers, curl endpoints, or write one-off verification scripts — the user tests everything. Running `pytest tests` is allowed.

## What this is

Lykompanion — a local, voice-first gaming companion. FastAPI backend + vanilla HTML/CSS/JS frontend (no build step, no framework, no bundler). All LLM calls go through OpenRouter (optionally Google AI Studio or any OpenAI-compatible endpoint). Windows-only for several features (OCR, Windows Graphics Capture, per-app volume). The [README.md](README.md) is thorough and kept current — consult it for feature behavior, config variables, the API endpoint table, and the agent tool table before reading code.

## Commands

```bash
# Dev server (auto-reload; use this, not RUN.cmd, when iterating)
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 6692 --reload

# Tests (pytest, fast, no server needed)
.venv\Scripts\python.exe -m pytest tests

# Single test
.venv\Scripts\python.exe -m pytest tests/test_memory.py -k test_name
```

- No linter/formatter/type-checker is configured. No frontend build step — edit `web/` files directly.
- `RUN.cmd` / `run_app.py` = native desktop app (pywebview/EdgeWebView2 wrapping the same server, plus a per-launch API token `LYKO_API_TOKEN` that the middleware in `app/main.py` enforces on `/api/*`). Dev uvicorn runs with no auth.

## Search hygiene (token saver)

`data/` (runtime state + a full EdgeWebView2 browser profile, ~12k files) and `.venv/` dominate unscoped Glob/Grep results. Always scope searches to `app/`, `web/`, or `tests/`.

Big files — read targeted sections, not whole files:
- `web/js/app.js` (~4,100 lines) — the ENTIRE frontend: chat, streaming SSE handling, live mic/VAD, wake word, narration queue, settings UI, game-state panel, toasts. Grep for function names/DOM ids first.
- `web/css/style.css` (~1,900), `web/index.html` (~800), `app/api/chat.py` (~480).

## Architecture

Request path: `web/js/app.js` → `/api/chat/stream` (SSE, primary) or `/api/chat/voice[/stream]` (raw audio sent directly to an audio-capable model — no local STT unless `transcription_enabled`) → agentic tool loop → streamed reply narrated sentence-by-sentence via TTS.

### Agentic tool loop — `app/api/chat.py`
The heart of the app. `_run_chat_with_tools` (non-streaming) and `_stream_chat_with_tools` (SSE) re-call the model while it requests tools, up to `MAX_TOOL_ITERATIONS = 8`. All tool specs are concatenated into `ALL_TOOLS` at the top of the file and passed on every call — the model self-selects.

**To add a tool**: create `app/services/llm/<name>_tool.py` exporting a `*_TOOLS` spec list (OpenAI function format) and an `execute_*` function; add both to `ALL_TOOLS` and the dispatch in `_execute_tool_impl` in `app/api/chat.py`; document it in the README tool table. Memory tools are the exception — they live together in `app/services/llm/tools.py`.

Tools with frontend-visible side effects (volume change, stop_listening) are emitted as out-of-band SSE events (`volume`, `stop_listening`) interleaved with `delta` text events; non-streaming responses carry them as extra JSON fields.

### Layering
- `app/api/*` — route handlers, one module per concern. Registered in `app/main.py` (which also holds the token middleware, the SSRF-hardened `/api/proxy/image`, and the `lifespan` that starts two background pollers: game-state OCR and reminders).
- `app/core/*` — settings + persistence stores. Each store is a small module owning one JSON file in `data/` (memory.json, chats.json, usage.json, game_state_sessions.json, game_state_trackers.json, reminders.json, ...). Flat-file JSON everywhere; no database.
- `app/services/llm/*` — OpenRouter/OpenAI-compatible client (`client.py`, also handles provider selection + usage/debug recording), one module per agent tool, and the two background LLM passes: `memory_extraction.py` (fire-and-forget after every exchange) and `game_state_extraction.py` (the OCR poller).
- `app/services/tts|screenshot|ocr|system` — provider backends. Two screenshot paths exist deliberately: `capture.py` (mss/GDI, on-demand vision) vs `wgc_capture.py` (Windows Graphics Capture, used by the high-frequency OCR poller because GDI causes in-game stutter).
- `app/prompts/*.md` — all system/task prompts as Markdown, loaded+cached by `app/core/prompts.py`. Edit prompts there, never inline in Python.

### Config flow (non-obvious)
`app/core/config.py` defines `Settings` (pydantic-settings, reads `.env`) and a module-level `settings` singleton. The Settings UI persists changes via `persist_env_values()` writing back to `.env` AND mutates the live singleton (see `app/api/config.py`) — no restart needed. When adding a setting: add the field to `Settings`, wire it in `app/api/config.py` GET/PUT, and add the UI control in `web/index.html` + `app.js`. Per-feature model/provider settings default to `""` meaning "inherit the main `openrouter_model` / `llm_provider`".

### Memory model (three scopes)
Flat JSON list in `data/memory.json`, injected into every system prompt. Scope is encoded by two nullable fields: `process=None` → user scope (always shown); `process` set, `session_id=None` → game scope; both set → session scope (current playthrough only). Two independent writers: explicit agent tools, and the background extraction pass after every exchange (exists because models can't be trusted to call memory tools proactively). `saved_at` is never updated on edit — the crash-rollback tool depends on it.

### Frontend conventions
Every `fetch()` in `app.js` must send the `x-lyko-token` header (or `?token=` for `<img>`/`<audio>` src URLs) or it breaks in the desktop app while appearing fine under dev uvicorn.

## Gotchas

- `.env` at repo root is the user's real config with live API keys — never overwrite it wholesale; settings changes go through `persist_env_values()`.
- `winrt-*`, `windows-capture`, `pycaw` imports are Windows-only; modules using them import lazily/guarded so the server still boots elsewhere.
- Background LLM passes (memory extraction, game-state extraction) are fire-and-forget asyncio tasks — they must never add latency to or raise into the visible reply path. The game-state pass is vision-grounded: it receives each poll window's first/last frames as screenshots, and self-maintains a per-game UI-decoding notes document via its `training_data_update` output (there is no separate trainer model/pass).
- `TODO.md` is the maintained backlog of open items; check it before proposing work.
