"""
harness/scenarios/move_sweep.py — run EVERY move through a real battle, list crashers.

The fuzzer *samples* moves; this is the exhaustive version for the move dimension.
It takes every move poke_engine knows (~885), gives it to the main Pokemon, and
makes the main attack with it in a real battle — surfacing any move whose
implementation (in poke_engine or Ankimon's bridge to it) throws or emits an
`error` event. Output is a clean per-move pass/fail: exactly which moves break.

    python3 harness/scenarios/move_sweep.py        # sweep all ~885 moves
    python3 harness/scenarios/move_sweep.py 200     # first 200 (quick check)

Not a gate probe — it's an exhaustive audit you run on demand.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harness.driver import Driver


def _test_move(mv):
    """Give the MAIN this move, attack with it a few times, report any crash."""
    try:
        d = Driver(seed={"main": {"species": "Mew", "level": 50, "moves": [mv]}},
                   settings_overrides={"battle.cards_per_round": 1})
        d.set_enemy(species="Snorlax", level=50)        # tanky, survives so the move fires
        for _ in range(3):
            for e in d.answer("good"):                   # main attacks WITH `mv`
                if e.get("type") == "error":
                    return "error: " + str(e.get("message") or e.get("exception") or "")[:90]
        return None
    except Exception as ex:
        return "%s: %s" % (type(ex).__name__, str(ex)[:90])


def _test_enemy_move(mv):
    """Give the WILD this move and make it attack with it (poor answers drop the
    multiplier so the wild swings); a tanky main survives so it keeps attacking."""
    try:
        d = Driver(seed={"main": {"species": "Blissey", "level": 90, "moves": ["softboiled"]}},
                   settings_overrides={"battle.cards_per_round": 1})
        d.set_enemy(species="Gengar", level=50, moves=[mv])
        attacked = False
        for _ in range(8):
            for e in d.answer("again"):
                if e.get("type") == "error":
                    return "error: " + str(e.get("message") or e.get("exception") or "")[:90]
                if e.get("type") == "battle" and e.get("enemy_move"):
                    attacked = True
            if attacked:
                break                                    # move fired cleanly -> done
        return None
    except Exception as ex:
        return "%s: %s" % (type(ex).__name__, str(ex)[:90])


def run(limit=None, verbose=True, side="main"):
    Driver()                                             # warmup: make Ankimon importable
    import Ankimon.poke_engine.data as ped
    moves = sorted(ped.all_move_json.keys())
    if limit:
        moves = moves[:limit]
    tester = _test_enemy_move if side == "enemy" else _test_move
    print("move sweep (%s side): %d moves" % (side, len(moves)))

    crashers = []
    for i, mv in enumerate(moves):
        why = tester(mv)
        if why:
            crashers.append((mv, why))
        if verbose and (i + 1) % 150 == 0:
            print("  %d/%d swept, %d crashers so far" % (i + 1, len(moves), len(crashers)))

    print("\nmove sweep: %d moves, %d crashed" % (len(moves), len(crashers)))
    if crashers:
        # group by the error signature to show distinct failure modes
        from collections import defaultdict
        groups = defaultdict(list)
        for mv, why in crashers:
            groups[why.split(":")[0][:40]].append(mv)
        print("by failure mode:")
        for sig, mvs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print("  [%3d moves] %s  e.g. %s" % (len(mvs), sig, ", ".join(mvs[:8])))
    else:
        print("CLEAN — every move ran without a crash.")
    return crashers


if __name__ == "__main__":
    side = "enemy" if "--enemy" in sys.argv else "main"          # default: main side
    lim = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
    sys.exit(1 if run(lim, side=side) else 0)
