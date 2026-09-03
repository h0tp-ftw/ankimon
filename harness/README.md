# Ankimon agent harness

Play Ankimon **headlessly** — no Anki, no Qt, no display — so an AI agent (or a
plain test) can drive the real game logic, observe a structured event stream, and
validate features/PRs without a human clicking through Anki.

This is dev-only tooling. It is **not** shipped with the add-on.

## Quickstart (one command)

```bash
python3 harness/check.py            # the full Tier-1 gate — no Anki/Qt/pip. Exit 0 = green.
python3 harness/check.py --doctor   # if something's off (python version / submodule)
# `make check` and `make doctor` are equivalent sugar if you have make.
```

`check.py` auto-discovers every Tier-1 check (import probes + a smoke play-through +
the regression test), runs each isolated, and returns a single PASS/FAIL. It's the
**exact command CI runs on every PR** (`.github/workflows/harness.yml`), so the loop is:
**agent writes code → `python3 harness/check.py` → green → review → ship.**

## Why this exists

Anki add-ons are painful to test: every module historically imported `aqt`, so
importing anything dragged in the whole GUI runtime, and there was no
machine-readable record of what the game *did*. This harness is the payoff of the
"headless-core" refactor:

- The game **core** (DB, settings, battle loop, encounters, catching, leveling,
  evolution checks, the poke-engine bridge) now imports with **no `aqt`/`PyQt6`**.
- Every observable action emits a structured **event** (`encounter`, `battle`,
  `faint`, `catch`, `defeat`, `levelup`, `evolution_offered`, `tooltip`, `sound`,
  `hud`, `log`, `notify`, `error`, …) via `src/Ankimon/events.py`.
- GUI side-effects are reached through a small **UI presenter port**
  (`services.ui`) and the window objects in `services` — real Qt in Anki,
  recording fakes here.

## Requirements

- Plain `python3` (3.10+). **No** `aqt`, `PyQt6`, `pytest`, or a display needed.
- The `poke_engine` git submodule must be present (battle simulation):
  `git submodule update --init --recursive`.
- `requests` is used by a couple of helpers but not by the core loop.

## Run it

```bash
# Import-safety + smoke probes
python3 harness/checks/probe_foundations.py
python3 harness/checks/probe_leaves.py      # 26 core modules import aqt-free
python3 harness/checks/probe_core.py        # build_core() boots the whole game state

# Scripted play sessions
python3 harness/scenarios/smoke_play.py       # answer cards, catch + defeat
python3 harness/scenarios/auto_battle.py      # automatic_battle modes 1/2/3
python3 harness/scenarios/economy.py          # cash + buying items
python3 harness/checks/probe_fixtures.py      # load a save, seed state, reproduce a bug
python3 harness/scenarios/profile_battles.py 10000   # cProfile + DB queries + memory

# Interactive REPL — one JSON request per line in, one JSON response per line out
python3 -m harness.server
printf '{"action":"answer","ease":"good"}\n{"action":"get_state"}\n{"action":"quit"}\n' \
  | python3 harness/server.py
```

## Tier 2 — run the REAL add-on (offscreen Qt)

Tier 1 (above) is fast and runs anywhere, but it swaps Ankimon's real Qt windows
for recording fakes — so it can't reproduce real-Qt behaviour (widget memory,
crashes, glitches) or run logic that lives *inside* the window classes (e.g. the
PC box).

**Tier 2 boots the genuine add-on** — real `import Ankimon` → real `__init__.py`
→ `singletons.py` → every real Qt window — with only the *Anki host* faked
(`harness/fake_aqt.py`: `mw`, `gui_hooks`, `aqt.*`) and **real PyQt6 in offscreen
mode**. Nothing is drawn, but the real widgets/memory/PC box are all live, which
is what makes real-Qt glitches and "crash after N encounters" reproducible.

Base Tier 2 needs PyQt6 and native Qt libs. Its setup scripts require a Linux
userland because they use `apt-get`, `dpkg-deb`, Linux library paths, and `/proc`
memory statistics. On Windows, run Tier 2 from **WSL**, not PowerShell or Git
Bash; the checkout can remain on `C:` and be opened through `/mnt/c/...`.

The setup is **sudo-free** (a venv with pip bootstrapped via get-pip.py, and the
Qt `.deb`s *downloaded and extracted* into a local dir — nothing installed
system-wide; `rm -rf .tier2` undoes it). These are harness-only dependencies and
are never shipped in the `.ankiaddon`:

