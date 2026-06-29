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
  - [Live mic / hands-free mode](#live-mic--hands-free-mode)
  - [Narration](#narration)
- [API reference](#api-reference)
- [Tools available to the agent](#tools-available-to-the-agent)
- [Development](#development)

---

## How it works

Lykompanion is a small FastAPI backend serving a vanilla HTML/CSS/JS frontend (no build step, no framework). All LLM calls go through [OpenRouter](https://openrouter.ai), so you can pick any chat-capable model OpenRouter offers, swap models per-purpose (text chat vs. voice-input vs. TTS), and OpenRouter handles billing/routing across providers.

A typical turn looks like:

1. You speak (hands-free live mic, or push-to-talk) or type.
2. Audio is sent straight to an audio-capable LLM — there's no local speech-to-text step.
3. The model replies, optionally calling one or more tools along the way (check memory, look at your screen, search the web, etc.) before producing its final answer.
4. The reply streams back and is narrated sentence-by-sentence via TTS as it arrives, so you don't wait for the full response before hearing the first words.
5. After the exchange, a background pass silently extracts anything worth remembering (or forgetting) and updates long-term memory — independent of whether the main reply called a memory tool itself.

## Features

- **Hands-free voice loop** — a live mic mode using voice-activity detection (with pre-roll buffering so the first word isn't clipped) automatically records your utterance and sends it, no push-to-talk needed. Narration is interrupted ("barge-in") if you start talking over it.
- **Streaming spoken replies** — text streams in and is narrated sentence-by-sentence as it's generated, not after the full reply finishes.
- **Persistent memory** — the agent proactively saves and forgets facts about you (preferences, what you're playing, life context) across sessions, without being explicitly told to. A dedicated background LLM pass also analyzes every exchange for things worth remembering, so this doesn't depend on the main chat model reliably deciding to call a memory tool mid-conversation. Memory is viewable/editable/removable directly in the UI.
- **Vision** — the agent can take a screenshot of your screen itself (auto-detecting which monitor you're actively using on multi-monitor setups), or you can manually attach one to a message.
- **Awareness of real-world context** — it can check which game/app is currently focused, your system specs, and can pause/resume its own hands-free listening (e.g. if you say you're stepping away).
- **Web search & game databases** — OpenRouter's web search plugin, IGDB (structured game data), and Steam (store info + your own library/playtime) are all available as agent tools.
- **Configurable everything** — LLM model, context window size, TTS provider/voice/speed/volume (the agent can also adjust its own narration volume if you tell it it's too loud), all from a Settings UI, persisted to `.env`.
- **Cost tracking** — cumulative token usage and real USD cost (via OpenRouter) tracked across all requests, viewable in the UI.
- **Multiple chat sessions** — sidebar with per-chat history (stored client-side in `localStorage`), auto-titled by the LLM after the first exchange.

## Setup

### Prerequisites

- Python 3.11+
- An [OpenRouter](https://openrouter.ai/keys) API key (required — this is the only LLM provider Lykompanion talks to)
- (Optional) [Kokoro](https://github.com/remsky/Kokoro-FastAPI) running locally for free, fast local TTS — or use OpenRouter's own Speech models instead

### Install

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### Run

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 6692 --reload
```

Or just double-click `RUN.cmd` (Windows), which does the same thing. Then open `http://localhost:6692` in a browser.

On first run, open **Settings** (⚙️ in the sidebar) and paste in your OpenRouter API key — everything else has sane defaults.

## Configuration

Everything is configurable from the Settings UI and persisted to a `.env` file in the project root (created automatically on first save). You can also edit `.env` directly. Relevant variables:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required. Your OpenRouter key. |
| `OPENROUTER_MODEL` | Main chat model. |
| `OPENROUTER_VOICE_MODEL` | Model used for voice messages (audio-capable). Falls back to `OPENROUTER_MODEL` if unset. |
| `TTS_PROVIDER` | `kokoro` (local) or `openrouter` (cloud Speech models). |
| `KOKORO_BASE_URL` | Where your local Kokoro server is running. |
| `KOKORO_VOICE`, `OPENROUTER_TTS_MODEL`, `OPENROUTER_VOICE` | TTS voice selection per provider. |
| `TTS_SPEED`, `TTS_VOLUME` | Narration speed/volume (the agent can also change these itself mid-conversation). |
| `CONTEXT_WINDOW_MESSAGES` | How many of the most recent messages to send as context. `0` = unlimited. |
| `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET` | Twitch Developer app credentials for the IGDB game-database tool. IGDB auth runs entirely through Twitch — register a free app at [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) (any placeholder OAuth Redirect URL like `https://localhost` works, since it's never actually used — only the client-credentials grant is used). |
| `STEAM_API_KEY`, `STEAM_ID` | Optional. Enables the agent checking your owned games/playtime. Get a free key at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey); `STEAM_ID` is your numeric SteamID64. Steam *store* lookups (price, description, etc.) work without these. |

No API key is needed for web search — it goes through OpenRouter's own `web` plugin, billed via your existing OpenRouter account.

## Project structure

```
app/
  main.py              FastAPI app, router registration, static file mount
  api/                  HTTP route handlers (one module per concern)
  core/                 Config/settings, prompt loading, memory store, usage tracking
  models/schemas.py     Pydantic request/response models
  prompts/              System/task prompt templates (Markdown, loaded at runtime)
  services/
    llm/                OpenRouter client + one module per agent tool
    tts/                 Kokoro / OpenRouter TTS backends
    screenshot/          Multi-monitor capture
    system/              Process/system-info lookups
web/
  index.html, css/, js/app.js   Static frontend, no build step
data/                   Runtime state (memory.json, usage.json, custom_instructions.txt) - gitignored
```

## Architecture notes

### The agentic tool loop

All tools (memory, screenshot, volume, listening, process lookup, web search, IGDB, Steam, system info) are registered as OpenAI-style function tools and passed on every chat completion call — the model decides on its own when to call them, there's no separate "tool selection" step. `app/api/chat.py` runs a loop (`_run_chat_with_tools` for non-streaming, `_stream_chat_with_tools` for streaming) that keeps re-calling the model as long as it keeps requesting tools, feeding results back in, until it produces a final text reply (capped at `MAX_TOOL_ITERATIONS` as a safety net).

Some tools have side effects the *frontend* needs to react to immediately rather than waiting for the full reply (e.g. the agent lowering its own narration volume, or disabling the live mic). For streaming responses, these are emitted as out-of-band Server-Sent Events (`volume`, `stop_listening`) interleaved with the normal `delta` text events, the instant the tool runs — not deferred until the reply finishes. Non-streaming responses (voice messages) carry the same information as extra fields on the JSON response.

### Memory

Memory is a flat JSON list of `{id, content}` entries (`data/memory.json`), injected into the system prompt as "Known facts about the user" on every request. There are two independent paths that can write to it:

1. **Explicit tools** (`save_memory`, `remove_memory`) — available to the main chat model, used when it decides mid-conversation to remember/forget something.
2. **A dedicated background extraction pass** (`app/services/llm/memory_extraction.py`) — after every text exchange, a separate, single-purpose LLM call analyzes the exchange against current memory and decides what to save/remove, applying it automatically. This runs as a fire-and-forget `asyncio` task so it never adds latency to the visible reply.

The second path exists because relying purely on the conversational model's own initiative to call memory tools turned out to be unreliable in practice, especially with smaller/cheaper models — they tend to only act on explicit instructions rather than proactively managing memory as a background habit. Forcing a dedicated pass every turn makes memory capture deterministic regardless of which model is handling the conversation.

### Live mic / hands-free mode

The live mic (🎙️) continuously records into a rolling ring buffer (not just monitoring volume) and uses simple amplitude-threshold voice activity detection. When speech is detected, ~600ms of pre-roll audio from *before* the threshold was crossed is prepended, so the first word isn't clipped (a fresh recorder starting only at detection time can't recover audio that already happened). After a configurable silence duration, the utterance is finalized as a WAV and sent directly to the LLM. Tuning (threshold, silence duration, minimum speech length) is in Settings → Live Mic, stored client-side.

While narrating, the live mic doesn't suppress itself — any loud sound is treated as the user interrupting ("barge-in"), cutting narration immediately and starting a new recording, so you can cut the companion off mid-sentence.

### Narration

Replies are split into sentences as they stream in, and each sentence is sent to TTS and queued for playback as soon as it's ready — synthesis for the next sentence starts immediately on enqueue, overlapping with current playback, rather than waiting for the full reply before saying anything.

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
| `GET /api/usage` | Cumulative token/cost stats. |
| `GET /api/models/llm` `/tts` `/voice-input` | Model lists for Settings dropdowns. |
| `GET /api/screenshot` | One-off screenshot capture (used by the manual screenshot toggle). |
| `POST /api/tts` | Synthesize speech for arbitrary text. |

## Tools available to the agent

| Tool | What it does | Requires setup? |
|---|---|---|
| `save_memory` / `remove_memory` | Persist or forget a fact about the user. | No |
| `take_screenshot` | Capture a monitor (defaults to the active one). | No |
| `set_narration_volume` | Adjust its own TTS volume. | No |
| `stop_listening` | Disable hands-free mic, optionally for a duration. | No |
| `fetch_active_process` | Check which app/game is currently focused. | No |
| `fetch_system_info` | Check OS/CPU/RAM. | No |
| `web_search` | Search the web (OpenRouter `web` plugin). | No (billed via OpenRouter) |
| `lookup_game_info` | IGDB game data (genre, platforms, release date, rating). | Twitch app credentials |
| `lookup_steam_game` | Steam store page details. | No |
| `fetch_steam_library` | User's owned games / playtime. | Steam API key + SteamID64 |

## Development

There's no build step for the frontend — edit `web/js/app.js`, `web/css/style.css`, or `web/index.html` directly and reload. The backend runs with `--reload`, so Python changes pick up automatically.

Prompts live in `app/prompts/*.md` as plain Markdown, loaded and cached at runtime (`app/core/prompts.py`) — edit them without touching Python code. `current_datetime_context()` is injected fresh on every request so the agent always knows the real current date/time, separate from the cached static prompt text.
