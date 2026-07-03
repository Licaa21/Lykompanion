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

The frontend is `web/js/app/*.js` — the former single `app.js` (~4,400 lines) split into 14 ordered **classic** `<script>` files (NOT ES modules), one per concern, loaded in dependency order by `web/index.html`. They share ONE global lexical scope (top-level `let`/`const`/`function` are visible across files, and mutable state is reassigned across file boundaries) exactly as before — the split is a pure verbatim, line-range carve-up (byte-identical when re-concatenated), not a refactor. Order matters: `core.js` first (shared DOM consts + state + helpers), `init.js` last (calls `init()`). When adding top-level load-time code, only reference bindings declared in the same or an earlier-loaded file. Files: core, sfx, chat-store, narration, chat-stream (the freeze-sensitive streaming core), voice, wake-word, settings, memory-journal, reminders, game-state, usage-backup-debug, quick-setup, init. Grep across `web/js/app/` for function names/DOM ids first.

Big files — read targeted sections, not whole files:
- `web/js/app/settings.js` (~720) and `web/js/app/game-state.js` (~740) are the largest frontend files.
- `web/css/style.css` (~1,900), `web/index.html` (~800), `app/api/chat.py` (~480).

## Architecture

Request path: `web/js/app/chat-stream.js` → `/api/chat/stream` (SSE, primary) or `/api/chat/voice[/stream]` (raw audio sent directly to an audio-capable model — no local STT unless `transcription_enabled`) → agentic tool loop → streamed reply narrated sentence-by-sentence via TTS.

### Agentic tool loop — `app/api/chat.py`
The heart of the app. `_run_chat_with_tools` (non-streaming) and `_stream_chat_with_tools` (SSE) re-call the model while it requests tools, up to `MAX_TOOL_ITERATIONS = 8`. All tool specs are concatenated into `ALL_TOOLS` at the top of the file and passed on every call — the model self-selects.

**To add a tool**: create `app/services/llm/<name>_tool.py` exporting a `*_TOOLS` spec list (OpenAI function format) and an `execute_*` function; add both to `ALL_TOOLS` and the dispatch in `_execute_tool_impl` in `app/api/chat.py`; document it in the README tool table. Memory tools are the exception — they live together in `app/services/llm/tools.py`.

Tools with frontend-visible side effects (volume change, stop_listening) are emitted as out-of-band SSE events (`volume`, `stop_listening`) interleaved with `delta` text events; non-streaming responses carry them as extra JSON fields.

### Layering
- `app/api/*` — route handlers, one module per concern. Registered in `app/main.py` (which also holds the token middleware, the SSRF-hardened `/api/proxy/image`, and the `lifespan` that starts two background pollers: game-state OCR and reminders).
- `app/core/*` — settings + persistence stores. Each store is a small module owning one JSON file in `data/` (memory.json, chats.json, usage.json, game_state_sessions.json, game_state_trackers.json, reminders.json, ...). Flat-file JSON everywhere; no database.
- `app/services/llm/*` — OpenRouter/OpenAI-compatible client (`client.py`, also handles provider selection + usage/debug recording), one module per agent tool, and the two background LLM passes: `memory_extraction.py` (fire-and-forget after every exchange) and `game_state_extraction.py` (the OCR poller).
- `app/services/tts|screenshot|ocr|system` — provider backends. Two screenshot paths exist deliberately: `capture.py` (mss/GDI, on-demand vision) vs `wgc_capture.py` (Windows Graphics Capture, used by the high-frequency OCR poller because GDI causes in-game stutter).
- **Native overlay** (`overlay/` at repo root, NOT Python): a standalone C++ exe (`overlay/overlay.cpp`, single TU, `build.cmd` → `Lykompanion-overlay.exe`) drawing a game-state panel + reply/reminder toasts over the game via layered windows + Direct2D (no GDI paint, no injection). It hosts its OWN named-pipe API (`\\.\pipe\lykompanion-overlay`, newline-delimited JSON: `toast`/`image`/`memory`/`handsfree`/`game_state`/`edit_mode`/`quit`; `image` downloads a web URL via WIC and renders it as a toast; `memory` shows a brain +/- save/remove toast; `handsfree` toggles a persistent bottom-center live-mic indicator with a pulsing gradient); all rendering, input, Ctrl+Shift+O edit mode, and layout/appearance persistence (`%LOCALAPPDATA%\Lykompanion\overlay_layout.json`) live in the exe. Python (`app/services/overlay_process.py`) is ONLY a thin client: spawns it on `game_state.start_tracking` with `--parent <pid>` (overlay self-exits if we die), quits it on stop/app-exit, and pushes toasts (reply sentences streamed as they complete, reminders), web images extracted from reply markdown (`_extract_overlay_images`, proxy-URLs unwrapped to the real source), + the game-state panel. `OVERLAY_ENABLED` gates it. Full plan/history in `TODO.md`; render approach + pipe protocol in `overlay/README.md`. This is the working native replacement for the reverted WebView2 overlay (gotcha below).
- `app/prompts/*.md` — all system/task prompts as Markdown, loaded+cached by `app/core/prompts.py`. Edit prompts there, never inline in Python.

