"""Preserve the intentional wild-level-100 gate for non-level evolutions."""

import hashlib
import json
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.bootstrap import bootstrap

# Generated in an isolated process by executing check_min_generate_level from
# PR #928's base fb7708be77f68c0440ddad70282cb2a8c707ca9a on its unchanged cache.
# SHA256 covers JSON [[canonical_name, minimum], ...], sorted by name, with
# separators=(",", ":") and ensure_ascii=True. CI requires neither Git nor a
# second copy of the algorithm; any species/form minimum change needs review.
BASELINE_SPECIES_COUNT = 1384
BASELINE_MINIMUMS_SHA256 = (
    "75a96b00b937d83534b58b4463d0dfba52370d348a533ce8db629793fb807c10"
)


class _NoQt:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"aqt", "PyQt6", "PyQt5"}:
            raise ModuleNotFoundError(f"{fullname}: Tier-1 is Qt-free")


def _check_rows(encounters, rows, expected):
    def lookup(name, field):
        # Even self-cycles fail promptly if ancestor traversal is reintroduced.
        assert field != "prevo", "Encounter minima must not walk evolution chains"
        assert name == "alpha", "An ancestor must not lower the selected species gate"
        return rows["alpha"].get(field, [])

    with patch.object(encounters, "search_pokedex", side_effect=lookup):
        assert encounters.check_min_generate_level("Alpha") == expected


def _ancestry(encounters, driver):
    for prevo in (
        "ALPHA",
        "B-eta",
        None,
        "",
        [],
        123,
        {"invalid": "ancestor"},
        "Missing",
    ):
        _check_rows(
            encounters,
            {
                "alpha": {"evoType": "trade", "prevo": prevo},
                "beta": {"evoLevel": 16, "prevo": "ALPHA"},
            },
            expected=100,
        )
    for row, expected in (
        ({"evoType": "trade", "evoLevel": 0, "prevo": "ALPHA"}, 100),
        ({"evoType": "trade", "evoLevel": 16, "prevo": "ALPHA"}, 16),
        ({"prevo": "ALPHA"}, 1),
        ({"evoType": "trade", "species_id": 151, "prevo": "ALPHA"}, 100),
        ({"evoType": "trade", "actual_id": 10199, "prevo": "ALPHA"}, 100),
        ({"actual_id": 10033, "prevo": "Missing"}, 60),
    ):
        _check_rows(encounters, {"alpha": row}, expected)
    return 14


def _cached_species(encounters, driver):
    from Ankimon.functions.pokedex_functions import _load_pokedex_cache

    pokedex = _load_pokedex_cache()
    assert len(pokedex) == BASELINE_SPECIES_COUNT, "Bundled Pokédex baseline changed"
    expected = {
        "Rattata": 1,
        "Pichu": 1,
        "Pikachu": 100,  # Friendship.
        "Raichu": 100,  # Item; do not inherit Pichu's minimum.
        "Raichu-Alola": 100,
        "Togekiss": 100,
        "Porygon-Z": 100,
        "Ambipom": 100,  # Move-based evolution.
        "Kadabra": 16,
        "Alakazam": 100,  # Trade; do not inherit Kadabra's level 16.
        "Vileplume": 100,  # Item; do not inherit Gloom's level 21.
        "Gengar": 100,
        "Bulbasaur": 30,
        "Ivysaur": 30,  # Rarity floor exceeds its evolution level 16.
        "Charizard": 36,
        "Buzzwole": 30,
        "Articuno": 50,
        "Venusaur-Mega": 60,
        "Charizard-Gmax": 65,
        # Preserve existing alias fallback too: the hyphenated name can read
        # Pikachu's evoType; canonical final-id selection reads the form's data.
        "Pikachu-Gmax": 100,
        "pikachugmax": 65,
        "Mew": 75,
        "sandslash": 22,
        "sandslashalola": 100,  # Canonical name used by final-id selection.
    }
    # All per-card filtering must keep using the warmed static cache.
    with patch("builtins.open", side_effect=AssertionError("Hot-path file I/O")):
        for name, minimum in expected.items():
            actual = encounters.check_min_generate_level(name)
            assert actual == minimum, (name, minimum, actual)
        minimums = [
            [name, encounters.check_min_generate_level(name)]
            for name in sorted(pokedex)
        ]
    serialized = json.dumps(minimums, separators=(",", ":"), ensure_ascii=True).encode()
    digest = hashlib.sha256(serialized).hexdigest()
    assert digest == BASELINE_MINIMUMS_SHA256, (
        "Encounter balance changed relative to PR #928's base",
        digest,
    )
    return len(minimums)


