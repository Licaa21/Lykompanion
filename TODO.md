# TODO

- [ ] Spotify integration needs to be properly tested.

# Risky changes (do this in a separate branch and properly test before merging to main):

- [ ] Real TTS network streaming (audio starts playing before synthesis fully completes) was deliberately NOT implemented in the 2026-07-04 latency pass — it requires switching narration.js off fetch()+blob() to an `<audio src>`-based GET stream (plus a WAV-header trick or MSE for the PCM-wrapping providers), which touches the working narration/overlay-toast-timing/barge-in logic and can't be verified without running the app. Done instead: persistent HTTP client reuse per TTS call (removes per-sentence connection setup) — see kokoro.py/chirp3.py `_get_client()`. If ever revisited, read the reasoning in that session before starting.

# Game State improvements:
- [ ] (2026-07-05, needs in-game validation) Addressed all three known issues via prompt changes in `app/prompts/game_state_extraction.md` — no code changes needed since these were reasoning gaps, not logic bugs:
    * Flashback/other-character-POV confusion (e.g. Ciri's flashback during Geralt's Bloody Baron dialogue misattributed to Geralt): added a rule to recognize scene shifts (flashback/vision/playable-as-another-character) and attribute events to that character/scene, not the tracked player character.
    * Character misidentification (Keira mistaken for Yennefer, no on-screen name tag to go on): added a rule barring identity assertions from vibe/appearance inference alone — requires an actually-visible name or pinned-down known fact, otherwise use a generic descriptor + lower confidence.
    * Needless low-value observations clogging memory (routine loot, trash mobs, generic XP, NPC directions): added an explicit materiality bar + exclusion list; report plainly rather than guessing when unsure — the confirmation pass (below) now does the deciphering.
    Keep an eye on real sessions to confirm these hold up; revise wording if any of the three recur.