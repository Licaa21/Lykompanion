# TODO

## Needs user verification

- [ ] **Overlay round 6: SetWindowRgn clipping (untested)** — round 5 (color-key) got the closest yet: widgets appeared and edit UI flashed for a few ms, then went black — Chromium's GPU compositor can't render into a `WS_EX_LAYERED` window (documented WebView2 limitation), which rules out ALL transparency/alpha/color-key approaches. Round 6 drops transparency entirely: the native window is clipped to exactly its visible card rects via `SetWindowRgn` (`set_overlay_regions` bridge, `syncWindowRegion()` in `overlay.js`), empty region at launch (also fixes widgets showing at startup). Test: launch → nothing visible; Ctrl+Shift+O → both widgets appear (whole-window rect while editing), draggable; exit → invisible again; send a chat message → toast card appears alone, game shows/desktop shows everywhere else. Known cosmetic quirk: the toast slide-in animation may clip briefly at the top edge. If STILL broken, the only remaining path is the GDI+/`UpdateLayeredWindow` rewrite (software-rendered, exempt from the GPU limitation).

## Ideas / nice-to-have

- [ ] **Overlay: images** — the overlay currently strips markdown images from replies; consider rendering images the companion sends (via `/api/proxy/image`) as overlay cards.
- [ ] **Overlay: multi-monitor** — the overlay window covers the primary monitor only; follow the game's monitor instead.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
