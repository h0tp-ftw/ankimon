# Import and Call Hotspots

This document highlights structural centers of gravity in the codebase. Identifying these helps recognize hidden coupling and areas where changes carry disproportionate risk.

## High Fan-in Files (Many files depend on these)
These files act as the foundation of the repository.
1. **`src/Ankimon/resources.py`**: Almost every file that touches the file system imports paths from here. It is a critical configuration leaf.
2. **`src/Ankimon/pyobj/settings.py`**: The `settings_obj` is injected into almost every UI component and feature module to check user preferences.
3. **`src/Ankimon/pyobj/pokemon_obj.py`**: The core domain model. Anything that interacts with Pokémon data imports this.
4. **`src/Ankimon/poke_engine/constants.py`**: Within the engine, this file is imported by every file to reference status, mutator, and stat string constants.

## High Fan-out Files (These depend on many files)
These files act as orchestrators. Changes in dependencies frequently force updates here.
1. **`src/Ankimon/__init__.py`**: Imports virtually everything in the add-on to bootstrap the system, register hooks, and wire events.
2. **`src/Ankimon/singletons.py`**: Imports all the major UI classes (`TestWindow`, `ItemWindow`, `PokemonPC`) to instantiate them globally.
3. **`src/Ankimon/poke_engine/instruction_generator.py`**: Imports damage calculators, side-effect modules, and constants to evaluate complex battle logic.
4. **`src/Ankimon/functions/encounter_functions.py`**: Imports tracking, UI updates, saving logic, and Pokémon generation logic to coordinate full encounter cycles.

## Modules Acting as Chokepoints
- **`src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`**: Every single battle calculation *must* pass through this file. It translates the Ankimon domain model (`PokemonObject`) into the engine's domain model, and then translates the engine's output back. It is a massive structural chokepoint.
- **`src/Ankimon/pyobj/data_handler.py` & `functions/update_main_pokemon.py`**: If a SQLite migration is ongoing, these files act as chokepoints for all disk operations. Any data persistence or load passes through them.

## Suspicious Dependency Concentrations
- **Domain Logic calling UI Directly:** Files in `src/Ankimon/functions/` (like `encounter_functions.py` and `battle_functions.py`) import UI classes directly to trigger visual updates (`test_window.display_battle()`). This is a suspicious dependency inversion; usually, UI should observe state, not have state modules command the UI.
- **`singletons.py` Circular Import Risk:** Because `singletons.py` imports so many things to instantiate them, if any of those imported modules attempt to import `singletons.py` back to access a global, it will cause a circular import crash. This is likely why the convention is to pass singletons into constructors.

## Candidates for Hidden Coupling
- **JSON Structure Assumptions:** Code in UI windows (`pc_box.py`) directly reads dictionaries loaded from `mypokemon.json`. If `update_main_pokemon.py` changes the schema of that JSON, the UI will crash silently or unexpectedly. The coupling is hidden in the JSON structure.
- **Engine State vs Ankimon State:** The `PokemonObject` attributes (`hp`, `current_hp`) are tightly coupled to the engine's `new_state.user.active.hp`. If the engine renames a property or changes how volatile statuses are tracked, `__init__.py`'s manual synchronization code will break.
