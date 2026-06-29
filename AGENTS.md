# Ankimon — Agent & Contributor Guide

## What is Ankimon?

A Pokemon game addon for [Anki](https://apps.ankiweb.net/) — the spaced repetition flashcard app. Every card review triggers a Pokemon battle. You catch, evolve, and collect Pokemon while studying.

The addon runs inside Anki's Python/Qt environment. `aqt` is Anki's module, `mw` is the main window singleton.

## Repository Structure

```
src/Ankimon/              # The Anki addon (symlinked to addons21/ for dev)
  __init__.py             # Entry point (~175 lines) — imports, wiring, hook registration
  battle_loop.py          # Core battle loop (on_review_card), BattleState dataclass
  card_hooks.py           # Card timer + answer quality tracking hooks
  changelog.py            # GitHub changelog fetch + update notification
  discord_integration.py  # Discord Rich Presence hooks
  hook_registry.py        # Catch/defeat hook system for external integrations
  profile_hooks.py        # Profile lifecycle: tip of the day, monthly pokemon, sync
  reviewer_ui.py          # Reviewer shortcut keys + bottom bar buttons
  startup.py              # Boot sequence: backup, migration, assets, first enemy
  singletons.py           # Production composition root: build_core() + builds Qt windows + back-compat names
  core.py                 # [NEW] aqt-free composition (build_core) — builds game state with NO Qt
  services.py             # [NEW] service registry: db/logger/settings/game-state + UI/window ports
  events.py               # [NEW] structured event bus — OFF by default, zero-cost; observability seam
  ui_port.py              # [NEW] UI presenter port (HeadlessPresenter, aqt-free)
  gui_presenter.py        # [NEW] QtPresenter — production implementation of the UI port
  resources.py            # File paths (honors ANKIMON_USER_PATH override), constants, version detection
  business.py             # CP calculation, experience formulas
  functions/              # Game logic functions (encounters, battles, badges, etc.)
  pyobj/                  # Qt dialog classes (settings, shop, PC box, evolution, etc.)
  gui_classes/            # More UI classes (pokemon details, team view, etc.)
  poke_engine/            # Battle simulation engine (from ArdentRoe/poke-engine)
  user_files/             # User data directory (gitignored — DB, sprites, saves)
    sprites/              # Pokemon sprites (gitignored, downloaded on first run)
    ankimon.db            # SQLite database (all user data post-migration)
tests/                    # Test suite                          (DEV-ONLY — not shipped)
harness/                  # Headless agent harness — DEV-ONLY, OUTSIDE src/, NEVER in the .ankiaddon
.tier2/                   # Tier-2 env (venv + Qt libs + sprite cache + screenshots) — gitignored
```

> **What ships:** the `.ankiaddon` is built from `src/Ankimon/` only. Everything an agent
> uses to test (`harness/`, `tests/`, `.tier2/`) lives **outside** `src/` and is never packaged.
> Keep it that way — see "Headless agent harness" below.

## Architecture

### Data Flow

1. User reviews a card in Anki
2. `card_hooks.py` tracks timing and answer quality
3. `battle_loop.py` runs the battle (calls poke-engine)
4. `encounter_functions.py` handles catch/defeat/level-up
5. `reviewer_obj.py` updates the HUD via JavaScript injection

### Key Singletons (singletons.py)

- `main_pokemon` / `enemy_pokemon` — PokemonObject instances
- `settings_obj` — Settings loaded from DB or config.obf
- `ankimon_tracker_obj` — Tracks reviews, battles, multipliers
- `ankimon_db` — AnkimonDB (SQLite database manager)
- `trainer_card` — Player profile (level, cash, badges)

These now live in the `services` registry too (see below); `singletons.py` keeps the
module-level names for back-compat.

### Core / GUI split (the headless seam)

The addon's own logic is being decoupled from Anki/Qt so it can run and be tested
headless (this is what the harness drives):

- `services.py` — a small registry holding db/logger/settings/translator, the live
  game state (tracker, pokemon, trainer card, achievements) and the UI ports. **Read
  shared objects from `services`, not by reaching into `mw`.**
- `core.py` `build_core()` — aqt-free composition of the game state, called by BOTH
  `singletons.py` (production — then it builds the Qt windows) and the harness (which
  wires recording fakes instead). One source of truth so they can't drift.
- `events.py` — a structured event bus. Off by default (a single bool check, zero
  cost in production); the harness enables it to observe outcomes. Emit a semantic
  event at notable moments (encounter/battle/catch/defeat/faint/levelup/evolution).
- `ui_port.py` / `gui_presenter.py` — the UI presenter port. Input dialogs and error
  reporting go through `services.ui` (QtPresenter in production, a headless presenter
  in the harness) instead of importing Qt dialogs inside the logic.
- Pure-output GUI helpers (tooltips, sounds, popups) are **self-adapting**: guard the
  Qt import, always emit an event, render only when Qt is present.

**When adding logic:** read shared state from `services`; route any new dialog/popup
through `services.ui`; emit an event for any notable outcome; keep `aqt`/`PyQt6` out of
core modules' top-level imports (guard or lazy-import) so they stay headless-importable.

### Data Storage

All user data is in SQLite (`user_files/ankimon.db`). The `database_manager.py` handles all DB operations. Legacy JSON files are migrated on first run via `migration_dialog.py`.

Key tables: `captured_pokemon`, `items`, `badges`, `team`, `pokemon_history`, `metadata`

## Running Tests

```bash
# Install dependencies (once)
pip install pytest pytest-qt PyQt6 markdown

# Run all tests
python -m pytest tests/ 

# Run just the integrity test (imports every module)
python -m pytest tests/test_addon_integrity.py 
```

All tests should pass. The integrity test dynamically imports every module to catch ImportError/AttributeError at load time.

## Running Anki for Manual Testing

```bash
# Using the anki-vscode dev setup:
<PATH_TO_ANKI_EXECUTABLE> -b "<PATH_TO_ANKI_PROFILE>"

# Quick 20-second smoke test:
timeout 20 <PATH_TO_ANKI_EXECUTABLE> -b "<PATH_TO_ANKI_PROFILE>" 2>&1 || true
```

Clean startup should show: `AnkimonDB: Database schema initialized.` and `Ankimon Startup.` with no tracebacks.

## Headless agent harness (`harness/` — DEV-ONLY, never shipped)

Lets an agent **play and test Ankimon with no Anki and no clicking** — and observe
every outcome as a structured event stream. It lives in `harness/` (a sibling of
`src/`), so it is never part of the `.ankiaddon`. Full docs: **`harness/README.md`**.

**The one command (use this):** `python3 harness/check.py` runs the entire Tier-1 suite
(import probes + smoke play-through + regression test) — no Anki/Qt/pip — and exits non-zero
on any failure. CI runs it on **every PR** (`.github/workflows/harness.yml`), so the loop is:
write code → `python3 harness/check.py` → green → review → ship. `--doctor` diagnoses setup;
`make check` is equivalent if you have make. New `probe_*.py` files join the gate automatically.

Two tiers:

**Tier 1 — fast, zero-deps (no Anki, no Qt).** Imports the aqt-free core directly and
drives the real battle loop with recording fake windows. Runs under plain `python3`.
Best for logic/PR validation (and CI without Qt).

```bash
python3 harness/checks/probe_leaves.py        # all core modules import without aqt
python3 harness/scenarios/smoke_play.py       # answer cards, catch + defeat
python3 harness/scenarios/longrun.py 2000     # thousands of turns; aggregates events
python3 tests/test_headless_harness.py        # pytest-compatible regression test
```

**Tier 2 — the REAL add-on, headless (offscreen Qt).** Boots the genuine
`import Ankimon` (real `__init__` → `singletons` → every real Qt window) with only the
Anki host faked (`harness/fake_aqt.py`) and real PyQt6 in offscreen mode. Reproduces
real-Qt behaviour — widget memory, glitches, crashes — and runs window-internal logic
(PC box, etc.). Sudo-free setup (venv + locally-extracted Qt libs under `.tier2/`):

```bash
bash harness/setup_tier2.sh                 # one-time: venv + native Qt libs (no sudo)
python3 harness/fetch_sprites.py            # optional: real sprite set (~600MB), pixel-accurate
source .tier2/env.sh                        # LD_LIBRARY_PATH + QT_QPA_PLATFORM=offscreen + venv
python -m harness.checks.probe_real_play    # boot + play the real add-on
python -m harness.scenarios.pc_box_moves    # open the real PC box, change a Pokemon's moves
python -m harness.scenarios.soak 5000       # memory soak — watch RSS for leaks
python -m harness.scenarios.screenshots     # PNGs of the real battle window + PC box
```

**Using it to validate changes:**
- Editing core/game logic → run a Tier-1 scenario (or `tests/test_headless_harness.py`)
  and confirm: no `error` events, HP stays in `[0, max]`, caught-count/levels move as
  expected. `drain_events()` after each action is how you observe.
- Editing a real window → run the matching Tier-2 scenario + a screenshot.
- Reproducing a bug report ("X's move/ability won't work") → construct the exact
  state and drive it: `Driver(seed={"main": {...}})` for a specific team, `set_enemy(...)`
  for a specific wild Pokemon, or `Driver(db=<save>)` to boot on an existing save, then
  watch the `battle` events. See `harness/fixtures.py` + `harness/checks/probe_fixtures.py`.
  (Dev-only — `fixtures.py` only writes the same plain-JSON DB a user can already edit;
  it stays in `harness/`, never `src/`, and generated saves are throwaway, never committed.)
- Hunting bugs/leaks/regressions → fuzz actions, soak for memory, or diff the event
  stream of two branches.
- Profiling perf/leaks → wrap a workload in `harness/diagnostics.py` `profile(d, memory=True)`
  (or `scenarios/profile_battles.py N`) for DB-query counts (spots N+1s/rescans), cProfile
  hotspots, and RSS/tracemalloc growth. Query counts + cProfile shape are hardware-independent;
  wall/RSS are indicative on this box, not the user's felt latency. Swap the engine with
  `backend="pyinstrument"` (etc.) — optional tools come from `harness/requirements-dev.txt`.
- Stepping through code → it's a normal process: `python3 -m pdb <scenario>`, or attach debugpy
  (`harness/debug.py`, or `python3 -m debugpy --listen 5678 --wait-for-client <scenario>`) to set
  breakpoints in `src/Ankimon` and inspect variables mid-battle. Tooling/debug packages go in a
  venv via `harness/requirements-dev.txt` — **never** as add-on deps (the shipped addon stays dep-free).
- Long-horizon: the session is persistent — issue thousands of sequential actions
  (`longrun.py` / `soak.py` do 10k+; ~900 turns/s in Tier 1). Real-time delays are
  skipped (full speed); the **calendar** is controllable — pass `clock_start=datetime(...)`
  and call `advance_time(days=…, hours=…)` to drive day/night evolutions, daily resets,
  and streaks. Full **event + action reference** is in `harness/README.md`.

**Harness rules (important):**
- It MUST stay outside `src/`. **Never move harness/test tooling into `src/Ankimon`** —
  that directory is the shipped add-on.
- Dependency is one-way: `harness/` imports `src/Ankimon`, never the reverse. (`src/`
  only *mentions* the harness in explanatory comments.)
- `.tier2/` is gitignored (large, machine-specific); recreate with `setup_tier2.sh` /
  `fetch_sprites.py`.
- Tier 2 prerequisites: PyQt6 + native Qt libs (the setup script handles both without
  sudo); poke_engine submodule initialized (`git submodule update --init`).

## Making Changes

### Rules

- Run `pytest tests/` if making changes that affect Python code. All tests must pass.
- Run the Anki smoke test for anything touching startup, imports, or singletons.
- Never modify user data files (anything gitignored).
- The `__init__.py` is a thin orchestrator — add new logic to the appropriate extracted module, not to init.
- `singletons.py` / `core.py` instantiate objects — don't add game logic there.
- Imports from `poke_engine/` should only happen via `functions/ankimon_hooks_to_poke_engine.py` (the bridge file). The engine itself has zero ankimon imports.
- Keep core modules aqt-free: read db/logger/settings/state from `services`, route dialogs/popups through `services.ui`, and guard or lazy-import any `aqt`/`PyQt6` so the modules stay headless-importable.
- You can validate most logic changes WITHOUT launching Anki via the headless harness (see "Headless agent harness") — much faster than the Anki smoke test.

### PR Workflow

- Every change goes through a PR, even small fixes. No direct pushes to main.
- PRs from external contributors: push adapted code to their branch if `maintainerCanModify` is true, then merge their PR so they get credit.
- Reference the original issue/PR number in commit messages: `fix: nickname bug (#361)`

### Common Pitfalls

- `aqt` and `anki` modules are only available inside Anki runtime. Tests must mock them.
- Qt widgets can only be created/accessed on the main thread.
- `settings_obj.get()` is called live everywhere — values are not cached at startup.
- `poke_engine/` contains the battle simulation engine. Only `functions/ankimon_hooks_to_poke_engine.py` bridges it to ankimon — the engine itself has zero ankimon imports.
- Sprites are gitignored and downloaded on first run. Source of truth: `h0tp-ftw/ankimon-sprites` repo.
- The `user_files/` directory is for runtime data. Never commit files there.

### Test Integrity Ignore List

The integrity test skips these modules (they require full Anki runtime):
- `Ankimon.singletons` (StopIteration from mock Qt widgets)
- `Ankimon.pyobj.tip_of_the_day` (uses `from aqt.qt import *` at class level)
- `Ankimon.poke_engine.tests.*` / `Ankimon.poke_engine.setup` (upstream test files)

If you add a new module that crashes during import without Anki, add it to `ignore_modules` in `test_addon_integrity.py` AND explain why.

## External Repos

- `h0tp-ftw/ankimon-sprites` — Sprite assets. GitHub Action auto-builds ZIP + syncs to HuggingFace.
- `ArdentRoe/poke-engine` — Battle simulation engine used as a submodule.
- `h0tp-ftw/anki-vscode` — Dev environment setup for running Anki with debugger.
