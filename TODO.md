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

  ### C++ native overlay — sprint plan
  Standalone C++ executable (`overlay/` dir at repo root), spawned/killed by the Python side as a child process. Toolchain: single `overlay.cpp` + `build.cmd` (MSVC `cl.exe` from VS Build Tools; no CMake/vcpkg — link user32/gdi32/d2d1/dwrite only). Data in: newline-delimited JSON on stdin (Python writes toast/game-state/edit-mode commands); data out: position saves as JSON lines on stdout. No sockets, no shared memory — a dead parent pipe = overlay exits itself.
  - [ ] Sprint C1: `overlay/overlay.cpp` skeleton — window class + `WS_EX_TOPMOST|WS_EX_LAYERED|WS_EX_TRANSPARENT|WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW` popup, Direct2D `DCRenderTarget` drawing into a 32bpp PARGB HBITMAP, `UpdateLayeredWindow` per frame, DirectWrite text. Acceptance: `build.cmd` compiles, running `overlay.exe --demo` shows a rounded dark card with crisp text over the desktop for 10s — desktop visible around it, not a black box.
  - [ ] Sprint C2: stdin command protocol — `{"type":"toast","text":...,"kind":"reply"|"reminder"}`, `{"type":"game_state","title":...,"rows":[[label,value],...]}`, `{"type":"edit_mode","enabled":bool}`, `{"type":"quit"}`; toast stacking, word wrap (DirectWrite layout), expiry timers, auto-sized windows anchored to configurable corners.
  - [ ] Sprint C3: edit mode in the exe — lift `WS_EX_TRANSPARENT`, dashed outline + hint, drag both widgets, emit `{"type":"layout","widget":...,"x":...,"y":...}` on stdout on release; Ctrl+Shift+O `RegisterHotKey` INSIDE the exe (its own message loop already runs).
  - [ ] Sprint C4: Python integration — `app/services/overlay_process.py`: spawn `overlay/overlay.exe` when game-state tracking starts, kill when it stops (and on app exit); feed it chat replies (re-add the small in-process publish hook in the chat endpoints) + reminders + game-state updates; persist layouts per game (re-add `app/core/overlay_layouts.py`); `overlay_enabled` setting back in Settings UI.
  - [ ] Sprint C5: ship a prebuilt `overlay/overlay.exe` in the repo (plus `build.cmd` to rebuild), so users without VS Build Tools still get the feature; graceful no-op if the exe is missing.

- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
