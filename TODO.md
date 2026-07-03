# TODO

## Needs user verification

- [ ] **Overlay round 6c: raw SetWindowRgn + error logging (untested)** — 6b's `Form.Region` property hung the app at boot (second WinForms composition property confirmed to deadlock under WebView2, after `TransparencyKey`; rule now in CLAUDE.md: raw win32 only). 6c goes back to raw `SetWindowRgn`, now logging return value + `GetLastError` on every call. Test: launch → nothing visible, console shows `[overlay] startup empty region applied`; Ctrl+Shift+O → both widgets appear as dark panels with dashed outlines, draggable; send a chat message → toast card appears alone. **Copy the `[overlay]` console lines whatever happens.** Decision rule: if the log shows regions applying successfully but widgets are still visible/black, WebView2's compositor ignores window regions → STOP iterating WebView2, do the GDI+/`UpdateLayeredWindow` rewrite.

## Ideas / nice-to-have

- [ ] **Overlay: images** — the overlay currently strips markdown images from replies; consider rendering images the companion sends (via `/api/proxy/image`) as overlay cards.
- [ ] **Overlay: multi-monitor** — the overlay window covers the primary monitor only; follow the game's monitor instead.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
