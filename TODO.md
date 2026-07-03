# TODO

## Needs user verification

- [ ] **Overlay round 7: fit-to-content window sizing (untested)** — round 6's `SetWindowRgn` was proven visually inert by the diagnostics (every call succeeded, windows stayed visible black boxes — WebView2's DirectComposition content ignores GDI window regions; the user's "useless" Ctrl+Shift+O presses actually toggled edit mode fine in the log). Round 7 resizes the native window to exactly its visible content via the `fit_overlay` bridge: empty → 1px sliver, toast → the window IS the card (one solid gapless stack card), edit mode → full bounds as an opaque panel. Known cosmetics: ~1s black-box flash per widget at launch (until the first fit), and toasts no longer slide vertically (opacity fade only). Test: launch → widgets vanish within ~1s; Ctrl+Shift+O → both widgets appear as solid dark panels with dashed outlines, draggable, positions save on release; send a chat message → a single toast card appears bottom-anchored, expires cleanly. **Copy the `[overlay]` console lines whatever happens.** If THIS also fails: GDI+/`UpdateLayeredWindow` rewrite, no more WebView2 overlay iterations.

## Ideas / nice-to-have

- [ ] **Overlay: images** — the overlay currently strips markdown images from replies; consider rendering images the companion sends (via `/api/proxy/image`) as overlay cards.
- [ ] **Overlay: multi-monitor** — the overlay window covers the primary monitor only; follow the game's monitor instead.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
