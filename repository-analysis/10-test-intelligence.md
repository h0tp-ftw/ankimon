# Test Intelligence

This document outlines the state of the test suite and what it implies about the codebase.

## Major Test Locations and Styles
- Tests are located entirely within the `tests/` directory.
- Based on `ls -l tests`, there are very few test files:
  - `test_addon_integrity.py`
  - `test_ankimon_integrity.py`
  - `test_code_integrity.py`
  - `test_settings_consistency.py`
- The tests are run using `pytest` (e.g., `python3 -m pytest tests/`).
- The prompt memory indicates that tests require extensive mocking of `aqt` and `anki` (via `sys.modules`) before import, because importing `src/Ankimon/__init__.py` triggers Anki-specific code that fails outside the Anki environment.
- The prompt memory also indicates the need for `QT_QPA_PLATFORM=offscreen` to run UI tests headlessly.

## What behavior the tests seem to define
- The test names (`*integrity.py`, `*_consistency.py`) strongly suggest the test suite is currently focused on **structural validation and basic smoke testing**, rather than deep behavioral specification.
- `test_settings_consistency.py` likely checks that configuration keys are present, correctly typed, and have defaults.
- `test_ankimon_integrity.py` likely checks that the main modules can be imported without crashing (once mocked) and that vital paths/resources exist.

## Areas with strong behavioral specification
- **None currently identified.** The absence of files like `test_battle.py`, `test_damage_calculator.py`, or `test_encounter.py` implies that the core domain logic is not heavily specified by automated tests.

## Areas with weak or missing specification
- **The `poke_engine`:** This is the most complex part of the system (damage calculation, turn logic), yet there is no evidence of a comprehensive test suite for it in the `tests/` directory.
- **State persistence:** Logic for writing/reading `mypokemon.json` or handling the SQLite migration does not appear to be tested.
- **Anki Hook Logic:** The orchestration in `__init__.py` is untested (and hard to test due to Anki coupling).

## Suspicious Gaps
- The `poke_engine` contains thousands of lines of highly conditional logic (e.g., `instruction_generator.py`). The lack of tests here suggests that modifications to battle mechanics carry a very high risk of introducing regressions.
- The complexity of mocking `aqt` suggests that the architecture is tightly coupled to Anki, making unit testing difficult and leading to a sparse test suite.

## Missing Coverage Zones inferred from Code Complexity
1. **Damage Calculation:** Needs parameterized tests for STAB, weather, and type effectiveness.
2. **Persistence/Migration:** Needs tests ensuring that saving `main_pokemon` to JSON doesn't drop attributes.
3. **Battle Hook Translation:** Needs tests ensuring `simulate_battle_with_poke_engine` correctly translates a fainted engine state back to a fainted `PokemonObject`.
