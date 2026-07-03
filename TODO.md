# TODO

- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
- [ ] Updating the tickboxes for overlay and certain trackers dosen't take effect immediately live as it should, you need to restart application in order to make them reset or take effect.
- [ ] When I tested the overlay in Baldur's Gate 3, the overlay/game state/OCR stuck and wouldn't update no more, could you debug this ?
- [ ] In the gaming journal, we need a feature to add or delete the tracked games and game's profiles and switch between profiles.
- [ ] **Clean up the machine's `PATH`** — it has a stray `"` after `C:\Program Files\Tailscale`, which makes `vcvars64.bat` (and anything sourcing it) abort with `"... was unexpected at this time."` `overlay/build.cmd` now strips quotes defensively, but the root cause is the corrupted user/system PATH env var.
- [ ] **Add `/utf-8` to the overlay compiler flags** (`overlay/build.cmd`) so non-ASCII string literals in `overlay.cpp` don't mojibake under MSVC's default codepage — would remove the need for `\x####` escapes (cf. the `Listening…`/`Loading image…` fix).
