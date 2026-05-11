# File Cards: Navigation Layer

## Primary Orchestration & Entrypoints

### `src/Ankimon/__init__.py`
- **Primary Responsibility:** Entrypoint for Anki. Initializes the add-on, sets up hooks, and runs the main battle loop on card review.
- **Why it matters:** It is the orchestrator. It connects Anki's events to the internal game state. Editing this affects the entire lifecycle.
- **Major Symbols:** `on_review_card`, `download_changelog`, hook registrations.
- **Dependencies:** `aqt`, `singletons`, `poke_engine`, various `functions`.
- **Role:** Entrypoint / Orchestrator
- **Confidence Level:** High

### `src/Ankimon/singletons.py`
- **Primary Responsibility:** Instantiates and holds global objects used throughout the add-on.
- **Why it matters:** It manages the central state. Almost every UI component and function relies on the instances created here.
- **Major Symbols:** `settings_obj`, `main_pokemon`, `enemy_pokemon`, `ankimon_tracker_obj`.
- **Dependencies:** `pyobj.*`, `gui_entities`.
- **Role:** State Container / Glue
- **Confidence Level:** High

### `src/Ankimon/resources.py`
- **Primary Responsibility:** Defines file paths and constants used across the project.
- **Why it matters:** Central source of truth for where data, sprites, and configs are stored.
- **Major Symbols:** `addon_dir`, `user_path`, `mypokemon_path`, `generate_startup_files`.
- **Role:** Config / Utility
- **Confidence Level:** High

## Core Domain & State

### `src/Ankimon/pyobj/pokemon_obj.py`
- **Primary Responsibility:** Defines the `PokemonObject` class, representing a Pokémon's stats, moves, and state.
- **Why it matters:** It is the core domain model. All battle logic and UI rendering depend on the structure of this object.
- **Major Symbols:** `PokemonObject`, `update_stats`, `calculate_max_hp`.
- **Role:** Domain Logic / State Container
- **Confidence Level:** High

### `src/Ankimon/pyobj/ankimon_tracker.py`
- **Primary Responsibility:** Tracks progress across review sessions (e.g., cards reviewed, encounters, time taken).
- **Why it matters:** Drives the progression of the game; decides when a battle round occurs based on review counts.
- **Major Symbols:** `AnkimonTracker`, `review`, `reset_card_timer`.
- **Role:** State Container / Orchestrator
- **Confidence Level:** High

### `src/Ankimon/functions/update_main_pokemon.py`
- **Primary Responsibility:** Functions to update the active `main_pokemon` state and synchronize it with storage.
- **Why it matters:** Critical for ensuring that changes during battle (HP loss, XP gain) are correctly saved and loaded.
- **Major Symbols:** `update_main_pokemon`, `save_main_pokemon`.
- **Role:** Persistence / Glue
- **Confidence Level:** High

## Battle Engine (`poke_engine/`)

### `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`
- **Primary Responsibility:** Translates Ankimon's `PokemonObject` into the format expected by the `poke_engine`, runs the simulation, and translates the result back.
- **Why it matters:** The bridge between the user's state and the complex battle simulator.
- **Major Symbols:** `simulate_battle_with_poke_engine`.
- **Role:** Integration Adapter
- **Confidence Level:** High

### `src/Ankimon/poke_engine/battle.py`
- **Primary Responsibility:** Defines the core data structures for the battle engine (`Battle`, `Pokemon`, `Move`).
- **Why it matters:** Source of truth for how the simulator views the battle state.
- **Major Symbols:** `Battle`, `Pokemon`, `Move`.
- **Role:** Domain Logic
- **Confidence Level:** High

### `src/Ankimon/poke_engine/instruction_generator.py`
- **Primary Responsibility:** Evaluates moves and status effects to generate mutation instructions for the battle state.
- **Why it matters:** Source of truth for battle mechanics and rules (e.g., immunities, damage application).
- **Major Symbols:** `get_instructions_from_damage`, `immune_to_status`.
- **Role:** Domain Logic
- **Confidence Level:** High

### `src/Ankimon/poke_engine/damage_calculator.py`
- **Primary Responsibility:** Calculates damage based on types, weather, STAB, and base stats.
- **Why it matters:** Determines how much HP is lost during an attack.
- **Major Symbols:** `calculate_damage`, `type_effectiveness_modifier`.
- **Role:** Domain Logic
- **Confidence Level:** High

## UI & Interactions

### `src/Ankimon/functions/encounter_functions.py`
- **Primary Responsibility:** Logic for generating wild Pokémon, catching them, and handling faints.
- **Why it matters:** Defines the core gameplay loop outside of the specific battle mechanics.
- **Major Symbols:** `generate_random_pokemon`, `catch_pokemon`, `handle_enemy_faint`.
- **Role:** Domain Logic / Orchestrator
- **Confidence Level:** High

### `src/Ankimon/functions/battle_functions.py`
- **Primary Responsibility:** Processes the results from `poke_engine` and formats them for the Anki UI.
- **Why it matters:** Translates raw engine instructions into user-facing text and updates visual status.
- **Major Symbols:** `process_battle_data`, `update_pokemon_battle_status`.
- **Role:** UI Surface / Glue
- **Confidence Level:** High

### `src/Ankimon/pyobj/pc_box.py`
- **Primary Responsibility:** The UI for managing captured Pokémon (The PC).
- **Why it matters:** The main interface for users to interact with their persistence data (`mypokemon.json`).
- **Major Symbols:** `PokemonPC`.
- **Role:** UI Surface
- **Confidence Level:** Medium

### `src/Ankimon/pyobj/settings.py`
- **Primary Responsibility:** Manages loading, saving, and accessing add-on settings.
- **Why it matters:** Accessed globally to determine feature toggles and user preferences.
- **Major Symbols:** `Settings`, `get`, `set`.
- **Role:** Config / State Container
- **Confidence Level:** High

### `src/Ankimon/pyobj/ankimon_sync.py`
- **Primary Responsibility:** Handles synchronization of user files across devices.
- **Why it matters:** Prevents data loss for users syncing between desktop and mobile/other machines.
- **Major Symbols:** `ImprovedPokemonDataSync`.
- **Role:** Persistence / Integration Adapter
- **Confidence Level:** Medium

## Secondary files worth checking later
- `src/Ankimon/functions/badges_functions.py`: Handles logic for achievements.
- `src/Ankimon/functions/pokemon_showdown_functions.py`: Export logic for showdown format.
- `src/Ankimon/gui_entities.py`: Common UI widget definitions.

## Probably low-priority areas
- `src/Ankimon/addon_sprites/` and `src/Ankimon/user_files/`: Image and sound assets.
- `src/Ankimon/lang/`: Localization JSON files.

## Files that appear deceptively important but are mostly glue
- `src/Ankimon/menu_buttons.py`: Just wiring actions to Anki's top menu.
- `src/Ankimon/hooks.py`: Wrapper for Anki hook registration, though much registration happens in `__init__.py`.

## Files that look small but are architecturally critical
- `src/Ankimon/utils.py` (or similar utility files): Often contain central I/O logic that impacts performance.
