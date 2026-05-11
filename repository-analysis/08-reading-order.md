# Reading Order

Optimized paths for understanding or editing the Ankimon codebase.

## 1. Fast Architectural Orientation
*Goal: Understand how the add-on attaches to Anki and drives the main game loop.*

1. **`src/Ankimon/__init__.py`**: Look at `on_review_card` and `on_profile_did_open`. Understand how Anki hooks drive the logic.
2. **`src/Ankimon/singletons.py`**: Note the instantiation of `main_pokemon`, `enemy_pokemon`, and `ankimon_tracker_obj`.
3. **`src/Ankimon/pyobj/ankimon_tracker.py`**: See how `cards_battle_round` increments and triggers a battle turn.
4. **`src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`**: Read `simulate_battle_with_poke_engine` to see how Ankimon state enters the engine.
5. **`src/Ankimon/functions/battle_functions.py`**: Read `process_battle_data` to see how engine results are formatted for the UI tooltip.
6. **`src/Ankimon/resources.py`**: Review to understand where data is stored on disk.
7. **`repository-analysis/01-architecture-map.md`**: Synthesize the mental model.

## 2. Deep Architecture Study (Battle Engine)
*Goal: Understand the internal mechanics of the battle simulator.*

1. **`src/Ankimon/poke_engine/battle.py`**: Read the `Battle`, `Pokemon`, and `Move` class definitions. Notice how they differ from the `PokemonObject` in `pyobj/`.
2. **`src/Ankimon/poke_engine/instruction_generator.py`**: Read `get_instructions_from_damage` and the status immunity functions.
3. **`src/Ankimon/poke_engine/damage_calculator.py`**: Read `calculate_damage` and its modifier functions (STAB, weather).
4. **`src/Ankimon/poke_engine/special_effects/moves/move_special_effect.py`**: See how unique move behaviors (like Trick Room) are implemented.
5. **`src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`**: Study the translation layer that wraps the above files.

## 3. Debugging Runtime Behavior (State and Saving)
*Goal: Trace how data is updated and persisted, especially for "lost progress" bugs.*

1. **`src/Ankimon/pyobj/pokemon_obj.py`**: Review the `PokemonObject` attributes and update methods.
2. **`src/Ankimon/functions/update_main_pokemon.py`**: Look at `save_main_pokemon` and `update_main_pokemon`. Note the disk I/O.
3. **`src/Ankimon/pyobj/data_handler.py`**: Look at how `mypokemon.json` is modified.
4. **`src/Ankimon/pyobj/ankimon_sync.py`**: Check if AnkiWeb sync logic is overriding local files.
5. **`src/Ankimon/functions/encounter_functions.py`**: Check `catch_pokemon` and `handle_enemy_faint` to see where new data is generated.

## 4. Safe Feature Implementation (Adding UI)
*Goal: Learn how to add a new window or feature without breaking the game.*

1. **`src/Ankimon/gui_classes/` or `src/Ankimon/pyobj/`**: Pick an existing window like `item_window.py` as a template.
2. **`src/Ankimon/singletons.py`**: Understand that you must instantiate your window here if it requires global scope or inject singletons into it.
3. **`src/Ankimon/menu_buttons.py`**: See how to add your new feature to the Anki top menu.
4. **`src/Ankimon/pyobj/settings.py`**: If your feature needs a toggle, add it to the default config here.
5. **`repository-analysis/06-conventions-observed.md`**: Ensure your UI has the correct `WA_DeleteOnClose` attribute and uses the global `logger`.

## 5. High-Risk Refactor Preparation (Data Migration)
*Goal: Prepare to modify the persistence layer (e.g., JSON to SQLite).*

1. **`repository-analysis/05-risk-register.md`**: Read the SQLite vs JSON Schism risk.
2. **`src/Ankimon/utils.py`**: Check for standalone functions doing raw JSON loads.
3. **`src/Ankimon/resources.py`**: Audit all `_path` variables to find every JSON file touched by the system.
4. **`src/Ankimon/pyobj/data_handler.py`**: Review the current generic data handler approach.
5. **`src/Ankimon/pyobj/pc_box.py`**: This is the heaviest consumer of collection data. Ensure it uses caches (`_all_pokemon_cache`) properly.
