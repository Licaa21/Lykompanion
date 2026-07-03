# TODO

## Needs user verification

- [ ] **Overlay transparency fix, round 5 (untested)** — root cause finally found by reading pywebview 6.2.1's installed source: `transparent=True` never makes the WinForms form transparent (only the WebView2 control's background), so every exstyle attempt was fighting an opaque gray form; AND bare `WS_EX_LAYERED` (rounds 3–4) blocks a window from rendering at all until layered attributes are committed — that's why "nothing ever showed up". Round 5 replaces all of it with the standard color-key recipe: form `BackColor` == `TransparencyKey` == near-black, WinForms manages the layered state, box-shadows stripped from `overlay.html` (they'd render as opaque halos under color-key). Test: launch with `overlay_enabled` on → both widgets should be invisible until a reply/reminder toast fires or a game is tracked; Ctrl+Shift+O should show both widgets with a dashed outline, draggable. If STILL broken, next step is the GDI+/`UpdateLayeredWindow` rewrite (see CLAUDE.md transparency history — no more exstyle permutations).

## Ideas / nice-to-have

- [ ] **Overlay: images** — the overlay currently strips markdown images from replies; consider rendering images the companion sends (via `/api/proxy/image`) as overlay cards.
- [ ] **Overlay: multi-monitor** — the overlay window covers the primary monitor only; follow the game's monitor instead.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
