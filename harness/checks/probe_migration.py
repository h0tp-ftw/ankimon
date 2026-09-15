"""
harness/checks/probe_migration.py — "never lose a save when you UPDATE Ankimon."

Old Ankimon kept progress in JSON files (mypokemon.json, mainpokemon.json,
items.json, team.json, ...); current Ankimon keeps it in ankimon.db. The one-time
upgrade that copies JSON -> DB is where silent data loss hurts most — yet it's the
hardest thing to see, because the harness normally seeds an already-migrated DB.

This probe builds a realistic LEGACY save (JSON files for a box, a main, a team,
items), runs the JSON->DB migration on a fresh database, and asserts every piece
of data made it across intact (species, level, moves, item counts) — then re-boots
a fresh session on the migrated DB to prove the upgraded save is actually playable.

The upgrade dialog delegates to this same migration runner. Real-Qt dialog
coverage lives in tests/test_migration_recovery.py and probe_real_migration.py.

Run:  python3 harness/checks/probe_migration.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from harness.driver import Driver
from harness.fixtures import build_pokemon


def _attacks(p):
    return [str(a).lower() for a in (p.get("attacks") or p.get("moves") or [])]


def main():
    # Boot a fresh (un-migrated) session FIRST: this puts Ankimon on the import
    # path and gives us an empty database to upgrade INTO.
    d = Driver()
    db = d.services.db
    assert not db.is_migrated(), "a fresh harness DB must start un-migrated"

    # --- Build a realistic LEGACY (pre-DB) save as JSON files ------------------
    box_specs = [
        {"species": "Pikachu", "level": 10, "moves": ["Thunderbolt"]},
        {"species": "Bulbasaur", "level": 16, "moves": ["Vine Whip", "Tackle"]},
        {"species": "Squirtle", "level": 12, "moves": ["Water Gun"]},
    ]
    main_spec = {
        "species": "Gengar",
        "level": 50,
        "ability": "Levitate",
        "moves": ["Shadow Ball", "Sludge Bomb"],
    }

    box_specs.extend(
        {"species": "Pikachu", "level": i % 70 + 5, "moves": ["Thunderbolt"]}
        for i in range(154)
    )
    box_specs.append(main_spec)
    box = [build_pokemon(s).to_dict() for s in box_specs]
    # Exercise old records with no persistent identity, including identical twins.
    for pokemon in box:
        pokemon.pop("individual_id", None)
    main = [box[-1]]
    items = ["great-ball"] * 5 + ["potion"] * 3
    team = [main[0], box[0]]

    tmp = Path(tempfile.mkdtemp())

    def w(name, obj):
        p = tmp / name
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    paths = dict(
        mypokemon_path=w("mypokemon.json", box),
        mainpokemon_path=w("mainpokemon.json", main),
        items_path=w("items.json", items),
        badges_path=w("badges.json", []),
        team_path=w("team.json", team),
    )

    # --- Run the upgrade migration on the fresh database ----------------------
    stats = db.migrate_from_json(**paths)

    # --- Assert nothing was lost ---------------------------------------------
    assert db.is_migrated(), "migration must mark the DB migrated"
    assert stats["pokemon"] == 158 and stats["main"] == 1 and stats["items"] == 2, stats
    assert db.get_pokemon_count() == 158, db.get_pokemon_count()

    loaded_main = db.get_main_pokemon()
    assert loaded_main and loaded_main.get("name") == "Gengar", loaded_main
    assert int(loaded_main.get("level")) == 50, loaded_main

    assert (db.get_item("great-ball") or {}).get("quantity") == 5, (
        "great-ball count lost"
    )
    assert (db.get_item("potion") or {}).get("quantity") == 3, "potion count lost"

    # every box species crossed over with its level + moves intact
    rows = db.get_all_pokemon()
    expected = sorted(json.dumps(p, sort_keys=True) for p in box)
    actual = sorted(
        json.dumps({k: v for k, v in p.items() if k != "individual_id"}, sort_keys=True)
        for p in rows
    )
    assert actual == expected, "legacy Pokemon fields or duplicate counts changed"
    assert len({p["individual_id"] for p in rows}) == 158
    assert len(db.get_team()) == 2
    print("migrated: 158 Pokemon + string inventory + team -> %s" % stats)

    # --- Re-boot on the migrated DB: the upgraded save must be playable --------
    import shutil

    saved = os.path.join(tempfile.mkdtemp(), "save.db")
    shutil.copy(os.path.join(d.env.user_path, "ankimon.db"), saved)
    st = Driver(db=saved).get_state()
    assert st["main"]["name"] == "Gengar", (
        "upgraded save did not boot with the main",
        st["main"],
    )
    assert st["collection"]["count"] == 158, (
        "upgraded save lost collection",
        st["collection"],
    )
    print("re-boot on upgraded save -> main=Gengar, collection=158 (playable)")

    print("probe_migration: OK")


if __name__ == "__main__":
    main()
