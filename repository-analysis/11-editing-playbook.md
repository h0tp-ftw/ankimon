# Editing Playbook

This document provides operational guidance for future agents to make safer edits to the Ankimon codebase.

## How to safely approach a bug fix

1. **Reproduce mentally via code tracing:** If the bug is a battle mechanic (e.g., wrong damage), trace from `__init__.py` -> `simulate_battle_with_poke_engine` -> `poke_engine/instruction_generator.py` -> `poke_engine/damage_calculator.py`. Do not assume the bug is in the UI text formatting.
2. **Identify the Source of Truth:** Look at `repository-analysis/04-source-of-truth.md`. Ensure you are modifying the actual logic, not a downstream wrapper.
3. **Check for JSON Caches:** If the bug involves missing data or "reverting" states, remember the "De-evolution bug" from the risk register. Check if the code is mutating memory but failing to call `save_main_pokemon()` or update `mypokemon.json`.
4. **Mock and Test (if possible):** If fixing a utility function, write a fast test. If fixing Anki-bound UI, test manually or rely on rigorous code inspection, as UI tests require complex mocks.

## How to safely approach a feature change

1. **UI Additions:**
   - Put new UI classes in `src/Ankimon/gui_classes/` or `src/Ankimon/pyobj/`.
   - Ensure you use `self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)` in the `__init__`.
   - Inject dependencies (`logger`, `settings_obj`) via the constructor. Do not rely on globals.
2. **Configuration:**
   - Add new settings to `src/Ankimon/pyobj/settings.py` defaults.
   - Use integer toggles (1/0) for booleans in UI groups to avoid Qt string comparison bugs.
3. **Data Changes:**
   - If adding a new attribute to a Pokémon, it must be added to:
     a) `new_values` dictionary in `data_handler.py`.
     b) The `__init__` and `update_stats` methods of `PokemonObject`.
     c) The serialization/deserialization logic when saving to JSON.
     d) The translation layer in `ankimon_hooks_to_poke_engine.py` (if the battle engine needs to know about it).

## How to safely approach a refactor

1. **Avoid Refactoring `__init__.py` unless requested:** It is a massive, brittle dependency hub.
2. **Helper Functions:** When removing redundant logic, create generic helpers in `src/Ankimon/utils.py` (like `get_random_file_from_directory`), but leave the old specific function names as wrappers pointing to the new helper to prevent breaking external dependencies.
3. **Database Migration:** If moving from JSON to SQLite, do not delete the JSON functions immediately. Implement a dual-write or migration layer first.

## What files should be checked before changing a core behavior

- **Changing when battles happen:** Check `__init__.py` and `ankimon_tracker.py`.
- **Changing Pokémon stats:** Check `pokemon_obj.py`, `resources.py` (for data paths), and `poke_engine/battle.py` (to see if the engine supports the stat).
- **Changing UI layout:** Check `gui_entities.py` (for base styles) and `texts.py` (for HTML templates).

## Distinguishing local vs system-wide changes

- **Local:** Changing a tooltip color in `battle_functions.py` or tweaking a specific move's base damage in the `poke_engine` JSONs.
- **System-wide:** Changing how `settings_obj` saves, modifying the signature of `PokemonObject.__init__`, or changing the file structure in `resources.py`. System-wide changes require cascading updates to almost every file in `pyobj/`.

## Common traps likely to mislead future agents

- **The `print` vs `logger` trap:** Do not use `print`. Use `logger.log("info", message)`. The project enforces this strictly.
- **The Global Variable trap:** Seeing `main_pokemon` used globally in `__init__.py` might tempt you to `import main_pokemon from Ankimon.singletons` inside a deep utility function. This causes circular imports. Pass it as an argument instead.
- **The Dictionary/List Array trap:** Due to ongoing SQLite migrations, stats objects might appear as dicts `{"hp": 10}` OR as lists `[10, ...]`. Code that aggregates stats must handle both formats dynamically.
- **The JSON library trap:** Use `orjson`, not `json`, for performance.

## What to verify before and after editing

- **Before:** Read the target file completely. Understand its imports.
- **After:** Use `read_file` to ensure your edit applied cleanly without breaking indentation or dropping `import` statements. Run `export PYTHONPATH=src:$PYTHONPATH && QT_QPA_PLATFORM=offscreen python3 -m pytest tests/` to ensure no syntax errors or import failures were introduced.
