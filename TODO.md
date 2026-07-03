# TODO


## Ideas / nice-to-have

- [ ] **In-game overlay?** — surface the companion (game-state panel, incoming reminders,
  maybe a mini chat) as an overlay on top of the running game instead of a separate window.
  A full attempt was built and REVERTED on 2026-07-03 after 7 failed rounds — before retrying,
  read the overlay gotcha in CLAUDE.md: WebView2/pywebview windows cannot be made transparent
  or region-clipped, period; the viable paths are native GDI+/`UpdateLayeredWindow` rendering
  or the untested fit-to-content approach preserved in git history (`4957a50`).
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