```bash
bash harness/setup_tier2.sh        # one-time: builds .tier2/ (venv + local Qt libs)
source .tier2/env.sh               # LD_LIBRARY_PATH + QT_QPA_PLATFORM=offscreen + venv
python -m harness.checks.probe_real_boot   # real add-on boots; objects are the REAL classes
python -m harness.checks.probe_real_play   # plays via real hooks: real windows, real battles
```

Real browser screens use the separate `PyQt6-WebEngine` package. Install it only
when needed, then run the strict browser probe:

```bash
bash harness/setup_webengine.sh
source .tier2/env.sh
export LD_LIBRARY_PATH="$PWD/.tier2/we-libs/extract/usr/lib/$(uname -m)-linux-gnu:$LD_LIBRARY_PATH"
python -m harness.checks.probe_real_webengine  # real Chromium Settings page + DOM save
```

Drive it from Python (same action surface as Tier 1, via real hooks/windows):

```python
from harness.real_driver import RealDriver
d = RealDriver(settings_overrides={"battle.cards_per_round": 1})
d.answer("good")     # fires the real reviewer_did_answer_card hook chain
d.catch()            # the real reviewer catch shortcut
d.get_state()
```

Tier-2 scenarios (drive + see the real windows):

```bash
python -m harness.scenarios.pc_box_moves   # open the real PC box, change a caught
                                           # Pokemon's moves (persists to DB) + screenshot
python -m harness.scenarios.screenshots    # PNGs of the real battle window + PC box
python -m harness.scenarios.soak 10000     # sustained real-Qt review/RSS soak
```

The whole-add-on `mega_fuzz` scenario also opens browser-backed menu screens, so
run `setup_webengine.sh` and export its native-library path as shown above first:

```bash
python harness/scenarios/mega_fuzz.py --seeds 12 --steps 100 --parallel 2
```

`harness/screenshot.py` (`grab(widget, path)`) renders any real widget to a PNG via
`widget.grab()` — offscreen Qt still paints to a buffer, so you get the genuine UI
(real sprites + layout) with no display.

### Real sprites (pixel-accurate)

By default a fresh profile has no sprites, so `real_env` seeds one placeholder
`substitute.png` — the real window code runs, just with placeholder pixels. To
run with the genuine Pokémon art, fetch the real sprite set (same `sprites.zip`,
~600 MB, the add-on uses; stdlib-only, sudo-free):

```bash
python3 harness/fetch_sprites.py          # -> .tier2/sprites-cache (one-time)
```

After that, each Tier-2 session symlinks its `sprites/` dir to that cache, so the
real windows load the real sprites (verified: e.g. `rhyhorn #111` ->
`front_default/111.png`). Set `ANKIMON_SPRITE_CACHE` to point elsewhere.

Normal Tier-2 boot/play probes still allow lightweight WebEngine stubs so they
remain useful on machines without Chromium. The dedicated
`probe_real_webengine` check is strict: it imports `PyQt6-WebEngine`, refuses to
fall back, opens the real HTML Settings shell, edits Trainer Name through the
DOM, clicks Save, and verifies the SQLite-backed Settings service changed.

## Drive it from Python

```python
from harness.driver import Driver

d = Driver(settings_overrides={"battle.cards_per_round": 1})
events = d.answer("good")     # answer a card -> the real battle loop runs
d.get_state()                 # JSON-able snapshot: main/enemy/tracker/collection/trainer
# when the wild Pokemon faints:
d.defeat()                    # or d.catch()  -> spawns the next encounter
```

### Actions (Driver methods, also the REPL `action` names)

| action | what it does |
|---|---|
| `answer(ease)` | answer a card (`1-4` or `again/hard/good/easy`) → runs the battle loop |
| `catch()` / `defeat()` | resolve a fainted wild Pokemon, then spawn the next |
| `encounter()` | force a brand-new (random) wild encounter |
| `set_enemy(species=, level=, ability=, moves=, …)` | force a **specific** wild encounter (dev fixtures) — for reproducing a reported bug |
| `set_setting(key, value)` | change a settings key (e.g. `battle.automatic_battle`) |
| `set_move(move)` | script the move chosen next turn (needs `controls.allow_to_choose_moves`) |
| `add_cash(n)` / `buy_item(name)` | drive the shop economy |
| `advance_time(days=, hours=, …)` | fast-forward the controllable clock (needs `clock_start=`) — drives day/night, daily resets, streaks |
| `time_of_day()` | Ankimon's current day/night reading |
| `get_state()` | snapshot of the world |
| `drain_events()` | events since the last drain |

Each action returns the events it produced; `get_state()` returns the snapshot.

## Existing progress, custom saves & bug repro (`harness/fixtures.py`)

Sessions don't have to start blank. You can **boot on an existing save** or
**construct an exact starting state**, then drive a reported bug to its conclusion
and watch it resolve in the event stream — no Anki, no clicking.

