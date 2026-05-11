# Architecture Map: Ankimon Codebase

## Entrypoints
- `src/Ankimon/__init__.py`: The primary entrypoint. Executed by Anki when the add-on is loaded. It bootstraps the application, registers hooks, initializes singletons, and attaches UI components to Anki's windows.

## Initialization Flow
1. **Module Import:** Anki imports `src/Ankimon/__init__.py`.
2. **Resource Setup:** `generate_startup_files` is called to ensure required JSON files (like `mypokemon.json`, `team.json`) exist in the `user_files/` directory.
3. **Singleton Instantiation:** `src/Ankimon/singletons.py` is imported, which instantiates global objects (`Settings`, `PokemonObject` for user/enemy, `AnkimonTracker`, `Translator`, `ShowInfoLogger`, etc.).
4. **Environment Check:** Checks if required image/sound folders exist (`check_folders_exist`). If not, prompts for download (`show_agreement_and_download_dialog`).
5. **Anki Hook Registration:** Hooks are attached to Anki events:
   - `gui_hooks.reviewer_did_show_question`
   - `gui_hooks.reviewer_did_answer_card` (The main driver for battle progression)
   - `gui_hooks.profile_did_open` (Triggers sync and daily events)
6. **Background Tasks:** Initiates online checks, changelog downloads, and Discord Rich Presence connection.
7. **Battle Initialization:** If valid Pokémon exist, their state is loaded. If not, a random enemy is generated (`generate_random_pokemon`).

## Module Boundaries
- **`Ankimon/` Root:** Orchestration, constant definitions (`const.py`, `resources.py`), and Anki-specific setup (`__init__.py`, `hooks.py`, `menu_buttons.py`).
- **`Ankimon/pyobj/`:** The bulk of the business logic and stateful objects. Contains classes for Pokémon (`pokemon_obj.py`), tracking (`ankimon_tracker.py`), and main GUI windows (`item_window.py`, `pc_box.py`, `settings_window.py`).
- **`Ankimon/functions/`:** Procedural helper functions and domain logic that doesn't strictly belong to a class. Separated roughly by domain (e.g., `encounter_functions.py`, `battle_functions.py`, `pokedex_functions.py`).
- **`Ankimon/poke_engine/`:** An isolated, complex subsystem responsible for simulating Pokémon battles. It takes Pokémon stats and moves, applies rules, and outputs instructions for state changes.
- **`Ankimon/gui_classes/` & `Ankimon/gui_entities.py`:** Additional UI components and PyQt window definitions.
- **`Ankimon/pokedex/`:** Logic and UI specifically for the Pokedex feature.

## Orchestration Model
The orchestration is heavily event-driven, relying on Anki's review cycle.
- The user reviews a flashcard.
- `on_review_card` in `__init__.py` fires.
- The `ankimon_tracker_obj` increments counters.
- When `cards_battle_round` meets the threshold, a battle turn occurs.
- Moves are selected (randomly for enemy, randomly or chosen for user).
- `simulate_battle_with_poke_engine` is called, passing the state into the `poke_engine`.
- The engine returns instructions and a new state.
- The global `main_pokemon` and `enemy_pokemon` objects are updated with the new state.
- UI components (tooltips, sounds, life bars) are updated based on the result.
- If a Pokémon faints, `handle_enemy_faint` or `handle_main_pokemon_faint` is triggered.

## State Flow
1. **Source of Truth (Disk):** JSON files in `user_files/` (e.g., `mypokemon.json` for collection, `mainpokemon.json` for active battle state).
2. **In-Memory Cache (Singletons):** Loaded into `singletons.py` objects (`main_pokemon`, `enemy_pokemon`).
3. **Mutation (Engine):** During a battle, `poke_engine` calculates changes.
4. **Synchronization:** The updated state is mapped back onto the in-memory `PokemonObject` instances.
5. **Persistence:** The state is saved back to disk (often via `update_main_pokemon()` or `data_handler.py`).

*Note on Memory:* The system prompt mentions an ongoing migration to a SQLite database (`ankimon.db`) using `AnkimonDB`, but the files inspected still show heavy reliance on JSON files. This represents a transitional architecture where old JSON logic coexists with or is being replaced by SQLite logic.

## Persistence Model
Currently, heavily file-based JSON.
- `mypokemon.json`: The collection of captured Pokémon.
- `mainpokemon.json`: The currently active Pokémon in battle.
- `items.json`, `badges.json`, `team.json`: Other persistent user data.
- `data.json` / `config.json`: Configuration settings.
- **Backup System:** `backup_manager.py` creates zip backups of the `user_files/` directory to prevent data loss.
- **Sync System:** `ankimon_sync.py` uses Anki's media sync or custom logic to synchronize the `user_files/` directory across devices.

## Configuration Model
- Handled by `src/Ankimon/pyobj/settings.py` (`Settings` class).
- Loads from a configuration file (likely `config.json` or `data.json`).
- Uses dot-notation keys (e.g., `misc.language`, `audio.battle_sounds`).
- The `settings_obj` singleton is injected into almost every UI class.

## External Integrations
- **Anki Core:** `aqt`, `anki`. Deep integration with the GUI and review scheduler.
- **GitHub:** Fetches updates and changelogs (`updateinfos.md`) from the Ankimon repository.
- **Discord:** Rich Presence integration via `pypresence` to show battle status on Discord.
- **PokeAPI (Historical/Data):** Data files in `user_files/data_files/` are likely scraped from PokeAPI or Pokemon Showdown, but the add-on operates offline using these local files.

## High-Confidence Findings
- The application is heavily coupled to `singletons.py`.
- The core loop is driven by Anki's flashcard review hooks.
- The `poke_engine` is the source of truth for battle mechanics and damage calculation.
- State mutation during battles involves a complex translation between `PokemonObject` and the engine's internal representation.

## Open Questions and Ambiguous Areas
- **SQLite vs JSON:** The memory prompt strongly emphasizes a SQLite migration (`ankimon.db`), but the inspected entrypoints and singletons still use `.json` files. It is unclear if the migration is complete, partial, or residing in an uninspected module (e.g., a hidden `database_manager.py`).
- **Data Handler Role:** `DataHandler` in `data_handler.py` exists, but there are also many standalone functions performing direct JSON I/O (e.g., `update_main_pokemon.py`). The boundary of responsibility for persistence is blurry.

## Change-Risk Hotspots
- **`src/Ankimon/__init__.py`:** Modifying the Anki hooks or the main battle loop here risks breaking the entire add-on or causing UI freezes.
- **`src/Ankimon/poke_engine/`:** Highly complex logic. Small changes to damage calculations or instructions can cascade into broken battles or infinite loops.
- **State Syncing:** Functions that sync state between the engine, `main_pokemon`, and the disk (e.g., `update_pokemon_battle_status`). If these desync, Pokémon might spontaneously heal, lose moves, or de-evolve.

## Likely Architectural Intent
The intent was to build a rich, game-like layer on top of Anki without modifying Anki's core functionality. To achieve this, it uses a global state pattern (`singletons.py`) to maintain game state across separate flashcard reviews. The separation of `poke_engine` suggests an intent to reuse or isolate the complex battle logic from the Anki-specific UI code, even though they remain tightly coupled in practice.
