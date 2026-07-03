# TODO


## Ideas / nice-to-have

- [ ] **Overlay: images** — the overlay currently strips markdown images from replies; consider rendering images the companion sends (via `/api/proxy/image`) as overlay cards.
- [ ] **Overlay: multi-monitor** — the overlay window covers the primary monitor only; follow the game's monitor instead.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