```python
from harness.driver import Driver

# Load arbitrary existing progress: the given ankimon.db is COPIED into a throwaway
# profile and booted on, so the source save is never mutated.
d = Driver(db="reports/issue361.ankimon.db")

# ...or construct a precise state. Pokemon are built from the game's OWN pokedex
# data (base stats, types, learnset, abilities), so only the fields you pin change.
d = Driver(seed={
    "main": {"species": "Gengar", "level": 50, "ability": "Levitate", "moves": ["Shadow Ball"]},
    "team": [{"species": "Pikachu", "level": 40}],
    "box":  [{"species": "Bulbasaur", "level": 5}],
    "items": {"great-ball": 10},
})

# Force the exact wild Pokemon a bug needs, then battle it:
d.set_enemy(species="Golem", level=50, moves=["Earthquake"])
d.answer("again")   # poor answers drop the multiplier < 1, so the wild Pokemon swings
```

`spec` fields (all optional except a species id): `species`|`id`, `level`, `ability`,
`moves`, `ivs`/`evs`, `nature`, `shiny`, `gender`, `held_item`, `hp` (pin a low HP to
reproduce a low-HP bug). Worked example + assertions: `harness/checks/probe_fixtures.py`
(e.g. it verifies Gengar's Levitate nullifies a forced Golem's Earthquake while a
non-immune control takes damage).

> **Dev-only, by design.** This lives in `harness/` and is **never shipped** — the
> add-on adds no "spawn Pokemon" affordance. It only writes the same plain-JSON
> `ankimon.db` a user can already edit by hand, and only from this unshipped tool.
> Keep generated saves as throwaway fixtures (temp dirs); never commit one or attach
> it to a release. Don't move any of this into `src/`.

## Profiling a workload (`harness/diagnostics.py`)

Wrap any sequence of actions and get a machine-readable report — **DB queries**
(grouped by normalized statement, so an N+1 collapses to one big-count row),
**cProfile** hotspots, **memory** (RSS delta + tracemalloc top allocators), and
wall time:

```python
from harness.driver import Driver
from harness.diagnostics import profile

d = Driver(settings_overrides={"battle.cards_per_round": 1})
with profile(d, label="10k battles", memory=True) as report:
    for _ in range(10_000):
        d.answer("good")
        if d.services.enemy_pokemon.hp <= 0:
            d.catch()
report.print()          # human-readable; report.as_dict() for assertions
```

Or just: `python3 harness/scenarios/profile_battles.py 10000`.

**Read it right:** the **query counts** and the **cProfile shape** are
hardware-independent — they tell you *where* the cost is and *how it scales*
(e.g. queries growing per page = an N+1; `copy.deepcopy` dominating = state-copy
churn), which is what you actually fix. **Wall time and RSS are indicative on this
box only** — for the felt milliseconds, measure the real window on real hardware.
This profiles the data/logic layer, not Qt rendering.

## Extending: other tooling & live debugging

The harness is a plain Python process driving the real game code, so any analysis or
debug tool that works on a Python program plugs into the same `Driver` workload.

**Profiler backends.** `profile(d, backend="pyinstrument")` swaps the engine; an
unknown or uninstalled backend falls back to stdlib cProfile with a note. The core
stays stdlib-only — optional tools live in a **venv**, never an add-on dependency:

```bash
python3 -m venv .venv && .venv/bin/pip install -r harness/requirements-dev.txt
```

That list includes pyinstrument, scalene, memray, py-spy, line_profiler, objgraph,
debugpy — point any of them at a `Driver` loop the way cProfile is wired.

**Live debugging — breakpoints + variable inspection.** Because it's a normal
process, set breakpoints in `src/Ankimon` and step through them while a *simulated*
review runs — no Anki:

```bash
# stdlib pdb (zero deps) — or drop breakpoint() anywhere in src/Ankimon:
python3 -m pdb harness/scenarios/smoke_play.py

# debugpy (VS Code / PyCharm / any DAP client): set breakpoints in the editor, then:
python3 -m debugpy --listen 5678 --wait-for-client harness/scenarios/smoke_play.py
#   ...or inside your own script:  from harness.debug import wait_for_client; wait_for_client()
```

See `harness/debug.py`. **Rule:** tooling/debug packages live in the venv + `harness/`,
**never** in `src/` — the shipped add-on must stay dependency-free.

## For agents: events, long-horizon runs, and time

**Minimal loop** (Tier 1):

