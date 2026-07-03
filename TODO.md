# TODO

- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
- [ ] Updating the tickboxes for overlay and certain trackers dosen't take effect immediately live as it should, you need to restart application in order to make them reset or take effect.
  - Overlay enable/disable + per-tracker "show in overlay" now applied live (`game_state_extraction.apply_overlay_enabled` on config PUT; `_refresh_overlay_panel` on tracker PUT/reset). **Verify in-app**; if any *other* tickbox still needs a restart, note which here.
- [ ] When I tested the overlay in Baldur's Gate 3, the overlay/game state/OCR stuck and wouldn't update no more, could you debug this ?
  - Root cause found: WGC `capture.start()` blocks its worker thread forever if a frame never arrives (exclusive-fullscreen / protected content), and the poller awaits it via `asyncio.to_thread`, so the whole loop wedged permanently. Fixed: `start_free_threaded()` + 6s frame timeout in `wgc_capture.py`. **Verify in BG3** (use borderless/windowed — exclusive-fullscreen still isn't capturable, but now it degrades gracefully instead of hanging).
- [ ] In the gaming journal, we need a feature to add or delete the tracked games and game's profiles and switch between profiles.
- [x] Add a button to delete the entire memories or game journal memories and a pop-up "Are you sure" when deleting them — done ("Delete all" in Personal Data → `scope=user`; "Delete all journal memories" in Gaming Journal → `scope=game&session`; both via `DELETE /api/memory?scope=` behind a `confirm()`).
- [x] Add "Are you sure?" pop-ups to deleting chats — done (`confirm()` on the chat-list × button).
