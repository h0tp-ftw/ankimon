# Contributing to Ankimon

Thank you for your interest in contributing to Ankimon! This document provides guidelines and instructions for setting up your environment, making changes, and submitting contributions.

Please read through these guidelines before starting your work.

---

## Table of Contents
1. [Code of Conduct](#code-of-conduct)
2. [How to Contribute](#how-to-contribute)
3. [Repository Structure](#repository-structure)
4. [Development Environment Setup](#development-environment-setup)
5. [Testing & Quality Assurance](#testing--quality-assurance)
6. [Development Rules & Best Practices](#development-rules--best-practices)
7. [PR Workflow](#pr-workflow)
8. [External Repositories](#external-repositories)

---

## Code of Conduct

We wish to maintain a welcoming, inclusive, and collaborative environment. Please treat all contributors and maintainers with respect.

### Expectations

- **No Unfair Advantage**: You cannot give people an unfair advantage (e.g., giving out Pokemon or items). Similarly, you cannot have a fork or Pull Request where users get an unfair advantage without lead and other contributors' approval. We must keep our rarity system balanced, and not give out advantages to boost progress or make the game easier than it is intended to be (which will make Ankimon lose its excitement and charm!).
- **Community Standards**: Please uphold community standards, making it a welcoming and supportive environment for everyone. We will work with people of all kinds of development and educational backgrounds, and this diversity should be a strength, not a barrier!

---

## How to Contribute

1. **Report Bugs / Request Features**: Check existing issues first. If your issue is new, open a detailed issue describing the problem, steps to reproduce, or the feature request.
2. **Submit Pull Requests (PRs)**: 
   - Fork the repository.
   - Create a feature branch (`git checkout -b feature/amazing-feature`).
   - Implement your changes while following our development guidelines.
   - Test your changes thoroughly.
   - Submit a pull request to the `main` branch.

---

## Repository Structure

```text
src/Ankimon/              # The Anki addon (symlinked to addons21/ for dev)
  __init__.py             # Entry point — imports, wiring, hook registration
  battle_loop.py          # Core battle loop (on_review_card), BattleState dataclass
  card_hooks.py           # Card timer + answer quality tracking hooks
  changelog.py            # GitHub changelog fetch + update notification
  discord_integration.py  # Discord Rich Presence hooks
  hook_registry.py        # Catch/defeat hook system for external integrations
  profile_hooks.py        # Profile lifecycle: tip of the day, monthly pokemon, sync
  reviewer_ui.py          # Reviewer shortcut keys + bottom bar buttons
  startup.py              # Boot sequence: backup, migration, assets, first enemy
  singletons.py           # All singleton objects (settings, pokemon, tracker, etc.)
  resources.py            # File paths, constants, version detection
  business.py             # CP calculation, experience formulas
  functions/              # Game logic functions (encounters, battles, badges, etc.)
  pyobj/                  # Qt dialog classes (settings, shop, PC box, evolution, etc.)
  gui_classes/            # More UI classes (pokemon details, team view, etc.)
  poke_engine/            # Battle simulation engine (from ArdentRoe/poke-engine)
  user_files/             # User data directory (gitignored — DB, sprites, saves)
    sprites/              # Pokemon sprites (gitignored, downloaded on first run)
    ankimon.db            # SQLite database (all user data post-migration)
tests/                    # Test suite
```

### Architecture Overview

- **Data Flow**:
  1. User reviews a card in Anki.
  2. `card_hooks.py` tracks timing and answer quality.
  3. `battle_loop.py` runs the battle (calls `poke-engine`).
  4. `encounter_functions.py` handles catch, defeat, and level-ups.
  5. `reviewer_obj.py` updates the HUD via JavaScript injection.

- **Key Singletons** (`src/Ankimon/singletons.py`):
  - `main_pokemon` / `enemy_pokemon` — `PokemonObject` instances
  - `settings_obj` — Settings loaded from the database or `config.obf`
  - `ankimon_tracker_obj` — Tracks reviews, battles, and multipliers
  - `ankimon_db` — `AnkimonDB` (SQLite database manager)
  - `trainer_card` — Player profile containing level, cash, badges, etc.

- **Data Storage**:
  All user data is in SQLite (`src/Ankimon/user_files/ankimon.db`). The database manager handles DB operations. Legacy JSON files are automatically migrated on first run via `migration_dialog.py`.
  - Key tables: `captured_pokemon`, `items`, `badges`, `team`, `pokemon_history`, `metadata`.

---

## Development Environment Setup

You can set up your development environment either automatically (recommended) or manually.

### Option A: Automated Setup (Recommended)

Ankimon provides an interactive setup script to configure git submodules, create a virtual environment, install dependencies, link the addon to Anki, and generate VS Code configuration files.

1. **Clone the Repo RECURSIVELY**:
   ```bash
   git clone --recursive https://github.com/h0tp-ftw/ankimon.git
   cd ankimon
   ```

2. **Run the Setup Script**:
   ```bash
   python setup.py
   ```
   Follow the prompts to detect your Anki addons directory and automatically configure VS Code.

---

### Option B: Manual Setup

If you prefer to configure everything yourself:

1. **Clone the Repo RECURSIVELY**:
   ```bash
   git clone --recursive https://github.com/h0tp-ftw/ankimon.git
   cd ankimon
   ```

2. **Install Dependencies**:
   It is recommended to use a virtual environment:
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate

   pip install -r requirements.txt
   ```

3. **Link the Addon to Anki**:
   Symlink or copy the `src/Ankimon/` directory to your Anki addons directory (usually under `addons21/` in your Anki profile folder) and name the folder `1908235722`.

---

## Testing & Quality Assurance

### Running Tests
Before submitting a PR, make sure all tests pass:

```bash
# Run all tests
python -m pytest tests/ -v

# Run just the integrity test (imports every module)
python -m pytest tests/test_addon_integrity.py -v
```

All tests must pass. The integrity test dynamically imports every module to catch `ImportError` or `AttributeError` at load time.

### Test Integrity Ignore List
The integrity test skips modules that require a full running Anki environment:
- `Ankimon.singletons` (due to `StopIteration` from mock Qt widgets)
- `Ankimon.pyobj.tip_of_the_day` (uses `from aqt.qt import *` at class level)
- `Ankimon.poke_engine.tests.*` / `Ankimon.poke_engine.setup` (upstream test files)

> [!NOTE]
> If you add a new module that crashes during import without Anki, add it to `ignore_modules` in `tests/test_addon_integrity.py` and explain why.

### Manual Smoke Testing
To test your changes inside Anki:
```bash
# Using the anki-vscode dev setup:
<PATH_TO_ANKI_EXECUTABLE> -b "<PATH_TO_ANKI_PROFILE>"

# Quick 20-second smoke test:
timeout 20 <PATH_TO_ANKI_EXECUTABLE> -b "<PATH_TO_ANKI_PROFILE>" 2>&1 || true
```
A clean startup should print:
```text
AnkimonDB: Database schema initialized.
Ankimon Startup.
```
Ensure there are no tracebacks.

---

## Development Rules & Best Practices

Please adhere to the following rules when developing features or fixing bugs:

- **Run Tests**: Always run `pytest tests/` after every change.
- **Run Smoke Tests**: Run the Anki smoke test for anything touching startup, imports, or singletons.
- **Keep `__init__.py` Thin**: The entry point should only handle wiring, hook registration, and imports. Add new logic to dedicated, extracted modules.
- **Do Not Add Logic to `singletons.py`**: It should only instantiate and expose objects.
- **Never Modify User Data Files**: Never modify or check in files in `src/Ankimon/user_files/` (sprites, DBs, configuration files, etc.) as they are gitignored and managed at runtime.
- **Bridge File for `poke_engine/`**: The battle simulation engine is a submodule. Imports from `poke_engine/` must only happen via `src/Ankimon/functions/ankimon_hooks_to_poke_engine.py` (the bridge file). The engine itself has zero `Ankimon` imports.
- **Error Handling**: Use the custom error handler `show_warning_with_traceback` (imported from `src/Ankimon/pyobj/error_handler.py`) when catching exceptions. This utility logs the error, automatically scrubs user paths from the traceback for privacy, and displays a styled warning dialog featuring report links and a "Copy Debug Info" button.

### Common Pitfalls
- **Mocking `aqt` and `anki`**: The `aqt` and `anki` modules are only available inside the Anki runtime environment. Tests must mock these modules.
- **Qt Main Thread Rule**: Qt widgets can only be created or accessed on the main thread.
- **Dynamic Settings**: `settings_obj.get()` is evaluated live everywhere; settings are not cached at startup.

---

## PR Workflow

- **Branch Off `main`**: Always create a feature branch off the `main` branch.
- **No Direct Pushes**: Every change must go through a Pull Request, even small ones.
- **External PRs**: For external contributors, if `maintainerCanModify` is set to true, maintainers may push adapted code directly to the branch before merging to ensure credit is given.
- **Commit Messages**: Reference the original issue or PR number in commit messages, e.g., `fix: nickname bug (#361)`.

---

## External Repositories

- [h0tp-ftw/ankimon-sprites](https://github.com/h0tp-ftw/ankimon-sprites): Sprite assets. The CI builds a ZIP and syncs it to HuggingFace.
- [ArdentRoe/poke-engine](https://github.com/ArdentRoe/poke-engine): The battle simulation engine submodule.
- [h0tp-ftw/anki-vscode](https://github.com/h0tp-ftw/anki-vscode): Dev environment setup for running Anki with the debugger.
