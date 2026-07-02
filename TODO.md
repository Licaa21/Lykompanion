# TODO

## Gaming Journal & Memory improvements:

- [ ] **Missing Game Knowledge** - Have the LLM fetch IGDB and search the web for a specific game whenever we start tracking it and generate correct trackers for it. Have it create it's own starting training data by also searching for UI images or other stuff like that. Currently, training data never actually gets written at all. Probably because the LLm sees is blank and thinks it shouldn't build anything there.
- [ ] **Uncomfirmed Observations** - While observations seem to be tracked correctly and are really relevant, They never seem to get stored as actual memories anymore. We need to run a confirmation pass whenever there are enough observations to analyze. The observations should be correctly sorted between the General memory, Game memories and playthrough memory. on each pass
- [ ] **RAG Retrieval** - Since memories will probably start overcrowding, especially playthrough memories, we should switch to a RAG retrieval system instead. The General memories should always be fed to the LLM. But for all game/game session memories, we should use rag retrieval instead.
- [ ] **Proactive LLM** - Give the LLM the possibility to talk to the user, making comments, giving tips, etc. This should not happen too often, but only when relevant. The user should also be able to control it by adding limitations (like setting a fixed interval for when this can happen or even disabling it alltogether). The best model to do this is probably the Game State model, since it's also seeing the game screen. Give it a tool to talk to the user. When that happens, we add it as a new message in the active chat as a normal interaction.
- [ ] **Smarter Game State Model** - We need to allow the Game State Model to think when saving observations or facts. Give it web search access so it can search game-specific stuff if something from the screenshots or the OCR is unknown to the model. E.g: Model sees "Lumina" in Claire Obscur, but has no idea what that word actually means in the game context.

## Random bugs:

- [ ] **Remove markdown syntaxes from Narration.** - I'm tired of the TTS saying "asterisk" over and over again. All markdown characters need to be completely stripped from the prompt before sending over to TTS.
- [ ] **LLM narrates image/picture links** - When the reply contains a markdown image or a bare image URL, the TTS reads out the full URL or alt text. Strip `![...](...)`  and bare image URLs from the narration text before sending to TTS, same pass as markdown cleanup.
- [ ] **TTS chirp / click on silence-to-speech transition** - There is a brief pop or click artifact at the very start of each TTS audio clip, audible when the audio goes from silence to the first spoken word. Likely a missing fade-in or a DC offset in the raw PCM. Needs investigation: try a short linear fade-in on the audio buffer before playback, or check if the TTS provider has a leading-silence / warmup option.
- [ ] **Hold my beer, I have a speech** - Sometimes the main LLM replies with a huge chunk of text, which takes over 30 seconds to narrate. This is bad. And annoying.

## Frontend / UX

- [ ] **`app.js` is ~4,000 lines in one file.** Split into ES modules: chat, voice/VAD, settings,
  game-state panel, quick-setup, toasts.
- [ ] **Accessibility pass, part 2** — aria-labels are done; still open: `--t3` contrast check,
  screen-reader usability of the model `<select>`s, focus trapping in modals.

## Ideas / nice-to-have

- [ ] **In-game overlay?** — surface the companion (game-state panel, incoming reminders,
  maybe a mini chat) as an overlay on top of the running game instead of a separate window.
- [ ] **Prune empty chat husks** — with lazy chat creation, a chat persisted mid-send that never
  got its message (e.g. app closed) lingers invisibly in chats.json forever.
- [ ] **Smarter game detection** — replace the hardcoded `NON_GAME_PROCESSES` denylist heuristic
  with signals like fullscreen/borderless window style, GPU usage, or Steam/IGDB process lists.
- [ ] **Game-state capture on multi-monitor** — the OCR poller captures one monitor; if the game
  runs on a secondary display it may OCR the wrong screen. Follow the foreground window's
  monitor instead.
- [ ] **Memory growth control** — all matching memories are injected into every prompt; add a cap,
  relevance ranking, or periodic consolidation pass. Maybe think of using QDrant DB here with RAG retrieval?
- [ ] **Export/import** — one-click backup of `data/` (chats, memories, settings) for machine moves.
## Random questions

- I think we're sending the main LLM all the previous voice messages too on each prompt, based on the context window setting. Aren't we bombarding the LLM with voice recording all the time, thus increasing costs significantly? Can we ask the LLM, that besides it's normal job, it should also feed us the exact phrase that the user said? We collect it and we update the chat message from recording -> Actual text. With no transcribe model needed at all. We also end up saving costs since we're only sending one voice input each prompt instead of 20, and we don't lose any context.
