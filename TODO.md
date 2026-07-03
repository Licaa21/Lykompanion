# TODO


## Ideas / nice-to-have

- [ ] **Native C++ overlay (layered window, Direct2D — NOT GDI, NOT injection)** — surface the
  companion (game-state panel, incoming reminders, chat toasts) on top of the running game.
  Prior WebView2/pywebview attempt REVERTED 2026-07-03 after 7 failed rounds — read the overlay
  gotcha in CLAUDE.md before touching this.

  **Decision (2026-07-03):** standalone C++ **layered window**, drawn with **Direct2D**
  (`ID2D1DCRenderTarget`) into a 32bpp premultiplied-ARGB DIB, committed each frame via
  `UpdateLayeredWindow`. Explicitly **NOT GDI drawing** (gdi32 is used only for the unavoidable
  `CreateDIBSection`/memory-DC plumbing that `UpdateLayeredWindow` requires — every pixel is
  painted by Direct2D/DirectWrite, no `TextOut`/`FillRect`/GDI+). Explicitly **NOT DLL injection /
  Present-hooking** — zero anti-cheat risk, no per-graphics-API hooking. Software-composited layered
  windows are exempt from the Chromium GPU-compositor limitation that killed the WebView2 approach.
  Works for borderless/windowed games (the common case); exclusive fullscreen won't show it — accepted.

  **Hard boundary:** ALL overlay logic is native C++ — window management, Direct2D/DirectWrite
  rendering, input/edit-mode, hotkeys, widget layout AND its persistence. **No overlay behavior is
  ever written in Python.** Lykompanion (the Python app) is nothing but a *client* that pushes
  content in over an API; it must be possible to run/test the overlay standalone with zero Python
  involved.

  **Shape:** standalone, self-contained C++ exe in `overlay/` at repo root (`overlay.cpp` +
  `build.cmd`, MSVC `cl.exe`, no CMake/vcpkg — link `user32 gdi32 d2d1 dwrite`). Runs its own Win32
  message loop. It **hosts its own local API** (a named-pipe or loopback-TCP JSON server, C++ side is
  the server) that any process can connect to and push JSON commands into — Lykompanion is just one
  such client. The overlay owns and persists its own widget layouts on disk; Python never touches
  layout files. API (client → overlay): `toast`, `game_state`, `edit_mode`, `quit`. The only thing
  Python does beyond posting content is process lifecycle: launch the exe when a game starts, ask it
  to quit when tracking stops.

  - [x] **Sprint C1 — window + render skeleton.** DONE (verified on-machine 2026-07-03).
        `overlay/overlay.cpp`: register a
    `WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW` popup
    (no taskbar entry, click-through, never steals focus). `ID2D1DCRenderTarget` bound to a memory
    DC holding a top-down 32bpp PARGB `CreateDIBSection`; draw a rounded dark card + DirectWrite
    text; commit with `UpdateLayeredWindow` (`AC_SRC_ALPHA` blend). `build.cmd` compiles a single TU.
    **Acceptance:** `overlay.exe --demo` shows a rounded translucent card with crisp anti-aliased
    text over the desktop for 10s — desktop visible around/through it, not a black box, no flicker.
  - [ ] **Sprint C2 — local API server + widgets (all C++).** Overlay hosts a named-pipe (or
    loopback-TCP) JSON server on its own thread and marshals commands onto the UI thread. Command
    schema: `{"type":"toast","text":...,"kind":"reply"|"reminder"}`,
    `{"type":"game_state","title":...,"rows":[[label,value],...]}`,
    `{"type":"edit_mode","enabled":bool}`, `{"type":"quit"}`. Toast stacking with expiry timers,
    DirectWrite word-wrap + auto-sized windows anchored to a configurable screen corner. One layered
    window per widget (toast stack + game-state panel) is simplest; revisit if perf demands merging.
    **Acceptance:** a trivial non-Python client (e.g. a `.cmd`/`echo` into the pipe) drives toasts
    and the game-state panel — proving the overlay is fully standalone.
  - [ ] **Sprint C3 — edit mode + layout persistence (all C++).** On `edit_mode:true`, drop
    `WS_EX_TRANSPARENT` so widgets take the mouse; draw a dashed outline + hint; drag either widget;
    the overlay **persists positions to its own config file** (e.g. `overlay/layouts.json` next to the
    exe or in `%LOCALAPPDATA%`) — Python is not involved. `RegisterHotKey` for Ctrl+Shift+O
    (configurable) INSIDE the exe to toggle edit mode; its message loop is already running.
  - [ ] **Sprint C4 — Python as a thin client.** `app/services/overlay_process.py` does only two
    things: (1) process lifecycle — launch `overlay/overlay.exe` when game-state tracking starts, ask
    it to quit when tracking stops and on app exit; (2) push content — connect to the overlay's API
    and post `toast`/`game_state` JSON (re-add the small in-process publish hook in the chat endpoints
    for replies + reminders + game-state updates). No layout files, no rendering, no widget logic on
    the Python side. Restore the `overlay_enabled` setting in the Settings UI. (Note: stale
    `data/overlay_layouts.json` + `app/core/overlay_layouts.py` from the reverted attempt should be
    deleted — layouts now live entirely in the C++ overlay.)
  - [ ] **Sprint C5 — ship prebuilt exe.** Commit a prebuilt `overlay/overlay.exe` (plus `build.cmd`
    to rebuild) so users without VS Build Tools get the feature; graceful no-op if the exe is missing.

  **Anti-cheat note:** layered windows compose entirely outside the game process (no injection, no
  memory/API hooking), so BattlEye/EAC have nothing to flag — the trade-off vs. Present-hooking is
  no exclusive-fullscreen support and no perfect per-object z-order, both accepted here.

- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
