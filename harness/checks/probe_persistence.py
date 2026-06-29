"""
harness/checks/probe_persistence.py — "never lose a save when you restart Anki."

The harness runs ONE session per process, which hides the single biggest class of
real bug: progress that lives only in memory and vanishes when Anki is closed and
reopened. This probe PLAYS a session (earns XP + cash, catches Pokemon, defeats
Pokemon), then copies the ``ankimon.db`` and BOOTS A FRESH SESSION on it — exactly
what reopening Anki does — and asserts every gameplay change survived the round
trip. If anything a player earned is lost on reload, it fails loudly and names
what was lost.

This currently PASSES (persistence works); its job is to keep it that way — any
future change that drops progress on restart turns this red.

Run:  python3 harness/checks/probe_persistence.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harness.driver import Driver

# The slice of state a player would be furious to lose on a restart.
TRACKED = ("count", "cash", "trainer_level", "trainer_xp", "main_xp", "main_level")


def _snap(drv):
    s = drv.get_state()
    return {
        "count": s["collection"]["count"],
        "cash": s["trainer"]["cash"],
        "trainer_level": s["trainer"]["level"],
        "trainer_xp": s["trainer"]["xp"],
        "main_xp": s["main"]["xp"],
        "main_level": s["main"]["level"],
    }


def _drive(events, what):
    for e in events:
        if e["type"] == "error":
            raise RuntimeError("error event during %s: %r" % (what, e))


def main():
    d = Driver(
        seed={"main": {"species": "Gengar", "level": 50, "moves": ["Shadow Ball"]}},
        settings_overrides={"battle.cards_per_round": 1},
    )
    before = _snap(d)
    assert before["count"] == 1, ("expected just the seeded main", before)

    # --- Play: earn XP/cash, then catch 3 and defeat 2 wild Pokemon -----------
    for _ in range(12):
        _drive(d.answer("good"), "answer")
    for sp in ("Pikachu", "Bulbasaur", "Charmander"):
        d.set_enemy(species=sp, level=5, hp=0)   # force an already-fainted wild
        _drive(d.catch(), "catch")
    for sp in ("Rattata", "Pidgey"):
        d.set_enemy(species=sp, level=5, hp=0)
        _drive(d.defeat(), "defeat")

    after = _snap(d)
    # Real, persistent progress actually happened (else the test is vacuous).
    assert after["count"] == before["count"] + 3, ("expected +3 caught", before, after)
    assert after["main_xp"] > 0 or after["trainer_xp"] > 0, ("expected XP from defeats", after)
    print("played: caught 3, defeated 2 -> %s" % after)

    # --- Restart: copy the save, boot a brand-new session on it ---------------
    saved = os.path.join(tempfile.mkdtemp(), "save.db")
    shutil.copy(os.path.join(d.env.user_path, "ankimon.db"), saved)
    reloaded = _snap(Driver(db=saved))

    lost = {k: (after[k], reloaded[k]) for k in TRACKED if after[k] != reloaded[k]}
    if lost:
        raise AssertionError(
            "DATA LOST ON RESTART: "
            + ", ".join("%s had %r, reloaded %r" % (k, a, b) for k, (a, b) in lost.items())
        )
    assert os.path.exists(saved), "source save must be left untouched"
    print("restart: re-booted on the saved file -> all progress survived %s" % reloaded)
    print("probe_persistence: OK")


if __name__ == "__main__":
    main()
