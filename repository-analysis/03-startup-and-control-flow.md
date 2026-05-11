# Startup and Control Flow

This document traces the most important execution and behavior paths in the Ankimon repository.

## 1. Add-on Initialization Path
- **Trigger:** Anki application startup loads installed add-ons.
- **Start File/Symbol:** `src/Ankimon/__init__.py` (module level).
- **Intermediate Steps:**
  1. Calls `generate_startup_files` (from `resources.py`) to create empty JSON files if they don't exist.
  2. Imports `singletons.py`, which instantiates `settings_obj`, `main_pokemon`, `ankimon_tracker_obj`, etc.
  3. Registers hooks: `gui_hooks.reviewer_did_show_question`, `gui_hooks.reviewer_did_answer_card`, `addHook("profileLoaded", on_profile_loaded)`.
  4. Checks for assets (`check_folders_exist`).
  5. If valid, loads `main_pokemon` data; if not, triggers starter selection or random generation.
- **Endpoint/Effect:** The add-on is fully loaded, UI hooks are active, and global state is ready for the first review.
- **State Changes Involved:** Global singletons are initialized.
- **Persistence Involved:** Reads configuration and potentially `mypokemon.json`/`mainpokemon.json`.
- **Integrations Involved:** Deep injection into Anki's hook system.
- **Why it matters:** It sets the stage. If this fails, the add-on doesn't load.
- **Confidence Level:** High

## 2. Review Card to Battle Turn Path (The Core Loop)
- **Trigger:** User answers a flashcard in Anki.
- **Start File/Symbol:** `src/Ankimon/__init__.py` -> `on_review_card` hook.
- **Intermediate Steps:**
  1. `ankimon_tracker_obj.cards_battle_round` is incremented.
  2. Checks if `cards_battle_round >= settings_obj.get("battle.cards_per_round")`.
  3. If true, a battle turn initiates. Moves are selected for `main_pokemon` and `enemy_pokemon`.
  4. Calls `simulate_battle_with_poke_engine` in `ankimon_hooks_to_poke_engine.py`.
  5. The `poke_engine` processes the turn (`instruction_generator.py`, `damage_calculator.py`).
  6. Results (damage, status changes) are returned.
  7. `main_pokemon` and `enemy_pokemon` HP/status are immediately updated in memory.
  8. `process_battle_data` (`battle_functions.py`) formats the output.
  9. Tooltips and sounds are triggered in the Anki UI.
- **Endpoint/Effect:** HP is reduced, status effects applied, and the user sees the battle result.
- **State Changes Involved:** `main_pokemon.hp`, `enemy_pokemon.hp`, `battle_status`, `cards_battle_round` mutated.
- **Persistence Involved:** Usually deferred, but state is updated in singletons.
- **Integrations Involved:** UI tooltips via `aqt.utils.tooltipWithColour`.
- **Why it matters:** This is the primary feature of the add-on. It connects learning to gameplay.
- **Confidence Level:** High

## 3. Enemy Pokémon Faint Path
- **Trigger:** Enemy HP drops below 1 during the `on_review_card` hook.
- **Start File/Symbol:** `src/Ankimon/__init__.py` -> `on_review_card`.
- **Intermediate Steps:**
  1. Detects `enemy_pokemon.hp < 1`.
  2. Calls `handle_enemy_faint` (`encounter_functions.py`).
  3. Within `handle_enemy_faint`, XP is calculated and awarded to `main_pokemon`.
  4. Handles catching logic if automated catching is enabled.
  5. `mutator_full_reset` is set to 1 to reset the engine state for the next battle.
- **Endpoint/Effect:** Enemy is defeated, rewards (XP/Items) are distributed, and a new enemy is prepared.
- **State Changes Involved:** Enemy state cleared, User XP increased, potential level up.
- **Persistence Involved:** Saving new XP/Level to `mainpokemon.json`.
- **Why it matters:** Handles progression and reward mechanics.
- **Confidence Level:** High

## 4. Main Pokémon Faint Path
- **Trigger:** Main Pokémon HP drops below 1 during the `on_review_card` hook.
- **Start File/Symbol:** `src/Ankimon/__init__.py` -> `on_review_card`.
- **Intermediate Steps:**
  1. Detects `main_pokemon.hp < 1`.
  2. Calls `handle_main_pokemon_faint` (`encounter_functions.py`).
  3. Automatically selects a new valid Pokémon from the user's collection or heals.
  4. `mutator_full_reset` is set to 1.
- **Endpoint/Effect:** The user's active Pokémon is swapped or revived.
- **State Changes Involved:** `main_pokemon` reference updated or HP restored.
- **Persistence Involved:** Disk sync for the fainted Pokémon and the newly active one.
- **Why it matters:** Ensures the game doesn't soft-lock when the user loses a battle.
- **Confidence Level:** Medium

## 5. Catch Pokémon Path
- **Trigger:** User presses the Catch shortcut or clicks the Catch button in the reviewer.
- **Start File/Symbol:** `src/Ankimon/__init__.py` -> `catch_shortcut_function` / `CatchPokemonHook`.
- **Intermediate Steps:**
  1. Validates `enemy_pokemon.hp < 1`.
  2. Calls `catch_pokemon` (`encounter_functions.py`).
  3. Generates new stats/IVs/EVs for the caught Pokémon.
  4. Appends the new `PokemonObject` data to `mypokemon.json`.
  5. Updates `collected_pokemon_ids`.
  6. Calls `new_pokemon` to spawn the next opponent.
- **Endpoint/Effect:** The Pokémon is added to the user's PC.
- **State Changes Involved:** Collection state updated.
- **Persistence Involved:** Writes to `mypokemon.json`.
- **Why it matters:** Core collection mechanic.
- **Confidence Level:** High

## 6. Profile Open / Sync Path
- **Trigger:** Anki finishes loading a user profile.
- **Start File/Symbol:** `src/Ankimon/__init__.py` -> `on_profile_did_open`.
- **Intermediate Steps:**
  1. Shows tip of the day.
  2. Checks for monthly rewards (`check_and_award_monthly_pokemon`).
  3. Initializes AnkiWeb sync hooks (`setup_ankimon_sync_hooks` in `ankimon_sync.py`).
  4. Checks for sync conflicts (`check_and_sync_pokemon_data`).
- **Endpoint/Effect:** Daily tasks run, and data is synced across devices.
- **Persistence Involved:** Reads/Writes across `user_files/`.
- **Integrations Involved:** Network access to AnkiWeb/GitHub.
- **Why it matters:** Ensures data consistency and recurring engagement features.
- **Confidence Level:** Medium
