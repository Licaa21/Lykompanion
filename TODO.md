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


# Youtube Player fixes/improvements (2026-07-05, needs in-app validation):

- [x] Play/pause icon already reflected player state (`ytPlayIcon`/`ytPauseIcon` toggled every 500ms poll) - no change needed, item was stale.
- [x] Quality-select change now forces an immediate apply: `setPlaybackQuality` alone only affects future buffering, so the handler (youtube-player.js + player-window.js) now also re-loads/re-cues the current video at its current time with the pick as `suggestedQuality`.
- [x] Fullscreen playlist/search/quality dropdown fix - two separate real bugs, not a direction problem: (1) `.cs-panel` (quality dropdown) had z-index 650, rendering *behind* the fullscreen video's z-index 10500 - bumped to 10600. (2) `#youtube-player-playlists`/`#youtube-player-browse` (the list panels) were unconditionally `display:none` in fullscreen - now shown as an absolutely-positioned overlay anchored just above the controls bar (opens upward over the video).
- [x] Volume slider visual fill wasn't updated on programmatic `set_volume` (voice/tool-call path) - only on manual slider drag. Added the missing `ytSyncVolumeFill()` call.
- [x] Quality dropdown was too narrow (66px, matched by its cs-panel) to fit "2160p" etc - widened `.youtube-player-quality-group` to 88px.
- [x] Removed the floating/"undocked" state entirely per user decision - the player is now always docked above the chat log; the only way out of that layout is popping out to a real movable window (native pywebview window or Document PiP). Deleted the pin/dock toggle button, drag-to-move and resize-grip handlers, and their localStorage keys.
- [ ] Playback stopping when popping out to the **native** window (desktop app path) is inherent to that hand-off design, not a bug: it's a second WebView2 window with its own separate IFrame API player instance, so there's no way to carry a live decode across without stopping one and starting the other. The Document-PiP path (browser dev mode) is already seamless since it just reparents the same iframe. Not worth chasing further without a much bigger redesign.