# Unknowns and Questions

This document captures areas of uncertainty in the repository analysis. Future agents should investigate these before making changes in the affected areas.

## 1. Status of the SQLite Database Migration
- **Uncertainty:** The memory context explicitly mentions an ongoing migration to an SQLite database (`ankimon.db`) to resolve JSON performance issues. However, the inspected active files (`data_handler.py`, `resources.py`, `update_main_pokemon.py`) still heavily rely on JSON read/write operations.
- **Why it matters:** If the migration is complete but housed in an uninspected file (e.g., a hidden `database_manager.py`), editing the JSON functions might introduce regressions or write to dead code paths.
- **Missing Evidence:** Inspection of the actual database manager file or the code that creates the SQLite tables.
- **Likely Location:** A file named `database_manager.py` or similar in `src/Ankimon/pyobj/` or `src/Ankimon/utils/`.
- **Priority:** High

## 2. Test Execution Environment Completeness
- **Uncertainty:** While the command `python3 -m pytest tests/` runs successfully headlessly with `pytest-qt`, it only executes two files (`test_ankimon_integrity.py`, `test_settings_consistency.py`). It is unknown if there is a separate test suite for the `poke_engine` that is run differently, or if the engine is truly completely untested.
- **Why it matters:** Making changes to the `poke_engine` without test coverage is extremely risky.
- **Missing Evidence:** A deep scan of the `poke_engine` directory for files starting with `test_` or a separate CI configuration file.
- **Likely Location:** `src/Ankimon/poke_engine/` or `.github/workflows/`.
- **Priority:** Medium

## 3. The Definition of `AnkimonTracker` State
- **Uncertainty:** The exact lifecycle of variables inside `AnkimonTracker` (e.g., `cards_battle_round`, `multiplier`) and how they reset upon Anki restart vs. profile switch vs. battle end.
- **Why it matters:** If an agent is tasked with changing the encounter rate or fixing a "battles trigger too often" bug, they need to know if the tracker persists to disk or is purely ephemeral in memory.
- **Missing Evidence:** Complete reading of `src/Ankimon/pyobj/ankimon_tracker.py` and its interaction with `data_handler.py`.
- **Likely Location:** `src/Ankimon/pyobj/ankimon_tracker.py`.
- **Priority:** Medium

## 4. `poke_engine` Sync Mechanics
- **Uncertainty:** How exactly does `ankimon_hooks_to_poke_engine.py` handle the discrepancy between `PokemonObject` attributes (like `current_hp`) and the engine's `State` objects? The comment `main_pokemon.hp = new_state.user.active.hp` exists in `__init__.py`, but it's unclear if stat boosts, volatile statuses, and PP are also synced back reliably.
- **Why it matters:** If a user complains about moves having infinite PP or statuses not wearing off, it might be due to a failure in the translation layer, not the engine itself.
- **Missing Evidence:** Full review of the return tuple mapping in `simulate_battle_with_poke_engine`.
- **Likely Location:** `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`.
- **Priority:** High
