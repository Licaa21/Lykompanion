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


- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
