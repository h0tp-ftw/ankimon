"""Guard: pokemon_evolution.csv must agree with pokedex.json about use-item evolutions.

``pokedex.json`` is what the evolution paths gate on (``evoType``/``evoItem``), while
``pokemon_evolution.csv`` is what ``item_window.load_evolution_items`` scans to decide
which items are usable for evolving at all, and what ``pokedex_functions`` consults for
the gender gate. When a species is ``evoType: "useItem"`` in the JSON but has no
``evolution_trigger_id == 3`` row in the CSV, the two files disagree about how that
species evolves — inert while some *other* species happens to list the same stone, and a
silent dead end the moment it does not.

Two species drifted this way: Probopass (Nosepass -> 476, Thunder Stone) and
Crabominable (Crabrawler -> 740, Ice Stone). Both carried only their location-based
level-up rows, mirroring the games' magnetic-field / Mount Lanakila conditions, which
this project remaps onto stones.
"""

import csv
import json
from pathlib import Path

import pytest

_DATA = Path(__file__).resolve().parents[1] / "src" / "Ankimon" / "data_files"


@pytest.fixture(scope="module")
def evolution_rows():
    with open(_DATA / "pokemon_evolution.csv", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def pokedex():
    with open(_DATA / "pokedex.json", encoding="utf-8") as handle:
        return json.load(handle)


def _rows_by_evolved(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["evolved_species_id"], []).append(row)
    return grouped


def test_use_item_species_have_a_trigger_3_row(evolution_rows, pokedex):
    """Every useItem species the CSV knows about must carry its use-item row."""
    grouped = _rows_by_evolved(evolution_rows)
    missing = []
    for name, entry in pokedex.items():
        if entry.get("evoType") != "useItem":
            continue
        species_id = entry.get("actual_id") or entry.get("num")
        if not isinstance(species_id, int) or species_id >= 10000:
            continue
        rows = grouped.get(str(species_id))
        if rows is None:
            # Species absent from the CSV entirely — a different gap, not this one.
            continue
        if not any(row["evolution_trigger_id"] == "3" for row in rows):
            missing.append((name, species_id, entry.get("evoItem")))
    assert not missing, (
        "pokedex.json calls these useItem evolutions but pokemon_evolution.csv has "
        f"no trigger-3 row for them: {missing}"
    )


@pytest.mark.parametrize(
    "evolved_id,item_id,label",
    [
        ("476", "83", "Nosepass -> Probopass via Thunder Stone"),
        ("740", "885", "Crabrawler -> Crabominable via Ice Stone"),
    ],
)
def test_remapped_location_evolutions_carry_their_stone(
    evolution_rows, evolved_id, item_id, label
):
    """The two rows this guard was added for, pinned individually."""
    rows = [
        row
        for row in evolution_rows
        if row["evolved_species_id"] == evolved_id
        and row["evolution_trigger_id"] == "3"
    ]
    assert rows, f"missing the use-item row for {label}"
    assert [row["trigger_item_id"] for row in rows] == [item_id], (
        f"wrong trigger_item_id for {label}"
    )
