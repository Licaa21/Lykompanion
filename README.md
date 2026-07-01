# Lykompanion

A local, voice-first gaming companion. It watches and discusses your game session with you, narrates replies out loud, and acts as an agent with tools of its own — it remembers things about you, looks at your screen, checks what game you're playing, searches the web, and queries game databases, all on its own initiative.

It's built to be used **hands-free while playing** — not as a chat app you sit and type into. The whole design (live mic, short spoken-style replies, agentic tool use) follows from that.

---

## Table of Contents

- [How it works](#how-it-works)
- [Features](#features)
- [Setup](#setup)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Architecture notes](#architecture-notes)
  - [The agentic tool loop](#the-agentic-tool-loop)
  - [Memory](#memory)
  - [Passive game-state awareness](#passive-game-state-awareness)
  - [Live mic / hands-free mode](#live-mic--hands-free-mode)
  - [Narration](#narration)
  - [Native desktop app](#native-desktop-app)
  - [Persistence](#persistence)
- [API reference](#api-reference)
- [Tools available to the agent](#tools-available-to-the-agent)
- [Development](#development)

---

## How it works

Lykompanion is a small FastAPI backend serving a vanilla HTML/CSS/JS frontend (no build step, no framework). All LLM calls go through [OpenRouter](https://openrouter.ai), so you can pick any audio-capable chat model OpenRouter offers (voice messages are sent straight to it), swap the TTS model separately, and OpenRouter handles billing/routing across providers.

A typical turn looks like:

1. You speak (hands-free live mic, or push-to-talk) or type.
2. Audio is sent straight to an audio-capable LLM — there's no local speech-to-text step.
3. The model replies, optionally calling one or more tools along the way (check memory, look at your screen, search the web, etc.) before producing its final answer.
4. The reply streams back and is narrated sentence-by-sentence via TTS as it arrives, so you don't wait for the full response before hearing the first words.
5. After the exchange, a background pass silently extracts anything worth remembering (or forgetting) and updates long-term memory — independent of whether the main reply called a memory tool itself.

## Features

- **Hands-free voice loop** — a live mic mode using voice-activity detection (with pre-roll buffering so the first word isn't clipped) automatically records your utterance and sends it, no push-to-talk needed. Narration is interrupted ("barge-in") if you start talking over it.
- **Wake word** — once hands-free is off (manually, or because the agent stopped it), say a configurable phrase (default "Hey Buddy") to turn it back on without touching the keyboard/mouse. Runs entirely in-browser via the Web Speech API.
- **Streaming spoken replies** — text streams in and is narrated sentence-by-sentence as it's generated, not after the full reply finishes.
- **Persistent memory** — the agent proactively saves and forgets facts about you (preferences, what you're playing, life context) across sessions, without being explicitly told to, and can tag a fact as specific to the game currently being played (so it only resurfaces while that game is active). A dedicated background LLM pass also analyzes every exchange for things worth remembering, so this doesn't depend on the main chat model reliably deciding to call a memory tool mid-conversation. Memory is viewable/editable/removable directly in the UI.
- **Vision** — the agent can take a screenshot of your screen itself (auto-detecting which monitor you're actively using on multi-monitor setups), or you can manually attach one to a message.
- **Awareness of real-world context** — it can check which game/app is currently focused, your system specs, and can pause/resume its own hands-free listening (e.g. if you say you're stepping away, or it notices the mic is picking up audio not meant for it).
- **Passive game-state awareness** *(opt-in, Windows-only, off by default)* — periodically OCRs your screen in the background and keeps a live snapshot of your current quest/location/character, injected into every conversation automatically. New/unfamiliar processes require explicit one-time approval through the notification center before anything gets read. See [Passive game-state awareness](#passive-game-state-awareness).
- **Web search & game databases** — OpenRouter's web search plugin, IGDB (structured game data), and Steam (store info + your own library/playtime) are all available as agent tools.
- **Configurable everything** — LLM model, context window size, TTS provider/voice/speed/volume (the agent can also adjust its own narration volume if you tell it it's too loud), wake word, live-mic sensitivity, screenshot quality, all from a Settings UI, persisted to `.env`.
- **Cost tracking & debugging** — per-call usage records (tokens, cost, which feature triggered it) with time-range filtering and a per-feature breakdown, optional OpenRouter account balance display, and a Debug panel showing the last 10 raw LLM requests/responses for troubleshooting.
- **Multiple chat sessions** — sidebar with per-chat history, auto-titled by the LLM after the first exchange. Persisted server-side (survives clearing browser data), along with replayable voice message recordings.
- **Native desktop app** — `RUN.cmd` launches Lykompanion in its own window (via `pywebview`/EdgeWebView2) instead of a browser tab, with the server running invisibly underneath. See [Native desktop app](#native-desktop-app).

## Setup

### Prerequisites

- Python 3.11+
- An [OpenRouter](https://openrouter.ai/keys) API key (required — this is the only LLM provider Lykompanion talks to)
- (Optional) [Kokoro](https://github.com/remsky/Kokoro-FastAPI) running locally for free, fast local TTS — or use OpenRouter's own Speech models instead
- (Optional, Windows 10/11 only, a recent-enough build for the Windows Graphics Capture API) an OCR-capable language pack installed for passive game-state awareness — the built-in Windows OCR engine is used, no separate binary to install, but it needs a language added via Settings → Time & Language → Language & region → Add a language, with "Optical character recognition" included for it

### Install

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### Run

**Native app (recommended):** double-click `RUN.cmd` (Windows), or run `.venv\Scripts\python.exe run_app.py`. This starts the FastAPI server in the background and opens Lykompanion in its own window via `pywebview` — no browser tab, no visible terminal once it's up. See [Native desktop app](#native-desktop-app) for details.

**Plain server (for development):**

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 6692 --reload
```

Then open `http://localhost:6692` in a browser. Use this instead of `RUN.cmd` when iterating on backend code, since `--reload` picks up Python changes automatically — `run_app.py` does not reload.

On first run, open **Settings** (⚙️ in the sidebar) and paste in your OpenRouter API key — everything else has sane defaults.

## Configuration

Everything is configurable from the Settings UI and persisted to a `.env` file in the project root (created automatically on first save). You can also edit `.env` directly. Relevant variables:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required. Your OpenRouter key (used for all LLM/TTS calls). |
| `OPENROUTER_MANAGEMENT_KEY` | Optional. A separate OpenRouter [Provisioning API key](https://openrouter.ai/settings/provisioning-keys) (not your regular inference key) used only to display your account's credit balance in the Consumption view. Everything else works fine without it. |
| `OPENROUTER_MODEL` | Main chat model. Must support audio input — voice messages are sent to it directly. |
| `TTS_PROVIDER` | `kokoro` (local) or `openrouter` (cloud Speech models). |
| `KOKORO_BASE_URL` | Where your local Kokoro server is running. |
| `KOKORO_VOICE`, `OPENROUTER_TTS_MODEL`, `OPENROUTER_VOICE` | TTS voice selection per provider. |
| `TTS_SPEED`, `TTS_VOLUME` | Narration speed/volume (the agent can also change these itself mid-conversation). |
| `CONTEXT_WINDOW_MESSAGES` | How many of the most recent messages to send as context. `0` = unlimited. |
| `WAKE_WORD_ENABLED`, `WAKE_WORD_PHRASE` | Enables the wake phrase (default `false`) and what to listen for (default `"Hey Buddy"`). |
| `VAD_THRESHOLD`, `VAD_SILENCE_MS`, `VAD_MIN_SPEECH_MS` | Live-mic voice-activity-detection tuning — amplitude threshold, how long to wait after speech stops before sending, and the minimum recording length to bother sending. Defaults `8` / `1200` / `300`. |
| `SCREENSHOT_MAX_WIDTH` | Downscale width (px) for screenshots sent to the LLM. Default `960`. Lower = cheaper in image tokens. |
| `SCREENSHOT_JPEG_QUALITY` | JPEG quality (1-95) for screenshots sent to the LLM. Default `70`. |
| `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET` | Twitch Developer app credentials for the IGDB game-database tool. IGDB auth runs entirely through Twitch — register a free app at [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) (any placeholder OAuth Redirect URL like `https://localhost` works, since it's never actually used — only the client-credentials grant is used). |
| `STEAM_API_KEY`, `STEAM_ID` | Optional. Enables the agent checking your owned games/playtime. Get a free key at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey); `STEAM_ID` is your numeric SteamID64. Steam *store* lookups (price, description, etc.) work without these. |
| `GAME_STATE_OCR_ENABLED` | `false` by default. Turns on passive game-state OCR (see [Passive game-state awareness](#passive-game-state-awareness)). Windows only, and requires an OCR-capable language pack installed for the built-in Windows OCR engine. |
| `GAME_STATE_POLL_INTERVAL_SECONDS` | How often (seconds) a batch of captured/OCR'd frames is sent to the model while a game is focused. Default `90`. |
| `GAME_STATE_CAPTURE_INTERVAL_SECONDS` | How often (seconds) the poller captures+OCRs a frame locally while building up that batch - free, no LLM call per capture. Default `1`. |
| `GAME_STATE_MODEL` | Dedicated model for the background game-state structuring pass. Falls back to `OPENROUTER_MODEL` if unset. |
| `GAME_STATE_TRAINING_ENABLED` | `false` by default. Turns on the game-state training pass (see [Passive game-state awareness](#passive-game-state-awareness)). |
| `GAME_STATE_TRAINING_MODEL` | Dedicated vision-capable model for the training pass. Falls back to `OPENROUTER_MODEL` if unset. |

No API key is needed for web search — it goes through OpenRouter's own `web` plugin, billed via your existing OpenRouter account.

## Project structure

```
app/
  main.py              FastAPI app, router registration, static file mount, background poller lifespan
  api/                  HTTP route handlers (one module per concern, incl. chats.py and voice.py for persistence)
  core/                 Config/settings, prompt loading, memory store, chat/voice persistence, ephemeral game-state store, usage tracking
  models/schemas.py     Pydantic request/response models
  prompts/              System/task prompt templates (Markdown, loaded at runtime)
  services/
    llm/                OpenRouter client + one module per agent tool, plus the memory/game-state background extraction passes
    tts/                 Kokoro / OpenRouter TTS backends
    screenshot/          Multi-monitor capture (mss/GDI for on-demand vision screenshots, Windows Graphics Capture for the game-state poller)
    ocr/                 Windows OCR (Windows.Media.Ocr) wrapper, used by passive game-state awareness
    system/              Process/system-info lookups
web/
  index.html, css/, js/app.js   Static frontend, no build step
run_app.py              Desktop launcher - runs the server in a background thread, opens it in a pywebview window
data/                   Runtime state (memory.json, usage.json, chats.json, custom_instructions.txt, voice/*.wav) - gitignored
```

## Architecture notes

### The agentic tool loop

All tools (memory, screenshot, volume, listening, process lookup, web search, IGDB, Steam, system info) are registered as OpenAI-style function tools and passed on every chat completion call — the model decides on its own when to call them, there's no separate "tool selection" step. `app/api/chat.py` runs a loop (`_run_chat_with_tools` for non-streaming, `_stream_chat_with_tools` for streaming) that keeps re-calling the model as long as it keeps requesting tools, feeding results back in, until it produces a final text reply (capped at `MAX_TOOL_ITERATIONS` as a safety net).

Some tools have side effects the *frontend* needs to react to immediately rather than waiting for the full reply (e.g. the agent lowering its own narration volume, or disabling the live mic). For streaming responses, these are emitted as out-of-band Server-Sent Events (`volume`, `stop_listening`) interleaved with the normal `delta` text events, the instant the tool runs — not deferred until the reply finishes. Non-streaming responses (voice messages) carry the same information as extra fields on the JSON response.

### Memory

Memory is a flat JSON list of `{id, content, process}` entries (`data/memory.json`), injected into the system prompt as "Known facts about the user" on every request. `process` is optional — when set (e.g. `"bg3.exe"`), that fact is treated as specific to whatever's currently being played and is only included in the prompt while that same process is the active foreground app; general facts (`process: null`) always show up. There are two independent paths that can write to memory:

1. **Explicit tools** (`save_memory`, `remove_memory`) — available to the main chat model, used when it decides mid-conversation to remember/forget something. `save_memory` takes a `game_specific` flag the model sets when the fact is tied to the current playthrough rather than generally true.
2. **A dedicated background extraction pass** (`app/services/llm/memory_extraction.py`) — after every text exchange, a separate, single-purpose LLM call analyzes the exchange against current memory and decides what to save/remove, applying it automatically. This runs as a fire-and-forget `asyncio` task so it never adds latency to the visible reply.

The second path exists because relying purely on the conversational model's own initiative to call memory tools turned out to be unreliable in practice, especially with smaller/cheaper models — they tend to only act on explicit instructions rather than proactively managing memory as a background habit. Forcing a dedicated pass every turn makes memory capture deterministic regardless of which model is handling the conversation.

### Passive game-state awareness

*Opt-in, off by default (`GAME_STATE_OCR_ENABLED=false`), Windows-only.* A long-lived background task (`app/services/llm/game_state_extraction.py`, started via FastAPI's `lifespan` in `app/main.py`) ticks every `GAME_STATE_CAPTURE_INTERVAL_SECONDS` (default 1s) and:

1. Checks the foreground process (`fetch_active_process`'s underlying lookup) and skips the tick entirely if it's not focused, or looks like an obviously non-game app (browser, terminal, IDE, etc. — a hardcoded denylist, not exhaustive).
2. If the process is neither denylisted nor already approved, it's added to a **pending queue** (`data/game_state_pending.json`, survives restarts) — the poller does *not* OCR it yet. It shows up in the notification bell (🔔 in the sidebar, with an unread-count badge) asking you to **Allow** or **Blacklist** each one. Nothing gets captured/read until you decide — this is the consent gate for an otherwise-automatic screen-reading feature. Decisions persist to `data/game_state_whitelist.json` / `data/game_state_blacklist.json`, both reviewable/editable from Settings → Game Awareness → Tracked Processes (an "Approved Processes" list with revoke buttons, and a "Blacklisted Processes" list with add/remove).
3. For an approved process, captures the screen every tick via the **Windows Graphics Capture** API (`app/services/screenshot/wgc_capture.py`, GPU-based — chosen specifically because the older GDI/BitBlt capture method used elsewhere in the app causes visible desktop-compositor stalls in games, most noticeable as stutter on cursor movement, when called this frequently) and runs it through the built-in **Windows OCR engine** (`app/services/ocr/windows_ocr.py`, `Windows.Media.Ocr` — free, local, no separate binary to install, just an OCR-capable language pack) — no LLM tokens spent on this step. Frames whose text is near-identical to the last kept frame (an unchanging HUD/menu) are dropped before ever reaching the LLM.
4. Once `GAME_STATE_POLL_INTERVAL_SECONDS` worth of ticks has accumulated, the surviving frames for that window are batched into **one** dedicated LLM pass (model configurable via `GAME_STATE_MODEL`, same fallback pattern as memory extraction), each labeled with how many seconds before the most recent frame it was captured — giving the model a short timeline instead of a single isolated snapshot, so it can better tell a transient UI flash from an actual state change. If every frame in the window got deduped away (nothing changed), the LLM pass is skipped entirely — this is what keeps the feature cheap. The pass also gets the current known-facts memory list (so it can recognize updates vs. duplicates, e.g. a level-up replacing an old level fact instead of stacking) and the **per-process tracker list** (`app/core/game_state_trackers.py`, persisted to `data/game_state_trackers.json`) telling it exactly which fields to fill and what each one means — every process starts out with the same defaults (Current Activity, Location, Quest, Character, Recent Choice, Known Stats, Game Completion), fully editable/removable from Settings → Game Awareness → Trackers per approved process (e.g. swap them out for "1v1 Rank"/"Goals scored this session" in Rocket League), except **Current Activity**, which always stays and can't be edited or removed since the extraction pass depends on it being refreshed every time. Previously-known field values are kept when a field isn't visible in a given window (Current Activity is the exception — always refreshed fresh, since it describes the current moment), and the pass can save/remove durable facts in the regular memory store, tagged to that process — not just stats, but narrative moments read straight from on-screen dialogue (e.g. "The player rejected Shadowheart's romantic advances in Baldur's Gate 3").

The structured snapshot is purely ephemeral (`app/core/game_state.py`, in-memory only, not written to disk — it resets on restart, unlike `memory.json`) and gets injected into the system prompt on every chat request, right after the memory block. You can inspect what's currently being tracked from the **Game State** button in the sidebar, which also shows a live status dot (gray = disabled, yellow = enabled but idle, green = actively tracking).

**Game-state training** *(opt-in, off by default)* — the extraction pass also self-reports a `confidence` score (0–1) for how well it understood the OCR text (never explained inline in a field's value — the prompt explicitly forbids meta-commentary like "the text is unclear" from leaking into `activity` etc., since that's what `confidence` is for). When training is enabled (Settings → Game Awareness → Awareness) and confidence drops below a threshold, a second, vision-capable "trainer" model (configurable separately via `GAME_STATE_TRAINING_MODEL`, falls back to the main chat model) is sent a fresh screenshot, the OCR text, and the process's **current training data document**, and asked to return a *revised* version of that document — e.g. explaining that on this game's HUD, a string like `02122` means home score, minutes:seconds remaining, away score. Rather than appending a new note per training pass, the trainer maintains one coherent living reference document per process (`app/core/game_state_training_data.py`, persisted to `data/game_state_training_data.json`), revising existing sections in place when they already cover the same UI element instead of piling up overlapping notes, and never describing the screenshot's visual content — only how to decode the OCR text. A per-process in-flight guard ensures only one training pass runs at a time for a given process, so two passes can never race to read-then-write the same document. Every future extraction pass for that process gets the current training data document in its prompt, so it can lean on what training already figured out instead of re-guessing. Training runs as a fire-and-forget background task so it never blocks the next poll tick. The training data document is reviewable/editable directly (a plain textarea, saved on blur) from Settings → Game Awareness → Training Data, per process.

Because OCR accuracy on stylized/low-contrast in-game fonts varies a lot by game, this is best-effort — treat it as a nice-to-have ambient signal, not a guaranteed-accurate game-state tracker. If a game runs in exclusive fullscreen and captures come back consistently empty, try borderless/windowed mode — some exclusive-fullscreen/protected-content surfaces aren't capturable even via Windows Graphics Capture.

### Live mic / hands-free mode

The live mic (🎙️) continuously records into a rolling ring buffer (not just monitoring volume) and uses simple amplitude-threshold voice activity detection, with `autoGainControl` requested on the mic stream so quiet speech gets normalized before it even reaches the threshold check. When speech is detected, 1500ms of pre-roll audio from *before* the threshold was crossed is prepended so the first word isn't clipped (a fresh recorder starting only at detection time can't recover audio that already happened), and finalization waits an extra fixed 500ms past the configured silence threshold (post-roll) so a trailing word doesn't get cut off either. After that silence window, the utterance is finalized as a WAV — a quick local pass through Silero VAD (`@ricky0123/vad-web`, loaded from CDN) then discards it if it doesn't actually look like speech (coughs, claps, keyboard noise that passed the amplitude gate), before sending what's left straight to the LLM. Tuning (amplitude threshold, silence duration, minimum speech length, wake word) is in Settings → Live Mic, persisted server-side via `/api/config`.

While narrating, the live mic doesn't suppress itself — any loud sound is treated as the user interrupting ("barge-in"), cutting narration immediately and starting a new recording, so you can cut the companion off mid-sentence.

Once hands-free is off, optionally say the configured **wake word** (Settings → Live Mic, default "Hey Buddy") to turn it back on — a separate `SpeechRecognition` instance listens only while hands-free is off (so it never competes with the live mic's own capture), with a debug transcript panel in Settings to see what it's hearing and confirm detection works. Chrome/Edge only (Web Speech API).

### Narration

Replies are split into sentences as they stream in, and each sentence is sent to TTS and queued for playback as soon as it's ready — synthesis for the next sentence starts immediately on enqueue, overlapping with current playback, rather than waiting for the full reply before saying anything.

### Native desktop app

`run_app.py` starts the FastAPI server in a background thread, waits for it to come up, then opens it in a native `pywebview` window (EdgeWebView2 on Windows 11) instead of a browser tab — `RUN.cmd` runs this by default. It also: pre-seeds the WebView2 profile so microphone permission and download behavior (auto-save to your real Downloads folder, no Save-As dialog) work from the very first launch without any manual prompts, disables dev-tools/right-click to keep it feeling like a real app rather than an obviously embedded browser, and exposes a small JS-callable API so voice-message downloads are copied directly by Python rather than going through WebView2's flakier download handling.

This is purely a presentation layer — the server underneath is the exact same FastAPI app you'd get running `uvicorn` directly, so anything documented elsewhere in this README applies whether or not you're using the desktop wrapper.

### Persistence

Chat sessions (`data/chats.json`, via `GET/PUT /api/chats`) and voice message recordings (`data/voice/*.wav`, via `/api/voice/{id}`) are persisted server-side rather than in browser `localStorage`/IndexedDB, so they survive clearing browser data and behave consistently whether you're using the desktop app or a browser tab. Voice messages are replayable directly from the chat log (play/pause, seek, speed, download) instead of just showing a "voice message" label.

## API reference

All endpoints are prefixed as shown; the frontend at `/` is served as static files.

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` | Non-streaming chat completion (tool loop included). |
| `POST /api/chat/stream` | Streaming chat completion (SSE) — primary path used by the UI. |
| `POST /api/chat/voice` | Send raw audio directly to an audio-capable model (no local STT). |
| `POST /api/chat/title` | Generate a short chat title from the first exchange. |
| `GET/PUT /api/config` | Read/update all settings. |
| `GET/POST /api/memory`, `PUT/DELETE /api/memory/{id}` | Memory CRUD. |
| `GET/PUT /api/instructions` | Custom personal instructions, injected into every conversation. |
| `GET/PUT /api/chats` | Server-side chat session persistence (`data/chats.json`) — the sidebar's full chat list, replacing client-side `localStorage`. |
| `POST/GET/DELETE /api/voice/{id}` | Upload, fetch, or delete a voice message recording (`data/voice/{id}.wav`) — powers the in-chat voice player. |
| `GET /api/usage/records`, `DELETE /api/usage` | Per-call usage records (timestamp, source, tokens, cost) and clearing them. The Consumption view aggregates these client-side by time range and feature. |
| `GET /api/usage/balance` | OpenRouter account credit balance (requires `OPENROUTER_MANAGEMENT_KEY`; returns `available: false` otherwise). |
| `GET /api/debug/requests` | Last 10 individual LLM API calls (not persisted) - full messages, tool calls, tokens, cost, duration. Powers the Debug panel. |
| `GET /api/models/llm` `/tts` | Model lists for Settings dropdowns. |
| `GET /api/screenshot` | One-off screenshot capture (used by the manual screenshot toggle). |
| `POST /api/tts` | Synthesize speech for arbitrary text. |
| `GET /api/game-state` | Current passive game-state snapshot (read-only) — powers the Game State sidebar indicator/modal. |
| `GET /api/game-state/pending` | Foreground processes currently awaiting allow/blacklist approval (a persisted queue, not just one) — powers the notification bell. |
| `GET/POST /api/game-state/whitelist`, `DELETE /api/game-state/whitelist/{process}` | Processes approved for game-state OCR. |
| `GET/POST /api/game-state/blacklist`, `DELETE /api/game-state/blacklist/{process}` | Processes the poller should never OCR. |

## Tools available to the agent

| Tool | What it does | Requires setup? |
|---|---|---|
| `save_memory` / `remove_memory` | Persist or forget a fact about the user, optionally tagged to the current game (`game_specific`). | No |
| `take_screenshot` | Capture a monitor (defaults to the active one). | No |
| `set_narration_volume` | Adjust its own TTS volume. | No |
| `stop_listening` | Disable hands-free mic indefinitely — on a sign-off, an explicit request, or unwanted overheard audio. Re-enable via the wake word or the mic toggle. | No |
| `fetch_active_process` | Check which app/game is currently focused. | No |
| `fetch_system_info` | Check OS/CPU/RAM. | No |
| `web_search` | Search the web (OpenRouter `web` plugin). | No (billed via OpenRouter) |
| `lookup_game_info` | IGDB game data (genre, platforms, release date, rating). | Twitch app credentials |
| `lookup_steam_game` | Steam store page details. | No |
| `fetch_steam_library` | User's owned games / playtime. | Steam API key + SteamID64 |

## Development

There's no build step for the frontend — edit `web/js/app.js`, `web/css/style.css`, or `web/index.html` directly and reload. The backend runs with `--reload`, so Python changes pick up automatically.

Prompts live in `app/prompts/*.md` as plain Markdown, loaded and cached at runtime (`app/core/prompts.py`) — edit them without touching Python code. `current_datetime_context()` is injected fresh on every request so the agent always knows the real current date/time, separate from the cached static prompt text.
