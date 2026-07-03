# TODO

## Needs user verification

- [ ] **Overlay round 6b: Form.Region clipping + diagnostics (untested)** — round 6a's raw-`SetWindowRgn` empty startup region didn't stick (widgets still visible as black boxes at launch); 6b applies regions via the WinForms `Form.Region` property on the UI thread (`Control.BeginInvoke`) so WinForms can't re-assert a null region over it, and every native step now logs `[overlay] ...` lines to the console. Test: launch → nothing visible; Ctrl+Shift+O → both widgets appear as deliberate dark panels with dashed outlines, draggable; exit → invisible; send a chat message → toast card appears alone over the desktop/game. **If anything is still wrong, copy the `[overlay]` console lines — they say exactly which step failed.** If regions fundamentally can't work either, the only remaining path is the GDI+/`UpdateLayeredWindow` rewrite (software-rendered, exempt from the WebView2 GPU limitation).

## Ideas / nice-to-have

- [ ] **Overlay: images** — the overlay currently strips markdown images from replies; consider rendering images the companion sends (via `/api/proxy/image`) as overlay cards.
- [ ] **Overlay: multi-monitor** — the overlay window covers the primary monitor only; follow the game's monitor instead.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
