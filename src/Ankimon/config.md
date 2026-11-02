# Ankimon settings

Use these settings to customize battles, shortcuts, and the reviewer display. For toggle settings, `true` enables the option and `false` disables it. Some changes, such as background checks, require an Anki restart.

## Battles

- `battle.cards_per_round` [number or range]: Cards to review between battle rounds. Use a number such as `2`, or a range such as `"2-4"` for a random interval.
- `battle.automatic_battle` [0/1/2/3] (default: 0): Choose what happens when a wild Pokémon reaches 0 HP:
    - `0`: Choose whether to catch or defeat it in the Ankimon window. Pokémon on your auto-catch wishlist are still caught automatically.
    - `1`: Catch it automatically.
    - `2`: Defeat it automatically so your main Pokémon gains XP.
    - `3` (Catch if uncollected): Catch it if it is missing from your permanent Pokédex caught history; otherwise, defeat it. Releasing, evolving, or trading away a Pokémon does not make it uncollected. Each form is checked by its exact ID, and seeing a Pokémon does not count as catching it. History refreshes after each completed encounter, including changes from evolution, trades, and imports.

In modes `2` and `3`, shiny Pokémon, Pokémon on `battle.auto_catch_wishlist`, and Pokémon covered by enabled always-auto-catch settings are caught instead.

### Generations

Enable each generation you want to encounter:

- `misc.gen1` [true/false]
- `misc.gen2` [true/false]
- `misc.gen3` [true/false]
- `misc.gen4` [true/false]
- `misc.gen5` [true/false]
- `misc.gen6` [true/false]
- `misc.gen7` [true/false]
- `misc.gen8` [true/false]
- `misc.gen9` [true/false]

### Level cap

`misc.remove_level_cap` [true/false] (default: false): Allow Pokémon to level beyond 100.

When disabled, your main Pokémon, Pokémon receiving shared XP, and wild encounters are capped at level 100. Below main level 100, wild levels roll from three below to three above your main Pokémon's level, then results above 100 are reduced to 100. For example, a level 98 main Pokémon gives a roll of 95–101 before the cap. At main level 100 or higher, all wild encounters are level 100.

When enabled, wild levels use the same ±3 range without the upper cap. Wild Pokémon are always at least level 1.

## Keyboard shortcuts

- `controls.key_for_opening_closing_ankimon` [shortcut] (default: `Ctrl+Shift+P`): Open or close the Ankimon window while reviewing.
- `controls.catch_key` [key or shortcut] (default: `6`): Catch the wild Pokémon when it reaches 0 HP.
- `controls.defeat_key` [key or shortcut] (default: `5`): Defeat the wild Pokémon when it reaches 0 HP.

Use the catch and defeat shortcuts as configured; the defaults do not need Ctrl. With automatic battle enabled, pressing either shortcut before the wild Pokémon faints toggles that action as an override for the encounter.

## Reviewer display

The reviewer is the screen where you study cards. Its HUD shows your Pokémon and battle information.

### Pokémon and animations

- `gui.show_mainpkmn_in_reviewer` [0/1/2]: Choose the Pokémon layout: `0` shows only the wild Pokémon, `1` shows both Pokémon with their HP bars at the same height, and `2` uses a battle-style layout.
- `gui.reviewer_image_gif` [true/false]: Use animated Pokémon GIFs in the reviewer.
- `gui.view_main_front` [true/false]: Show your main Pokémon from the front when reviewer GIFs are enabled. Disable it to show the back view.
- `gui.animate_time` [true/false]: Play a brief animation when either Pokémon takes damage.
- `gui.gif_in_collection` [true/false]: Use animated GIFs instead of static sprites in your collection.
- `gui.show_sprites_across_ankimon` [true/false] (default: true): Show Pokémon sprites across Ankimon, including the evolution prompt and completion screen. These two screens use shiny sprites for shiny Pokémon, falling back to an ordinary sprite if a shiny image is missing. Disable this setting to hide sprites on both evolution screens.

### Messages

- `gui.reviewer_text_message_box` [true/false]: Show colorful battle messages in the reviewer.
- `gui.reviewer_text_message_box_time` [1–5 seconds]: How long each battle message stays visible.
- `gui.pop_up_dialog_message_on_defeat` [true/false]: Show Anki popups when a wild Pokémon is defeated or your Pokémon levels up.

