# Agent Handoff

This document is a concise, operational summary designed to bring the next coding agent up to speed immediately.

## What is well understood
- **The Core Loop:** The add-on is driven by Anki's flashcard review hooks (`__init__.py` -> `on_review_card`).
- **The Battle Engine:** Complex logic is outsourced to a sub-package (`poke_engine/`) which acts as a state machine. The translation layer (`ankimon_hooks_to_poke_engine.py`) is the critical bridge.
- **Global State:** The add-on heavily relies on dependency injection of global singletons created in `singletons.py` (e.g., `settings_obj`, `main_pokemon`).
- **UI Management:** PyQt windows must generally be configured with `WA_DeleteOnClose` to prevent memory leaks.

## What is partially understood
- **The Persistence Layer:** Data is stored in JSON files in `user_files/`. However, there are references to a SQLite migration (`ankimon.db`). The boundary between old JSON methods and new DB methods is porous.

## What is uncertain
- **Test Coverage limits:** It is unconfirmed whether the `poke_engine` has its own isolated test suite.
- **`AnkimonTracker` lifecycle:** The exact reset triggers for the session tracking variables.

## What files should be consulted first for future work
1. **Adding a Feature:** `src/Ankimon/pyobj/settings.py` (for configs), `src/Ankimon/singletons.py` (to inject dependencies).
2. **Fixing a Battle Bug:** `src/Ankimon/poke_engine/instruction_generator.py` and `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`.
3. **Fixing a Save/Load Bug:** `src/Ankimon/functions/update_main_pokemon.py` and `src/Ankimon/pyobj/pokemon_obj.py`.
4. **General Flow:** `src/Ankimon/__init__.py`.

## Safest vs. Riskiest Edits
- **Safest Edits:** Changing UI text in HTML templates (`texts.py`), adjusting default settings, or adding helper functions to `utils.py`.
- **Riskiest Edits:** Modifying the Anki hooks in `__init__.py`, changing the signature of `PokemonObject`, or altering the execution order in `instruction_generator.py`.

## What future agents should absolutely not overlook
1. **Never use `print`:** Use the global `logger` (e.g., `logger.log("info", "message")`).
2. **Never blind-update state:** If you modify `main_pokemon` dynamically, ensure you call the appropriate save functions so progress isn't overwritten by the static `mypokemon.json` cache on the next load.
3. **Always use strict type checks:** Differentiate between `True` and `1` when checking Qt settings.
4. **Beware circular imports:** Do not import `singletons.py` directly into deeply nested utility functions. Pass the required objects as arguments.
