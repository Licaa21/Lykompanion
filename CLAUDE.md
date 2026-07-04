# CLAUDE.md

Guidance for Claude Code in this repo. Keep this file under ~80 lines: prune stale detail before adding new detail.

## Standing workflow (user-mandated)

1. Read this file first before any task.
2. Capture mistakes/corrections here immediately so they can't recur.
3. Keep this file current toward: no re-exploration after context loss, minimal tokens, under ~80 lines.
4. After finishing a task, update `TODO.md` (remove done items, add newly found ones).
5. After each task, commit and push to `main` (pre-authorized — don't ask).
6. Never start servers, curl endpoints, or write one-off verification scripts — user tests everything. `pytest tests` is allowed. Never run `overlay/build.cmd` — `RUN.cmd` already rebuilds it on every launch.

## What this is

Lykompanion — local, voice-first gaming companion. FastAPI backend + vanilla HTML/CSS/JS frontend (no build step). LLM calls go through OpenRouter (or Google AI Studio / any OpenAI-compatible endpoint). Windows-only for OCR/WGC/per-app volume. [README.md](README.md) has feature behavior, config vars, API/tool tables — check it before reading code.

## Commands

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 6692 --reload  # dev server
.venv\Scripts\python.exe -m pytest tests                                                 # tests
.venv\Scripts\python.exe -m pytest tests/test_memory.py -k test_name                      # single test
```
No linter/formatter configured. `RUN.cmd`/`run_app.py` = desktop app (pywebview/EdgeWebView2, frameless window with its own JS-driven titlebar drag/resize/snap — see `init.js`/`run_app.py` if touching window chrome; dev uvicorn has no auth, desktop app enforces `LYKO_API_TOKEN` on `/api/*`).

## Search hygiene

`data/` (~12k runtime files) and `.venv/` dominate unscoped searches — scope to `app/`, `web/`, or `tests/`. Frontend is `web/js/app/*.js`, 14 ordered classic `<script>` files (not ES modules) sharing one global scope, loaded in dependency order by `index.html` (core.js first, init.js last). Grep function names/DOM ids there first. Largest files: `settings.js`/`game-state.js` (~700ea), `style.css` (~1900), `index.html` (~800), `chat.py` (~500).

## Architecture

Request path: `chat-stream.js` → `/api/chat/stream` (SSE) or `/api/chat/voice[/stream]` (raw audio) → agentic tool loop → sentence-by-sentence TTS narration.

- **Tool loop** (`app/api/chat.py`): `_run_chat_with_tools`/`_stream_chat_with_tools` re-call the model up to `MAX_TOOL_ITERATIONS=8`. Add a tool: new `app/services/llm/<name>_tool.py` with `*_TOOLS` spec + `execute_*`, wire into `ALL_TOOLS`/`_execute_tool_impl`, document in README (memory tools live in `tools.py` instead).
- `app/api/*` route handlers; `app/core/*` settings + one-JSON-file-per-store persistence (no DB); `app/services/llm/*` client + tools + background passes (`memory_extraction.py`, `game_state_extraction.py` OCR poller); `app/services/tts|screenshot|ocr|system` provider backends (`capture.py` GDI vs `wgc_capture.py` WGC for the OCR poller, to avoid game stutter).
- **Native overlay** (`overlay/overlay.cpp`, C++, not Python): layered-window + Direct2D exe, own named-pipe API (`\\.\pipe\lykompanion-overlay`). Python (`overlay_process.py`) is a thin client only — spawn/quit/push toasts. Full details in `overlay/README.md` + `TODO.md`. Do not rebuild it yourself (see workflow #6).
- `app/prompts/*.md` — all prompts live here, loaded via `app/core/prompts.py`; never inline in Python.
- Config: `app/core/config.py`'s `Settings` singleton; Settings UI writes `.env` via `persist_env_values()` AND mutates the live singleton — no restart needed. Per-feature model/provider fields default to `""` = inherit main setting.
- Memory: `data/memory.json`, scoped `user`/`game`/`session`. All writers go through `memory.remember()` (owns scope-degradation rule: unresolvable scope degrades to `user`, never cross-game). OCR pass writes to the observations journal instead (`app/core/observations.py`); extraction promotes corroborated observations into memory.
- Anthropic models get real prompt-caching (`_build_base_messages` splits system prompt into cached prefix + variable suffix) — gated to Claude only, `cache_control` hurt latency on Gemini via OpenRouter.
- `app/core/memory.py`/`observations.py`/`reminders.py`/`game_state.py` keep in-memory caches of their JSON files — tests that monkeypatch their `*_PATH` constants must also reset the matching cache var to `None`.

## Frontend conventions

Every `fetch()` must send `x-lyko-token` (or `?token=` for `<img>`/`<audio>` src) — otherwise fine under dev uvicorn but breaks in the desktop app. No native browser chrome: use `showConfirm`/`showAlert` (core.js) instead of `alert`/`confirm`/`prompt`; selects are auto-themed by `custom-select.js`; tooltips via `title`/`data-tip` are auto-themed by `core.js`.

## Gotchas

- **Kokoro TTS misreads ALL-CAPS 2-3 letter gaming acronyms as chemical formulas** (HP→"Hydrogen Phosphorus", etc). Fixed client-side via `narration.js`'s curated `GAMING_ACRONYM_FIXES` map (dots the letters before TTS) — extend the map, don't switch to a general heuristic (would mangle Roman numerals in game titles).
- Sentence-splitting (`chat-stream.js`'s `extractCompleteSentences`) must treat a complete `![alt](url)`/`[label](url)` as atomic via `MARKDOWN_LINK_RE`, or punctuation inside alt text splits mid-pattern and leaks a raw URL fragment into narration. Also: NEVER use a backtracking regex there — one froze the JS main thread for minutes (tell: server logs go quiet while process lives = frontend wedged, not backend).
- `show_image`/`/api/proxy/image` must use identical headers to `web_search_tool.py`'s `SEARXNG_HEADERS` for both validation and render fetch, or some hosts' hotlink protection 403s only at render time.
- Overlay build traps: MSVC rejects a function-local struct default-init referencing a function-local enum (move to namespace scope); a stray `"` in PATH breaks `vcvars64.bat` (build.cmd strips quotes — keep that).
- Gamepad input can't be given to the overlay exclusively — `XInputGetState()` is a raw device poll, no per-process exclusivity API exists (would need ViGEmBus). Documented limitation, not a bug to fix casually.
- `.env` has live API keys — never overwrite wholesale; go through `persist_env_values()`.
- `winrt-*`/`windows-capture`/`pycaw` imports are Windows-only and import lazily/guarded.
- Background LLM passes (memory/game-state extraction) are fire-and-forget — must never add latency to or raise into the reply path.
- Voice raw-audio replies prefix a `<transcript>` block, peeled by `_peel_transcript` (chat.py) — must be peeled on text paths too.
- Tool-round boundary in `_stream_chat_with_tools`: the `"\n\n"` separator must be yielded the instant a text round ends (before its tool calls run), not deferred to the next round's first delta, or a trailing sentence gets stuck un-narrated.
- **Google AI Studio + streaming tool calls: intermittent "missing thought_signature" 400.** Gemini's OpenAI-compat endpoint attaches `extra_content.google.thought_signature` to tool calls and rejects a later request that replays one without it. Non-streaming path preserved it for free (SDK models are `extra="allow"`); streaming path (`_stream_chat_with_tools`/`_tool_calls_to_dict` in chat.py) now also captures/replays it via `getattr(tc, "extra_content", None)`.
- **In-game overlay via pywebview/WebView2 is a dead end — fully reverted (2026-07-03), do not re-attempt.** WebView2's Chromium compositor cannot render into a layered/transparent window by any mechanism tried (transparent color, `WS_EX_LAYERED`, color-key, region clipping) — the GPU compositor ignores all of them. If rebuilt, start from native layered windows + GDI+/Direct2D via ctypes + `UpdateLayeredWindow` (software-rendered, how real overlays work) — see the current native `overlay/` for exactly that. Exclusive-fullscreen games bypass any overlay regardless.
- `debug_log.py`'s `record_request`/`_track` (client.py) only fire on a successful provider call — a raised exception (e.g. a 400) used to leave zero trace. `client.py`'s four `create()` call sites now wrap the call in try/except and call `debug_log.record_error()` (same ring buffer/disk file, `error` field instead of `reply`) before re-raising, so failed calls are now visible in the Debug panel/`data/debug_log.json` too.
- `TODO.md` is the maintained backlog — check it before proposing work.
- YouTube in-app player (`youtube-player.js`): uses `playerVars.controls:0` + a custom volume slider — YouTube's native volume popout renders outside our small floating panel and loses the mouse. `play_on_youtube` auto-queues YouTube's generated "Mix" (`RD<video_id>` via yt-dlp) so next/previous have something to walk — that's an algorithmic mix, not the user's real playlist (use `play_youtube_playlist` for that). Panel position AND width (resize via corner grip) persist together in one `localStorage` key.
