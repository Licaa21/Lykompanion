# TODO

- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
- [ ] Updating the tickboxes for overlay and certain trackers dosen't take effect immediately live as it should, you need to restart application in order to make them reset or take effect.
  - Overlay enable/disable + per-tracker "show in overlay" now applied live (`game_state_extraction.apply_overlay_enabled` on config PUT; `_refresh_overlay_panel` on tracker PUT/reset). **Verify in-app**; if any *other* tickbox still needs a restart, note which here.
- [ ] When I tested the overlay in Baldur's Gate 3, the overlay/game state/OCR stuck and wouldn't update no more, could you debug this ?
- [ ] In the gaming journal, we need a feature to add or delete the tracked games and game's profiles and switch between profiles.
- [x] Add a button to delete the entire memories or game journal memories and a pop-up "Are you sure" when deleting them — done ("Delete all" in Personal Data → `scope=user`; "Delete all journal memories" in Gaming Journal → `scope=game&session`; both via `DELETE /api/memory?scope=` behind a `confirm()`).
- [x] Add "Are you sure?" pop-ups to deleting chats — done (`confirm()` on the chat-list × button).
