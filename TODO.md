# TODO


## Ideas / nice-to-have

- [ ] **In-game overlay?** — surface the companion (game-state panel, incoming reminders,
  maybe a mini chat) as an overlay on top of the running game instead of a separate window.
  A full attempt was built and REVERTED on 2026-07-03 after 7 failed rounds — before retrying,
  read the overlay gotcha in CLAUDE.md: WebView2/pywebview windows cannot be made transparent
  or region-clipped, period;
  Next approach to tackle:
  Native C++ overlay is the right direction for low-latency, GPU-accelerated rendering and reliable per-frame control. Rendering approaches (ordered by recommended path for safety & compatibility):
  Layered window + UpdateLayeredWindow (software-backed per-pixel alpha): simplest to prototype, avoids GPU-compositor issues noted in CLAUDE.md. Render to an HBITMAP (e.g., via Direct2D DCRenderTarget or GDI) and call UpdateLayeredWindow each frame. Works well for borderless/windowed games; exclusive fullscreen may not show but that's okay. Most games nowadays are meant to be played borderless.
  
  Input handling: create the overlay window with WS_EX_TOPMOST | WS_EX_LAYERED and use WS_EX_TRANSPARENT + SetWindowLong toggles to let clicks pass through. On CTRL+SHIFT+O (configurable by user), we disable clickthrough and allow the user to move overlay elements, saving the new positions.

  Lykompanion should be able to start/kill the overlay accordingly (start when game state is active, kill when not active). The need to manually start/kill the overlay should never be a problem.

  Sprint plan (small committed steps — Python ctypes, not C++: same UpdateLayeredWindow technique, no build step):
  - [x] Sprint 1: `overlay_native.py` — layered window skeleton, message-loop thread, GDI+ PARGB rendering, rounded card + text helpers. **User test: `.venv\Scripts\python.exe overlay_native.py` → dark card top-right over the desktop for 10s, crisp text, NOT a black box.**
  - [ ] Sprint 2: toast rendering — text wrap/measure (GdipMeasureString), tag line, stacked toasts, expiry timers, window auto-sized to content.
  - [ ] Sprint 3: game-state panel rendering (title + tracker label/value rows).
  - [ ] Sprint 4: re-add `app/core/events.py` bus + publishers (chat endpoints final reply, reminders) and consume from the overlay.
  - [ ] Sprint 5: wire into `run_app.py` — `overlay_enabled` setting back; start overlay when game-state tracking starts, kill when it stops.
  - [ ] Sprint 6: Ctrl+Shift+O edit mode — click-through lift, drag to move, per-game position persistence (re-add `overlay_layouts`).


- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
