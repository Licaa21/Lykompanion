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
  - [Game sessions](#game-sessions)
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
2. Audio is sent straight to an audio-capable LLM — there's no local speech-to-text step. The model is asked to prefix its reply with a verbatim `<transcript>` of what you said; the backend peels it off and the chat log swaps the "🎤 (voice message)" placeholder for your actual words, so later turns keep full conversational context (only text history is re-sent — past audio never is) and the memory pass sees what you said, all without a dedicated transcription model.
3. The model replies, optionally calling one or more tools along the way (check memory, look at your screen, search the web, etc.) before producing its final answer.
4. The reply streams back and is narrated sentence-by-sentence via TTS as it arrives, so you don't wait for the full response before hearing the first words.
5. After the exchange, a background pass silently extracts anything worth remembering (or forgetting) and updates long-term memory — independent of whether the main reply called a memory tool itself.

## Features

- **Hands-free voice loop** — a live mic mode using voice-activity detection (with pre-roll buffering so the first word isn't clipped) automatically records your utterance and sends it, no push-to-talk needed. Narration is interrupted ("barge-in") if you start talking over it.
- **Wake word** — once hands-free is off (manually, or because the agent stopped it), say a configurable phrase (default "Hey Buddy") to turn it back on without touching the keyboard/mouse. Runs entirely in-browser via the Web Speech API.
- **Sleep word** — the inverse: while hands-free is on, say a configurable phrase (default "Go to sleep") to turn it back **off**, hands-free. The same in-browser recognizer that watches for the wake phrase (while off) watches for the sleep phrase (while on); on a match it stops listening with a descending chime and **suppresses that utterance so the sleep phrase is never sent to the model**. As a backstop, if recognition misses it (or isn't available), the system prompt tells the model to treat a clear stop-listening request as a command — `stop_listening` + "Signing off...", not a question to answer. Both are in Settings → Live Mic.
- **Audio device selection** — pick which **microphone** captures your voice (push-to-talk + hands-free) and which **output device** everything plays through — narration, voice replies, and the synthesized beeps/SFX/wake chime — in Settings → Voice. The choice is saved per-device (localStorage), applied via `getUserMedia` device constraints, `HTMLAudioElement.setSinkId` (narration/voice), and `AudioContext.setSinkId` (Web Audio beeps/SFX). Output routing falls back to the system default where `setSinkId` isn't supported.
- **Streaming spoken replies** — text streams in and is narrated sentence-by-sentence as it's generated, not after the full reply finishes. Voice-mode replies stream identically to text replies — the transcript appears word-by-word in the chat log as the model speaks it.
- **Persistent memory with three scopes** — the agent proactively saves and forgets facts about you across sessions without being asked. Facts are tagged at three granularities: **user** (your name, preferences — always visible), **game** (applies to all runs of a specific game — e.g. preferred class), or **session** (specific to the current playthrough — character level, quest progress, decisions). A dedicated background LLM pass also analyzes every exchange for things worth remembering. User-scope facts are managed in the **Personal Data** modal; game- and playthrough-scope facts (plus unconfirmed screen observations) per game in the **Gaming Journal** modal.
- **Named game sessions** — each time you start a game, a session is created. You can name sessions ("first playthrough", "NG+"), switch between them, create new ones, and delete them — inline in the floating Game State panel, or fully managed (add/switch/delete profiles, and delete a whole tracked game) in the Gaming Journal modal. Facts saved during a session are scoped to it, so starting a new run doesn't pollute the context with the last one's progress.
- **Modpack awareness (variants)** — when a tracked game launches, a background pass reads the launch signals (window title, command line, install paths, parent process — e.g. Mod Organizer, the FTB/CurseForge launchers) and one LLM call decides whether it's a modded session and which pack ("Nolvus", "FTB StoneBlock 4", …). A confident detection auto-creates/switches to a profile named after the pack (tagged as its *variant*, shown as a badge in the Gaming Journal, manually editable per profile via "Set modpack"); it also resolves what a generic host exe like `javaw.exe` really is, fixing the journal title/cover art. If launch signals aren't enough, the OCR extraction pass can still name the pack later from on-screen branding — only the pack's own branding (a main menu, loading screen, quest book title) counts, never a single bundled mod's item/block/creature name, and the current window title is surfaced as a cross-check signal against the guess. The active pack is stated in the chat prompt, pack-specific game memories are tagged to it (they never leak into vanilla or other-pack runs), the self-training document AND the tracker list are both kept per-variant (training data editable per-pack in the Gaming Journal's Training Data tab — falls back to showing the base game's notes with an "inherited" hint until the pack gets its own), and a variant bootstrap web-searches the pack itself to seed pack-appropriate knowledge/trackers.
- **My Games library** — the Gaming Journal's main tab shows every game the companion knows about as a Steam-like poster grid, cover art fetched automatically (Steam store search first, IGDB fallback, then SteamGridDB as a last resort for whatever still has no cover, then an LLM+web-search lookup for the game's official title if nothing matches a made-up process name). Click a game to open its full journal — memories, playthrough profiles, training data, and per-game delete/delete-and-blacklist actions.
- **Crash/rollback recovery** — if the companion notices a stat regression (health dropped, level went down), it brings it up naturally in conversation. Once you confirm you loaded an older save or the game crashed, the agent can roll back the memories it saved during the lost window so they don't contradict your actual current state.
- **Vision** — the agent can take a screenshot of your screen itself (auto-detecting which monitor you're actively using on multi-monitor setups), or you can attach any image to a message yourself via **Send Image** (browse, drag and drop, or paste from your clipboard).
- **Awareness of real-world context** — it can check which game/app is currently focused, your system specs, and can pause/resume its own hands-free listening (e.g. if you say you're stepping away, or it notices the mic is picking up audio not meant for it).
- **Passive game-state awareness** *(opt-in, Windows-only, off by default)* — periodically OCRs your screen in the background and keeps a live snapshot of your current quest/location/character, injected into every conversation automatically. New/unfamiliar processes require explicit one-time approval through the notification center before anything gets read. See [Passive game-state awareness](#passive-game-state-awareness).
- **Web search & game databases** — web search via OpenRouter's plugin or a self-hosted [SearXNG](https://github.com/searxng/searxng) instance, IGDB (structured game data), and Steam (store info + your own library/playtime) are all available as agent tools.
- **Profile pictures & display name** — set your own name (shown on your messages in the sidebar) and an avatar image for yourself and the companion, uploaded via Settings.
- **Configurable everything** — LLM model, context window size, TTS provider/voice/speed/volume (the agent can also adjust its own narration volume if you tell it it's too loud), wake word, live-mic sensitivity, screenshot quality, all from a Settings UI, persisted to `.env`. All API credentials live in a single dedicated **API Keys** tab with ✕ clear buttons so stored server-side keys can be wiped without re-entering them.
- **Sound effects** — short synthesized cues (no audio files) for sending a message and for tool calls (a distinct sound for web search, memory saved, memory removed, plus a generic one for everything else), toggleable in Settings.
- **Backup & restore** — one-click export/import of `data/` (chats, memories, sessions, reminders, voice recordings, profile pictures) plus `.env` as a single zip, for moving to another machine. In Settings → General → Backup.
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
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 6692 --reload
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
| `USER_DISPLAY_NAME` | Your name as shown on your chat messages. Default `"You"`. |
| `WEB_SEARCH_PROVIDER` | `openrouter` (default, billed via OpenRouter) or `searxng` (self-hosted, free). |
| `SEARXNG_BASE_URL` | Base URL of a running SearXNG instance when `WEB_SEARCH_PROVIDER=searxng`. Default `http://localhost:8080`. |
| `TTS_PROVIDER` | `kokoro` (local), `openrouter` (cloud Speech models), or `chirp3` (Google Cloud Text-to-Speech Chirp 3 HD). |
| `KOKORO_BASE_URL` | Where your local Kokoro server is running. |
| `KOKORO_VOICE`, `OPENROUTER_TTS_MODEL`, `OPENROUTER_VOICE` | TTS voice selection per provider. |
| `GOOGLE_TTS_API_KEY` | Optional. Google Cloud API key for Chirp 3 HD. Leave blank to use Application Default Credentials (`gcloud auth application-default login`) instead — ADC works as long as you run that command once and the Cloud Text-to-Speech API is enabled for your project. |
| `GOOGLE_TTS_VOICE` | Chirp 3 HD voice name, e.g. `en-US-Chirp3-HD-Aoede`. Voice list populates in Settings → Voice & Narration once credentials are valid. |
| `GOOGLE_AI_STUDIO_API_KEY` | Optional. Gemini API key from Google AI Studio — used for any LLM feature (main chat, memory extraction, game-state) set to the Google AI Studio provider. |
| `CUSTOM_OPENAI_BASE_URL` | Base URL of any OpenAI-compatible API (self-hosted model, alternative aggregator). |
| `CUSTOM_OPENAI_API_KEY` | API key for the custom provider above. |
| `TTS_SPEED`, `TTS_VOLUME` | Narration speed/volume (the agent can also change these itself mid-conversation). |
| `CONTEXT_WINDOW_MESSAGES` | How many of the most recent messages to send as context. `0` = unlimited. |
| `WAKE_WORD_ENABLED`, `WAKE_WORD_PHRASE` | Enables the wake phrase (default `false`) and what to listen for (default `"Hey Buddy"`). |
| `SLEEP_WORD_ENABLED`, `SLEEP_WORD_PHRASE` | Enables the sleep phrase (default `false`) that turns hands-free off while it's on (default `"Go to sleep"`). Detected in-browser and suppressed from being sent; also enforced as an LLM stop-intent backstop. |
| `HANDSFREE_MODE` | `"handsfree"` (default) or `"single_command"` — see [Live mic / hands-free mode](#live-mic--hands-free-mode). |
| `VAD_THRESHOLD`, `VAD_SILENCE_MS`, `VAD_MIN_SPEECH_MS` | Live-mic voice-activity-detection tuning — amplitude threshold, how long to wait after speech stops before sending, and the minimum recording length to bother sending. Defaults `8` / `1200` / `300`. |
| `SCREENSHOT_MAX_WIDTH` | Downscale width (px) for screenshots sent to the LLM. Default `960`. Lower = cheaper in image tokens. |
| `SCREENSHOT_JPEG_QUALITY` | JPEG quality (1-95) for screenshots sent to the LLM. Default `70`. |
| `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET` | Twitch Developer app credentials for the IGDB game-database tool. IGDB auth runs entirely through Twitch — register a free app at [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) (any placeholder OAuth Redirect URL like `https://localhost` works, since it's never actually used — only the client-credentials grant is used). |
| `STEAM_API_KEY`, `STEAM_ID` | Optional. Enables the agent checking your owned games/playtime. Get a free key at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey); `STEAM_ID` is your numeric SteamID64. Steam *store* lookups (price, description, etc.) work without these. |
| `STEAMGRIDDB_API_KEY` | Optional. Last-resort cover art fallback for the Gaming Journal's My Games library, used only when a game's box art isn't found on Steam or IGDB. Get a free key at [steamgriddb.com/profile/preferences/api](https://www.steamgriddb.com/profile/preferences/api). |
| `OVERLAY_ENABLED` | `false` by default. Launches the native in-game overlay (`overlay/Lykompanion-overlay.exe`) while a game is tracked, drawing the game-state panel plus reply/reminder toasts on top of the game (borderless/windowed only — exclusive fullscreen can't show it). Reply text streams in sentence-by-sentence to keep pace with narration, and any web image the companion embeds (`![alt](url)`) is downloaded and shown in the overlay too; per-tracker visibility is toggleable (Game Awareness → Trackers → "Overlay"). Press the configured hotkey in-game (**Ctrl+Shift+O** by default, changeable in Settings) to move widgets, change opacity/accent color, and manage named position/appearance **presets** (Save / Discard & Close, switch/create/delete/rename); a connected gamepad can drive the whole editor too (D-pad/left-stick to select a widget, right-stick to move it, A/B/X/Y/LB/RB for save/discard/delete/new/prev/next preset). Saying the configured "Edit overlay" phrase (off by default) opens edit mode directly, detected locally and never sent to the AI. The layout persists to `%LOCALAPPDATA%\Lykompanion\overlay_layout.json`. Windows only, and requires the built exe (`overlay/build.cmd`, needs VS Build Tools). |
| `GAME_STATE_OCR_ENABLED` | `false` by default. Turns on passive game-state OCR (see [Passive game-state awareness](#passive-game-state-awareness)). Windows only, and requires an OCR-capable language pack installed for the built-in Windows OCR engine. |
| `GAME_STATE_POLL_INTERVAL_SECONDS` | How often (seconds) a batch of captured/OCR'd frames is sent to the model while a game is focused. Default `90`, minimum `5`. |
| `GAME_STATE_CAPTURE_INTERVAL_SECONDS` | How often (seconds) the poller captures+OCRs a frame locally while building up that batch - free, no LLM call per capture. Default `1`. |
| `GAME_STATE_VISUAL_DIFF_THRESHOLD_PERCENT` | If every frame in a poll window dedupes away as OCR-text-identical, the LLM pass is normally skipped entirely (e.g. a paused/static screen). As a fallback, the window's first and last raw screenshots are compared via a coarse pixel diff; if they differ by at least this percent, the window is sent through anyway on pixels alone - catches minimal-UI games where the HUD text never changes even though the player is genuinely moving. A truly frozen screen has ~0% diff and stays skipped. Default `12`, `100` = disable this fallback. |
| `GAME_STATE_VISUAL_DIFF_NOISE_FLOOR_PERCENT` | The reverse check: if OCR *did* flag a frame as changed but the same window's pixel diff falls below this much lower floor, it's treated as OCR misread noise (jittery text extraction on an otherwise static screen) and the LLM pass is skipped anyway. Keep well below `GAME_STATE_VISUAL_DIFF_THRESHOLD_PERCENT` - it only catches near-zero-diff noise, not real small HUD changes. Default `1.5`, `0` = disable (trust OCR alone, prior behavior). |
| `GAME_STATE_MAX_CONSECUTIVE_SKIPS` | Both change-detection checks above only ever compare frames *within* the current poll window, so a screen that became static entirely inside one window (e.g. a death/game-over screen reached mid-window) would otherwise be skipped forever - every later window also compares that same static screen against itself and finds nothing new. To fix that, the first "should skip" window in any streak is *always* let through unconditionally (not gated by this setting) - the guaranteed single look that actually fixes the bug. This setting only controls an optional periodic re-check after that: `0` (default) means no further forced pass for the rest of the streak (no recurring LLM cost while genuinely static); a positive value re-forces one through every N further consecutive skips as extra insurance, at the cost of periodic calls even while paused. |
| `GAME_STATE_MODEL` | Dedicated model for the background game-state structuring pass. Falls back to `OPENROUTER_MODEL` if unset. Should be vision-capable — the first and most recent captured frames of each poll window are attached as screenshots (a non-vision model still works: the call is retried text-only, losing the visual grounding). |
| `GAME_BOOTSTRAP_MODEL` | Dedicated model for the one-time game-knowledge bootstrap (seeding trackers + the starting training-data document for a newly-tracked game or modpack). Falls back to `GAME_STATE_MODEL`, then `OPENROUTER_MODEL`, if unset. Text-only (structures gathered IGDB/web-search results, no screenshots) — since this runs rarely (once per game/pack, not every poll window), a stronger/pricier model here is cheap in aggregate and produces noticeably better starting notes than reusing a small/fast `GAME_STATE_MODEL`. |
| `GAME_STATE_OCR_SIMILARITY_THRESHOLD` | A captured frame is dropped as a near-duplicate if its OCR text is at least this similar (0-1 ratio) to the last kept frame. Default `0.9`. Lower catches more subtle changes; higher tolerates more unchanged HUD text before sending a new frame through. |
| `GAME_STATE_OCR_MAX_WIDTH` | Downscale width (px) each captured frame is resized to before OCR — independent of `SCREENSHOT_MAX_WIDTH` (which only applies to vision LLM calls). Default `1600`. Lower cuts OCR CPU time with negligible accuracy loss for HUD-sized text. |
| `GAME_STATE_VISUAL_DIFF_THUMBNAIL_SIZE` | Square thumbnail size (px) both frames are downscaled to for the visual-diff fallback comparison (see `GAME_STATE_VISUAL_DIFF_THRESHOLD_PERCENT`). Default `64`. Smaller is cheaper and blurs out noise; larger is more sensitive to small changes. |
| `GAME_STATE_CAPTURE_FRAME_TIMEOUT_SECONDS` | How long the Windows Graphics Capture poller waits for a single frame before giving up on that tick instead of blocking indefinitely. Default `6.0`. |
| `GAME_STATE_CAPTURE_CURSOR_ENABLED` | `false` by default. Whether the mouse cursor is included in captured frames used for OCR and the extraction pass's screenshots. |
| `GAME_STATE_EMPTY_OCR_WARN_THRESHOLD` | How many consecutive capture ticks with no OCR text before a warning is logged. Default `10`. |
| `GAME_STATE_TRAINING_ENABLED` | `false` by default. Lets the game-state extraction pass maintain per-game UI-decoding notes for itself (self-training — see [Passive game-state awareness](#passive-game-state-awareness)). When enabled, tracking a brand-new game also runs a one-time knowledge bootstrap: IGDB + web search results seed game-specific trackers (only if you haven't customized them) and a starting training-data document. |
| `PROACTIVE_MESSAGES_ENABLED` | `false` by default. Lets the game-state model speak up unprompted — a tip or comment lands in the active chat (and is narrated) when it spots something genuinely worth mentioning on screen. |
| `PROACTIVE_MIN_INTERVAL_MINUTES` | Hard cooldown between two unprompted messages. Default `15`. |
| `MEMORY_RAG_LIMIT` | Once the active game/playthrough has more than this many game/session-scoped memories, only the most relevant + most recent ones (up to this cap) are injected into the chat prompt (lexical RAG-lite; general memories are always injected in full). Default `30`, `0` = inject everything. |

No API key is needed for web search — it goes through OpenRouter's own `web` plugin, billed via your existing OpenRouter account.

### Per-model OpenRouter provider routing

Each of the three LLM model pickers that use OpenRouter (main chat, memory extraction, game-state) has a **Providers…** button next to its dropdown. It opens a live table of the real providers OpenRouter currently routes that exact model through (DeepInfra, Together, Google Vertex, etc.) with per-provider price, context length, quantization, uptime, and latency/throughput where OpenRouter has recent samples. Check the providers you want to allow, optionally set a sort preference (price/throughput/latency), allow-fallbacks, and max price — this maps to OpenRouter's [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection) request field. Leaving every box unchecked uses OpenRouter's own default routing.

This is a per-model preference, not an env var — it's stored in `data/provider_routing.json` (keyed by model id) via `GET`/`PUT /api/provider-routing/{model_id}`, and merged into every OpenRouter chat completion request for that model (`app/services/llm/client.py`'s `_openrouter_extra_body`). OpenRouter-only — a feature set to Google AI Studio or a custom endpoint ignores it entirely.

### Setting up Chirp 3 HD

Chirp 3 HD is Google Cloud Text-to-Speech — the highest quality TTS option. To enable it:

1. Log in with `gcloud auth application-default login` (install the [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) first if needed).
2. Enable the Cloud Text-to-Speech API for your project: visit the activation URL shown in Settings after clicking Refresh Model Lists, or go to Google Cloud Console → APIs & Services → Library and search "Text-to-Speech".
3. In Settings → TTS Provider, choose **Chirp 3 HD (Google)** and click **Refresh Model Lists** — the voice dropdown will populate.

Alternatively, skip `gcloud` entirely by creating an API key in Google Cloud Console (APIs & Services → Credentials) and pasting it into Settings → API Keys → Cloud TTS Key.

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
    tts/                 Kokoro / OpenRouter / Chirp 3 HD (Google Cloud TTS) backends
    screenshot/          Multi-monitor capture (mss/GDI for on-demand vision screenshots, Windows Graphics Capture for the game-state poller)
    ocr/                 Windows OCR (Windows.Media.Ocr) wrapper, used by passive game-state awareness
    system/              Process/system-info lookups
web/
  index.html, css/, js/app/*.js   Static frontend, no build step (app/ = ordered classic scripts, one per concern)
run_app.py              Desktop launcher - runs the server in a background thread, opens it in a pywebview window
data/                   Runtime state (memory.json, usage.json, chats.json, custom_instructions.txt, voice/*.wav) - gitignored
```

## Architecture notes

### The agentic tool loop

All tools (memory, screenshot, volume, listening, process lookup, web search, IGDB, Steam, system info) are registered as OpenAI-style function tools and passed on every chat completion call — the model decides on its own when to call them, there's no separate "tool selection" step. `app/api/chat.py` runs a loop (`_run_chat_with_tools` for non-streaming, `_stream_chat_with_tools` for streaming) that keeps re-calling the model as long as it keeps requesting tools, feeding results back in, until it produces a final text reply (capped at `MAX_TOOL_ITERATIONS` as a safety net).

Some tools have side effects the *frontend* needs to react to immediately rather than waiting for the full reply (e.g. the agent lowering its own narration volume, or disabling the live mic). For streaming responses, these are emitted as out-of-band Server-Sent Events (`volume`, `stop_listening`) interleaved with the normal `delta` text events, the instant the tool runs — not deferred until the reply finishes. Non-streaming responses (voice messages) carry the same information as extra fields on the JSON response.

### Memory

Memory is a flat JSON list of `{id, content, scope, process, session_id, saved_at}` entries (`data/memory.json`), injected into the system prompt as "Known facts about the user" on every request. `scope` is stored explicitly (older entries are migrated on read) and rendered in prompts and the UI, so every consumer can tell the tiers apart:

- **User scope** — about the person regardless of game: name, preferences, life context. Always shown. Managed in the **Personal Data** modal.
- **Game scope** — specific to one game but true across all its runs: preferred class style, how they approach this title. Shown only while that game is active. Rendered as "(game: X, all playthroughs)". A game-scope fact may additionally carry a **variant** (the modpack it belongs to): then it's only shown while a session of that pack is active — mod-added mechanics never leak into vanilla runs or other packs. Rendered as "(game: X, Nolvus playthroughs only)". This tagging is automatic, not a model judgment call: any game-scope fact saved (via `save_game_memory`, the extraction pass, or a manual Gaming Journal add) while a session of a modpack is active is scoped to that modpack, always anchored to the tracked variant, never a model-invented name. Mis-scoped facts (or ones that should be widened/narrowed) can be reclassified after the fact by clicking the variant chip on a game memory in the Gaming Journal.
- **Session scope** — specific to one playthrough/profile: character level, quest progress, decisions made this run. Shown only in that named session. Rendered as "(this playthrough of X)". Both game- and session-scope facts are managed per game in the **Gaming Journal** modal.

`saved_at` is an ISO timestamp written at creation (never updated on edit) — used by the rollback tool to find which memories fall within a lost-progress window.

**Every writer goes through one entry point, `memory.remember(content, scope, process, session_id)`** (`app/core/memory.py`), which owns scope resolution, exact-duplicate rejection, and the degradation rule: a fact whose requested scope can't be honored (e.g. "session" while no session is active) is saved at **user scope** — never silently re-tiered into a *different game tier*, which is how playthrough facts used to leak into game-wide memory.

Two paths write to memory (both via `remember()`):

1. **Explicit tools** — available to the main chat model: `save_user_memory`, `save_game_memory`, `save_session_memory` (each maps to the appropriate scope automatically using the tracked game state), `remove_memory`, and `rollback_session_memories(hours_lost)` (removes session-scoped memories saved within the specified window — used after the player confirms a crash or loaded an older save; never touches global or other-session memories).
2. **A dedicated background extraction pass** (`app/services/llm/memory_extraction.py`) — after every text exchange, a separate, single-purpose LLM call analyzes the exchange against current memory and decides what to save/remove, applying it automatically. Scope tagging is anchored strictly to the tracked game. Runs as a fire-and-forget `asyncio` task, never adding latency to the visible reply.

The second path exists because relying purely on the conversational model's own initiative to call memory tools turned out to be unreliable in practice, especially with smaller/cheaper models — they tend to only act on explicit instructions rather than proactively managing memory as a background habit. Forcing a dedicated pass every turn makes memory capture deterministic regardless of which model is handling the conversation.

**The passive game-state OCR pass does *not* write memory.** It appends timestamped, confidence-tagged **observations** to a per-session staging journal (`app/core/observations.py`, `data/observations.json`, capped per session) — a single misread frame no longer carries the same authority as the user stating a fact. A recent tail of observations is injected into the chat system prompt (explicitly marked unconfirmed), and the memory-extraction pass sees them too: it **promotes** an observation into a real memory when the conversation corroborates it (or it's clearly durable and uncontested) and clears handled/stale ones. Observations are visible per profile in the Gaming Journal, where they can also be discarded manually.

**Silent play sessions still build memory.** The conversational promotion path above only fires when the user actually chats. A dedicated **observation confirmation pass** (`app/services/llm/observation_confirmation.py`) runs in the background whenever a session accumulates enough pending observations (12+): it reviews the whole journal for that session against the known facts, promotes what holds up on its own evidence (corroborated across frames, high-confidence durable milestones, progression updates, or **a behavioral pattern** — two or more separate playstyle-revealing observations, e.g. thoroughly clearing optional content and backtracking for missable items, synthesized into one trait like "tends to fully explore areas before advancing — a completionist playstyle") into the correct scope (`user`/`game`/`session` — personality/playstyle traits are always `user` scope, even when every supporting instance came from one game), removes known facts the observations supersede, and clears everything it handled or judged noise. A single instance never earns a trait promotion on its own — it takes at least two independent ones pointing the same way. It doesn't trust the OCR pass's wording at face value — it reads the session's observations as a whole story and reasons about whether it actually holds up (a misattributed flashback, a misheard name, a choice merely shown rather than picked), correcting or clearing entries rather than promoting them as-is when they don't. Since it runs far less often than the extraction pass, it can afford to actually research: it's fed the per-game training data document alongside known facts and the game snapshot, and can chain up to `MAX_SEARCH_ROUNDS` (4) web searches in one review — following up on what a first search turns up — to decipher an unfamiliar boss/quest/area/NPC name or verify a suspected misattribution before finalizing its decisions.

**RAG-lite memory injection** — general (`user`-scope) memories are always injected into every prompt in full, but once the active game/playthrough accumulates more than `MEMORY_RAG_LIMIT` game/session-scoped memories (default 30, `0` disables), only the most relevant ones make the chat prompt: a lexical TF-IDF-style ranking against the recent conversation + live game activity (`app/core/memory_retrieval.py`), with a share of the slots reserved for the most recently saved facts. No embeddings, no DB — deterministic and free. Background extraction passes always see the full memory list (they need every fact to dedupe/remove correctly).

### Game sessions

Each time a game is tracked, Lykompanion creates a named **session** for it (default name "default", auto-created on first track). Sessions are stored per-process in `data/game_state_sessions.json`, keyed `process::session_id`. The active session for a given game is remembered across restarts.

You can manage sessions from the **floating Game State panel** (bottom-right when a game is tracked): an inline expandable list shows all sessions for the current game, lets you rename any of them in place, switch to a different one, or create a new one. The panel grows to fit — no dropdown clipping issues.

Session facts (scope `"session"`) only appear in the system prompt while that specific session is active. Switching to a new session gives the companion a clean slate for playthrough-specific context while all user-scoped and game-scoped facts remain visible.

**Divergence detection** — the game-state extraction pass compares each update against the previous snapshot. If it spots stat regressions (e.g. health dropped dramatically, level went down) that can't be explained by normal gameplay, it emits a `divergence_warning`. On the next chat turn the companion mentions this naturally and asks what happened (crash? loaded an older save?). Once you confirm how much progress was lost, the agent calls `rollback_session_memories(hours_lost)` to remove session memories from the affected window so they no longer contradict your actual current state.

### Passive game-state awareness

*Opt-in, off by default (`GAME_STATE_OCR_ENABLED=false`), Windows-only.* A long-lived background task (`app/services/llm/game_state_extraction.py`, started via FastAPI's `lifespan` in `app/main.py`) ticks every `GAME_STATE_CAPTURE_INTERVAL_SECONDS` (default 1s) and:

1. Checks the foreground process (`get_foreground_process_name` in `app/services/system/processes.py`) and skips the tick entirely if it's not focused, or looks like an obviously non-game app (browser, terminal, IDE, etc. — a hardcoded denylist, not exhaustive).
2. If the process is neither denylisted nor already approved **and its window looks like a game**, it's added to a **pending queue** (`data/game_state_pending.json`, survives restarts) — the poller does *not* OCR it yet. Two signals qualify: borderless/fullscreen (covers the whole monitor with no title bar, via `is_foreground_window_fullscreen`) queues instantly, and a *windowed* app whose window covers most of the work area (`is_foreground_window_large`, ≥70%) queues after holding focus for a few consecutive ticks — so windowed Minecraft/emulators get the prompt while a briefly-focused big utility window doesn't. Small windows never nag (still whitelistable manually). Web browsers are deliberately **not** denylisted, so a fullscreen browser game reaches this same approval flow rather than being hard-skipped. It shows up in the notification bell (🔔 in the sidebar, with an unread-count badge) asking you to **Allow** or **Blacklist** each one. Nothing gets captured/read until you decide — this is the consent gate for an otherwise-automatic screen-reading feature. Decisions persist to `data/game_state_whitelist.json` / `data/game_state_blacklist.json`, both reviewable/editable from Gaming Journal → Journal Settings → Tracked Processes (an "Approved Processes" list with revoke buttons, and a "Blacklisted Processes" list with add/remove).
3. For an approved process, captures the screen every tick via the **Windows Graphics Capture** API (`app/services/screenshot/wgc_capture.py`, GPU-based — chosen specifically because the older GDI/BitBlt capture method used elsewhere in the app causes visible desktop-compositor stalls in games, most noticeable as stutter on cursor movement, when called this frequently). A game running **windowed** is captured as *just its window* (only the game's own pixels reach OCR — no desktop, taskbar, or other windows leaking in), falling back to whole-monitor capture when it's fullscreen or the window capture fails and runs it through the built-in **Windows OCR engine** (`app/services/ocr/windows_ocr.py`, `Windows.Media.Ocr` — free, local, no separate binary to install, just an OCR-capable language pack) — no LLM tokens spent on this step. Frames whose text is near-identical to the last kept frame (an unchanging HUD/menu) are dropped before ever reaching the LLM — this dedupe carries across poll windows indefinitely, so sitting in a pause menu costs zero further structuring passes rather than one per window. If a whole window dedupes away with nothing kept, before giving up the poller compares that window's true first and last raw screenshots with a coarse pixel diff (`GAME_STATE_VISUAL_DIFF_THRESHOLD_PERCENT`, default 12%, `100` disables it): a frozen/paused screen scores ~0% and stays skipped, but a minimal-UI game where the HUD text never changes even though the camera/environment is genuinely moving scores above threshold and gets sent through on pixels alone (using the last OCR reading, which found nothing to say, plus those two screenshots).
4. Once `GAME_STATE_POLL_INTERVAL_SECONDS` worth of ticks has accumulated, the surviving frames for that window are batched into **one** dedicated LLM pass (model configurable via `GAME_STATE_MODEL`, same fallback pattern as memory extraction — should be vision-capable), each labeled with how many seconds before the most recent frame it was captured. The **first and most recent kept frames are also attached as actual screenshots** (downscaled per the screenshot settings), so the model can ground its reading in pixels — selection/highlight state on dialogue choices, who's speaking, layout — instead of guessing from OCR text alone; if the configured model rejects images, the pass retries once text-only — giving the model a short timeline instead of a single isolated snapshot, so it can better tell a transient UI flash from an actual state change. If every frame in the window got deduped away (nothing changed), the LLM pass is skipped entirely — this is what keeps the feature cheap. The pass also gets the current known-facts memory list (so it can recognize updates vs. duplicates, e.g. a level-up replacing an old level fact instead of stacking) and the **tracker list** (`app/core/game_state_trackers.py`, persisted to `data/game_state_trackers.json`) telling it exactly which fields to fill and what each one means — every process starts out with the same defaults (Current Activity, Location, Quest, Character, Recent Choice, Known Stats, Game Completion), fully editable/removable from Gaming Journal → Journal Settings → Trackers per approved process (e.g. swap them out for "1v1 Rank"/"Goals scored this session" in Rocket League), except **Current Activity**, which always stays and can't be edited or removed since the extraction pass depends on it being refreshed every time. Keyed like the training-data document (base process, or `process::variant` for a modpack's own list) so a pack's own pack-specific trackers (seeded by its bootstrap) never bleed into that same process's vanilla sessions or other packs — a fresh variant starts from the same defaults, not whatever the base process happens to be customized to. Previously-known field values are kept when a field isn't visible in a given window; the model can also explicitly retract a field value it now believes was a misread (Current Activity is the exception — always refreshed fresh, since it describes the current moment). Noteworthy events the pass spots — milestones, confirmed decisions, character details — go to the **observations journal** (see [Memory](#memory)), *not* directly into long-term memory; the conversation-side extraction pass promotes the ones that hold up.

**One-time game knowledge bootstrap** — the first time a brand-new game is tracked (and self-training is enabled or its trackers are still the untouched defaults), a background pass (`app/services/llm/game_knowledge_bootstrap.py`) gathers what it can about the game — an IGDB lookup, Steam's public store-search + appdetails (genres/categories/description, no API key needed, and often the only source with real coverage of a small/indie title; also pulls the game's achievement schema when `STEAM_API_KEY` is set — achievement text routinely spells out an obscure mechanic better than a generic search does), and a web search about its HUD/UI — then one LLM call seeds **game-specific trackers** (a ranked shooter gets rank/loadout fields instead of Quest/Character; never overwrites user-customized trackers) and a **starting training-data document**, so the extraction pass isn't staring at a blank document it never fills in. The document is split into two labeled sections: `## Lore` (a short grounding blurb — included only when the game is obscure enough that a capable model wouldn't already know it, e.g. a small indie title with no IGDB entry and thin web results; **always** included for a modpack/variant, scoped strictly to what the pack itself adds) and `## UI/UX` (always included, scoped to pack-added/changed elements only for a modpack/variant). When IGDB/Steam/web search all come up empty (a genuinely obscure title), the LLM call still runs rather than silently giving up — it may still recognize the game by name from its own knowledge and produce real trackers instead of leaving the process on generic RPG-flavored defaults forever. Retried once per app run if it fails.

**Memory retagging on first track** — a fact mentioned before a game was ever tracked this run (e.g. "I've got 41 hours in it" said while just chatting) has nowhere to land but `user` scope at save time — `remember()`'s degradation rule never re-tiers into a specific game tier on its own. The moment that game actually starts being tracked, a background pass (`app/services/llm/memory_retagging.py`) reviews the standing `user`-scope facts and moves any that turn out to be specifically about it into `game` or `session` scope, so they surface only while it's relevant instead of in every conversation. Runs once per process per app run.

The extraction pass itself has **no web search access** — it's optimized to run fast and often on a timer, so it writes its best plain reading from training data + known facts alone rather than pausing on a research round; the observation confirmation pass (below) is where research happens, on a much less frequent cadence.

**Proactive companion** *(opt-in, off by default — Gaming Journal → Journal Settings)* — with `PROACTIVE_MESSAGES_ENABLED` on, the extraction pass may also return a short `proactive_message` — a tip, a warning about something missed, a comment on a real milestone — which lands in the active chat as a normal unprompted assistant message (narrated aloud if narration is on), delivered through the same pending queue fired reminders use. It's encouraged to lean on the training data document to make the comment specific ("that's the Ashen Idol, it opens with a poison cloud") rather than generic ("careful, tough boss") — and to stay quiet rather than fake confidence when it doesn't actually know what's on screen. A hard cooldown (`PROACTIVE_MIN_INTERVAL_MINUTES`, default 15) caps how often it can speak up, and the field is only even offered to the model when a message would actually be allowed.

The structured snapshot is persisted per process (`app/core/game_state.py`, `data/game_state_sessions.json`), so tracked values (stats, progress, etc.) survive the game losing focus, being closed and reopened, or the companion itself restarting — only the "which process is actively showing right now" pointer is in-memory and resets on restart, not the values themselves. It gets injected into the system prompt on every chat request, right after the memory block. You can inspect what's currently being tracked from the **Game State** button in the sidebar, which also shows a live status dot (gray = disabled, yellow = enabled but idle, green = actively tracking).

**Game-state self-training** *(opt-in, off by default)* — since the extraction pass sees the actual screenshots itself, it maintains its own per-game **training data document**: a living reference combining UI-decoding notes (e.g. explaining that on this game's HUD, a string like `02122` means home score, minutes:seconds remaining, away score) and evergreen game-knowledge notes gathered via `web_search_query` (e.g. "the Ashen Idol is a mid-game boss known for a poison-cloud phase") — both feed into sharper observations and proactive comments later. When self-training is enabled (Gaming Journal → Journal Settings → Awareness), each extraction pass may return a revised version of the document alongside its normal output — only when the window actually taught it something new, revising existing notes in place rather than appending per-pass entries, and never describing a screenshot's momentary content — only things true in general about the game. Where the bootstrap-seeded `## Lore` / `## UI/UX` split exists, the extraction pass preserves it: `## Lore` rarely changes (only to fix something wrong), while nearly every real update grows `## UI/UX` with what it just confirmed on screen. Every future extraction pass for that process gets the current document in its prompt, so it leans on what it already figured out instead of re-guessing or re-searching. (There used to be a separate vision-capable "trainer" model re-examining low-confidence frames — that made sense only while the extraction pass itself was OCR-text-only and vision-blind; it's gone.) The extraction pass still self-reports a `confidence` score (0–1) covering both legibility and interpretation certainty, kept out of field values (the prompt forbids meta-commentary like "the text is unclear" from leaking into `activity` etc.). The training data document is per-process (`app/core/game_state_training_data.py`, persisted to `data/game_state_training_data.json`) and reviewable/editable directly (a plain textarea, saved on blur) from Gaming Journal → My Games → (pick a game) → Training data.

Because OCR accuracy on stylized/low-contrast in-game fonts varies a lot by game, this is best-effort — treat it as a nice-to-have ambient signal, not a guaranteed-accurate game-state tracker. If a game runs in exclusive fullscreen and captures come back consistently empty, try borderless/windowed mode — some exclusive-fullscreen/protected-content surfaces aren't capturable even via Windows Graphics Capture.

### Live mic / hands-free mode

The live mic (🎙️) continuously records into a rolling ring buffer (not just monitoring volume) and uses simple amplitude-threshold voice activity detection, with `autoGainControl` requested on the mic stream so quiet speech gets normalized before it even reaches the threshold check. When speech is detected, 1500ms of pre-roll audio from *before* the threshold was crossed is prepended so the first word isn't clipped (a fresh recorder starting only at detection time can't recover audio that already happened), and finalization waits an extra fixed 500ms past the configured silence threshold (post-roll) so a trailing word doesn't get cut off either. After that silence window, the utterance is finalized as a WAV — a quick local pass through Silero VAD (`@ricky0123/vad-web`, loaded from CDN) then discards it if it doesn't actually look like speech (coughs, claps, keyboard noise that passed the amplitude gate), before sending what's left straight to the LLM. Tuning (amplitude threshold, silence duration, minimum speech length, wake word) is in Settings → Live Mic, persisted server-side via `/api/config`.

While narrating, the live mic doesn't suppress itself — any loud sound is treated as the user interrupting ("barge-in"), cutting narration immediately and starting a new recording, so you can cut the companion off mid-sentence.

Once hands-free is off, optionally say the configured **wake word** (Settings → Live Mic, default "Hey Buddy") to turn it back on — a separate `SpeechRecognition` instance listens only while hands-free is off (so it never competes with the live mic's own capture), with a debug transcript panel in Settings to see what it's hearing and confirm detection works. Chrome/Edge only (Web Speech API).

**Listening Mode** (Settings → Live Mic, top of the tab) picks between two hands-free styles: **Hands-free** (default) keeps listening indefinitely once woken, with the sleep word available to end the session; **Single Command** arms listening for exactly one question after the wake word — the mic automatically turns itself back off the instant that utterance is actually sent to the LLM, so there's no need for a sleep word (its settings are hidden while this mode is selected, since a session that's never longer than one utterance has nothing for it to end). The "Edit overlay" phrase is unaffected by this setting either way — it's detected independently of hands-free state entirely (works with the mic on or off) and stays available in both modes. The mic toggle button's tooltip in the chat toolbar reflects whichever mode is currently selected.

### Narration

Replies are split into sentences as they stream in, and each sentence is sent to TTS and queued for playback as soon as it's ready — synthesis for the next sentence starts immediately on enqueue, overlapping with current playback, rather than waiting for the full reply before saying anything.

Three TTS backends are supported: **Kokoro** (local, free, fast — needs the Kokoro-FastAPI server running separately), **OpenRouter Speech** (any Speech-category model on OpenRouter, billed per character), and **Chirp 3 HD** (Google Cloud Text-to-Speech — highest quality, requires a Google Cloud project with the TTS API enabled and either an API key or ADC credentials). All three use the same sentence-streaming pipeline.

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
| `POST /api/chat/voice` | Send raw audio directly to an audio-capable model (no local STT). Non-streaming; returns full reply JSON. |
| `POST /api/chat/voice/stream` | Streaming (SSE) voice chat — same as `/voice` but streams `delta`/`transcript`/`stop_listening`/`done` events; used by the UI so the voice-reply transcript appears live. |
| `GET /api/proxy/image` | Server-side image proxy (`?url=...`). Fetches external images with browser-like headers to bypass hotlink protection — all SearXNG image results are routed through this. |
| `POST /api/chat/title` | Generate a short chat title from the first exchange. |
| `GET/PUT /api/config`, `DELETE /api/config/key/{field}` | Read/update all settings. The scoped `DELETE` immediately clears one stored secret (allowlisted key fields only) — powers the ✕ next to each API-key box, no full Save needed. |
| `GET/POST /api/memory`, `PUT/DELETE /api/memory/{id}`, `DELETE /api/memory?scope=…` | Memory CRUD (entries carry an explicit `scope`: user/game/session). The scoped `DELETE` bulk-clears every memory of the given scope(s) (repeatable `?scope=`; at least one required) — powers the "Delete all" buttons in the Personal Data / Gaming Journal modals. |
| `GET /api/gaming-journal` | Per-game rollup: title/cover art, game-scope memories, profiles (sessions) with their playthrough memories and unconfirmed observations. Powers the Gaming Journal "My Games" library grid + detail view. |
| `POST /api/game-art/{process}/fetch` | Fetches (or returns cached) cover art + title for a process — Steam store search, then IGDB, then SteamGridDB, then an LLM+web-search title lookup as a last resort. Cached to `data/game_art.json`. |
| `PUT /api/game-art/{process}/title` | User-corrected title override; never overwritten by a later auto-fetch. |
| `DELETE /api/observations/{id}` | Discard a single screen observation from the staging journal. |
| `GET/PUT /api/instructions` | Custom personal instructions, injected into every conversation. |
| `GET/PUT /api/chats` | Server-side chat session persistence (`data/chats.json`) — the sidebar's full chat list, replacing client-side `localStorage`. |
| `POST/GET/DELETE /api/voice/{id}` | Upload, fetch, or delete a voice message recording (`data/voice/{id}.wav`) — powers the in-chat voice player. |
| `GET /api/usage/records`, `DELETE /api/usage` | Per-call usage records (timestamp, source, tokens, cost) and clearing them. The Consumption view aggregates these client-side by time range and feature. |
| `GET /api/usage/balance` | OpenRouter account credit balance (requires `OPENROUTER_MANAGEMENT_KEY`; returns `available: false` otherwise). |
| `GET /api/debug/requests` | Last 10 individual LLM API calls (not persisted) - full messages, tool calls, tokens, cost, duration. Powers the Debug panel. |
| `GET /api/models/llm` `/tts` | Model lists for Settings dropdowns. |
| `GET /api/screenshot` | One-off screenshot capture. |
| `POST /api/tts` | Synthesize speech for arbitrary text. |
| `GET /api/game-state` | Current passive game-state snapshot including active session id/name — powers the floating Game State panel. |
| `GET/POST /api/game-state/sessions/{process}`, `PUT …/{session_id}/active`, `PATCH …/{session_id}`, `PUT …/{session_id}/variant`, `DELETE …/{session_id}` | Per-game profiles (playthroughs): list, create, switch active, rename, set/clear the modpack (variant) tag, delete. Deleting a profile also drops its session-scope memories and observations. Surfaced in the Gaming Journal modal (and the floating Game State panel). |
| `DELETE /api/game-state/games/{process}` | Remove a tracked game entirely — every profile, its trackers, training-data document, all game/session memories, observations, and its OCR whitelist approval (user-scope memories untouched). |
| `GET /api/game-state/pending` | Foreground processes currently awaiting allow/blacklist approval (a persisted queue, not just one) — powers the notification bell. |
| `GET/POST /api/game-state/whitelist`, `DELETE /api/game-state/whitelist/{process}` | Processes approved for game-state OCR. |
| `GET/POST /api/game-state/blacklist`, `DELETE /api/game-state/blacklist/{process}` | Processes the poller should never OCR. |
| `GET /api/sessions/{process}` | List all named sessions for a game process. |
| `POST /api/sessions/{process}` | Create a new session for a game process. |
| `PUT /api/sessions/{process}/{id}/active` | Switch the active session for a process. |
| `PATCH /api/sessions/{process}/{id}` | Rename a session. |
| `GET /api/backup/export` | Downloads a zip of `data/` (chats, memories, sessions, reminders, voice recordings, profile pictures) plus `.env` for moving to another machine. |
| `POST /api/backup/import` | Restores `data/` and `.env` from a zip produced by `/export`, overwriting current files. Restart required for imported settings to take effect. |

## Tools available to the agent

| Tool | What it does | Requires setup? |
|---|---|---|
| `save_user_memory` | Persist a fact about the user regardless of any game (name, preferences, life context). Always visible. | No |
| `save_game_memory` | Persist a fact about the currently tracked game that applies across all playthroughs (preferred class, how they approach the game). Visible only while that game is active. | No |
| `save_session_memory` | Persist a fact specific to the current playthrough (character level, quest progress, decisions this run). Only visible in the active session. | No |
| `remove_memory` | Forget a saved fact by id. | No |
| `rollback_session_memories` | Remove session-scoped memories saved within a recent time window — called after the player confirms a crash or loaded an older save, so lost progress doesn't contradict actual game state. | No |
| `correct_game_title` | Fix the currently tracked game's title when it's wrong (e.g. showing a raw process name like "javaw" instead of "Minecraft") — used when the user corrects it in conversation, instead of only saving it as a memory. Forces a training-data + tracker refresh under the corrected name (both are worse than none when seeded under the wrong one). | No |
| `correct_game_modpack` | Fix the CURRENT session's modpack/variant tag in place when it's wrong or missing (e.g. self-referentially tagged with the base game's own name, or missing a real pack like "FTB StoneBlock 4"). Forces a training-data + tracker refresh for the corrected pack; clearing to vanilla also deletes that session's modpack-specific playthrough memories, since they no longer apply. Use `switch_to_vanilla_session` instead when the player is just playing without the pack (not correcting this session's own tag). | No |
| `switch_to_vanilla_session` | Switch the active playthrough to a vanilla (no modpack) session without touching the current modpack session's tag or memories — for "I'm playing without the pack now," not a correction. Reuses an existing vanilla session if one exists, otherwise creates one; the modpack session stays exactly as it was and can be returned to later. | No |
| `rename_current_session` | Rename the current playthrough's profile (e.g. "first playthrough", "NG+"). Purely cosmetic — no effect on the tracked title, modpack tag, memories, or trackers. | No |
| `add_game_tracker` / `remove_game_tracker` / `update_game_tracker` | Add, remove, or rename/redescribe a tracked field for the currently tracked game (the same customization the Gaming Journal's Journal Settings → Trackers page exposes, reachable in conversation instead — e.g. "also keep an eye on my combo count"). Scoped to the current process + active modpack variant (or the base game if none is active), not the individual profile — shared across every playthrough of that same scope. The built-in "Current Activity" field can't be removed or edited. | No |
| `regenerate_game_trackers` | Regenerate the tracked fields for the currently tracked game (or its active modpack) from scratch via a fresh knowledge lookup — use when they've gone stale/generic (e.g. reverted to defaults) and the player asks for a fix/reroll. Leaves the training-data notes untouched; runs in the background. | No |
| `take_screenshot` | Capture a monitor (defaults to the active one). | No |
| `set_narration_volume` | Adjust its own TTS volume. | No |
| `stop_listening` | Disable hands-free mic indefinitely — on a sign-off, an explicit request, or unwanted overheard audio. Re-enable via the wake word or the mic toggle. | No |
| `fetch_system_info` | Check OS/CPU/RAM. | No |
| `web_search` | Search the web via OpenRouter's plugin or a self-hosted SearXNG instance (controlled by `WEB_SEARCH_PROVIDER`). | No (OpenRouter path billed via OpenRouter) |
| `show_image` | Find a real web picture and embed it inline (SearXNG image search; validates each candidate actually loads before handing the model a ready-to-paste `![alt](url)` line). SearXNG provider only. | No |
| `lookup_game_info` | IGDB game data (genre, platforms, release date, rating). | Twitch app credentials |
| `lookup_steam_game` | Steam store page details. | No |
| `fetch_steam_library` | User's owned games / playtime. | Steam API key + SteamID64 |
| `play_on_youtube` | Search YouTube Music (via `ytmusicapi`, no API key) for the official-audio track, or a plain YouTube search (via `yt-dlp`) for anything else, and play it in the app's own floating YouTube player (bottom-right, draggable) via the YouTube IFrame Player API — no browser tab is opened. | No |
| `control_youtube_player` | Play/pause/restart/skip/stop whatever's loaded in the in-app YouTube player. | No |
| `get_now_playing` | Check what's actually playing right now (title, channel, up next/previous) - the frontend pushes its queue/track state to the backend on every change (`app/core/media_state.py`), so this stays accurate even if the user skipped or picked something from the player's own UI without going through a tool call. | No |
| `play_on_spotify` | Search Spotify's catalog and start the track playing on whatever device the connected account is already active on (falls back to a `spotify:track:` deep-link into the local app if no device is active). | Spotify OAuth connection (Settings → API Keys → Spotify) + Spotify Premium for remote playback |
| `list_youtube_playlists` | List the names of the user's own YouTube playlists. | YouTube OAuth connection (Settings → API Keys → YouTube) |
| `play_youtube_playlist` | Fuzzy-match a playlist by name and queue every one of its videos into the in-app YouTube player. | YouTube OAuth connection (Settings → API Keys → YouTube) |

## Development

There's no build step for the frontend — edit the files under `web/js/app/`, `web/css/style.css`, or `web/index.html` directly and reload. The backend runs with `--reload`, so Python changes pick up automatically.

Prompts live in `app/prompts/*.md` as plain Markdown, loaded and cached at runtime (`app/core/prompts.py`) — edit them without touching Python code. `current_datetime_context()` is injected fresh on every request so the agent always knows the real current date/time, separate from the cached static prompt text. The currently focused application is injected the same way (it's only a few tokens), so the agent knows what's running without spending a tool round-trip on it.
