# TODO

- [ ] `play_on_spotify` needs to be properly tested.
- [ ] New "Providers" per-model routing picker (Settings, next to LLM/Memory/Game-State model dropdowns) needs real-world verification: (1) confirm OpenRouter's `provider.only` actually accepts the `tag` values we store/send (e.g. `"deepinfra/base"`) rather than requiring bare provider slugs (e.g. `"deepinfra"`) — check via Debug panel / observed provider actually used after restricting to one provider; (2) confirm `max_price.prompt`/`max_price.completion` are interpreted as $ per million tokens (matching the picker's "$/M" fields) and not $ per token.
- [ ] New `GAME_STATE_VISUAL_DIFF_NOISE_FLOOR_PERCENT` (default `1.5`) needs real-world tuning against actual gameplay — use `tools/ocr_debug.py` to watch OCR-similarity and visual-diff numbers together while playing and confirm the default doesn't suppress genuine small HUD changes (e.g. HP ticking) or fail to filter OCR misread noise.


# Risky changes (do this in a separate branch and properly test before merging to main):

- [ ] Real TTS network streaming (audio starts playing before synthesis fully completes) was deliberately NOT implemented in the 2026-07-04 latency pass — it requires switching narration.js off fetch()+blob() to an `<audio src>`-based GET stream (plus a WAV-header trick or MSE for the PCM-wrapping providers), which touches the working narration/overlay-toast-timing/barge-in logic and can't be verified without running the app. Done instead: persistent HTTP client reuse per TTS call (removes per-sentence connection setup) — see kokoro.py/chirp3.py `_get_client()`. If ever revisited, read the reasoning in that session before starting.
