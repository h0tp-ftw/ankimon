"""
Scenario: a short play session that exercises the core loop end-to-end.

Answers cards (driving the real battle loop), and whenever the wild Pokemon
faints, alternates catching and defeating it (spawning the next encounter).
Asserts the invariants an agent would check: no errors, HP stays in range,
battles/faints/encounters actually happen, and the collection grows.

Run:  python3 harness/scenarios/smoke_play.py
"""

import sys
import pathlib
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.driver import Driver


def run(max_answers: int = 120, target_resolutions: int = 4, verbose: bool = True, seed=7):
    # Deterministic by default: wild encounters + battles use the global `random`,
    # and the weak starter sometimes rolls its 0-damage move, so an unseeded run can
    # fail to faint enough enemies in time (~12% flake). Seeding makes this a stable
    # CI gate; pass seed=None to fuzz with fresh randomness instead.
    if seed is not None:
        import random
        random.seed(seed)

    # cards_per_round=1 → a battle turn on every answer; manual mode → we decide
    # whether to catch or defeat each fainted Pokemon.
    d = Driver(settings_overrides={
        "battle.cards_per_round": 1,
        "battle.automatic_battle": 0,
        "audio.sounds": False,
        "audio.sound_effects": False,
    })

    all_events = []
    resolutions = 0  # catches + defeats
    caught = 0
    defeated = 0

    start = d.get_state()
    if verbose:
        print(f"start: main={start['main']['name']} Lv{start['main']['level']} "
              f"vs {start['enemy']['name']} Lv{start['enemy']['level']} "
              f"(HP {start['enemy']['hp']}/{start['enemy']['max_hp']})")

    for i in range(max_answers):
        all_events += d.answer("good")
        st = d.get_state()

        # Invariants every step.
        for who in ("main", "enemy"):
            p = st[who]
            assert 0 <= p["hp"] <= p["max_hp"], f"{who} HP out of range: {p}"

        if st["enemy"]["hp"] == 0:
            # Alternate catch / defeat to exercise both paths.
            if resolutions % 2 == 0:
                all_events += d.catch()
                caught += 1
            else:
                all_events += d.defeat()
                defeated += 1
            resolutions += 1
            if resolutions >= target_resolutions:
                break

    final = d.get_state()
    kinds = Counter(e["type"] for e in all_events)
    errors = [e for e in all_events if e["type"] == "error"]

    if verbose:
        print(f"answers issued: ~{i + 1}, resolutions: {resolutions} "
              f"(caught {caught}, defeated {defeated})")
        print("event counts:", dict(kinds))
        print(f"collection: {final['collection']['count']} pokemon, "
              f"ids={final['collection']['ids']}")
        print(f"trainer: Lv{final['trainer']['level']} xp={final['trainer']['xp']}")
        if errors:
            print("ERRORS:")
            for e in errors:
                print("  -", e.get("message"), "|", e.get("exception"))

    # Assertions: the session genuinely played.
    assert not errors, f"{len(errors)} error event(s) during play"
    assert kinds["battle"] > 0, "no battle turns happened"
    assert kinds["faint"] > 0, "nothing fainted"
    assert kinds["encounter"] > 0, "no new encounters spawned"
    assert resolutions > 0, "never resolved an encounter"
    assert final["collection"]["count"] >= caught and caught > 0, "nothing was caught"

    return {
        "answers": i + 1,
        "resolutions": resolutions,
        "caught": caught,
        "defeated": defeated,
        "event_counts": dict(kinds),
        "collection": final["collection"]["count"],
    }


if __name__ == "__main__":
    summary = run()
    print("smoke_play: OK", summary)
