# TODO

- [ ] `play_on_spotify` needs to be properly tested.
- [ ] Add sort options to the Gaming Journal's My Games library grid: sort by last played, A-Z, date added, plus an ascending/descending toggle.

# Risky changes (do this in a separate branch and properly test before merging to main):

- [ ] Real TTS network streaming (audio starts playing before synthesis fully completes) was deliberately NOT implemented in the 2026-07-04 latency pass — it requires switching narration.js off fetch()+blob() to an `<audio src>`-based GET stream (plus a WAV-header trick or MSE for the PCM-wrapping providers), which touches the working narration/overlay-toast-timing/barge-in logic and can't be verified without running the app. Done instead: persistent HTTP client reuse per TTS call (removes per-sentence connection setup) — see kokoro.py/chirp3.py `_get_client()`. If ever revisited, read the reasoning in that session before starting.
