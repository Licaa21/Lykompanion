# TODO

- [ ] `play_on_spotify` needs to be properly tested.
- [ ] New `GAME_STATE_VISUAL_DIFF_NOISE_FLOOR_PERCENT` (default `1.5`) needs real-world tuning against actual gameplay — use `tools/ocr_debug.py` to watch OCR-similarity and visual-diff numbers together while playing and confirm the default doesn't suppress genuine small HUD changes (e.g. HP ticking) or fail to filter OCR misread noise.
- [ ] Confirmed bug (2026-07-05, Slay the Spire): died at 18HP, sat on the death screen 2 minutes, tracker panel stayed frozen at "In combat, 18HP" the whole time - the death screen's OCR/pixels apparently read similar enough to the last combat frame that no pass ran. Root-caused as likely the noise-floor merge above discarding a real OCR-detected change because the visual diff (64px thumbnail) between combat and a similar-background death screen was small. Mitigated with `GAME_STATE_MAX_STALE_SECONDS` (safety net forcing a pass through after N idle seconds regardless), but the underlying suppression case itself hasn't been reproduced/verified fixed - watch for recurrence.


# Risky changes (do this in a separate branch and properly test before merging to main):

- [ ] Real TTS network streaming (audio starts playing before synthesis fully completes) was deliberately NOT implemented in the 2026-07-04 latency pass — it requires switching narration.js off fetch()+blob() to an `<audio src>`-based GET stream (plus a WAV-header trick or MSE for the PCM-wrapping providers), which touches the working narration/overlay-toast-timing/barge-in logic and can't be verified without running the app. Done instead: persistent HTTP client reuse per TTS call (removes per-sentence connection setup) — see kokoro.py/chirp3.py `_get_client()`. If ever revisited, read the reasoning in that session before starting.
