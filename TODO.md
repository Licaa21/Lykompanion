# TODO — Improvement Opportunities

**Everything below is still OPEN.** Bugs found during the 2026-07-02 reviews were fixed and
committed immediately — they are NOT listed here. For the record, already fixed:

<details>
<summary>✅ Already fixed (2026-07-02, not TODOs)</summary>

Review round 1:
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

Lazy-chat-creation regressions:
- Fired reminders were silently destroyed while no chat was active (now opens a chat).
- Empty-state greeting said "What's up, You?" before the display name loaded.
- `var(--text-muted)` used but never defined; dead `.chat-welcome*` CSS removed.
- `/api/models/tts` 500'd on a transient OpenRouter failure, emptying every voice dropdown
  (likely the "chirp errors on start, refresh fixes it" report).

Review round 2 (the big TODO batch):
- Per-launch API auth token: run_app.py generates it, middleware rejects /api/* without it
  (header for fetch, query param for `<img>`/`<audio>` loads). Dev uvicorn without the env var
  runs unauthenticated as before.
- Image proxy hardened: http(s) only, private/loopback targets blocked (configured SearXNG host
  excepted), 10MB response cap.
- All JSON stores (memory, reminders, chats, usage) now lock their read-modify-write cycles.
- usage.json → append-only usage.jsonl with one-time migration (no more full rewrite per call).
- Per-chat save endpoints (`PUT/DELETE /api/chats/{id}`); frontend saves one chat per message
  instead of the entire history.
- Model catalogs cached server-side for 5 min; "Refresh Model Lists" bypasses with force=true.
- "Narrate replies" persisted in config (wizard saves it too).
- Wake-word matching is Unicode-aware (diacritics fold instead of being deleted).
- lifespan awaits cancelled pollers; voice history parse errors 400 instead of 500;
  vad_silence_ms schema/config drift fixed; .env values sanitized against newlines;
  tool-call plumbing deduplicated into one shared helper.
- SearXNG relative `/api/proxy/image` URLs now render as images (the https-only markdown
  pattern showed them as literal text) and are stripped from narration.
- Voice-player blob URLs revoked on chat switch/delete; streaming/voice error text persisted
  into chat history; aria-labels on all icon-only buttons; context-aware empty-state chip
  ("Catch me up on my <game> session") while a game is tracked.
- `tests/`: 31 pytest tests covering reminders due-logic, memory prompt filtering, tracker
  slugify/dedupe/reserved-keys, usage JSONL + migration, and .env persistence.

</details>

These are the remaining improvements, roughly by priority.

## Reported bugs

- [ ] **Chirp voices error on startup; "Refresh Model Lists" fixes it.** (Reported 2026-07-02.)
  Likely cause fixed already: `/api/models/tts` 500'd whenever the OpenRouter catalog fetch
  failed transiently, emptying ALL voice dropdowns — now degrades gracefully. If it still
  reproduces after that, capture the server log line on startup and dig into
  `chirp3.list_voices` auth timing.

## Medium priority

- [ ] **More test coverage** — the core stores are covered; still untested: `game_state` session
  migration/active-session logic, `reminder_tool`/`tools.py` execution paths, chat API tool loop
  (needs an LLM client fake).
- [ ] **Screenshot endpoint is still exposed to any token-holder page context** — consider
  dropping `GET /api/screenshot` entirely if nothing uses it (the tool path captures
  server-side).

## Frontend / UX

- [ ] **`app.js` is ~4,000 lines in one file.** Split into ES modules: chat, voice/VAD, settings,
  game-state panel, quick-setup, toasts.
- [ ] **Accessibility pass, part 2** — aria-labels are done; still open: `--t3` contrast check,
  screen-reader usability of the model `<select>`s, focus trapping in modals.
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
