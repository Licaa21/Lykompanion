# TODO

## Needs user verification

- [ ] **Overlay transparency/sizing fix (untested)** — fixed the overlay rendering as an opaque dark rectangle (was setting `WS_EX_LAYERED` for click-through, which breaks WebView2's own per-pixel transparency) and not covering the full screen on scaled displays (`SetProcessDPIAware()` now called at `run_app.py` import time, before window sizing). Needs a real run to confirm both are actually fixed.

## Ideas / nice-to-have

- [ ] **Overlay: images** — the overlay currently strips markdown images from replies; consider rendering images the companion sends (via `/api/proxy/image`) as overlay cards.
- [ ] **Overlay: multi-monitor** — the overlay window covers the primary monitor only; follow the game's monitor instead.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
