"""Tier-1 contract for the Ankidex 'Unlocked' badge source (ankidex_data).

build_encounterable_ids() answers "has this account unlocked the species for the
wild roll?" — a PROGRESSION predicate (main-Pokemon level, generation toggles,
active region, Pokemon currently owned). It deliberately does NOT answer "can the
next roll produce it": the live roll additionally weights tiers by today's review
progress, and the legacy path folds every non-Normal tier into "Normal" until 40%
of battle.daily_average is done. Modelling that here would flip ~370 species to
Locked every morning and churn the two Ankidex form lists that read this same
set. tests/test_encounter_functions.py pins that weighting on the production path;
this file pins the progression half.

Within that scope it mirrors generate_random_pokemon()'s per-candidate guards
rather than unioning the raw encounter_data tier lists. The regressions this pins:

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
* Guard 3 — a Mega/Gmax candidate is only reachable once the player owns its base
  species, exactly as the roll's _player_owns_base_form() decides it. Without this
  all 111 Mega and 34 Gmax ids were badged Unlocked for a brand-new player.
* UNAVAILABLE ids never appear.

Guard 4 (prerequisites) is NOT applied here on purpose — the payload ships
`prerequisites` separately and the SPA gates the badge on it.

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
    "Mythical": [1024],  # Gen 9 (disabled)
    # 10041 / 10043 are Mega forms of bases 6 / 150; 10186 is a Gmax form of 3.
    "Mega": [10041, 10043],
    "Gmax": [10186],
    "Starter": [152, 155, 157],
}

# actual_id -> species_id, the remap _player_owns_base_form() does before checking
# the collection.
_BASE_SPECIES = {10041: 6, 10043: 150, 10186: 3}

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
    27: {"alola": [10101]},  # 10101 is generation-disabled below
    52: {"alola": [10107], "galar": [10161]},
    157: {"hisui": [10233]},  # base lives in the level-80 Starter tier
}

# Ids the generation toggle rejects (stand-in for misc.gen9 == False, plus a
# disabled regional form).
_DISABLED = {1007, 1024, 10101}

# Individual minimum generate levels; everything else is 1.
_MIN_LEVELS = {
    "pokemon-7": 20,  # exercises the [main - 3, main + 3] wild-level window
    "pokemon-155": 90,  # a Starter with its own requirement above the tier gate
    "pokemon-888": 50,  # Legendary sitting exactly on its tier floor
    "pokemon-999": 103,  # above the level-100 wild cap
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


def _fake_player_owns_base_form(actual_id, collected_ids):
    """Stand-in for the roll's Guard 3 predicate.

    Mirrors the real one's shape, including its "can't determine -> allow
    through" fallback for an id with no known base species.
    """
    species_id = _BASE_SPECIES.get(actual_id)
    if not species_id:
        return True
    return species_id in collected_ids


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
    ef._player_owns_base_form = _fake_player_owns_base_form
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
    # owned_ids omitted -> Guard 3 is skipped, so the Mega/Gmax ids are present.
    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=100)
    assert ids == {
        1,
        7,
        19,
        27,
        52,  # Normal (4 is UNAVAILABLE, 999 needs 103)
        172,
        793,
        888,  # Baby / Ultra / Legendary
        152,
        155,
        157,  # Starter tier, gate open at 80
        10041,
        10043,
        10186,  # Mega / Gmax, ungated without an owned set
        10091,
        10107,
        10161,
        10233,  # every region's forms — none is active
    }


def test_encounterable_applies_unavailable_exclusion(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(_Settings(), player_level=100)
    assert 4 not in ids  # in UNAVAILABLE


def test_encounterable_regional_forms_are_generation_gated(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(
        _Settings(region="alola"), player_level=100
    )
    assert 10091 in ids  # enabled regional form for the active region
    assert 10101 not in ids  # disabled regional form must not appear


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
def test_regional_forms_show_for_every_region_when_none_is_active(ankidex_data, region):
    # The roll's form-resolution step falls back to *all* regions when no region
    # is set (the shipped default), so every form of a rolled base can spawn.
    ids = ankidex_data.build_encounterable_ids(
        _Settings(region=region), player_level=100
    )
    assert {10091, 10107, 10161} <= ids


def test_active_region_restricts_forms_to_that_region(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(
        _Settings(region="alola"), player_level=100
    )
    assert 10107 in ids  # meowth-alola, the active region
    assert 10161 not in ids  # meowth-galar is out of region


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
    assert 1 in ids  # a level-1 Normal is still Available
    assert 152 not in ids  # the Starter gate stays shut


def test_stats_key_removed_from_empty_payload(ankidex_data):
    # The dead 'stats' block (2 aggregate SQL queries nothing in the UI read) is
    # gone from the payload shape.
    assert "stats" not in ankidex_data._empty_payload()


# --- Guard 3: Mega/Gmax base-form ownership --------------------------------


def test_mega_and_gmax_require_owning_the_base_species(ankidex_data):
    # The roll skips a Mega/Gmax candidate whose base species the player does
    # not own (generate_random_pokemon Guard 3). Before this was mirrored, all
    # 111 Mega and 34 Gmax ids were badged Unlocked for a brand-new player —
    # "No requirements. Find Mega Charizard in the wild." for someone with an
    # empty collection.
    ids = ankidex_data.build_encounterable_ids(
        _Settings(), player_level=100, owned_ids=set()
    )
    assert 10041 not in ids  # base 6 not owned
    assert 10043 not in ids  # base 150 not owned
    assert 10186 not in ids  # base 3 not owned
    # Non-Mega/Gmax tiers are untouched by Guard 3.
    assert 152 in ids
    assert 888 in ids


def test_owning_the_base_species_unlocks_its_mega(ankidex_data):
    ids = ankidex_data.build_encounterable_ids(
        _Settings(), player_level=100, owned_ids={6}
    )
    assert 10041 in ids  # base 6 owned
    assert 10043 not in ids  # base 150 still not owned
    assert 10186 not in ids


def test_guard_three_is_skipped_when_no_owned_set_is_supplied(ankidex_data):
    # None means "caller supplied no collection", not "an empty collection" —
    # a host that cannot resolve the DB must not have every Mega vanish.
    ids = ankidex_data.build_encounterable_ids(
        _Settings(), player_level=100, owned_ids=None
    )
    assert {10041, 10043, 10186} <= ids


def test_mega_still_obeys_the_tier_level_gate_when_owned(ankidex_data):
    # Ownership does not bypass the Mega tier's own level-60 threshold.
    ids = ankidex_data.build_encounterable_ids(
        _Settings(), player_level=59, owned_ids={3, 6, 150}
    )
    assert 10041 not in ids
    assert 10186 not in ids  # Gmax opens at 65

    ids = ankidex_data.build_encounterable_ids(
        _Settings(), player_level=60, owned_ids={3, 6, 150}
    )
    assert 10041 in ids
    assert 10186 not in ids  # still below the Gmax threshold


# --- the payload keeps the two owned sets apart -----------------------------


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDb:
    """Minimal stand-in for AnkimonDB covering only what get_ankidex_data reads.

    ``history`` are ids that were caught and later released: they live in
    pokemon_history but no longer in captured_pokemon, which is exactly the
    divergence between the two owned sets.
    """

    def __init__(self, captured=(), history=()):
        self._captured = list(captured)
        self._history = list(history)
        self.get_seen_ids = lambda: set()

    def execute(self, sql, *args):
        if "shiny = 1" in sql:
            return _FakeCursor([])
        if "captured_pokemon" in sql:
            return _FakeCursor([(pid,) for pid in self._captured])
        if "pokemon_history" in sql:
            return _FakeCursor([(pid,) for pid in self._history])
        raise AssertionError(f"unexpected query: {sql}")


def test_payload_ships_the_roll_owned_set_separately(ankidex_data):
    payload = ankidex_data.get_ankidex_data(
        _FakeDb(captured=[6], history=[151]), _Settings(), player_level=100
    )
    # "owned" stays the wide set the sprite / "Completed" states need...
    assert set(payload["owned"]) == {6, 151}
    # ...while "ownedNow" is exactly what the roll's guards see.
    assert set(payload["ownedNow"]) == {6}


def test_payload_applies_guard_three_with_the_currently_owned_set(ankidex_data):
    # Base 6 owned, base 150 only in history (released). Guard 3 must unlock the
    # Mega of 6 and keep the Mega of 150 out, matching the roll.
    payload = ankidex_data.get_ankidex_data(
        _FakeDb(captured=[6], history=[150]), _Settings(), player_level=100
    )
    encounterable = set(payload["encounterable"])
    assert 10041 in encounterable  # base 6 currently owned
    assert 10043 not in encounterable  # base 150 released — the roll skips it


# --- the payload names the released set, so the SPA can explain a "not met" --
# "owned" minus "ownedNow" is not the same thing as "released": it also holds
# every Pokemon evolved away (mark_as_caught keeps the pre-evolution in
# pokedex_caught). The SPA can only say the word "Released" about a species it
# is told was released, so pokemon_history ships as its own key.


def test_payload_names_the_released_species(ankidex_data):
    payload = ankidex_data.get_ankidex_data(
        _FakeDb(captured=[6], history=[151]), _Settings(), player_level=100
    )
    assert set(payload["released"]) == {151}
    # ...and it is still folded into the wide set: releasing does not un-catch.
    assert set(payload["owned"]) == {6, 151}
    assert set(payload["ownedNow"]) == {6}


def test_released_does_not_claim_an_evolved_away_species(ankidex_data):
    # 4 sits in pokedex_caught (get_caught_ids) because it was caught and then
    # evolved. It is in "owned" and out of "ownedNow" exactly like a released
    # Pokemon — but nothing recorded a release, so it must not be named one.
    db = _FakeDb(captured=[5], history=[])
    db.get_caught_ids = lambda: {4}
    payload = ankidex_data.get_ankidex_data(db, _Settings(), player_level=100)
    assert set(payload["owned"]) == {4, 5}
    assert set(payload["ownedNow"]) == {5}
    assert set(payload["released"]) == set()


def test_a_species_can_be_released_and_still_owned(ankidex_data):
    # One Mew released, another still in the box. The set is "has ever been
    # released", not "is gone" — the SPA resolves the overlap by checking
    # ownedNow first, so the row still reads "Caught".
    payload = ankidex_data.get_ankidex_data(
        _FakeDb(captured=[151], history=[151]), _Settings(), player_level=100
    )
    assert set(payload["released"]) == {151}
    assert set(payload["ownedNow"]) == {151}


def test_an_unreadable_history_table_leaves_released_empty(ankidex_data):
    # Same degradation the wide set already takes: a restored backup predating
    # pokemon_history must serve a payload, not raise.
    class _NoHistoryDb(_FakeDb):
        def execute(self, sql, *args):
            if "pokemon_history" in sql:
                raise RuntimeError("no such table: pokemon_history")
            return super().execute(sql, *args)

    payload = ankidex_data.get_ankidex_data(
        _NoHistoryDb(captured=[6]), _Settings(), player_level=100
    )
    assert payload["released"] == []
    assert set(payload["owned"]) == {6}


def test_empty_payload_carries_the_released_key(ankidex_data):
    # The DB-absent payload has to have the same shape, or the SPA's
    # `data.released || []` fallback silently becomes the only code path.
    assert ankidex_data._empty_payload()["released"] == []
