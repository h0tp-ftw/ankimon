"""Tier-1 contract for the Ankidex 'Available' badge source (ankidex_data).

build_encounterable_ids() must mark a species 'Available' only when the live
wild-encounter roll can actually produce it — i.e. it must gate every candidate
through encounter_functions.get_all_pokemon_in_tier() + check_id_ok(), NOT union
the raw encounter_data tier lists. The regression this pins:

* Starters appear only once the player's Pokémon reaches the live level-80
  tier gate, and only when they meet their individual level requirement.
* Generation-disabled species (e.g. Gen 9 legendaries when misc.gen9 is off)
  never appear, because check_id_ok() rejects them.
* UNAVAILABLE ids and generation-disabled regional forms never appear.

Qt-free: ankidex_data.py imports no Qt; encounter_functions / encounter_data are
stubbed so the test controls the gate and never touches the real 1000-line data.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_src = Path(__file__).parent.parent / "src"

# Simulated live-roll pool.
_TIER_POOL = {
    "Normal": [1, 4, 7],
    "Baby": [172],
    "Ultra": [793],
    "Legendary": [1007, 888],  # 1007 = Gen 9 (disabled below), 888 = Gen 8
    "Mythical": [1024],        # Gen 9 (disabled)
    "Mega": [],
    "Gmax": [],
    "Starter": [152, 155],
}

# Ids the generation toggle rejects (stand-in for misc.gen9 == False, plus a
# disabled regional form).
_DISABLED = {1007, 1024, 10101}


def _fake_get_all_pokemon_in_tier(tier):
    return _TIER_POOL.get(tier, [])


def _fake_check_id_ok(pid):
    return pid not in _DISABLED


def _fake_search_pokedex_by_id(pid):
    return f"pokemon-{pid}"


def _fake_check_min_generate_level(name):
    return 90 if name == "pokemon-155" else 1


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
    ed.REGIONAL_FORMS = {"alola": [10100, 10101]}
    ed.PREREQUISITES = {}
    ed.REGIONAL_FORM_REGION = {}
    monkeypatch.setitem(sys.modules, "Ankimon.functions.encounter_data", ed)

    ef = types.ModuleType("Ankimon.functions.encounter_functions")
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

    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=80)
    # The level-80 Starter gate is open, but individual level requirements stay
    # in effect.
    assert 152 in ids
    assert 155 not in ids


def test_encounterable_keeps_eligible_starters(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=100)
    assert ids == {1, 7, 152, 155, 172, 793, 888}


def test_encounterable_applies_unavailable_exclusion(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=100)
    assert 4 not in ids  # in UNAVAILABLE


def test_encounterable_regional_forms_are_generation_gated(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(
        _Settings(region="alola"), player_level=100
    )
    assert 10100 in ids       # enabled regional form for the active region
    assert 10101 not in ids   # disabled regional form must not appear


def test_stats_key_removed_from_empty_payload(ankidex_data):
    # The dead 'stats' block (2 aggregate SQL queries nothing in the UI read) is
    # gone from the payload shape.
    assert "stats" not in ankidex_data._empty_payload()
