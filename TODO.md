# TODO

- [ ] Spotify integration needs to be properly tested
- [ ] Modpack/variant system (2026-07-06) needs live testing: launch FTB StoneBlock 4 (javaw.exe → should retitle to Minecraft + create an "FTB StoneBlock 4" profile) and a Nolvus-style MO2 launch. Tune variant_detection.py confidence thresholds if it over/under-triggers.
- [ ] Gaming Journal's Training Data tab only shows/edits the base-game document — per-variant documents (training data keyed `process::variant`) have no UI yet.
- [ ] If OCR-poller memory still creeps after the 2026-07-06 leak fix (wgc_capture.py): each tick still leaks a small uncollectable WindowsCapture cycle + whatever native resources the Rust side pins. Real fix = stop per-tick session churn — either one persistent WGC session per monitor, or the `DxgiDuplicationSession` pull API the installed windows-capture package already exposes (one session, `acquire_frame()` per tick).

# Risky changes (do this in a separate branch and properly test before merging to main):

- [ ] Real TTS network streaming (audio starts playing before synthesis fully completes) was deliberately NOT implemented in the 2026-07-04 latency pass — it requires switching narration.js off fetch()+blob() to an `<audio src>`-based GET stream (plus a WAV-header trick or MSE for the PCM-wrapping providers), which touches the working narration/overlay-toast-timing/barge-in logic and can't be verified without running the app. Done instead: persistent HTTP client reuse per TTS call (removes per-sentence connection setup) — see kokoro.py/chirp3.py `_get_client()`. If ever revisited, read the reasoning in that session before starting.
