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

### In-game overlay (desktop app only, `overlay_enabled`, off by default)
Second pywebview window (created in `run_app.py` at launch only when the setting is on — enabling needs a restart, disabling hides live) — frameless/transparent/on-top, made click-through + hidden from Alt-Tab via win32 exstyles (`_apply_overlay_base_styles` for the one-time WS_EX_LAYERED/TOOLWINDOW/NOACTIVATE setup on `shown`, `_make_overlay_click_through_setter` for the toggleable WS_EX_TRANSPARENT bit — applied to the form AND its WebView2 child hwnds, re-swept 2s after `shown`). It loads `web/overlay.html` + `web/js/overlay.js` (standalone, NOT part of the `web/js/app/*.js` chain; token via `?token=` everywhere since EventSource can't set headers). Data flow: `app/core/events.py` is an in-process pub/sub bus → `GET /api/overlay/events` SSE. Publishers: all four chat endpoints publish the final peeled reply (`{"type":"reply"}`), `reminders.add_pending` publishes `{"type":"reminder"}` (display-only — the main window still owns the pending-queue ack); game state is polled by the overlay directly. Layout editor: per-game element positions in `data/overlay_layouts.json` (`app/core/overlay_layouts.py`, viewport fractions, `"default"` key = no game, GET falls back to it). Edit-mode state is centralized in `app/core/events.py` (`set_overlay_edit_mode`/`toggle_overlay_edit_mode`, single `_overlay_editing` bool) so both the Settings "Edit layout" button (`POST /api/overlay/edit-mode`, always enables) and the global hotkey drive the same click-through lift + `edit_mode` SSE broadcast; the overlay's own Save/Cancel (or the hotkey) turn it back off, and `exitEditMode()` in `overlay.js` always re-fetches the layout on exit so an unsaved drag never sticks. Global hotkey **Ctrl+Shift+O** toggles it from anywhere (works while a game has input focus, since the overlay window itself can't receive key events): `run_app.py` runs `RegisterHotKey`/`GetMessageW` on its own daemon thread (ctypes, no pywin32 dependency) — that thread is neither the uvicorn asyncio loop nor the WinForms UI thread, so it must call `events.toggle_overlay_edit_mode(threadsafe=True)`, which hops onto the loop via `call_soon_threadsafe` (the loop reference is registered from `app/main.py`'s `lifespan` at startup — `asyncio.Queue.put_nowait` is not safe to call from a different thread than the loop's).

Win32 gotchas hit during development — keep all in mind if the overlay looks wrong again. **Getting the overlay window genuinely see-through has been the hard, unresolved part across three attempts** (dark tint → opaque white → dark tint again with `WS_EX_LAYERED` alone) — see history below; if the current combination (`WS_EX_LAYERED` bare + `DwmExtendFrameIntoClientArea` full glass, both in `_apply_overlay_base_styles`) still isn't transparent, the next thing to try is NOT another exstyle permutation but a structurally different approach — see "if win32 flag tweaking keeps failing" at the end.
- **`WS_EX_LAYERED` must never be paired with `SetLayeredWindowAttributes`/`UpdateLayeredWindow`** (the classic "flat alpha bitmap" APIs) — doing that switches the window into legacy flat-blend mode and made the whole screen render as an opaque dark tint. Set bare (attempt 2), on Windows 8+ DWM instead pulls per-pixel alpha directly from the window's own GPU swap chain / DirectComposition surface, which is how WebView2 renders — but bare `WS_EX_LAYERED` alone was NOT sufficient either (still showed a dark tint, attempt 3), so attempt 3 combines it with `DwmExtendFrameIntoClientArea(hwnd, {-1,-1,-1,-1})` ("sheet of glass" over the whole client area — the older Aero-glass DWM mechanism, Vista+; pywebview itself calls this same API but only with a 1px margin, for the window-shadow effect, never for full transparency). Both applied together in `_apply_overlay_base_styles`, once at window `shown` (before first paint), never toggled.
- **Overlay window smaller than the real screen** (visible gap at the edge) on any display with DPI scaling on: `GetSystemMetrics(0/1)` returns OS-virtualized (scaled-down) values until the process is marked DPI-aware — pywebview only calls `SetProcessDPIAware()` itself inside `webview.start()`, for the "master" window, which runs *after* `run_app.py` already needs real physical screen dimensions to size the overlay. Fixed by calling `SetProcessDPIAware()` at module import time in `run_app.py`, before any window sizing happens. (This one is a much simpler/more certain fix than the transparency issue — no reason to doubt it without evidence otherwise.)
- **If win32 flag tweaking keeps failing**: stop iterating on exstyle/DWM-attribute permutations on the existing WebView2 overlay window — pywebview's WinForms+EdgeChrome transparency support (`transparent=True`) may simply not be reliable for a full-screen top-level overlay in this pywebview version (6.2.1), as opposed to the small embedded-widget case it's presumably tested against. Two structurally different fallbacks worth trying instead, in order of effort: (1) size the overlay window tightly around each element's actual content instead of covering the full screen — small transparent WebView2 windows are a much more common, better-tested use case than a full-screen one, might just work where the full-screen case doesn't; (2) drop WebView2 for the overlay's rendering entirely and draw the toasts/game-state panel with GDI+/Direct2D directly via ctypes and the classic `UpdateLayeredWindow` per-pixel-alpha-bitmap technique — more code, no HTML/CSS, but this is the long-established, unambiguously reliable way to get a real transparent overlay on Windows, independent of whatever WebView2/DWM is or isn't doing under the hood.

### Backup / restore
`app/api/backup.py`: `GET /api/backup/export` zips `data/` (excluding `webview_profile/`, `debug_log.json`, `crash_log.txt`) plus `.env` into a downloadable archive; `POST /api/backup/import` extracts one back over the project root (zip-slip guarded, only accepts `.env`/`data/*` entries). Settings from an imported `.env` only take effect after a restart — import does not touch the live `Settings` singleton.

## Gotchas

- `.env` at repo root is the user's real config with live API keys — never overwrite it wholesale; settings changes go through `persist_env_values()`.
- `winrt-*`, `windows-capture`, `pycaw` imports are Windows-only; modules using them import lazily/guarded so the server still boots elsewhere.
- Background LLM passes (memory extraction, game-state extraction) are fire-and-forget asyncio tasks — they must never add latency to or raise into the visible reply path. The game-state pass is vision-grounded: it receives each poll window's first/last frames as screenshots, and self-maintains a per-game UI-decoding notes document via its `training_data_update` output (there is no separate trainer model/pass).
- `TODO.md` is the maintained backlog of open items; check it before proposing work.
- Voice transcript extraction (raw-audio mode): the main model prefixes replies with a `<transcript>` block, peeled by `_peel_transcript` in `app/api/chat.py`; delivered only in the final `done` SSE payload. Text chat paths must peel/discard it too — models emit it out of habit after voice turns.
- "Reply freezes mid-stream" had TWO causes, both fixed — keep both guards when touching streaming code: (1) any `renderChatLog()` during streaming (chat switch) wipes `chatLog` and detaches the live bubbles; the stream loops re-attach via `assistantEl.isConnected` checks + a tail re-render. (2) The REAL "whole app hangs" bug: `extractCompleteSentences` used a catastrophically backtracking regex (`(?:[^.!?\n]+|[.!?](?!\s))*(...)`) — one `exec()` on a long incomplete sentence took minutes and froze the JS main thread (UI dead, all polling stops, window only closable via X). It is now a linear scan; NEVER split streaming text with a backtracking regex. Diagnosis tell: uvicorn console request logs going silent while the process lives = frontend JS wedged, not the server.
- "UI element missing but the API is polling fine" — check localStorage-persisted UI state first (`lyko-game-state-panel-pos`, `GAME_STATE_CLOSED_KEY`): the floating panel's position/closed state persist across reloads and can leave an element unhidden yet off-screen. `web/index.html` + `web/js/app/*.js` can be exercised headlessly with jsdom (see the scratchpad repro pattern: stub `fetch` per route, eval the app scripts in load order, assert on DOM).
- Tool-round boundary in `_stream_chat_with_tools` (`app/api/chat.py`): the `"\n\n"` separator delta must be yielded the moment a text-bearing round ENDS (right before its tool calls run), not lazily deferred until the next round's first text delta shows up. Deferring it left a round's trailing sentence ("Let me check that...") stuck un-narrated in the frontend's `sentenceBuffer` remainder for as long as the tool call took — or forever, if the next round turned out to be tool-only with no text. Also, models tend to restate the pre-tool-call text as a second paragraph once the tool result comes back — the system prompt now has an explicit line telling it to continue instead of re-greeting.