### Config flow (non-obvious)
`app/core/config.py` defines `Settings` (pydantic-settings, reads `.env`) and a module-level `settings` singleton. The Settings UI persists changes via `persist_env_values()` writing back to `.env` AND mutates the live singleton (see `app/api/config.py`) — no restart needed. When adding a setting: add the field to `Settings`, wire it in `app/api/config.py` GET/PUT, and add the UI control in `web/index.html` + the relevant `web/js/app/*.js` file (settings for config lives in `web/js/app/settings.js`). Per-feature model/provider settings default to `""` meaning "inherit the main `openrouter_model` / `llm_provider`".

### Memory model (three scopes + observations journal)
Flat JSON list in `data/memory.json` with an **explicit `scope` field** (`user`/`game`/`session`; pre-scope entries migrated on read), injected into every system prompt with scope suffixes. ALL writers must go through `memory.remember(content, scope, process, session_id)` — it owns scope resolution and the degradation rule (unresolvable game/session scope degrades to **user**, never to a different game tier). Writers: chat tools + the per-exchange extraction pass (exists because models can't be trusted to call memory tools proactively). The OCR game-state pass does NOT write memory — it appends to the observations journal (`app/core/observations.py`, per-session staging, capped), and the extraction pass promotes corroborated observations. `saved_at` is never updated on edit — the crash-rollback tool depends on it. UI: user scope = Personal Data modal; game/session scope + observations = Gaming Journal modal (which also hosts the Game Awareness settings, moved out of Settings — its awareness inputs save via the shared `saveSettings()` in `web/js/app/settings.js`).

A `user`-scope fact stated before a game was ever tracked (e.g. playtime mentioned in passing) can't be placed more specifically at save time — `remember()`'s degradation rule has no session/process to anchor to yet. `app/services/llm/memory_retagging.py` catches this: hooked into the same `game_state.start_tracking()` call site as `schedule_bootstrap` (`app/services/llm/game_state_extraction.py`), it runs once per process per app run the moment that game starts being tracked, reviewing standing `user` facts and moving the ones specifically about it into `game`/`session` scope via `memory.update_memory` (preserves `saved_at`, unlike remove+re-add).

### Frontend conventions
Every `fetch()` in the frontend (`web/js/app/*.js`) must send the `x-lyko-token` header (or `?token=` for `<img>`/`<audio>` src URLs) or it breaks in the desktop app while appearing fine under dev uvicorn.

### Sound effects (cosmetic, client-side only)
`web/js/app/sfx.js` synthesizes short SFX via the Web Audio API (`playSfxNote`/`playSfxSweep` + `playSentSfx`/`playToolSfx`, near the pre-existing mic `beep()`/`playWakeChime()`) — no audio asset files. Gated by `sfx_enabled` (mirrors `narrate_enabled`: `Settings.sfx_enabled` → `app/api/config.py` GET/PUT → `#cfg-sfx-enabled` checkbox), unlike the mic beeps which are unconditional functional feedback. `_execute_parsed_tool_calls` (`app/api/chat.py`) appends a `{"type": "tool_sfx", "name": <tool name>}` side effect for **every** tool call (in addition to any real side effect like volume/stop_listening), forwarded as SSE `tool_sfx` in both `/stream` and `/voice/stream`; the frontend's `TOOL_SFX` map picks a specific sound (web search, memory save/delete) or falls back to a generic click. The non-streaming path ignores the unknown side-effect type harmlessly.

### Backup / restore
`app/api/backup.py`: `GET /api/backup/export` zips `data/` (excluding `webview_profile/`, `debug_log.json`, `crash_log.txt`) plus `.env` into a downloadable archive; `POST /api/backup/import` extracts one back over the project root (zip-slip guarded, only accepts `.env`/`data/*` entries). Settings from an imported `.env` only take effect after a restart — import does not touch the live `Settings` singleton.

## Gotchas

- **Overlay build (`overlay/build.cmd`)**: two traps hit before. (1) MSVC rejects a **function-local `struct` with a default member initializer that references a function-local `enum`** (`error C2065: 'PkText' undeclared` in the generated ctor) — declare such enum+struct pairs at namespace scope, not inside the function. (2) A stray `"` anywhere in the machine's `PATH` makes `vcvars64.bat` abort with `"... was unexpected at this time."` — `build.cmd` now strips quotes from PATH defensively, so don't remove that line. `RUN.cmd` rebuilds the overlay every launch (non-fatal on failure). Libs are pulled via `#pragma comment(lib)` in `overlay.cpp`, not the link line.
- `.env` at repo root is the user's real config with live API keys — never overwrite it wholesale; settings changes go through `persist_env_values()`.
- `winrt-*`, `windows-capture`, `pycaw` imports are Windows-only; modules using them import lazily/guarded so the server still boots elsewhere.
- Background LLM passes (memory extraction, game-state extraction) are fire-and-forget asyncio tasks — they must never add latency to or raise into the visible reply path. The game-state pass is vision-grounded: it receives each poll window's first/last frames as screenshots, and self-maintains a per-game UI-decoding notes document via its `training_data_update` output (there is no separate trainer model/pass).
- `TODO.md` is the maintained backlog of open items; check it before proposing work.
- Voice transcript extraction (raw-audio mode): the main model prefixes replies with a `<transcript>` block, peeled by `_peel_transcript` in `app/api/chat.py`; delivered only in the final `done` SSE payload. Text chat paths must peel/discard it too — models emit it out of habit after voice turns.
- "Reply freezes mid-stream" had TWO causes, both fixed — keep both guards when touching streaming code: (1) any `renderChatLog()` during streaming (chat switch) wipes `chatLog` and detaches the live bubbles; the stream loops re-attach via `assistantEl.isConnected` checks + a tail re-render. (2) The REAL "whole app hangs" bug: `extractCompleteSentences` used a catastrophically backtracking regex (`(?:[^.!?\n]+|[.!?](?!\s))*(...)`) — one `exec()` on a long incomplete sentence took minutes and froze the JS main thread (UI dead, all polling stops, window only closable via X). It is now a linear scan; NEVER split streaming text with a backtracking regex. Diagnosis tell: uvicorn console request logs going silent while the process lives = frontend JS wedged, not the server.
- "UI element missing but the API is polling fine" — check localStorage-persisted UI state first (`lyko-game-state-panel-pos`, `GAME_STATE_CLOSED_KEY`): the floating panel's position/closed state persist across reloads and can leave an element unhidden yet off-screen. `web/index.html` + `web/js/app/*.js` can be exercised headlessly with jsdom (see the scratchpad repro pattern: stub `fetch` per route, eval the app scripts in load order, assert on DOM).
- Tool-round boundary in `_stream_chat_with_tools` (`app/api/chat.py`): the `"\n\n"` separator delta must be yielded the moment a text-bearing round ENDS (right before its tool calls run), not lazily deferred until the next round's first text delta shows up. Deferring it left a round's trailing sentence ("Let me check that...") stuck un-narrated in the frontend's `sentenceBuffer` remainder for as long as the tool call took — or forever, if the next round turned out to be tool-only with no text. Also, models tend to restate the pre-tool-call text as a second paragraph once the tool result comes back — the system prompt now has an explicit line telling it to continue instead of re-greeting.
- **In-game overlay: FULLY REVERTED after 7 failed rounds (2026-07-03) — read this before ever re-attempting it.** A pywebview/WebView2-based overlay (extra frameless windows drawn over the game) is a dead end; every mechanism was tried and disproven on-machine: (1) pywebview 6.2.1's `transparent=True` never makes the WinForms form transparent — only the WebView2 control's `DefaultBackgroundColor` (confirmed in its installed source) — so the page's transparent pixels show an opaque gray form, and no exstyle/DWM permutation on top can fix it; (2) bare `WS_EX_LAYERED` stops a window rendering at all until `SetLayeredWindowAttributes`/`UpdateLayeredWindow` commits it; (3) color-key done correctly (`LWA_COLORKEY` via ctypes) → black window after a split-second flash: Chromium's GPU compositor cannot render into a layered window at all — rules out ALL alpha/color-key approaches; (4) `SetWindowRgn` region clipping returns success but is visually inert — WebView2's DirectComposition content ignores GDI window regions; (5) setting WinForms `Form.TransparencyKey` OR `Form.Region` on a pywebview window deadlocks the entire app at boot (shared UI thread; only raw win32 via ctypes is safe); (6) fit-to-content window resizing (the last WebView2-shaped idea) was implemented but reverted with everything else, untested. If the overlay is ever rebuilt, start directly at: native layered windows drawn with GDI+/Direct2D via ctypes + `UpdateLayeredWindow` per-pixel alpha (software-rendered — exempt from the GPU limitation; how real game overlays work), or resurrect the fit-to-content approach from git history (`4957a50`, reverted). Exclusive-fullscreen games bypass any of this regardless — only borderless/windowed can ever show an overlay.
