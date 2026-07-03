# TODO

## Needs user verification

- [ ] **Overlay transparency fix, round 3 (untested)** — went dark-tinted (WS_EX_LAYERED + SetLayeredWindowAttributes, wrong) → opaque white (dropped WS_EX_LAYERED entirely, also wrong) → now WS_EX_LAYERED alone with no legacy attribute/bitmap APIs, applied once at window `shown` before first paint (`_apply_overlay_base_styles` in `run_app.py`). This is the standard Windows 8+ technique for GPU-swap-chain-backed transparent windows and should be correct, but needs a real run to confirm the overlay is actually see-through now. Sizing fix (`SetProcessDPIAware()` at import time) is separate and still also unverified.

## Ideas / nice-to-have

- [ ] **Overlay: images** — the overlay currently strips markdown images from replies; consider rendering images the companion sends (via `/api/proxy/image`) as overlay cards.
- [ ] **Overlay: multi-monitor** — the overlay window covers the primary monitor only; follow the game's monitor instead.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
