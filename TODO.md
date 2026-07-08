# TODO

- [ ] Spotify integration needs to be properly tested
- [ ] Modpack/variant system (2026-07-06) still needs a Nolvus-style MO2 launch tested live (FTB StoneBlock 4/javaw.exe already confirmed working: retitled to Minecraft, "FTB StoneBlock 4" profile auto-created). Tune variant_detection.py confidence thresholds if it over/under-triggers.
- [ ] Games not sold on Steam (e.g. Minecraft Java) get no cover art with a fresh install — the zero-config source (Steam store search) can never match them, and both fallbacks (IGDB, SteamGridDB) need their own free API key configured in Settings/`.env` (`IGDB_CLIENT_ID`/`SECRET`, `STEAMGRIDDB_API_KEY`) to kick in. Not a bug to silently work around (no key = no art, by design) — just easy to forget why a game's card is blank.
- [ ] If OCR-poller memory still creeps after the 2026-07-06 leak fix (wgc_capture.py): each tick still leaks a small uncollectable WindowsCapture cycle + whatever native resources the Rust side pins. Real fix = stop per-tick session churn — either one persistent WGC session per monitor, or the `DxgiDuplicationSession` pull API the installed windows-capture package already exposes (one session, `acquire_frame()` per tick).

# Risky changes (do this in a separate branch and properly test before merging to main):

- [ ] Real TTS network streaming (audio starts playing before synthesis fully completes) was deliberately NOT implemented in the 2026-07-04 latency pass — it requires switching narration.js off fetch()+blob() to an `<audio src>`-based GET stream (plus a WAV-header trick or MSE for the PCM-wrapping providers), which touches the working narration/overlay-toast-timing/barge-in logic and can't be verified without running the app. Done instead: persistent HTTP client reuse per TTS call (removes per-sentence connection setup) — see kokoro.py/chirp3.py `_get_client()`. If ever revisited, read the reasoning in that session before starting.
