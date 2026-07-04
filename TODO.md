# TODO

- [ ] `play_on_spotify` needs to be properly tested.
- [ ] Expand the "Include Screenshot" button: Rename it to "Send Image". When clicked, show a modal allowing the user to browse locally for an image or drag and drop an image file inside it. Pasting an image crom clipboard should also work. Add a hint in the modal -> "You can always ask the companion to take a screenshot of your active screen if you want to quickly show something." Reword it to sound proffesional.
- [ ] Design/build a proactive-nudge feature: companion notices something worth pointing out on screen (via the OCR poller's existing screenshot pass) and asks "Can I show you something?" through an overlay toast; a "yes" answered through the normal voice pipeline should make it elaborate using what the poller saw. Note it can only react to what's visible in a screenshot/OCR, not true off-screen world-space location.

# Risky changes (do this in a separate branch and properly test before merging to main):

- [ ] Real TTS network streaming (audio starts playing before synthesis fully completes) was deliberately NOT implemented in the 2026-07-04 latency pass — it requires switching narration.js off fetch()+blob() to an `<audio src>`-based GET stream (plus a WAV-header trick or MSE for the PCM-wrapping providers), which touches the working narration/overlay-toast-timing/barge-in logic and can't be verified without running the app. Done instead: persistent HTTP client reuse per TTS call (removes per-sentence connection setup) — see kokoro.py/chirp3.py `_get_client()`. If ever revisited, read the reasoning in that session before starting.
