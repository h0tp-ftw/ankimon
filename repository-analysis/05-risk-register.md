# Risk Register

This document outlines fragile, high-impact, or technically precarious areas of the codebase. Future edits should approach these areas with caution.

## 1. Global Singleton Coupling
- **Affected Files:** `src/Ankimon/singletons.py`, `src/Ankimon/__init__.py`, almost all UI classes.
- **Why it is risky:** Crucial state (`main_pokemon`, `settings_obj`, `logger`) is instantiated in `singletons.py` and heavily relied upon globally.
- **Dangerous Changes:** Removing a singleton, renaming it, or failing to pass the singleton explicitly into a new class constructor (relying on undeclared global scopes).
- **Precautions:** Ensure dependency injection is used when creating new classes, explicitly passing `logger` and `settings_obj`.
- **Confidence:** High

## 2. In-Memory vs. Disk State Desynchronization (The "De-evolution" Bug)
- **Affected Files:** `src/Ankimon/functions/update_main_pokemon.py`, `src/Ankimon/pyobj/pokemon_obj.py`, `mypokemon.json`.
- **Why it is risky:** Active party syncing must merge dynamic progress (HP, XP) into the static data source (`mypokemon.json`). A blind overwrite from the in-memory cache can erase static progression (like evolutions).
- **Dangerous Changes:** Calling `update_main_pokemon()` without first calling `save_main_pokemon()` to write the memory attributes to disk.
- **Precautions:** Always verify that state changes to `main_pokemon` are explicitly saved to disk before triggering reloads.
- **Confidence:** High

## 3. SQLite vs. JSON Persistence Schism
- **Affected Files:** `src/Ankimon/pyobj/data_handler.py`, `src/Ankimon/utils.py`, `database_manager.py` (if present).
- **Why it is risky:** The repository is in the middle of or has completed a migration from large JSON files to SQLite (`ankimon.db`) for performance. However, legacy JSON paths are still present.
- **Dangerous Changes:** Writing new features that iterate over large JSON files directly instead of using the new database cache, or assuming data shapes (lists vs dicts) are strictly uniform.
- **Precautions:** Check if `_all_pokemon_cache` or AnkimonDB is expected to handle the persistence layer for the feature being edited. Handle both dicts and arrays for stats.
- **Confidence:** High

## 4. `poke_engine` State Mismatches
- **Affected Files:** `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`, `src/Ankimon/poke_engine/battle.py`.
- **Why it is risky:** The `poke_engine` has its own internal representation of a Pokémon (`poke_engine.objects.Pokemon`) which differs from Ankimon's (`pyobj.pokemon_obj.PokemonObject`).
- **Dangerous Changes:** Adding new attributes to `PokemonObject` without mapping them into the engine, or assuming the engine modifies `PokemonObject` directly.
- **Precautions:** Ensure changes to Pokémon stats/mechanics are correctly mapped back and forth in `simulate_battle_with_poke_engine`.
- **Confidence:** High

## 5. Anki Hook Freezes
- **Affected Files:** `src/Ankimon/__init__.py`.
- **Why it is risky:** Functions attached to `gui_hooks.reviewer_did_answer_card` run synchronously during a user's study session.
- **Dangerous Changes:** Introducing blocking network requests, heavy disk I/O without caching, or infinite loops here will freeze Anki completely.
- **Precautions:** Use background operations (`QueryOp`) for network calls, and ensure file reads are cached (`@functools.lru_cache`).
- **Confidence:** High

## 6. PyQt Window Memory Leaks
- **Affected Files:** `src/Ankimon/gui_classes/`, `src/Ankimon/pyobj/`.
- **Why it is risky:** Ankimon opens many popups (Details, Pokedex, PC Box). If windows are hidden instead of destroyed, they leak memory.
- **Dangerous Changes:** Creating new UI windows without proper cleanup attributes.
- **Precautions:** Ensure `self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)` is set on standard popups. Exempt heavily used windows like `pc_box.py` for performance, but manage their lifecycle carefully.
- **Confidence:** Medium
