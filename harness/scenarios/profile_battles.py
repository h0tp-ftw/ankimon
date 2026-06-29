"""
harness/scenarios/profile_battles.py [N] — profile N battle answers.

Drives N "good" answers (resolving each wild Pokemon as it faints) under the
diagnostics profiler, then prints DB-query counts, cProfile hotspots, memory, and
wall time. The query counts + cProfile shape are hardware-independent; treat the
wall time / RSS as indicative on this box.

    python3 harness/scenarios/profile_battles.py 2000
    python3 harness/scenarios/profile_battles.py 10000   # the "10k battles" run
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harness.driver import Driver
from harness.diagnostics import profile


def main(n: int = 2000):
    d = Driver(settings_overrides={"battle.cards_per_round": 1})
    with profile(d, label=f"{n} battles", memory=True) as report:
        for _ in range(n):
            d.answer("good")
            if d.services.enemy_pokemon.hp <= 0:   # in-memory check (no DB query)
                d.catch()                          # resolve + spawn the next wild Pokemon
    report.print()
    return report


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    main(n)
