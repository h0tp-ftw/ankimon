"""Tier-1 contract for the Ankidex 'Available' badge source (ankidex_data).

build_encounterable_ids() must mark a species 'Available' when and only when the
live wild-encounter roll can actually produce it. It therefore mirrors
generate_random_pokemon() guard for guard rather than unioning the raw
encounter_data tier lists. The regressions this pins:

* Tier gate — a tier below its main-Pokémon level threshold has probability 0 and
  the roll's fallback only degrades to "Normal", so it is unreachable (Starter
  opens at 80, Mythical at 75, ...).
* Generation gate — check_id_ok() rejects disabled generations and regional forms.
* Level gate — the roll rolls a wild level in [main - 3, main + 3] and gates on
  THAT level, so a species is Available three levels before the player reaches
  its own minimum. Marking it Unavailable until then was a false negative.
* Regional forms — the roll reaches a variant only after rolling its BASE species,
  and when no region is active it offers variants from *every* region. Ankidex
  used to add only the active region's raw list, which marked all ~57 forms
  Unavailable at the default (no region) setting while they could still spawn.
* UNAVAILABLE ids never appear.

Qt-free: ankidex_data.py imports no Qt; encounter_functions / encounter_data are
stubbed so the test controls the gate and never touches the real 1000-line data.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_src = Path(__file__).parent.parent / "src"

# Simulated live-roll pool. 19 / 27 / 52 are bases that own regional forms; 157
# is a Starter-tier base that owns one (the Hisui starter evolutions).
_TIER_POOL = {
    "Normal": [1, 4, 7, 19, 27, 52, 999],
    "Baby": [172],
    "Ultra": [793],
    "Legendary": [1007, 888],  # 1007 = Gen 9 (disabled below), 888 = Gen 8
    "Mythical": [1024],        # Gen 9 (disabled)
    "Mega": [],
    "Gmax": [],
    "Starter": [152, 155, 157],
}

# Live tier thresholds (encounter_functions.OVERHAUL_LEVEL_THRESHOLDS, which is
# identical to the legacy inline dict in _modify_percentages_legacy).
_TIER_THRESHOLDS = {
    "Ultra": 30,
    "Legendary": 50,
    "Mega": 60,
    "Gmax": 65,
    "Mythical": 75,
    "Starter": 80,
}

# species_id -> {region -> [actual_id, ...]}, the shape of
# encounter_data.REGIONAL_FORM_LOOKUP that the roll resolves variants through.
_REGIONAL_LOOKUP = {
    19: {"alola": [10091]},
    27: {"alola": [10101]},           # 10101 is generation-disabled below
    52: {"alola": [10107], "galar": [10161]},
    157: {"hisui": [10233]},          # base lives in the level-80 Starter tier
}

# Ids the generation toggle rejects (stand-in for misc.gen9 == False, plus a
# disabled regional form).
_DISABLED = {1007, 1024, 10101}

# Individual minimum generate levels; everything else is 1.
_MIN_LEVELS = {
    "pokemon-7": 20,      # exercises the [main - 3, main + 3] wild-level window
    "pokemon-155": 90,    # a Starter with its own requirement above the tier gate
    "pokemon-888": 50,    # Legendary sitting exactly on its tier floor
    "pokemon-999": 103,   # above the level-100 wild cap
}


def _fake_get_all_pokemon_in_tier(tier):
    return _TIER_POOL.get(tier, [])


def _fake_check_id_ok(pid):
    return pid not in _DISABLED


def _fake_search_pokedex_by_id(pid):
    return f"pokemon-{pid}"


def _fake_check_min_generate_level(name):
    return _MIN_LEVELS.get(name, 1)


def _fake_get_regional_form_lookup():
    return _REGIONAL_LOOKUP


class _Settings:
    def __init__(self, region=None):
        self._region = region

    def get(self, key, default=None):
        if key == "misc.active_region":
            return self._region
        return default


@pytest.fixture
def ankidex_data(monkeypatch):
    # Stub encounter_data (top-level import) and encounter_functions (lazy import
    # inside build_encounterable_ids) so the gate is fully under test control.
    ed = types.ModuleType("Ankimon.functions.encounter_data")
    ed.UNAVAILABLE = [4]  # Normal-tier id that is explicitly excluded
    ed.REGIONAL_FORMS = {"alola": [10091, 10101, 10107], "galar": [10161]}
    ed.PREREQUISITES = {}
    ed.REGIONAL_FORM_REGION = {}
    monkeypatch.setitem(sys.modules, "Ankimon.functions.encounter_data", ed)

    ef = types.ModuleType("Ankimon.functions.encounter_functions")
    ef.OVERHAUL_LEVEL_THRESHOLDS = _TIER_THRESHOLDS
    ef._get_regional_form_lookup = _fake_get_regional_form_lookup
    ef.check_id_ok = _fake_check_id_ok
    ef.check_min_generate_level = _fake_check_min_generate_level
    ef.get_all_pokemon_in_tier = _fake_get_all_pokemon_in_tier
    ef.search_pokedex_by_id = _fake_search_pokedex_by_id
    monkeypatch.setitem(sys.modules, "Ankimon.functions.encounter_functions", ef)

    spec = importlib.util.spec_from_file_location(
        "Ankimon.ankidex.ankidex_data", _src / "Ankimon" / "ankidex" / "ankidex_data.py"
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "Ankimon.ankidex.ankidex_data", mod)
    spec.loader.exec_module(mod)
    return mod


def test_encounterable_excludes_generation_disabled_legendaries(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=100)
    # The exact reported bug: a Gen-9-disabled legendary/mythical shown Available.
    assert 1007 not in ids
    assert 1024 not in ids
    # A gen-enabled legendary from the same tier list still shows.
    assert 888 in ids


def test_encounterable_starters_require_level_80_gate(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=79)
    assert 152 not in ids
    assert 155 not in ids
    assert 157 not in ids

    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=80)
    # The level-80 Starter gate is open, but individual level requirements stay
    # in effect (155 needs 90; the wild window only reaches 83).
    assert 152 in ids
    assert 157 in ids
    assert 155 not in ids


def test_encounterable_keeps_eligible_starters(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=100)
    assert ids == {
        1, 7, 19, 27, 52,           # Normal (4 is UNAVAILABLE, 999 needs 103)
        172, 793, 888,              # Baby / Ultra / Legendary
        152, 155, 157,              # Starter tier, gate open at 80
        10091, 10107, 10161, 10233,  # every region's forms — none is active
    }


def test_encounterable_applies_unavailable_exclusion(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=100)
    assert 4 not in ids  # in UNAVAILABLE


def test_encounterable_regional_forms_are_generation_gated(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(
        _Settings(region="alola"), player_level=100
    )
    assert 10091 in ids       # enabled regional form for the active region
    assert 10101 not in ids   # disabled regional form must not appear


# --- the wild-level window ---------------------------------------------------

def test_species_available_three_levels_before_its_own_minimum(ankidex_data):
    # pokemon-7 needs level 20. The roll can produce a wild level of main + 3,
    # so it becomes reachable at main level 17 — not 20.
    assert 7 not in ankidex_data.build_encounterable_ids(_Settings(), player_level=16)
    assert 7 in ankidex_data.build_encounterable_ids(_Settings(), player_level=17)


def test_level_100_pins_the_wild_window_to_100(ankidex_data):
    # At the cap the roll forces the wild level to exactly 100, so the +3 window
    # must not open for a species that needs 103.
    assert 999 not in ankidex_data.build_encounterable_ids(
        _Settings(), player_level=100
    )
    # Above the cap (remove_level_cap) the ordinary window applies again.
    assert 999 in ankidex_data.build_encounterable_ids(_Settings(), player_level=101)


def test_tier_gate_still_blocks_a_species_the_window_would_reach(ankidex_data):
    # 888 needs level 50 and its Legendary tier opens at 50. The +3 window alone
    # would reach it at main level 47, but the tier has probability 0 there and a
    # failed roll degrades only to Normal — so it must stay Unavailable.
    assert 888 not in ankidex_data.build_encounterable_ids(_Settings(), player_level=47)
    assert 888 in ankidex_data.build_encounterable_ids(_Settings(), player_level=50)


# --- regional forms ----------------------------------------------------------

@pytest.mark.parametrize("region", [None, "no region", "No Region", ""])
def test_regional_forms_show_for_every_region_when_none_is_active(
    ankidex_data, region
):
    # The roll's form-resolution step falls back to *all* regions when no region
    # is set (the shipped default), so every form of a rolled base can spawn.
    ids = ankidex_data.build_encounterable_ids(_Settings(region=region), player_level=100)
    assert {10091, 10107, 10161} <= ids


def test_active_region_restricts_forms_to_that_region(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(
        _Settings(region="alola"), player_level=100
    )
    assert 10107 in ids       # meowth-alola, the active region
    assert 10161 not in ids   # meowth-galar is out of region


def test_active_region_is_case_and_whitespace_insensitive(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(
        _Settings(region="  ALOLA "), player_level=100
    )
    assert 10107 in ids
    assert 10161 not in ids


def test_regional_form_requires_its_base_species_to_be_reachable(ankidex_data):
    # 10233 hangs off base 157, which lives in the level-80 Starter tier. The
    # form is only reachable once the roll can reach that base.
    ids = ankidex_data.build_encounterable_ids(
        _Settings(region="hisui"), player_level=79
    )
    assert 10233 not in ids

    ids = ankidex_data.build_encounterable_ids(
        _Settings(region="hisui"), player_level=80
    )
    assert 10233 in ids


# --- defensive ---------------------------------------------------------------

@pytest.mark.parametrize("level", [None, "not a level"])
def test_unusable_player_level_falls_back_to_one(ankidex_data, level):
    # services.main_pokemon.level is None before the first main Pokémon is bound;
    # the level comparisons must not raise.
    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=level)
    assert 1 in ids       # a level-1 Normal is still Available
    assert 152 not in ids  # the Starter gate stays shut


def test_stats_key_removed_from_empty_payload(ankidex_data):
    # The dead 'stats' block (2 aggregate SQL queries nothing in the UI read) is
    # gone from the payload shape.
    assert "stats" not in ankidex_data._empty_payload()
