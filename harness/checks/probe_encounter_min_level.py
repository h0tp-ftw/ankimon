"""PR #928: inherited encounter minima, malformed chains, and rarity floors."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.bootstrap import bootstrap


class _NoQt:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"aqt", "PyQt6", "PyQt5"}:
            raise ModuleNotFoundError(f"{fullname}: Tier-1 is Qt-free")


def _check_rows(encounters, rows, expected=1):
    """Bound lookups so a reverted cycle guard fails quickly instead of hanging."""
    calls = 0
    budget = 4 * len(rows) + 8

    def lookup(name, field):
        nonlocal calls
        calls += 1
        assert calls <= budget, "Encounter minimum did not terminate its ancestor walk"
        # Match the genuine resolver's case/hyphen normalization for aliases.
        row = rows.get(name.lower().replace("-", ""), {})
        return row.get(field, [])

    with patch.object(encounters, "search_pokedex", side_effect=lookup):
        assert encounters.check_min_generate_level("Alpha") == expected


def _cycles(encounters):
    _check_rows(encounters, {"alpha": {"prevo": "ALPHA", "evoLevel": 0}})
    _check_rows(
        encounters,
        {"alpha": {"prevo": "B-eta"}, "beta": {"prevo": "ALPHA"}},
    )
    # A bad chain must retain the requested species/form's rarity requirement.
    _check_rows(
        encounters,
        {"alpha": {"prevo": "ALPHA", "species_id": 151}},
        expected=75,
    )


def _missing_ancestors(encounters):
    for prevo in (None, "", [], 123, {"invalid": "ancestor"}, "Missing"):
        _check_rows(encounters, {"alpha": {"prevo": prevo}})
    _check_rows(
        encounters,
        {"alpha": {"prevo": "Missing", "actual_id": 10033}},
        expected=60,
    )


def _cached_species(encounters):
    from Ankimon.functions.pokedex_functions import _load_pokedex_cache

    assert len(_load_pokedex_cache()) >= 1300, "Require the bundled Pokédex"
    expected = {
        "Pikachu": 1,  # Friendship, no explicit evolution level.
        "Raichu": 1,  # Item after friendship.
        "Raichu-Alola": 1,  # Normalized form name.
        "Togekiss": 1,  # Another item/friendship chain.
        "Porygon-Z": 1,  # Multiple item stages.
        "Kadabra": 16,  # Direct evolution level.
        "Alakazam": 16,  # Trade evolution inherits Kadabra's level.
        "Vileplume": 21,  # Stone evolution inherits Gloom's level.
        "Bulbasaur": 30,
        "Ivysaur": 30,  # Rarity floor exceeds evolution level 16.
        "Charizard": 36,  # Evolution level exceeds the rarity floor.
        "Buzzwole": 30,
        "Articuno": 50,
        "Venusaur-Mega": 60,
        "Pikachu-Gmax": 65,
        "Mew": 75,
    }
    # Encounter filtering must keep using warmed static caches during reviews.
    with patch("builtins.open", side_effect=AssertionError("Hot-path file I/O")):
        for name, minimum in expected.items():
            actual = encounters.check_min_generate_level(name)
            assert actual == minimum, (name, minimum, actual)


def run_proof(section="all"):
    blocker = _NoQt()
    sys.meta_path.insert(0, blocker)
    try:
        with tempfile.TemporaryDirectory(prefix="ankimon-min-level-") as profile:
            bootstrap(user_path=profile)
            from Ankimon.functions import encounter_functions as encounters

            checks = {
                "cycles": _cycles,
                "missing": _missing_ancestors,
                "cached": _cached_species,
            }
            selected = checks.values() if section == "all" else (checks[section],)
            for check in selected:
                check(encounters)
    finally:
        sys.meta_path.remove(blocker)
    print(f"probe_encounter_min_level: {section} OK")
    return True


if __name__ == "__main__":
    run_proof(sys.argv[1] if len(sys.argv) > 1 else "all")
