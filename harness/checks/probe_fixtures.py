"""
harness/checks/probe_fixtures.py — load-a-save + construct-state + bug-repro.

Demonstrates (and regression-tests) the dev-only fixtures layer:
  1. seed  — boot on a constructed state (specific main/team/box/items)
  2. db    — save it, boot a fresh session ON that save (load arbitrary progress)
  3. set_enemy + a real bug-repro: Gengar(Levitate) is immune to a forced Golem's
     Earthquake, while a non-immune control takes damage — all observed through the
     event stream, with no Anki and no clicking.

Run:  python3 harness/checks/probe_fixtures.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harness.driver import Driver


def main():
    # 1) Seed a precise starting state -----------------------------------------
    d = Driver(
        seed={
            "main": {"species": "Gengar", "level": 50, "ability": "Levitate",
                     "moves": ["Shadow Ball", "Sludge Bomb"]},
            "team": [{"species": "Pikachu", "level": 40, "moves": ["Thunderbolt"]}],
            "box":  [{"species": "Bulbasaur", "level": 5}],
            "items": {"great-ball": 10},
        },
        settings_overrides={"battle.cards_per_round": 1},
    )
    st = d.get_state()
    assert st["main"]["name"] == "Gengar", st["main"]
    assert d.services.main_pokemon.ability == "Levitate"
    assert st["collection"]["count"] == 3, st["collection"]
    assert (d.services.db.get_item("great-ball") or {}).get("quantity") == 10
    print("seed: main=Gengar(Levitate) Lv%d, team+box=%d caught, 10 great-balls"
          % (st["main"]["level"], st["collection"]["count"]))

    # 2) Save -> load a fresh session ON that save -----------------------------
    saved = os.path.join(tempfile.mkdtemp(), "save.db")
    shutil.copy(os.path.join(d.env.user_path, "ankimon.db"), saved)
    d2 = Driver(db=saved)
    st2 = d2.get_state()
    assert st2["main"]["name"] == "Gengar" and st2["collection"]["count"] == 3, st2
    assert os.path.exists(saved), "source save must be left untouched"
    print("db: re-booted on the saved file -> main + collection preserved (source untouched)")

    # 3) set_enemy + bug-repro through the event stream ------------------------
    def earthquake_damage(species, ability):
        drv = Driver(
            seed={"main": {"species": species, "level": 80, "ability": ability, "moves": ["Tackle"]}},
            settings_overrides={"battle.cards_per_round": 1},
        )
        drv.set_enemy(species="Golem", level=50, moves=["Earthquake"])
        hits = []
        for _ in range(6):  # poor answers drop the multiplier < 1 so the wild Pokemon swings
            for e in drv.answer("again"):
                if e["type"] == "battle" and e.get("enemy_move") == "earthquake":
                    hits.append(e.get("dmg_to_user"))
                if e["type"] == "error":
                    raise RuntimeError("error event during battle: %r" % e)
            if drv.get_state()["enemy"]["hp"] <= 0:
                drv.set_enemy(species="Golem", level=50, moves=["Earthquake"])
        return hits

    immune = earthquake_damage("Gengar", "Levitate")     # Ground-immune
    control = earthquake_damage("Geodude", "Sturdy")      # not immune
    assert immune and all(d == 0 for d in immune), ("expected all 0 for Levitate", immune)
    assert any(d > 0 for d in control), ("expected damage for the control", control)
    print("set_enemy+repro: Golem's Earthquake -> Gengar(Levitate) took %s, Geodude took %s"
          % (immune, control))

    print("probe_fixtures: OK")


if __name__ == "__main__":
    main()
