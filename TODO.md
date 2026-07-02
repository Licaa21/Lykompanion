# TODO

## Active bug hunt

- [ ] **Silent process death at voice-reply end** — the Python process dies with no traceback
  (cmd drops straight to `pause`) when a voice reply finishes while navigating Settings; the
  WebView2 window survives as a frozen zombie ("app hangs"). Signature of a native access
  violation (WGC / winrt OCR / pycaw / WebView2 COM). `faulthandler` now writes a C-level
  traceback to `data/crash_log.txt` on crash — reproduce once, then read that file.

## Frontend / UX

- [ ] **`app.js` is ~4,000 lines in one file.** Split into ES modules: chat, voice/VAD, settings,
  game-state panel, quick-setup, toasts. 

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
- [ ] **Export/import** — one-click backup of `data/` (chats, memories, settings) for machine moves.
- [ ] **More SFX** - Add a short "dot dot dot" sound effect after a user prompt is registered and sent to the LLM. Add sound effects for tool calls, like a plane sound effect when web search is used or a nice crescendo when a memory is saved or a descendo when it's deleted, etc.