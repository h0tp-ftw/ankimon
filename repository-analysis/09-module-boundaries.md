# Module Boundaries

This document describes the major subsystem boundaries and how responsibilities are divided.

## Major Modules and Subsystems

### 1. The Core Orchestrator (`Ankimon/`)
- **Responsibilities:** Hooks into Anki, sets up the environment, initializes global state, and connects the review events to the game logic.
- **Communication:** Calls into `functions/` to execute game events. Calls into `pyobj/` to show UI windows. Injects state from `singletons.py` into almost everything.
- **Dependencies:** Heavily depends on Anki (`aqt`, `anki`), `singletons.py`, and `poke_engine/`.

### 2. The Battle Simulator (`Ankimon/poke_engine/`)
- **Responsibilities:** A pure(ish) state machine representing a Pokémon battle. Calculates damage, handles statuses, manages turn order, and enforces Pokémon rules.
- **Communication:** Should only communicate via `ankimon_hooks_to_poke_engine.py`. Takes in initial state, returns instructions/results.
- **Dependencies:** Should have *no* dependencies on Anki or the Ankimon UI. Depends on its own internal data JSONs (`moves.json`, `pokedex.json` in `data/`).

### 3. The Object Model & State Layer (`Ankimon/pyobj/`)
- **Responsibilities:** Holds the Python representations of the user's game state (`PokemonObject`, `AnkimonTracker`, `Settings`). Contains the definitions for the major UI windows.
- **Communication:** Consumed by `__init__.py` and `functions/`. Reads from `resources.py` and `data_handler.py`.
- **Dependencies:** Depends on PyQt6 for the UI classes.

### 4. The Action Functions (`Ankimon/functions/`)
- **Responsibilities:** Procedural scripts that execute specific game events (e.g., catching a Pokémon, evolving, calculating achievements, formatting text).
- **Communication:** Called by `__init__.py` or UI buttons. They mutate the objects in `pyobj/`.
- **Dependencies:** Tends to import heavily from `pyobj/` and `resources.py`.

## Boundary Violations and Layering Leaks

- **`functions/` vs `pyobj/` UI:** Functions in the `functions/` directory often import UI windows directly (e.g., `encounter_functions` importing `test_window` or `evo_window` to update their displays). This creates tight coupling where domain logic is responsible for triggering UI redraws.
- **`__init__.py` Overcrowding:** The root `__init__.py` handles too much. It does Anki hook registration, but also handles the raw logic for detecting when to grant an item based on reviews, calculating HP tooltips, and managing Discord presence.
- **Singletons Everywhere:** Because `singletons.py` makes instances globally available, functions deep in the hierarchy can reach out and mutate global state (`settings_obj`, `main_pokemon`) without it being passed explicitly through the call stack.

## Where future architecture cleanup would likely pay off most

1. **Decoupling UI updates from domain logic:** Instead of `encounter_functions.py` calling `test_window.display_battle()`, the encounter function should return a state change, and an event listener on the UI should redraw itself.
2. **Slimming down `__init__.py`:** Move the specific battle-turn logic (the large block inside `on_review_card`) into a dedicated `battle_controller.py` file.
3. **Formalizing the Persistence Interface:** The SQLite migration mentioned in memory needs a strict DAO (Data Access Object) boundary. Currently, JSON paths (`mypokemon_path`) are imported all over the place, meaning any switch to a DB requires changing dozens of files. Abstracting this behind a `StorageManager` would be highly beneficial.
