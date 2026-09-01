# Ankimon Invariants & Architectural Rules

When reviewing code changes in Ankimon, verify that none of these core architectural invariants are violated.

---

## 🏛️ 1. Core / GUI Split (The Headless Seam)

Ankimon decouples the game core from Anki/Qt so it can be tested completely headless.

- **No Top-Level `aqt` or `PyQt6` in Core**: Modules under `src/Ankimon/` that represent domain logic (`battle_loop.py`, `card_hooks.py`, `business.py`, `functions/`, `database_manager.py`, `core.py`) must **never** import `aqt`, `anki`, or `PyQt6` at top-level.
- **Read State from `services`**: Read shared objects (`db`, `logger`, `settings`, `main_pokemon`, `enemy_pokemon`, `trainer_card`, `achievements`) from `services`, never by reaching into `mw`.
- **UI Operations Route Through `services.ui`**: Input dialogs, prompts, and user alerts must go through `services.ui` (`HeadlessPresenter` in headless mode, `QtPresenter` in production) rather than instantiating Qt dialogs directly inside game logic.
- **Self-Adapting Pure-Output Helpers**: Pure GUI helpers (tooltips, sound effects, HUD updates) must guard Qt imports, always emit a structured event (`events.py`), and render only when Qt is present.

---

## 🗄️ 2. Database & Data Storage

- **Database Path**: All user data is stored in SQLite (`user_files/ankimon.db`).
- **Never Modify Shipped Databases**: Static assets and bundled data are read-only; runtime data lives exclusively in `user_files/`.
- **Backward Compatibility & Migrations**: Schema alterations must be handled safely in `database_manager.py` without requiring manual DB deletes or crashing on existing user saves.
- **Key Table Invariants**:
  - `captured_pokemon`: `(individual_id, is_main, data)` — `data` is JSON-encoded Pokemon dictionary.
  - `items`: `(item_id, count)`
  - `badges`: `(badge_id, timestamp)`
  - `team`: `(slot, individual_id)`

---

## 🐉 3. Pokémon Data & Encounter System

- **Mega / Gmax / Alternate Forms (IDs ≥ 10000)**: Must be resolved to their base species ID via `check_id_ok()` before generation-toggle checks or Pokédex registry lookups. Failing to resolve base IDs causes silent crashes or missing dex entries.
- **Encounter Prerequisite Chains**: Encounter prerequisite chains must form a **strict Directed Acyclic Graph (DAG)**. Any circular dependency will cause infinite recursion and freeze Anki.
- **Lowercase Lookup Keys**: Always lowercase and strip hyphens from species/item keys before database lookups or cache indexing. Capitalized keys fail silently.
- **Stat Formulas**: Max HP and CP must follow standard formulas:
  $$\text{Max HP} = \left\lfloor\frac{(2 \times \text{Base HP} + \text{IV} + \lfloor\text{EV}/4\rfloor) \times \text{Level}}{100}\right\rfloor + \text{Level} + 10$$

---

## ⚡ 4. Performance & Hot-Path Safety

- **No Synchronous Disk I/O During Review**: Static data (`pokedex.json`, `learnsets.json`, `pokemon_species.csv`) is parsed once at startup. Use cached lookups (`search_pokedex_by_id`, `_get_learnset_moves`), never `json.load(open(...))` during card answers.
- **No Background Thread UI Access**: Anki/Qt widgets can only be manipulated on the main Qt thread. Background tasks (`QueryOp` workers) must return plain data and execute UI changes exclusively in the main-thread success callback.
- **WebEngine Rendering**: Avoid CSS `backdrop-filter: blur()` in WebShell views as it causes severe DWM compositor flicker on Windows under QWebEngine.

---

## 📦 5. Submodule & Packaging Invariants

- **`poke_engine` Submodule**: Battle simulation lives in `src/Ankimon/poke_engine`. The engine has zero ankimon imports. All interactions must go through the bridge file `functions/ankimon_hooks_to_poke_engine.py`.
- **Packaging Boundary**: The shipped `.ankiaddon` package consists **only** of `src/Ankimon/`. Tooling, tests, and harness scripts must remain in `harness/` or `tests/` and never inside `src/`.
