---
name: ankimon-harness
description: >-
  Drive the headless Ankimon harness to test, profile, fuzz, and reproduce bugs in
  the game with no Anki and no clicking. Use whenever the task is to run reviews/
  battles, validate a change or PR, profile CPU/memory/DB-queries, soak/stress test,
  fuzz a setting with weird input, fuzz EVERYTHING a user can do (every window/menu/
  right-click across realistic or corrupt save states) to hunt crashes/leaks/memory-
  footprint, validate that a new feature or menu behaves as intended, reproduce a
  reported bug (specific Pokémon/move/ability), load an existing save, open or
  screenshot the real windows, or step through the game's code in a debugger.
argument-hint: "[what to investigate, in plain English]"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# Ankimon headless harness — agent playbook

Ankimon is an Anki add-on (a Pokémon game; every card review = a battle). The
**harness** (`harness/`, a sibling of `src/`, **never shipped**) runs the *real*
game code with **no Anki and no Qt clicking**, so you can drive it, observe a
structured event stream, profile it, and reproduce bugs — programmatically.

**Request to answer:** $ARGUMENTS

## Golden rule
The harness is **dev-only**. Everything you do lives in `harness/` (+ a throwaway
profile + `.tier2/`). **Never** put test/diagnostic code in `src/Ankimon/` — that's
the shipped add-on. Generated saves are throwaway (temp dirs), never committed.

## First: orient + verify the environment
```bash
python3 harness/check.py --doctor     # python>=3.10 + poke_engine submodule present?
python3 harness/check.py              # the whole Tier-1 gate, ~5s, exit 0 = green
```
If `--doctor` flags the submodule: `git submodule update --init --recursive`.

**Complete reference** — every module, action, event, scenario, and setting key — is in
**`${CLAUDE_SKILL_DIR}/reference.md`**. This file is the quick playbook; open `reference.md`
whenever you need an exact signature or the full surface. (Prose/architecture:
`harness/README.md`; contributor rules: `AGENTS.md`.)

## The two tiers (pick the cheapest that answers the question)
- **Tier 1** — `from harness.driver import Driver`. No Anki, no Qt, no deps; ~900
  reviews/s. Real battle loop / encounters / DB / settings, with recording *fake*
  windows. Use for: logic, state, rewards, profiling the data/logic layer, fuzzing
  settings, reproducing battle/ability/move bugs. **Default to this.**
- **Tier 2** — `from harness.real_driver import RealDriver`. Boots the genuine
  add-on with real Qt windows offscreen (needs the `.tier2` env: `bash
  harness/setup_tier2.sh`). Use for: real-window behavior/memory/glitches,
  window-internal logic (PC box), and offscreen screenshots.
- **Honest limit:** logic / state / data → yes. *How it looks* (pixels/CSS) and
  *how fast it feels on a user's machine* → needs a human or real Anki. The harness
  finds the **cause** (e.g. an N+1 query, deepcopy churn), not the felt milliseconds.

## Core loop
```python
from harness.driver import Driver
d = Driver(settings_overrides={"battle.cards_per_round": 1})
for e in d.answer("good"):            # answer a card -> the real battle turn runs
    if e["type"] == "error":          # a crash surfaces here, not as a traceback
        raise RuntimeError(e["exception"])
if d.services.enemy_pokemon.hp <= 0:
    d.catch()                         # or d.defeat() -> spawns the next encounter
d.get_state()                         # JSON snapshot: main/enemy/tracker/collection/trainer
```
Actions: `answer/catch/defeat/encounter/set_setting/set_move/add_cash/buy_item/`
`advance_time/get_state/drain_events` (+ `set_enemy`). Each returns the events it
produced. To investigate: **scan events for `type=="error"`** and **assert
invariants** from `get_state()` (HP in `[0,max]`, caught-count grows, …).

## Recipes (map the request to one of these)

### Validate a change / PR
`python3 harness/check.py` (the gate CI runs). For deeper logic checks run a
scenario (`smoke_play.py`, `auto_battle.py`, `economy.py`, `longrun.py N`) and
confirm: no `error` events, invariants hold, caught-count/levels move.

### Fuzz EVERYTHING — crashes, edge cases, memory (the mega-fuzzer, Tier 2)
```bash
source .tier2/env.sh
python3 harness/scenarios/mega_fuzz.py                                   # sweep random worlds × random actions
python3 harness/scenarios/mega_fuzz.py --world corrupt,blank --seeds 40 --steps 150 --parallel 3
python3 harness/scenarios/mega_fuzz.py --replay 7 80 corrupt             # re-run one seed verbatim to watch it
```
Boots the real add-on into a random **world** (`first_run`=sprites/empty · `blank`=no
sprites, i.e. user declined the download · `seeded`=full box · `corrupt`=one save row
mangled) and loops random **actions** over auto-discovered targets: gameplay
(answer/catch/defeat/encounter/setting) + GUI (open-menu/click/type/close) +
**right-click→context-menu** (PC box release/give-item/favorite/…). Each action is
journaled+fsync'd BEFORE it runs and each seed runs in its own child process, so a C++
Qt abort becomes a reproducible finding (last journal line = culprit + a `--replay`).
One ranked report: **hard crashes + soft error-events + footprint** (RSS growth/world).
**TRIAGE every finding** (the discipline that makes it trustworthy): `--replay` it,
read the traceback, attribute it — a crash can be a real game bug OR a harness gap (a
missing fake-mw attr, an un-neutered modal). Don't report a harness artifact as a user
bug. Smaller siblings: `gui_fuzz.py` (GUI-only monkey), `fuzz.py` (Tier-1 logic fuzz),
`move_sweep.py` (every move through a real battle).

