"""PR #838: prove bundled shop availability and every remapped item evolution.

Run with plain Python: python3 harness/checks/probe_item_evolutions.py
"""

import csv
import pathlib
import sys
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from harness.driver import Driver


# Explicit cases: deriving these from evoType would hide a reverted data entry.
LINKING_CORD_EVOLUTIONS = (
    (64, 65),
    (67, 68),
    (75, 76),
    (10110, 10111),
    (93, 94),
    (525, 526),
    (533, 534),
    (708, 709),
    (710, 711),
    (10027, 10030),
    (10028, 10031),
    (10029, 10032),
)


def run_proof():
    """Check every remapped evolution and shop fallback in a disposable profile."""
    d = Driver(first_encounter=False)
    from Ankimon import utils
    from Ankimon.functions import pokedex_functions
    from Ankimon.functions.pokedex_functions import (
        _load_pokedex_cache,
        check_evolution_by_item,
        return_id_for_item_name,
        search_pokedex_by_id,
    )
    from Ankimon.resources import poke_evo_path

    pokedex = _load_pokedex_cache()
    with open(poke_evo_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for item, pairs, item_id in (
        ("linking-cord", LINKING_CORD_EVOLUTIONS, 2160),
        ("oval-stone", ((440, 113),), 110),
    ):
        assert int(return_id_for_item_name(item)) == item_id
        for prevo, evolved in pairs:
            if item == "oval-stone":
                with patch.object(
                    pokedex_functions, "get_time_of_day", return_value="day"
                ):
                    assert check_evolution_by_item(prevo, item_id) == evolved, (
                        item,
                        prevo,
                    )
            else:
                assert check_evolution_by_item(prevo, item_id) == evolved, (item, prevo)
            source = pokedex[search_pokedex_by_id(prevo)]
            target = pokedex[search_pokedex_by_id(evolved)]
            assert target["evoType"] == "useItem"
            assert target["evoItem"].lower().replace(" ", "-") == item
            if item == "oval-stone":
                assert target["evoCondition"] == "during the day"
            assert any(
                row["evolves_from_species_id"] == str(source["species_id"])
                and row["evolved_species_id"] == str(target["species_id"])
                and row["evolution_trigger_id"] == "3"
                and row["trigger_item_id"] == str(item_id)
                for row in rows
            ), (item, prevo, evolved)
            assert (
                check_evolution_by_item(prevo, 110 if item_id == 2160 else 2160) is None
            )
        assert check_evolution_by_item(25, item_id) is None
    with patch.object(pokedex_functions, "get_time_of_day", return_value="night"):
        assert check_evolution_by_item(440, 110) is None

    # Held-item trades retain their own item; a cord cannot bypass the requirement.
    assert check_evolution_by_item(112, 2160) is None
    assert check_evolution_by_item(112, return_id_for_item_name("protector")) == 464

    # Simulate an installed older pack with no Linking Cord, entirely in the
    # harness's disposable profile. No user's sprite directory is written.
    utils.items_path.mkdir(parents=True, exist_ok=True)
    cord = utils.items_path / "linking-cord.png"
    assert not cord.exists()
    fallback = utils.get_item_sprite_path("linking-cord")
    assert fallback.is_file()
    assert fallback.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    expected = [
        {"name": "linking-cord", "description": "Item: linking-cord", "price": 8000}
    ]
    assert [
        item for item in utils.daily_item_list() if item["name"] == "linking-cord"
    ] == expected
    cord.write_bytes(fallback.read_bytes())
    assert utils.get_item_sprite_path("linking-cord") == cord
    assert [
        item for item in utils.daily_item_list() if item["name"] == "linking-cord"
    ] == expected
    assert not [event for event in d.drain_events() if event["type"] == "error"]
    print(
        "probe_item_evolutions: OK (13 evolutions, exact CSV items, invalid targets, sprite fallback + deduplication)"
    )
    return True


if __name__ == "__main__":
    run_proof()
