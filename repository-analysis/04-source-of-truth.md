# Source of Truth

This document identifies the authoritative files for various aspects of the Ankimon system. When making changes, it is critical to modify the *source of truth* rather than a downstream wrapper, helper, or cached value.

## Startup and Orchestration
**Authoritative File:** `src/Ankimon/__init__.py`
- **Why:** It contains the root hook registrations (`addHook("profileLoaded", ...)` and `gui_hooks.reviewer_did_answer_card.append(...)`). No other file dictates *when* the add-on runs.
- **Distinction:** Files like `hooks.py` exist but are mostly wrappers or secondary setup. `__init__.py` is the ultimate arbiter of the lifecycle.
- **Confidence:** High

## Configuration
**Authoritative File:** `src/Ankimon/pyobj/settings.py` (The `Settings` class)
- **Why:** It wraps the underlying config files (`config.json` / `data.json`). Any read/write to user preferences must go through `settings_obj.get()` or `settings_obj.set()`.
- **Distinction:** Do not edit JSON config files manually in code; use the `Settings` class methods to ensure memory and disk stay synced.
- **Confidence:** High

## Domain Logic: Pokémon State
**Authoritative File:** `src/Ankimon/pyobj/pokemon_obj.py`
- **Why:** The `PokemonObject` class defines what a Pokémon *is* in this system (HP, moves, IVs, EVs, status). All other systems (UI, battle engine) consume or mutate instances of this class.
- **Distinction:** While `mypokemon.json` is the persisted truth on disk, `PokemonObject` is the authoritative runtime logic for that state.
- **Confidence:** High

## Domain Logic: Battle Mechanics
**Authoritative File:** `src/Ankimon/poke_engine/instruction_generator.py` and `src/Ankimon/poke_engine/damage_calculator.py`
- **Why:** These files calculate what actually happens when an attack is used (damage amounts, status effect applications, immunities).
- **Distinction:** `battle_functions.py` in the root `functions/` directory merely *formats* the text and updates the Anki UI based on the output of the `poke_engine`. Do not try to fix damage calculations in `battle_functions.py`.
- **Confidence:** High

## State Transitions: Battle Loop
**Authoritative File:** `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`
- **Why:** It acts as the reducer/state-transition manager. It takes the Ankimon state, feeds it to the engine, receives the instructions, and returns the *new* state.
- **Confidence:** High

## Persistence
**Authoritative File:** JSON files in `src/Ankimon/user_files/` (specifically `mypokemon.json` and `mainpokemon.json`).
- **Why:** The codebase explicitly relies on these files to save collection state.
- **Distinction:** There is an ongoing/planned migration to `ankimon.db` (SQLite) managed by `database_manager.py` (noted in memory). However, current inspected active paths still heavily utilize JSON logic (e.g., `update_main_pokemon.py`). If a bug involves data not saving, both the JSON writers and any new DB managers must be checked.
- **Confidence:** Medium (Due to transitionary architecture).

## UI Rendering
**Authoritative Files:** `src/Ankimon/pyobj/*.py` (e.g., `pc_box.py`, `item_window.py`) and `src/Ankimon/texts.py`.
- **Why:** PyQt6 widget layouts are defined in the `pyobj` files. HTML templates for Anki's reviewer are defined in `texts.py`.
- **Confidence:** High

## If a future agent edits only one file per category, start here:
- **Orchestration:** `src/Ankimon/__init__.py`
- **Config:** `src/Ankimon/pyobj/settings.py`
- **Pokémon Data Model:** `src/Ankimon/pyobj/pokemon_obj.py`
- **Battle Mechanics:** `src/Ankimon/poke_engine/instruction_generator.py`
- **Battle/Anki Integration:** `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`
