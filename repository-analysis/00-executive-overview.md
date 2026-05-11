# Executive Overview: Ankimon Codebase

## What this repository appears to be
This repository contains the source code for **Ankimon**, an Anki add-on that gamifies the flashcard learning experience by integrating a Pokémon-style battle and collection system. Users engage in simulated Pokémon battles, catch Pokémon, level them up, and manage their collection, all driven by their flashcard review activity within Anki.

Directly evidenced by:
- The integration with `aqt` and `anki.hooks` (e.g., `src/Ankimon/__init__.py`).
- The presence of a `poke_engine/` directory which simulates Pokémon battles based on SirSkaro's Poke-Engine (e.g., `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`).
- File names like `pokemon_obj.py`, `pokedex_functions.py`, `ankimon_tracker.py`, and `evolution_window.py`.
- The `addon.json` and `manifest.json` files defining it as an Anki add-on.

## How it likely starts
The primary entrypoint for the Anki add-on system is `src/Ankimon/__init__.py`. When Anki loads the add-on, this file is executed.
1. It imports dependencies and Anki core modules (`aqt`, `anki`).
2. It generates startup files (`generate_startup_files`).
3. It initializes core singletons via `src/Ankimon/singletons.py` (e.g., `logger`, `settings_obj`, `main_pokemon`, `ankimon_tracker_obj`).
4. It sets up web exports and UI hooks (`gui_hooks.reviewer_did_show_question`, `gui_hooks.reviewer_did_answer_card`).
5. It checks for the existence of required sprites and data files, triggering downloads if missing (`download_sprites.py`).
6. It hooks into Anki's profile loading (`profileLoaded`) and review events (`reviewer_did_answer_card`), which drives the battle progression.

Directly evidenced by:
- `src/Ankimon/__init__.py`'s extensive setup code at the module level.
- `src/Ankimon/singletons.py` instantiating central objects like `Settings`, `PokemonObject`, and `AnkimonTracker`.

## Major subsystems
1. **Add-on Orchestration:** `src/Ankimon/__init__.py` hooks into Anki, manages initialization, and wires up the review lifecycle to the battle engine.
2. **Data & State Management:** `src/Ankimon/pyobj/pokemon_obj.py` represents individual Pokémon state. `singletons.py` holds global state. `data_handler.py` and JSON files (`mypokemon.json`, `mainpokemon.json`) handle persistence.
3. **Battle Engine (`poke_engine/`):** A complex sub-package for simulating Pokémon battles. It calculates damage, determines move effects, and manages battle state. Connected via `ankimon_hooks_to_poke_engine.py`.
4. **GUI Components (`pyobj/` and `gui_classes/`):** PyQt6 windows and dialogs for the user interface (e.g., Pokedex, PC Box, Team Builder, Settings, Evolution).
5. **Add-on Hooks & Logic (`functions/`):** Utilities for specific tasks like encountering Pokémon, evolving them, checking badges, and updating the GUI.

## State and persistence at a glance
- **State Flow:** The flashcard review (`reviewer_did_answer_card`) triggers an attack in the battle. The battle state is updated in the `poke_engine` and synced back to `PokemonObject` instances (`main_pokemon`, `enemy_pokemon`).
- **Persistence:** State is persisted to JSON files located in `src/Ankimon/user_files/` (e.g., `mypokemon.json`, `mainpokemon.json`, `items.json`, `badges.json`, `team.json`).
- **In-Memory Objects:** `singletons.py` maintains the live instances of the user's main Pokémon, the current enemy Pokémon, and the `AnkimonTracker`.
- **Note:** The memory context mentions a SQLite migration (`ankimon.db`) to solve JSON performance issues, but currently inspected files still heavily reference JSON (`mypokemon.json`). This indicates a transitional state or a potential discrepancy between the agent prompt memory and the raw files.

Directly evidenced by:
- `src/Ankimon/resources.py` defining paths like `mypokemon_path = addon_dir / "user_files" / "mypokemon.json"`.
- JSON load/dump calls in functions like `update_main_pokemon` and `data_handler.py`.

## Integration boundaries
- **Anki Integration:** Uses `aqt.gui_hooks`, `anki.hooks.addHook`, `aqt.mw`, and custom modifications to Anki's UI (e.g., `Reviewer._bottomHTML`).
- **File System:** Extensive I/O in `user_files/` and `addon_sprites/`.
- **Network Integration:**
  - Downloads sprites and updates from GitHub (`raw.githubusercontent.com`).
  - Discord Rich Presence integration (`pypresence` via `discord_function.py`).
  - AnkiWeb Sync hook for synchronizing user files across devices (`ankimon_sync.py`).

## Top risks
- **Global State Coupling:** Heavy reliance on global singletons (`mw.logger`, `mw.settings_obj`, `main_pokemon`, `enemy_pokemon`) defined in `singletons.py` and used throughout the codebase. This makes testing and refactoring difficult.
- **Complex Battle Logic:** The `poke_engine` is a large, dense state machine. Changes here risk breaking battle logic, damage calculations, or causing infinite loops.
- **Anki Version Compatibility:** Deep hooks into Anki's `Reviewer` and UI (e.g., overriding `_bottomHTML` and `_shortcutKeys`) may break when Anki updates its internal APIs.
- **Performance bottlenecks:** The memory prompt indicates that reading/writing large JSON files for every data change causes severe performance bottlenecks. Future edits must respect caching rules and any ongoing SQLite migrations.

## What to read first
1. `src/Ankimon/__init__.py`: To understand the Anki hooks and the high-level flow of a review card triggering a battle round.
2. `src/Ankimon/singletons.py`: To see the global objects that drive the add-on.
3. `src/Ankimon/pyobj/pokemon_obj.py`: To understand the core data structure representing a Pokémon.
4. `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`: To understand how the Ankimon UI state bridges into the complex battle simulator.
5. `src/Ankimon/pyobj/ankimon_tracker.py`: To see how the add-on tracks progress across flashcard reviews.

## Top 15 Most Important Files (Ranked)
1. `src/Ankimon/__init__.py` (Entrypoint and orchestration)
2. `src/Ankimon/singletons.py` (Global state initialization)
3. `src/Ankimon/pyobj/pokemon_obj.py` (Core domain model)
4. `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py` (Bridge between Anki events and the battle engine)
5. `src/Ankimon/poke_engine/battle.py` (Core battle simulator classes)
6. `src/Ankimon/pyobj/ankimon_tracker.py` (Session and progress tracking)
7. `src/Ankimon/poke_engine/instruction_generator.py` (Battle state mutation logic)
8. `src/Ankimon/functions/battle_functions.py` (Processing battle results)
9. `src/Ankimon/resources.py` (File paths and constants)
10. `src/Ankimon/functions/encounter_functions.py` (Logic for generating and catching Pokémon)
11. `src/Ankimon/pyobj/pc_box.py` (UI and logic for managing the collected Pokémon)
12. `src/Ankimon/functions/update_main_pokemon.py` (Logic for syncing the active Pokémon)
13. `src/Ankimon/pyobj/data_handler.py` (Persistence logic)
14. `src/Ankimon/pyobj/settings.py` (Configuration management)
15. `src/Ankimon/pyobj/ankimon_sync.py` (Data synchronization logic)
