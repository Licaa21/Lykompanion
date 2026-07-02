# TODO — Improvement Opportunities

**Everything below is still OPEN.** Bugs found during the 2026-07-02 review were fixed and
committed immediately — they are NOT listed here. For the record, already fixed:

<details>
<summary>✅ Already fixed (2026-07-02, not TODOs)</summary>

- Alarms never fired (naive vs aware datetime crash); malformed timestamps stalled all reminders.
- LLM client cache cleared itself when two providers were configured.
- Streaming tool-loop exhaustion ended the stream silently (empty bubble).
- Server bound to 0.0.0.0 (LAN-exposed, no auth) → now 127.0.0.1 only.
- Voice messages lost from chat history when the request errored.
- Narration TTS queue leaked one blob URL per spoken sentence.
- Background pollers threw unhandled rejections on any server hiccup.
- Voice replies waited for the full response before narrating → now sentence-pipelined.
- Prompt audit: stale `game_specific` schema in memory_extraction.md (broke scope tagging);
  memory extraction now told which game is tracked; active reminders/alarms now injected into
  the system prompt (model can list/remove them); screenshots now placed next to the newest
  message instead of before all history; reserved-key collision guard for custom tracker ids;
  training-data document now has a compactness rule; dead narration.md prompt removed.

</details>

These are the remaining improvements, roughly by priority.

## Reported bugs

- [ ] **Chirp voices error on startup; "Refresh Model Lists" fixes it.** (Reported 2026-07-02.)
  Likely cause fixed already: `/api/models/tts` 500'd whenever the OpenRouter catalog fetch
  failed transiently, emptying ALL voice dropdowns — now degrades gracefully. If it still
  reproduces after that, capture the server log line on startup and dig into
  `chirp3.list_voices` auth timing.

## High priority

- [ ] **API authentication / local hardening.** The server now binds to `127.0.0.1` only, but any
  process on the machine can still hit the API (read chats, take desktop screenshots via
  `GET /api/screenshot`, rewrite settings). Generate a random session token at launch, have the
  webview pass it as a header, and reject requests without it.
- [ ] **Image proxy is an open relay.** `GET /api/proxy/image?url=...` fetches any URL server-side
  (SSRF). Restrict to `http(s)`, block private/loopback IP ranges, and cap response size.
- [ ] **Memory/chat/usage files race under concurrency.** `memory.json`, `reminders.json`, etc. use
  read-modify-write with no locking, and parallel tool calls (`asyncio.gather` in
  `_run_tool_calls`) can save concurrently — two simultaneous `save_*_memory` calls can lose one
  entry. Add an `asyncio.Lock` per store (or funnel writes through one writer).
- [ ] **`usage.json` grows unbounded and is rewritten on every LLM call** (parse + full rewrite per
  request — O(n²) over time). Switch to append-only JSONL (or SQLite) with rotation.
- [ ] **No tests.** `tests/` is empty. Start with pure-logic core modules: `reminders.due_entries`
  (naive/aware datetimes), `memory.format_memories_for_prompt` filtering, `game_state` session
  migration, `game_state_trackers.set_trackers` dedupe/slugify.

## Medium priority

- [ ] **"Narrate replies" checkbox isn't persisted** — resets to checked on every restart. Move it
  into `/api/config` (or localStorage) like every other setting.
- [ ] **Whole chat history is PUT to `/api/chats` on every message** — payload grows with total
  history across all chats. Add per-chat endpoints (`PUT /api/chats/{id}`) or incremental saves.
- [ ] **Model catalogs re-fetched from OpenRouter on every Settings open** (slow modal). Cache
  server-side with a short TTL; keep "Refresh Model Lists" as the manual bypass.
- [ ] **Wake-word matching strips everything but `[a-z0-9]`** — a wake phrase with diacritics
  (e.g. Romanian) can never match. Use a Unicode-aware normalization (NFKD + strip combining
  marks) instead.
- [ ] **`lifespan` cancels the pollers but never awaits them** — add
  `await asyncio.gather(poller_task, reminder_task, return_exceptions=True)` after cancel so
  shutdown doesn't leak "Task was destroyed but it is pending" warnings.
- [ ] **Voice `history` form field parse errors return 500** — `json.loads(history)` /
  `ChatMessage.model_validate` in `/api/chat/voice*` should 400 on malformed input.
- [ ] **Schema/config default drift.** `CompanionConfig.vad_silence_ms` defaults to 1200 while
  `Settings.vad_silence_ms` defaults to 3000. Single-source the defaults.
- [ ] **Duplicated tool-call plumbing** — `_run_tool_calls` and the inline block in
  `_stream_chat_with_tools` parse/execute/append identically. Extract one shared helper.
- [ ] **`persist_env_values` doesn't escape values** — a value containing a newline would corrupt
  `.env`. Sanitize or quote.

## Frontend / UX

- [ ] **`app.js` is ~3,800 lines in one file.** Split into ES modules: chat, voice/VAD, settings,
  game-state panel, quick-setup, toasts.
- [ ] **Accessibility pass** — icon-only buttons (mic, hands-free, screenshot, stop) need
  `aria-label`s; check `--t3` text contrast; the model `<select>`s are hard to use with a
  screen reader.
- [ ] **Voice-player blob URLs are never revoked** (one per rendered voice bubble). Revoke on
  chat switch/delete.
- [ ] **Streaming error text isn't persisted** — if `/api/chat/stream` fails mid-reply the error
  shows in the bubble but vanishes on chat switch. Persist a marker so users understand the gap.
- [ ] **Empty-state suggestion chips could be context-aware** — e.g. surface "What should I know
  about <current game>?" while a game is tracked.
- [ ] **i18n** — all UI strings are hard-coded English.

## Ideas / nice-to-have

- [ ] **In-game overlay?** — surface the companion (game-state panel, incoming reminders,
  maybe a mini chat) as an overlay on top of the running game instead of a separate window.
- [ ] **Prune empty chat husks** — with lazy chat creation, a chat persisted mid-send that never
  got its message (e.g. app closed) lingers invisibly in chats.json forever.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
- [ ] **Game-state capture on multi-monitor** — the OCR poller captures one monitor; if the game
  runs on a secondary display it may OCR the wrong screen. Follow the foreground window's
  monitor instead.
- [ ] **Memory growth control** — all matching memories are injected into every prompt; add a cap,
  relevance ranking, or periodic consolidation pass.
- [ ] **Export/import** — one-click backup of `data/` (chats, memories, settings) for machine moves.
- [ ] **Persist debug log opt-in across restarts with size cap on disk** for post-mortem debugging.