def _generate(
    encounters,
    driver,
    target,
    wild_level,
    tier="Normal",
    main_level=None,
    random_values=(0.99,),
    include_fallback=True,
):
    """Control only tier/pool/RNG, retaining real level, form, and ownership guards."""
    from Ankimon.functions.pokedex_functions import _load_pokedex_cache

    # Satisfy unrelated ownership/prerequisites with a supplied caught snapshot;
    # their real checks still run, while this proof isolates encounter levels.
    collected = {row.get("actual_id") for row in _load_pokedex_cache().values()}
    main_level = wild_level if main_level is None else main_level
    first_roll = True
    original_randint = encounters.random.randint
    random_rolls = iter(random_values)
    chosen_pools = []

    def randint(low, high):
        nonlocal first_roll
        if first_roll:
            first_roll = False
            assert low <= wild_level <= high, (main_level, wild_level, low, high)
            return wild_level
        return original_randint(low, high)

    def choose_first(options):
        if all(isinstance(option, int) for option in options):
            chosen_pools.append(list(options))
        return options[0]

    def tier_pool(current_tier):
        if current_tier == tier:
            return (
                [target, 19]
                if tier == "Normal" and include_fallback and target != 19
                else [target]
            )
        return [19] if current_tier == "Normal" else []

    with ExitStack() as stack:
        stack.enter_context(patch.object(encounters, "get_tier", return_value=tier))
        stack.enter_context(
            patch.object(encounters, "get_all_pokemon_in_tier", side_effect=tier_pool)
        )
        stack.enter_context(
            patch.object(encounters.random, "randint", side_effect=randint)
        )
        stack.enter_context(
            patch.object(encounters.random, "choice", side_effect=choose_first)
        )
        stack.enter_context(
            patch.object(
                encounters.random,
                "random",
                side_effect=lambda: next(random_rolls, 0.99),
            )
        )
        stack.enter_context(
            patch.object(encounters, "pick_random_gender", return_value="M")
        )
        stack.enter_context(
            patch.object(encounters, "shiny_chance", return_value=False)
        )
        result = encounters.generate_random_pokemon(
            main_level,
            driver.services.tracker,
            trainer_level=1,
            main_level=main_level,
            collected_ids=collected,
        )
    assert result[2] == wild_level, (target, wild_level, result[:3])
    return result, chosen_pools


def _encounters(encounters, driver):
    driver.set_setting("misc.active_region", "No Region")
    cases = 0
    for name, minimum, tier in (
        ("Pikachu", 100, "Normal"),
        ("Raichu", 100, "Normal"),
        ("Raichu-Alola", 100, "Normal"),
        ("Alakazam", 100, "Normal"),
        ("Vileplume", 100, "Normal"),
        ("Gengar", 100, "Normal"),
        ("Togekiss", 100, "Normal"),
        ("Porygon-Z", 100, "Normal"),
        ("Ambipom", 100, "Normal"),
        ("Pikachu-Gmax", 65, "Gmax"),
        ("Kadabra", 16, "Normal"),
        ("Charizard", 36, "Normal"),
        ("Buzzwole", 30, "Ultra"),
        ("Articuno", 50, "Legendary"),
        ("Venusaur-Mega", 60, "Mega"),
        ("Charizard-Gmax", 65, "Gmax"),
        ("Mew", 75, "Mythical"),
    ):
        target = encounters.safe_int(encounters.search_pokedex(name, "actual_id"))
        assert target > 0, name
        for wild_level in (minimum - 1, minimum):
            result, _ = _generate(encounters, driver, target, wild_level, tier)
            expected = 19 if wild_level < minimum else target
            assert result[1] == expected, (name, wild_level, expected, result[:3])
            assert result[14] == ("Normal" if expected == 19 else tier)
            cases += 1
    result, _ = _generate(encounters, driver, 19, 1)
    assert result[1] == 19
    # The policy is about WILD level, so a main level 97 can roll a level-100 Pikachu.
    result, _ = _generate(encounters, driver, 25, 100, main_level=97)
    assert result[1] == 25
    return cases + 2


def _regional(encounters, driver):
    # Real base/form data: Sandslash evolves at 22; Alolan Sandslash uses an item.
    assert encounters.check_min_generate_level("sandslash") == 22
    assert encounters.check_min_generate_level("sandslashalola") == 100
    assert encounters._get_regional_form_lookup()[28]["alola"] == [10102]
    for route, region, rolls in (
        ("boosted", "Alola", (0.0,)),
        ("active-region substitution", "Alola", (0.99, 0.0)),
        ("unselected-region substitution", "No Region", (0.0,)),
    ):
        driver.set_setting("misc.active_region", region)
        for wild_level in (99, 100):
            result, pools = _generate(
                encounters,
                driver,
                28,
                wild_level,
                random_values=rolls,
                include_fallback=False,
            )
            expected = 28 if wild_level == 99 else 10102
            assert result[1] == expected, (route, wild_level, expected, result[:3])
            if wild_level == 100:
                assert pools == (
                    [[10102]] if route == "boosted" else [[28], [10102]]
                ), pools
    return 6


def run_proof(section="all"):
    blocker = _NoQt()
    sys.meta_path.insert(0, blocker)
    try:
        with tempfile.TemporaryDirectory(prefix="ankimon-min-level-") as profile:
            bootstrap(user_path=profile)
            from Ankimon.functions import encounter_functions as encounters

            checks = {
                "ancestry": _ancestry,
                "cached": _cached_species,
                "encounters": _encounters,
                "regional": _regional,
            }
            selected = checks if section == "all" else {section: checks[section]}
            driver = None
            if set(selected) & {"encounters", "regional"}:
                from harness.driver import Driver

                driver = Driver(
                    user_path=profile,
                    first_encounter=False,
                    settings_overrides={f"misc.gen{i}": True for i in range(1, 10)},
                )
            for name, check in selected.items():
                print(f"  {name}: {check(encounters, driver)} cases OK")
            if driver:
                assert not [e for e in driver.drain_events() if e["type"] == "error"]
    finally:
        sys.meta_path.remove(blocker)
    print(f"probe_encounter_min_level: {section} OK")
    return True


if __name__ == "__main__":
    run_proof(sys.argv[1] if len(sys.argv) > 1 else "all")
