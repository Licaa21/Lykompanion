# TODO

## Needs user verification

- [ ] **Overlay transparency fix, round 4 (untested)** — three win32-flag attempts so far have all still shown a dark/opaque overlay instead of a transparent one (dark tint via `WS_EX_LAYERED`+`SetLayeredWindowAttributes` → opaque white via no `WS_EX_LAYERED` → dark tint again via `WS_EX_LAYERED` alone). Round 4 adds `DwmExtendFrameIntoClientArea` full-glass margins alongside bare `WS_EX_LAYERED` (`_apply_overlay_base_styles` in `run_app.py`). If this ALSO still isn't transparent, stop trying more exstyle permutations — see the "if win32 flag tweaking keeps failing" fallback plan in CLAUDE.md's in-game overlay section (small per-element windows instead of full-screen, or drop WebView2 for the overlay entirely in favor of raw GDI+/`UpdateLayeredWindow`). Sizing fix (`SetProcessDPIAware()` at import time, unrelated to transparency) still also unverified but much more likely to already be correct. Test findings: Overlay still not transparent, unusable. Nothing ever shows up in the overlays.

## Ideas / nice-to-have

- [ ] **Overlay: images** — the overlay currently strips markdown images from replies; consider rendering images the companion sends (via `/api/proxy/image`) as overlay cards.
- [ ] **Overlay: multi-monitor** — the overlay window covers the primary monitor only; follow the game's monitor instead.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