```python
from harness.driver import Driver
d = Driver(settings_overrides={"battle.cards_per_round": 1})
for _ in range(50):
    for e in d.answer("good"):
        if e["type"] == "error":          # a crash during play surfaces here
            raise RuntimeError(e["exception"])
    if d.get_state()["enemy"]["hp"] == 0:
        d.catch()                          # or d.defeat() — spawns the next encounter
```

**Event types** (returned by each action / `drain_events()` / the REPL):

| type | when | key fields |
|---|---|---|
| `encounter` | a wild Pokémon appears | pokemon, id, level, tier, shiny, hp, max_hp |
| `battle` | a battle turn resolves | user, enemy, user_move, enemy_move, dmg_to_enemy, dmg_to_user, user_hp, enemy_hp, multiplier |
| `faint` | a Pokémon faints | who (`enemy`/`main`), pokemon |
| `catch` / `defeat` | resolution | pokemon, id, tier (catch also: shiny, nickname) |
| `levelup` | the main levels up | pokemon, level |
| `evolution_offered` | evolution eligible | pokemon, trigger (`level`/`friendship`), evo_id |
| `tooltip` | on-screen battle/level text | message, color |
| `sound` | a cry/effect would play | kind, sound / pokemon_id |
| `hud` | HUD repaint | action |
| `log` / `notify` | log line / would-be popup | level, message |
| `dialog` | a move/attack/evolution choice point | dialog, options, chosen |
| `error` | an exception in the game loop (= Anki's error dialog) | message, exception, traceback |
| `buy` | shop purchase | item, ok, price/reason |

So: drive an action, scan the returned events for `error` (a real crash), and
assert invariants from `get_state()` (HP in `[0, max]`, caught-count grows, …).

**Long-horizon (thousands of turns).** The driver and the REPL keep ONE
persistent session, so you can issue thousands of sequential actions. `longrun.py`
and `soak.py` do exactly that (`python3 harness/scenarios/longrun.py 10000` — ~11s
in Tier 1; Tier 2 is slower but the same API). Drain + check events as you go.

**Time.** Two different things:
- Real-time *delays* (animations, tooltip/card timers) are **skipped** — the Qt
  event loop isn't pumped continuously, so actions run at full CPU speed (10k
  Tier-1 turns in ~11s). Nothing to speed up; the waiting just doesn't happen.
- The *calendar* (`datetime.now`/`date.today`, used for day/night evolutions, the
  daily cash reset, streaks, capture stamps) is **controllable**. Create the
  driver with `clock_start=datetime(...)` and fast-forward with `advance_time()`:

```python
from datetime import datetime
d = Driver(clock_start=datetime(2026, 6, 1, 12, 0))
d.time_of_day()           # "day"
d.advance_time(hours=10)  # -> 22:00
d.time_of_day()           # "night"
d.advance_time(days=7)    # a week later — streaks, daily resets, day/night evolutions
```

(`harness/clock.py` swaps in a faithful `datetime` subclass; harness-only, no
`src/` change. Off unless you pass `clock_start`.)

## How it boots (architecture)

```
bootstrap()         install a stub `Ankimon` package + ANKIMON_USER_PATH (temp dir)
   │                so the aqt-free core imports without running Ankimon/__init__.py
core.build_core()   construct logger/DB/settings/translator/Pokemon/trainer/tracker,
   │                register them in services  (SAME code production uses)
fakes.install_fakes recording stand-ins for test_window/evo_window/pokemon_pc/reviewer
core.bind_runtime_globals()   point the battle-loop modules' globals at the registry
Driver              high-level actions over that session
```

The production composition root (`src/Ankimon/singletons.py`) calls the *same*
`build_core()` and then builds the real Qt windows + `QtPresenter` on top — so the
harness and Anki share one source of truth and can't drift.

## Scope & caveats

- **Two fidelity tiers.** *Tier 1* (fake windows) validates game logic/state/PR
  behaviour and runs anywhere with no deps — but can't reproduce real-Qt
  behaviour or window-internal logic. *Tier 2* (real add-on, offscreen Qt)
  reproduces real widgets/memory/glitches and runs the real window code; it needs
  the `.tier2` env. Neither renders to a screen, so pure visual/CSS bugs still
  need a human or real Anki — though Tier 2 *can* `widget.grab().save("x.png")` to
  capture how a widget looks offscreen.
- **One session per process.** Sessions reset the DB singleton + registry, but the
  writable `user_path` is fixed at first import; for full isolation run each
  session in a fresh interpreter (the scenarios/tests do).
- **Errors surface as events,** not crashes: the battle loop reports exceptions
  through the `error` event (check for `type == "error"`), mirroring how Anki shows
  the error dialog.
- The bundled `poke_engine` prints battle debug to stdout; the Driver suppresses it
  so it never pollutes the REPL's JSON channel.
```
