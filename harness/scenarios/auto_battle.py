"""
Scenario: automatic battle modes.

With battle.automatic_battle set, a fainted wild Pokemon is resolved
automatically and the next one spawns — so just answering cards keeps the game
cycling. Modes: 1 = auto-catch, 2 = auto-defeat, 3 = catch-if-uncollected.

Run:  python3 harness/scenarios/auto_battle.py
"""

import sys
import pathlib
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.driver import Driver


def run(mode: int = 2, answers: int = 40, verbose: bool = True, seed=7):
    # Deterministic by default: encounters + battles use the global `random`, and an
    # unlucky run can resolve 0 faints within `answers` cards — which flakes the gate
    # (this scenario asserts faint>=1). Seed it (mirrors smoke_play / #501); pass
    # seed=None to fuzz with fresh randomness instead.
    if seed is not None:
        import random
        random.seed(seed)
    d = Driver(settings_overrides={
        "battle.cards_per_round": 1,
        "battle.automatic_battle": mode,
        "audio.sounds": False,
        "audio.sound_effects": False,
    })

    events = []
    for _ in range(answers):
        events += d.answer("good")

    kinds = Counter(e["type"] for e in events)
    errors = [e for e in events if e["type"] == "error"]
    final = d.get_state()

    if verbose:
        print(f"mode={mode}: {dict(kinds)}")
        print(f"  collection={final['collection']['count']} "
              f"trainer Lv{final['trainer']['level']} xp={final['trainer']['xp']}")
        for e in errors:
            print("  ERROR:", e.get("message"), "|", e.get("exception"))

    assert not errors, f"errors in auto-battle mode {mode}"
    assert kinds.get("encounter", 0) >= 1, "no encounters cycled"
    assert kinds.get("faint", 0) >= 1, "nothing fainted"

    return {"mode": mode, "event_counts": dict(kinds),
            "collection": final["collection"]["count"]}


if __name__ == "__main__":
    for m in (1, 2, 3):
        print("auto_battle: OK", run(mode=m))