### Validate a feature works AS INTENDED (not just "no crash")
The fuzzer proves *no crash*; this proves *correctness*. `harness/scenarios/feature_check.py`
is the template (3 worked checks, all green) — drive the real feature like a user, then
assert the intended OUTCOME + no error event:
```python
# e.g. the "Rename Pokémon" feature, driven through the real PC-box widgets:
pc.show_pokemon_details(db.get_pokemon(iid)); app.processEvents()
edit = _find(app, QLineEdit, placeholderText="Nickname"); edit.clear(); QTest.keyClicks(edit, "Sparky")
_find(app, QPushButton, text="Rename").click(); app.processEvents()
assert db.get_pokemon(iid)["nickname"] == "Sparky"      # intended behavior held
```
`python3 harness/scenarios/feature_check.py` runs the suite. **When asked to check a new
menu/feature, add a `check_*` to `CHECKS`** (drive it → assert its intended state change)
— it joins the suite as a permanent acceptance test.

### Profile a workload — "do N reviews, watch cProfile + DB + memory"
```python
from harness.driver import Driver
from harness.diagnostics import profile
d = Driver(settings_overrides={"battle.cards_per_round": 1})
with profile(d, label="10k reviews", memory=True) as report:   # backend="pyinstrument" optional
    for _ in range(10_000):
        d.answer("good")
        if d.services.enemy_pokemon.hp <= 0: d.catch()
report.print()        # DB queries grouped (spots N+1s) + cProfile hotspots + RSS/tracemalloc
```
Shortcut: `python3 harness/scenarios/profile_battles.py 10000`. Read it right:
query counts + cProfile *shape* are hardware-independent (the *where*/*how-it-scales*
you fix); wall/RSS are indicative on this box only.

### Reproduce a reported bug — specific main/team/enemy
Build state from the game's own pokedex data (only the fields you pin change):
```python
d = Driver(seed={"main": {"species": "Gengar", "level": 50, "ability": "Levitate",
                          "moves": ["Shadow Ball"]}})
d.set_enemy(species="Golem", level=50, moves=["Earthquake"])   # force the exact wild mon
d.answer("again")     # poor answers drop the multiplier <1 so the enemy actually attacks
# then read the `battle` events: dmg_to_user/dmg_to_enemy/enemy_move/multiplier
```
spec fields: `species|id, level, ability, moves, ivs/evs, nature, shiny, gender,`
`held_item, hp`. Or boot an existing save (copied, never mutated): `Driver(db="save.db")`.
Worked example: `harness/checks/probe_fixtures.py`.

### Fuzz a setting — "put weird chars in entry X and see what happens"
```python
d = Driver()
for val in ["", "🦊", "x"*5000, "'; DROP TABLE config;--", "<script>", None, -1, 1e308, "\x00"]:
    d.set_setting("trainer.name", val)                # then exercise paths that USE it:
    evs = d.answer("good")
    bad = [e for e in evs if e["type"] == "error"]
    print(repr(val)[:30], "->", "ERROR: "+bad[0]["exception"] if bad else "ok",
          "| round-trips:", d.services.settings.get("trainer.name") == val)
```
This surfaces encoding/persistence/JS-injection bugs (the HUD renders names via JS).
Use any settings key (see `DEFAULT_CONFIG` in `src/Ankimon/pyobj/settings.py`).

### Open every real window + screenshot + memory (Tier 2)
```bash
bash harness/setup_tier2.sh          # one-time (sudo-free; venv + local Qt libs)
python3 -m harness.scenarios.screenshots    # PNGs of the real battle window + PC box
python3 -m harness.scenarios.soak 5000      # boot real windows, drive N reviews, watch RSS
```
`harness/screenshot.py:grab(widget, path)` renders any real widget offscreen.
Wrap a Tier-2 run in `diagnostics.profile(...)` for window memory/CPU.

### Long-horizon + time
The session is persistent — issue thousands of actions (`longrun.py`/`soak.py` do
10k+). The calendar is controllable: `Driver(clock_start=datetime(...))` then
`d.advance_time(days=…, hours=…)` drives day/night evolutions, daily resets, streaks.

### Step through the code in a debugger
```bash
python3 -m pdb harness/scenarios/smoke_play.py        # stdlib, or drop breakpoint() in src/
python3 -m debugpy --listen 5678 --wait-for-client harness/scenarios/smoke_play.py  # VS Code
```
Set breakpoints in `src/Ankimon`; inspect variables while a *simulated* review runs.

### Diff two branches
Run the same scenario on each branch and compare the event-count summary / profile
report (`report.as_dict()`), or `git worktree` each branch and run `check.py`.

## Extending with other tools
`profile(d, backend="pyinstrument")` swaps the profiler; install optional tools into
a **venv** from `harness/requirements-dev.txt` (pyinstrument/scalene/memray/py-spy/
objgraph/debugpy) — never as add-on deps. Any Python tool plugs into the same Driver.

## Reporting back
State plainly: what you ran, what the events/profile showed (with the concrete
numbers/`error` payloads), whether invariants held, and — for perf — *where* the cost
is and *how it scales*, not just a wall-clock number. If a bug reproduced, give the
minimal `seed`/`set_enemy` repro so a human can re-run it.
