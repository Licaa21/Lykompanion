# TODO

## Game-state / memory redesign (analyzed 2026-07-02, not yet approved for implementation)

Root causes found: (1) scope silently degrades session→game in `memory_extraction.py:82-88`
when no session is resolvable (the "Ironclad run saved as game memory" bug); (2) the OCR pass
hard-codes every saved fact to session scope (`game_state_extraction.py:215-217`); (3) scope is
invisible downstream — both game- and session-scope facts render as `(game: X)` in prompts;
(4) the choice-certainty gate in `game_state_extraction.md` covers tracker fields only, while
`save_memories` examples prime dialogue-outcome saving from OCR that can't show selection state;
(5) `data.get(tid) or previous_values.get(tid)` makes wrong tracker values unclearable and
self-reinforcing; (6) `confidence` gates only training — low-confidence windows still write memory.

- [x] **Layer 1** — done 2026-07-02: explicit `scope` field (+ read-time migration), scope
  degradation goes to user (never cross-tier), choice-certainty gate extended to observations,
  tracker fields clearable (omitted key = keep previous, explicit null = clear).
- [x] **Layer 2** — done 2026-07-02: `memory.remember()` is the single write entry point; the
  OCR pass appends to a per-session observations journal (`app/core/observations.py`) instead
  of writing memory; the chat extraction pass promotes corroborated observations and clears
  handled ones. Frontend: new Gaming Journal modal (per-game memories, profiles with
  playthrough memories + observations; Game Awareness settings moved there from Settings);
  Personal Data modal now shows user-scope facts only. Possible follow-up: a periodic
  consolidation pass for long unattended play sessions (observations currently only get
  promoted when the user actually chats).
- [x] **Layer 3 (vision-grounded extraction)** — done 2026-07-02: the first and most recent
  kept frames of each poll window are attached to the extraction pass as screenshots (OCR still
  does free change-detection/dedup; middle frames stay text-only; non-vision models retried
  text-only). Follow-up also done 2026-07-02: the separate trainer model/pass is removed —
  the extraction pass now self-maintains the per-game training notes via a
  `training_data_update` output field, and the Settings game-state model dropdown fetches
  vision-capable models only (`/api/models/llm/vision`).

## Medium priority

- [ ] **More test coverage** — the core stores are covered (31 tests); still untested:
  `game_state` session migration/active-session logic, `reminder_tool`/`tools.py` execution
  paths, chat API tool loop (needs an LLM client fake).
- [ ] **Consider dropping `GET /api/screenshot`** — nothing in the frontend uses it (the
  screenshot tool captures server-side); removing it shrinks what a token holder can do.

## Frontend / UX

- [ ] **`app.js` is ~4,000 lines in one file.** Split into ES modules: chat, voice/VAD, settings,
  game-state panel, quick-setup, toasts.
- [ ] **Accessibility pass, part 2** — aria-labels are done; still open: `--t3` contrast check,
  screen-reader usability of the model `<select>`s, focus trapping in modals.

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
  relevance ranking, or periodic consolidation pass. Maybe think of using QDrant DB here with RAG retrieval?
- [ ] **Export/import** — one-click backup of `data/` (chats, memories, settings) for machine moves.
- [ ] **Persist debug log opt-in across restarts with size cap on disk** for post-mortem debugging.