### HUD styling and bars

- `gui.hud_styling` [true/false] (default: true): Style the HUD. Disable it for plain text. HP and XP bars require Styling; their controls are unavailable when Styling is off. Selecting an unavailable bar control in web settings shows a reminder to enable Styling.
- `gui.hud_hp_bars` [true/false]: Show HP bars.
- `gui.review_hp_bar_thickness` [number]: Set HP bar thickness in units of 4 pixels: `2` = 8 px, `3` = 12 px, `4` = 16 px, and `5` = 20 px.
- `gui.hud_xp_bar` [true/false]: Show your main Pokémon's XP progress bar.
- `gui.xp_bar_location` [1/2]: Place the XP bar at the top (`1`) or bottom (`2`) of the reviewer.
- `gui.hud_xp_values` [true/false] (default: false): Show `XP: current/required`, independently of the XP bar and Battle Log. With Styling on, the values appear on the XP bar, or in a separate pill if the bar is hidden. With Styling off, they appear as plain text.

### HUD elements

Enable an element to show it; disable it to hide it.

- `gui.hud_player_sprite` [true/false]: Your main Pokémon's sprite.
- `gui.hud_enemy_sprite` [true/false]: The wild Pokémon's sprite.
- `gui.hud_hp_text` [true/false]: Current and maximum HP.
- `gui.hud_pokemon_id` [true/false]: Pokédex number.
- `gui.hud_pokemon_gen` [true/false]: Generation.
- `gui.hud_pokemon_lvl` [true/false]: Level.
- `gui.hud_pokemon_name` [true/false]: Pokémon names.
- `gui.hud_enemy_shiny_indicator` [true/false]: A star for shiny wild Pokémon.
- `gui.hud_player_shiny_indicator` [true/false]: A star for your shiny main Pokémon.

`gui.hud_status_badge` [true/false]: Show the opponent's status as a badge with Styling on, or plain text with Styling off. In plain text, this also shows stat multipliers and supported conditions such as `*ASLEEP` or `*BADLY POISONED` beside Pokémon details. Your Pokémon's conditions need at least one visible detail: ID, generation, level, or name. Automatic battle hides the plain BATTLING/FAINTED readout, but condition suffixes can still appear.

`gui.hud_owned_indicator` [true/false] (default: true): Mark wild Pokémon whose exact species or form you have caught before. With Styling on, this setting is called **Pokéball Icon** and shows a Pokéball. With Styling off, it is called **Captured State** and shows `[CAPTURED]` beside the opponent's name or status. The marker remains after you release, evolve, or trade away that Pokémon. New catches and incoming trades update it without a restart.

## Sound

- `audio.sounds` [true/false]: Play a Pokémon's cry when it appears.
- `audio.battle_sounds` [true/false]: Replay Pokémon cries during battle, including every 10 cards. Requires `audio.sounds`.
- `audio.sound_effects` [true/false]: Play sound effects for actions such as healing and dealing damage.

## Updates and background checks

- `misc.YouShallNotPass_Ankimon_News` [true/false]: Hide the update patch notes popup at startup when set to `true`.
- `misc.ssh` [true/false] (default: true): Allow automatic update checks and changelog downloads at startup. This setting is labelled **Allow Background Checks** (formerly **SSH Access**). Disable it if these checks cause connection errors, such as on restricted networks. Restart Anki after changing it. Sprite update checks run independently and remain enabled.

## Language

Set `misc.language` to the ID for your preferred Pokémon names and descriptions.

| ID | Language | Official |
| --- | --- | --- |
| 1 | Japanese (Hiragana and Katakana) | Yes |
| 2 | Japanese (Romaji) | Yes |
| 3 | Korean | Yes |
| 4 | Chinese (Traditional) | Yes |
| 5 | French | Yes |
| 6 | German | Yes |
| 7 | Spanish | Yes |
| 8 | Italian | Yes |
| 9 | English | Yes |
| 10 | Czech | No |
| 11 | Japanese | Yes |
| 12 | Chinese (Simplified) | Yes |
| 13 | Portuguese (Brazil) | No |
| 14 | Spanish (Latin America) | No |
