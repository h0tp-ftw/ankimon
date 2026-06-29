"""
harness/scenarios/fuzz.py — property/fuzz testing for Ankimon's real game logic.

Throws MANY random configurations at the real code and reports any that crash,
emit an `error` event, or break an invariant — each with an exact, reproducible
seed. What it randomizes:
  * the main + wild Pokemon: random species (all ~1300), level, moves (incl. the
    occasional bogus move name), IVs/EVs (incl. out-of-range), nature, shiny,
    gender, and weird nicknames (unicode/emoji/empty/very long/injection-y)
  * settings overrides (boundary values + the occasional wrong type)
  * a random action sequence (answer/catch/defeat/encounter/set_enemy/set_setting)
Invariants checked after every action: HP within [0, max], non-negative cash and
collection count.

    python3 harness/scenarios/fuzz.py 500          # 500 random iterations
    python3 harness/scenarios/fuzz.py --seed 12345 # reproduce ONE case, verbose

This catches the "bad code merged, some edge case errors out" class before a user
does. Tier 1 (no Qt) — fast, runs anywhere. Deterministic per seed.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harness.driver import Driver

NATURES = ["hardy", "lonely", "brave", "adamant", "bold", "relaxed", "impish",
           "timid", "hasty", "serious", "jolly", "modest", "quiet", "calm", "careful"]
WEIRD_NICKS = ["", "A" * 200, "Ž★🔥", "'; DROP TABLE x;--", "<b>x</b>", "💀💀💀",
               "  ", "\n\t", "Ünïçödé", "名前", "%s%d{}", "../../etc/passwd"]
BOGUS_MOVES = ["notamove", "", "Hyper Beam!!!", "移动", "z" * 40]
NUM_KEYS = ["battle.cards_per_round", "battle.daily_average", "gui.xp_bar_location",
            "evolution.day_start_hour", "evolution.night_start_hour", "trainer.cash_reward_amount"]
BOOL_KEYS = ["misc.remove_level_cap", "misc.gen9", "misc.gen1", "audio.sounds"]

_POOL = None
_CONFIG = None
_warm = False


def _warmup():
    """Boot one throwaway Driver so the Ankimon pokedex/learnset modules are
    initialised before we build random specs from them — and capture the FULL
    settings dict so fuzzing covers every key, not a hardcoded few."""
    global _warm, _CONFIG
    if not _warm:
        d = Driver()
        _CONFIG = dict(d.services.settings.config)   # ALL ~63 settings + their defaults
        _warm = True


def _pool():
    global _POOL
    if _POOL is None:
        from Ankimon.functions.pokedex_functions import _load_pokedex_id_index
        _POOL = list((_load_pokedex_id_index() or {}).values()) or ["bulbasaur"]
    return _POOL


def _moves(rng, name, level):
    from Ankimon.functions.learnset_retrieval import get_all_pokemon_moves
    pool = list(get_all_pokemon_moves(name, level) or [])
    rng.shuffle(pool)
    moves = pool[:rng.randint(1, 4)] or ["tackle"]
    if rng.random() < 0.12:
        moves[rng.randrange(len(moves))] = rng.choice(BOGUS_MOVES)
    return moves


def random_spec(rng):
    name = rng.choice(_pool())
    level = rng.choice([rng.randint(1, 100), 1, 100, 5])
    spec = {
        "species": name, "level": level, "moves": _moves(rng, name, level),
        "ivs": {k: rng.randint(0, 31) for k in ("hp", "atk", "def", "spa", "spd", "spe")},
        "evs": {k: rng.choice([0, rng.randint(0, 252), rng.randint(0, 999)])
                for k in ("hp", "atk", "def", "spa", "spd", "spe")},
        "nature": rng.choice(NATURES),
        "shiny": rng.random() < 0.1,
        "gender": rng.choice(["M", "F", "N"]),
    }
    if rng.random() < 0.3:
        spec["nickname"] = rng.choice(WEIRD_NICKS)
    return spec


def _fuzz_value(rng, default):
    """A random value for a setting, typed from its default + boundary picks."""
    if isinstance(default, bool):                # bool BEFORE int (bool is an int subclass)
        return rng.random() < 0.5
    if isinstance(default, int):
        return rng.choice([0, 1, 2, -1, -5, rng.randint(0, 9999), 999999])
    if isinstance(default, float):
        return rng.choice([0.0, -1.0, rng.random(), rng.uniform(0, 1000), 1e9])
    return rng.choice(["", "x" * 100, "Ž★🔥", "999", "-1", str(default)])  # string


def random_settings(rng):
    # Fuzz a random subset of ALL real settings keys, each typed from its default.
    keys = list(_CONFIG or {})
    out = {k: _fuzz_value(rng, _CONFIG[k]) for k in rng.sample(keys, rng.randint(1, 6))} if keys else {}
    if keys and rng.random() < 0.1:             # occasional wrong type → robustness
        out[rng.choice(keys)] = rng.choice(["x", "", None, [], -1])
    return out


ACTIONS = ["answer", "answer", "answer", "catch", "defeat", "encounter",
           "set_enemy", "set_setting"]


def _scan(events, where):
    for e in events or []:
        if e.get("type") == "error":
            raise AssertionError("error event during %s: %r"
                                 % (where, {k: e.get(k) for k in ("type", "exception", "message")}))


def _invariants(d):
    st = d.get_state()
    mp, ep = st["main"], st["enemy"]
    assert 0 <= mp["hp"] <= mp["max_hp"], "main HP out of range: %s/%s" % (mp["hp"], mp["max_hp"])
    assert ep["hp"] >= 0 and (ep["max_hp"] == 0 or ep["hp"] <= ep["max_hp"]), \
        "enemy HP out of range: %s/%s" % (ep["hp"], ep["max_hp"])
    assert st["collection"]["count"] >= 0, "negative collection count"
    cash = st["trainer"]["cash"]
    assert cash is None or cash >= 0, "negative cash: %s" % cash


def one(seed, verbose=False):
    _warmup()
    rng = random.Random(seed)
    main_spec = random_spec(rng)
    settings = random_settings(rng)
    if verbose:
        print("seed %d\n  main: %s\n  settings: %s" % (seed, main_spec, settings))
    d = Driver(seed={"main": main_spec}, settings_overrides=settings)
    for a in [rng.choice(ACTIONS) for _ in range(rng.randint(5, 25))]:
        if verbose:
            print("  action:", a)
        if a == "answer":
            _scan(d.answer(rng.choice([1, 2, 3, 4, "again", "good"])), "answer")
        elif a == "catch":
            _scan(d.catch(), "catch")
        elif a == "defeat":
            _scan(d.defeat(), "defeat")
        elif a == "encounter":
            _scan(d.encounter(), "encounter")
        elif a == "set_enemy":
            _scan(d.set_enemy(random_spec(rng)), "set_enemy")
        elif a == "set_setting":
            k = rng.choice(list(_CONFIG or NUM_KEYS))     # any real setting, mid-play
            d.set_setting(k, _fuzz_value(rng, (_CONFIG or {}).get(k, 0)))
        _invariants(d)


def run(n=300, master=20260623):
    crashes = []
    for i in range(n):
        seed = master * 100000 + i
        try:
            one(seed)
        except Exception as e:
            crashes.append((seed, type(e).__name__, str(e)[:200]))
        if (i + 1) % 100 == 0:
            print("  %d/%d run, %d crashes so far" % (i + 1, n, len(crashes)))

    print("\nfuzz: %d iterations, %d crashed/errored" % (n, len(crashes)))
    distinct = {}
    for seed, kind, msg in crashes:
        distinct.setdefault((kind, msg.split(":")[0][:70]), (seed, kind, msg))
    if distinct:
        print("distinct failures (%d) — each reproducible:" % len(distinct))
        for (_k, _m), (seed, kind, msg) in list(distinct.items())[:25]:
            print("  [seed %d] %s: %s" % (seed, kind, msg))
        print("\nreproduce any with:  python3 harness/scenarios/fuzz.py --seed <seed>")
    else:
        print("fuzz: CLEAN — no crashes, error events, or invariant breaks.")
    return len(crashes)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--seed" in args:
        s = int(args[args.index("--seed") + 1])
        one(s, verbose=True)
        print("seed %d: completed with no detected problem" % s)
    else:
        n = int(args[0]) if args and args[0].isdigit() else 300
        sys.exit(1 if run(n) else 0)
