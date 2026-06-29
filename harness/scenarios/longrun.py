"""
Scenario: a long unattended run — thousands of turns — to stress the core loop
and aggregate everything that happened. This is the "simulate N turns and see
every logic outcome" use case.

Run:  python3 harness/scenarios/longrun.py [N]
"""

import sys
import pathlib
import time
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.driver import Driver


def run(n: int = 1000, verbose: bool = True):
    d = Driver(settings_overrides={
        "battle.cards_per_round": 1,
        "battle.automatic_battle": 2,   # auto-defeat on faint so encounters cycle
        "audio.sounds": False,
        "audio.sound_effects": False,
    })

    kinds = Counter()
    t0 = time.time()
    for _ in range(n):
        for e in d.answer("good"):
            kinds[e["type"]] += 1
    dt = time.time() - t0

    final = d.get_state()
    if verbose:
        print(f"{n} answers in {dt:.2f}s  ({n / dt:,.0f} turns/sec)")
        print("event totals:", dict(kinds))
        print(f"trainer Lv{final['trainer']['level']} xp={final['trainer']['xp']} "
              f"cash={final['trainer']['cash']} | collection={final['collection']['count']}")

    assert kinds["error"] == 0, f"{kinds['error']} error events over {n} turns"
    return {"answers": n, "seconds": round(dt, 2),
            "turns_per_sec": round(n / dt), "events": dict(kinds),
            "trainer_level": final["trainer"]["level"]}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    print("longrun: OK", run(n))
