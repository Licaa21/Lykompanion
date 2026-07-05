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

# Training Data improvements:
- [ ] (2026-07-05, needs in-game validation) Diagnosed why Example 1 below reads like generic wiki filler while Example 2 reads like real decoding notes: Example 1 came from the **bootstrap pass** (`app/services/llm/game_knowledge_bootstrap.py`, seeded from IGDB + a web search, no screenshots ever seen) and its prompt (`app/prompts/game_knowledge_bootstrap.md`) asked for "what the main HUD elements are and where stats appear" + "game-specific terms" with no floor on specificity — so it wrote a generic genre overview, lore any capable model already knows (what Signs/Igni/Aard are), and hedge-guessed layout ("typically bottom-left") never grounded in a real screenshot. Example 2 came from the **extraction pass** itself, which actually sees screenshots, so its notes were concrete and OCR-grounded. Since the extraction prompt only revises the doc "when you now know better," ungrounded bootstrap filler that's never contradicted just sits there forever, permanently taxing every future pass's prompt. Fixed via prompt changes only, no code:
    * `game_knowledge_bootstrap.md`: narrowed "training_data" to in-scope-only (terms/abbreviations, menu names, choice/selection quirks, an identifiable HUD number format) and explicitly out-of-scope (genre overview, common lore/vocabulary a model already knows, guessed on-screen layout/positioning) — told to return a short/empty doc rather than pad it.
    * `game_state_extraction.md`: `training_data_update`'s "Game knowledge" bullet now requires specificity a model wouldn't already know cold (forbids generic lore/vocabulary restatement); added an explicit "unconfirmed hedge" rule — a note phrased as "typically/likely" is corrected to a plain confirmed statement (or removed) the moment a real screenshot confirms or contradicts it, and a note that turns out to be generic filler can now be removed entirely (previously only "wrong" notes were revisable).
    Keep an eye on newly-bootstrapped games to confirm the seeded documents are shorter/tighter and that stale hedge-phrased notes actually get corrected once real screenshots come in.

Here's what's currently getting collected in the training data (kept for reference — the examples that prompted the above fix). 2 examples:

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