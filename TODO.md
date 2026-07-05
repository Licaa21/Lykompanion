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
- [x] (2026-07-05) Removed the game-state extraction pass's own web search (`web_search_query` + the research round in `app/services/llm/game_state_extraction.py`) — it added latency to a pass that runs every poll window. Research responsibility now lives entirely in the observation confirmation pass instead, which runs far less often and can afford it (see below).
- [x] (2026-07-05) Reinforced the observation confirmation pass (`app/services/llm/observation_confirmation.py` + its prompt): it's no longer capped at one web search per review — it can chain up to `MAX_SEARCH_ROUNDS` (4) searches in the same pass to actually run down what an unfamiliar name/quest/context turns out to be, and it's now also fed the per-game training data document (UI-decoding + game-knowledge notes) alongside known facts and the game snapshot, so it has real context to reason from instead of just the bare observation text.


# Training Data improvements:
Here's what's currently getting collected in the training data. Analyze, identify key issues, improve prompts in order to make the auto-training better. 2 examples:

Example 1 - Witcher 3:
***
    # The Witcher 3: Wild Hunt - UI Reference

    ## Overview
    The Witcher 3 is an action RPG with a HUD during gameplay and detailed character screens. The UI provides health, stamina, and combat status.

    ## HUD Elements
    - Vitality Bar (Red): Health indicator. When depleted, player dies. Located typically at the bottom-left or top-left.
    - Stamina Bar (Yellow/White): Used for actions like sprinting, casting Signs, and parrying. Depletes during use.
    - Adrenaline Points: Fills during combat. Each point increases attack damage by 10%. Represented by icons or a bar.
    - Toxicity Level (Green/White Bar): Indicates potion intoxication. High toxicity can cause health loss.
    - Enemy Health Bars: Color-coded: Red for human enemies (use Steel Sword), White for monsters (use Silver Sword).

    ## Stats Screen (Character Panel)
    Accessed via Inventory. Shows detailed build stats:
    - Weapon DPS: Separate for Silver and Steel swords.
    - Armor Rating: Total damage reduction.
    - Sign Intensity: Effectiveness of magic signs (e.g., Igni, Aard).
    - Attack Stats: Critical hit chance and damage for fast/strong attacks.
    - Effect Chances: Probabilities for status effects like poison, bleeding, burning.
    - Resistances: Damage mitigation against specific types (slashing, piercing, etc.).

    ## Key Terms and Abbreviations
    - Signs: Magic spells (e.g., Igni for fire, Aard for force).
    - Mutagens: Items that enhance skills when equipped in character grid.
    - Skill Trees: Combat, Signs, Alchemy, General. Allocate skills here.
    - Steel/Silver Swords: Weapons for different enemy types.

    ## Navigation and Menus
    - HUD is customizable via Options menu.
    - Stats are viewed in the Character panel within Inventory.
    - Quests and objectives appear in the HUD or quest log.

    ## OCR Reading Notes
    - Look for color-coded bars: red for health, yellow for stamina, green for toxicity.
    - Text for quests and locations may appear in HUD or menus.
    - Enemy health bar colors indicate weapon type needed.
    - Adrenaline points may be shown as numbers or icons.
    - Use context from surrounding elements to interpret ambiguous readings.
***

Example 2 - PRAGMATA:
*** 
### Menus & Screens
- **Inventory/Outfit Screen**: For managing Hugh and Diana's outfits (e.g., from Shelter Variety Pack).
- **Hacking Puzzle Screen**: When interacting with a hackable terminal or node, the view shifts to a puzzle interface with a rotating 3D cube-like structure with nodes labeled O1, O2, O3 and icons (triangles, crosses). A timer (e.g., 07:23) is visible in the upper right. The weapon wheel may still be partially visible at the bottom.
- **Map Screen**: Shows sector progress (e.g., 'Sector 02 Mass Production Array 83%') and block information for specific areas like 'Shopping District'. Collectibles are listed: Safe Boxes (e.g., 4/5), Pure Lunum (e.g., 2/2), Mods (e.g., 2/2), Read Earth Memory (e.g., 1/1). Tabs at top: Map, Status, Archive, System.
- **Weapon Wheel**: Displays equipped weapons and abilities with their current ammo/capacity. The Grip Gun DS shows ammo (e.g., 7/7). Other abilities like Charge Piercer, Stasis Net, and Decoy Gen. show their current ammo (e.g., 4, 3, 2).
***