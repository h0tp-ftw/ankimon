"""Tests for gender-gated item evolutions and CSV helper hygiene.

Covers two same-class findings discovered while reviewing PRs #785/#744/#706
(evolution requirements carried by the bundled data but ignored by the code):

* ``check_evolution_by_item`` now honors the ``gender_id`` column of
  ``pokemon_evolution.csv`` when the caller supplies a gender: Gallade (475)
  requires a male Kirlia, Froslass (478) a female Snorunt/Kirlia. Without a
  gender argument the historical no-check behavior is preserved.
* ``rows_for_key_in_table`` was defined twice in pokedex_functions.py; the
  second definition shadowed the first, so only one may exist.

Loading strategy mirrors ``tests/test_move_evo_timing.py``: stub Anki/aqt,
load ``resources`` + ``pokedex_functions`` FOR REAL so the bundled data drives
every lookup.
"""

import importlib.util
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"


class _FakeSettings:
    """Minimal stand-in for ``settings_obj`` backed by a mutable dict."""

    def __init__(self):
        self.values = {"misc.active_region": None}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _load_pf():
    sys.modules["aqt"] = mock.MagicMock()
    sys.modules["aqt.qt"] = mock.MagicMock()
    sys.modules["aqt.utils"] = mock.MagicMock()
    sys.modules["Ankimon.pyobj.error_handler"] = mock.MagicMock()

    fake_settings = _FakeSettings()
    singletons_stub = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("Ankimon.singletons", loader=None)
    )
    singletons_stub.settings_obj = fake_settings
    sys.modules["Ankimon.singletons"] = singletons_stub

    res_spec = importlib.util.spec_from_file_location(
        "Ankimon.resources", _SRC / "Ankimon" / "resources.py"
    )
    resources = importlib.util.module_from_spec(res_spec)
    sys.modules["Ankimon.resources"] = resources
    res_spec.loader.exec_module(resources)

    pf_spec = importlib.util.spec_from_file_location(
        "Ankimon.functions.pokedex_functions",
        _SRC / "Ankimon" / "functions" / "pokedex_functions.py",
    )
    pokedex_functions = importlib.util.module_from_spec(pf_spec)
    sys.modules["Ankimon.functions.pokedex_functions"] = pokedex_functions
    pf_spec.loader.exec_module(pokedex_functions)
    return pokedex_functions


pf = _load_pf()

_SINGLETONS_STUB = sys.modules["Ankimon.singletons"]
_POKEDEX_FUNCTIONS_STUB = sys.modules["Ankimon.functions.pokedex_functions"]

# Dawn Stone item id from items.csv (Kirlia -> Gallade/Froslass).
_DAWN_STONE_ID = 109


@pytest.fixture(autouse=True)
def _reset_env():
    sys.modules["Ankimon.singletons"] = _SINGLETONS_STUB
    sys.modules["Ankimon.functions.pokedex_functions"] = _POKEDEX_FUNCTIONS_STUB
    yield


# --------------------------------------------------------------------------- #
# Gender-gated useItem evolutions (Kirlia 281 -> Gallade 475 / Froslass 478)
# --------------------------------------------------------------------------- #
def test_dawn_stone_on_male_kirlia_gives_gallade():
    assert pf.check_evolution_by_item(281, _DAWN_STONE_ID, gender="Male") == 475


def test_dawn_stone_on_female_snorunt_gives_froslass():
    # Froslass (478) evolves only from a FEMALE Snorunt (361) with a Dawn
    # Stone; the CSV gender gate must make this exact and not leak to males.
    assert pf.check_evolution_by_item(361, _DAWN_STONE_ID, gender="F") == 478


def test_dawn_stone_gender_mismatch_does_not_evolve_to_wrong_species():
    # The core bug: without a gender gate either sex could become Gallade.
    assert pf.check_evolution_by_item(281, _DAWN_STONE_ID, gender="F") != 475
    assert pf.check_evolution_by_item(281, _DAWN_STONE_ID, gender="M") != 478


def test_female_snorunt_with_dawn_stone_gives_froslass():
    # Snorunt (361) has both Glalie (plain level) and Froslass (female +
    # Dawn Stone); only the female form is item-reachable.
    assert pf.check_evolution_by_item(361, _DAWN_STONE_ID, gender="Female") == 478


def test_male_snorunt_with_dawn_stone_gets_no_female_locked_evo():
    assert pf.check_evolution_by_item(361, _DAWN_STONE_ID, gender="M") is None


def test_unknown_gender_preserves_historical_no_check_behavior():
    # Callers without gender data keep working exactly as before.
    result = pf.check_evolution_by_item(281, _DAWN_STONE_ID, gender=None)
    assert result in (475, 478)


def test_junk_gender_values_degrade_to_no_check():
    for junk in ("Genderless", "", "x"):
        result = pf.check_evolution_by_item(281, _DAWN_STONE_ID, gender=junk)
        assert result in (475, 478)


def test_non_gendered_items_unaffected_by_gender_argument():
    # A Thunder Stone on Pikachu must behave identically with or without gender.
    thunder_stone_id = 82
    without = pf.check_evolution_by_item(25, thunder_stone_id)
    for gender in ("M", "F", None):
        assert (
            pf.check_evolution_by_item(25, thunder_stone_id, gender=gender) == without
        )


def test_csv_gender_id_normalization():
    assert pf._csv_gender_id("M") == 2
    assert pf._csv_gender_id("male") == 2
    assert pf._csv_gender_id("F") == 1
    assert pf._csv_gender_id("Female") == 1
    assert pf._csv_gender_id("Genderless") is None
    assert pf._csv_gender_id(None) is None
    assert pf._csv_gender_id(7) is None


def test_rows_for_key_in_table_defined_exactly_once():
    # The helper used to be defined twice (the second shadowing the first);
    # pin the de-duplicated state.
    import inspect

    source = inspect.getsource(pf)
    count = source.count("def rows_for_key_in_table(")
    assert count == 1


# --------------------------------------------------------------------------- #
# Gender-gated LEVEL evolutions (CSV gender_id on trigger-1 rows).
#
# pokedex.json lists only the female target for Combee/Salandit and both
# Burmy targets with no gender data, so without the CSV gate a male Combee
# could evolve into Vespiquen. The level path must honor the same
# pokemon_evolution.csv gender gate as the item path.
# --------------------------------------------------------------------------- #
class _FakeEvoWindow:
    def __init__(self):
        self.calls = []

    def ask_pokemon_evo(self, individual_id, pokemon_id, evo_id):
        self.calls.append((individual_id, pokemon_id, evo_id))


def test_female_combee_level_evolves_to_vespiquen():
    win = _FakeEvoWindow()
    assert pf.check_evolution_for_pokemon("ind-f", 415, 21, win, gender="F") == 416
    assert win.calls == [("ind-f", 415, 416)]


def test_male_combee_cannot_become_vespiquen():
    win = _FakeEvoWindow()
    assert pf.check_evolution_for_pokemon("ind-m", 415, 21, win, gender="M") is None
    assert win.calls == []


def test_male_burmy_becomes_mothim_not_wormadam():
    win = _FakeEvoWindow()
    assert pf.check_evolution_for_pokemon("ind-m", 412, 20, win, gender="M") == 414


def test_burmy_gender_gate_blocks_cross_sex_targets():
    assert (
        pf.check_evolution_for_pokemon("ind-m", 412, 20, _FakeEvoWindow(), gender="M")
        != 413
    )
    assert (
        pf.check_evolution_for_pokemon("ind-f", 412, 20, _FakeEvoWindow(), gender="F")
        != 414
    )


def test_salandit_gender_gate_on_level_path():
    assert (
        pf.check_evolution_for_pokemon(
            "ind-f", 757, 33, _FakeEvoWindow(), gender="Female"
        )
        == 758
    )
    assert (
        pf.check_evolution_for_pokemon(
            "ind-m", 757, 33, _FakeEvoWindow(), gender="Male"
        )
        is None
    )


def test_unknown_gender_keeps_historical_level_behavior():
    # None/unrecognised gender keeps the no-check behavior so callers (and any
    # legacy saves) without gender data are unaffected.
    result_none = pf.check_evolution_for_pokemon(
        "ind-x", 415, 21, _FakeEvoWindow(), gender=None
    )
    assert result_none == 416


def test_everstone_still_blocks_gated_level_evolutions():
    win = _FakeEvoWindow()
    assert (
        pf.check_evolution_for_pokemon(
            "ind-f", 415, 21, win, everstone=True, gender="F"
        )
        is None
    )
    assert win.calls == []


def test_plain_level_evolvers_unaffected_by_gender_argument():
    win = _FakeEvoWindow()
    for gender in ("M", "F", None, "junk"):
        assert (
            pf.check_evolution_for_pokemon(
                "ind-p", 4, 20, win, everstone=False, gender=gender
            )
            == 5
        )


# --------------------------------------------------------------------------- #
# The gender lookup sits on the review path (a level-up runs it per candidate).
# Repo rule: "No synchronous disk I/O in the review path — static data is parsed
# once at startup." The rows now come from an in-memory index built once from
# _load_poke_evo_cache, so eligibility checks must open NO file at all; the
# lru_cache on top saves the scan rather than the parse. Also pin the trigger
# scoping that keeps one evolution method's gate from leaking onto another.
# --------------------------------------------------------------------------- #
def _count_evolution_csv_opens(fn):
    """Run ``fn`` and return how many times pokemon_evolution.csv was opened."""
    import builtins

    opens = []
    original = builtins.open

    def counting_open(file, *args, **kwargs):
        if str(file).endswith("pokemon_evolution.csv"):
            opens.append(str(file))
        return original(file, *args, **kwargs)

    builtins.open = counting_open
    try:
        result = fn()
    finally:
        builtins.open = original
    return result, opens


def test_evolution_row_gender_id_opens_no_file_once_initialised():
    # This used to be "one parse per cache miss" — the lru_cache suppressed
    # repeats of the same key but every NEW (species, trigger) pair still
    # re-opened and re-parsed the whole ~500-row CSV mid-review. Measured across
    # the dex that was 372 parses for 966 level-evolution checks.
    pf._evolution_row_gender_id_cached.cache_clear()
    pf.warm_evolution_caches()  # what startup does

    def _probe():
        first = pf._evolution_row_gender_id(416, pf._LEVEL_EVO_TRIGGERS)
        # 24 repeats of the same key, then a spread of DISTINCT keys — the case
        # the lru_cache alone could never cover.
        for _ in range(24):
            assert pf._evolution_row_gender_id(416, pf._LEVEL_EVO_TRIGGERS) == first
        for species_id in (413, 414, 758, 475, 478, 1, 25, 133, 700, 10004):
            for triggers in (pf._LEVEL_EVO_TRIGGERS, pf._ITEM_EVO_TRIGGERS):
                pf._evolution_row_gender_id(species_id, triggers)
        return first

    first, opens = _count_evolution_csv_opens(_probe)

    assert first == 1  # Vespiquen is female-only
    assert opens == [], f"eligibility checks re-parsed the CSV {len(opens)}x"


def test_evolution_index_matches_the_csv_scan_for_every_species_and_key_type():
    """The index must be a drop-in for rows_for_key_in_table on this column.

    A silent str/int key mismatch would return () and quietly stop offering
    evolutions with no error — the same shape of failure as the reverted
    stale-moveset optimisation (9a54562f). The two caller families genuinely
    pass different types: ``_evolution_row_gender_id_cached`` passes an int,
    while ``get_friendship_evolutions_for_species`` passes the strings
    ``pokemon_evolves_from_id`` returns. Sweep every id, both key types.
    """
    rows = pf._load_poke_evo_cache()
    ids = {r["evolved_species_id"] for r in rows if r.get("evolved_species_id")}
    assert len(ids) > 400, "sanity: the bundled CSV should carry ~479 species"

    for raw in ids:
        scanned = tuple(
            pf.rows_for_key_in_table("evolved_species_id", raw, pf.poke_evo_path)
        )
        assert pf.evolution_rows_for_evolved_species(raw) == scanned
        assert pf.evolution_rows_for_evolved_species(int(raw)) == scanned

    # Sylveon carries two rows; both must survive (the first-match bug).
    assert len(pf.evolution_rows_for_evolved_species(700)) == 2
    # Misses and malformed keys stay empty, exactly as the scan does. "0700"
    # matters: int-normalising the key would silently make it match Sylveon.
    for miss in (0, -1, 99999, "0700", "", "junk", None):
        assert pf.evolution_rows_for_evolved_species(miss) == ()


def test_clearing_the_pokedex_caches_also_clears_the_evolution_index():
    # The index holds references into the rows _load_poke_evo_cache built, so
    # leaving it warm past a clear would keep the pre-clear rows alive. Parity
    # with _pokedex_id_index, which is already in the clear list.
    pf._load_poke_evo_index()
    assert pf._poke_evo_index is not None
    pf.clear_pokedex_caches()
    assert pf._poke_evo_cache is None
    assert pf._poke_evo_index is None
    # And it rebuilds correctly afterwards.
    assert len(pf.evolution_rows_for_evolved_species(700)) == 2


def _fail_to_open_the_evolution_csv(fn):
    """Run ``fn`` with every open of pokemon_evolution.csv raising."""
    import builtins

    original = builtins.open

    def failing_open(file, *args, **kwargs):
        if str(file).endswith("pokemon_evolution.csv"):
            raise OSError("simulated read failure")
        return original(file, *args, **kwargs)

    builtins.open = failing_open
    try:
        return fn()
    finally:
        builtins.open = original


def test_warming_the_evolution_caches_takes_the_parse_off_the_review_path():
    """The index has to be built by STARTUP, not by the first level-up.

    The lazy loaders are correct, but their first CALLER decides when the parse
    happens — and every production caller of these rows (the gender gate here,
    friendship_evolution's level and friendship lookups) runs inside
    ``on_review_card``. Nothing outside the lookup itself ever built the index,
    so the ~500-row parse landed mid-review the first time a Pokemon levelled
    up: exactly the synchronous review-path I/O the docstrings on this path
    already claimed was gone, and that AGENTS.md forbids.
    """
    pf.clear_pokedex_caches()
    assert pf._poke_evo_index is None

    indexed = pf.warm_evolution_caches()

    assert indexed > 400, "the bundled CSV carries ~507 rows"
    assert pf._poke_evo_cache is not None
    assert pf._poke_evo_index is not None

    # With the warm done, the review path's own lookups open nothing at all.
    _, opens = _count_evolution_csv_opens(
        lambda: [
            pf._evolution_row_gender_id(species_id, pf._LEVEL_EVO_TRIGGERS)
            for species_id in (416, 413, 758, 475, 478)
        ]
    )
    assert opens == [], f"a warmed review path still parsed the CSV {len(opens)}x"


def test_a_failed_warm_leaves_the_lazy_path_to_retry_instead_of_memoizing_empty():
    """The warm is an optimization; it must not add a failure mode.

    ``_load_poke_evo_cache`` swallows a read error and memoizes ``[]`` for the
    session. Lazily that is survivable — the first read happens whenever the
    game needs the data. Moving the first read to boot means a file that is
    momentarily unavailable *then* (an add-on update mid-write, a cold network
    drive) would freeze "this species has no evolution rows" in for the whole
    session, silently disabling every gender gate and friendship lookup. So a
    warm that comes back empty puts the globals back rather than keeping it.
    """
    pf.clear_pokedex_caches()

    assert _fail_to_open_the_evolution_csv(pf.warm_evolution_caches) == 0
    assert pf._poke_evo_cache is None
    assert pf._poke_evo_index is None

    # Nothing was memoized: the ordinary lazy path still reads real data.
    assert len(pf.evolution_rows_for_evolved_species(700)) == 2


def test_warming_twice_is_a_no_op_that_keeps_the_same_rows():
    """Boot warms once per process and the profile-open hook warms again after
    a profile switch cleared the caches, so back-to-back warms are normal. The
    second must not re-parse or hand back different row objects — the index
    holds references into the cached rows, and callers memoize verdicts from
    them (``_evolution_row_gender_id_cached``)."""
    pf.clear_pokedex_caches()
    first_count = pf.warm_evolution_caches()
    first_rows = pf.evolution_rows_for_evolved_species(700)

    second_count, opens = _count_evolution_csv_opens(pf.warm_evolution_caches)

    assert second_count == first_count
    assert opens == []
    assert pf.evolution_rows_for_evolved_species(700) is first_rows


def test_evolution_row_gender_id_is_scoped_to_the_calling_trigger():
    # Gallade/Froslass are gated on use-item rows (trigger 3); Vespiquen and the
    # Burmy pair on level-up rows (trigger 1). Asking with the wrong trigger must
    # find no gate rather than borrowing another method's.
    assert pf._evolution_row_gender_id(475, pf._ITEM_EVO_TRIGGERS) == 2  # Gallade
    assert pf._evolution_row_gender_id(475, pf._LEVEL_EVO_TRIGGERS) is None
    assert pf._evolution_row_gender_id(478, pf._ITEM_EVO_TRIGGERS) == 1  # Froslass
    assert pf._evolution_row_gender_id(478, pf._LEVEL_EVO_TRIGGERS) is None

    assert pf._evolution_row_gender_id(416, pf._LEVEL_EVO_TRIGGERS) == 1  # Vespiquen
    assert pf._evolution_row_gender_id(416, pf._ITEM_EVO_TRIGGERS) is None
    assert pf._evolution_row_gender_id(413, pf._LEVEL_EVO_TRIGGERS) == 1  # Wormadam
    assert pf._evolution_row_gender_id(414, pf._LEVEL_EVO_TRIGGERS) == 2  # Mothim
    assert pf._evolution_row_gender_id(758, pf._LEVEL_EVO_TRIGGERS) == 1  # Salazzle

    # No trigger scope keeps the historical "any row" behavior.
    assert pf._evolution_row_gender_id(475) == 2
    # An ungated species stays ungated under every scope.
    assert pf._evolution_row_gender_id(2, pf._LEVEL_EVO_TRIGGERS) is None


def test_evolution_row_gender_id_resolves_form_ids_and_survives_junk():
    # Characterization pin, not a regression test: cloak forms (>= 10000) must
    # resolve to their base species before the CSV lookup, per the repo tripwire,
    # and junk must degrade to "no gate" rather than raising. Both held before
    # the cache was added; this pins that CACHING DID NOT BREAK THEM — the
    # unhashable cases below would die inside lru_cache's key lookup if the
    # coercion were not in the uncached wrapper.
    assert pf._evolution_row_gender_id(10004, pf._LEVEL_EVO_TRIGGERS) == 1
    for junk in (99999, 0, -1, None, "x", object()):
        assert pf._evolution_row_gender_id(junk, pf._LEVEL_EVO_TRIGGERS) is None
    for unhashable in ([1, 2], {"a": 1}, {1, 2}, bytearray(b"x")):
        assert pf._evolution_row_gender_id(unhashable, pf._LEVEL_EVO_TRIGGERS) is None
    # Numeric strings must not open a second cache entry for the same species.
    assert pf._evolution_row_gender_id("416", pf._LEVEL_EVO_TRIGGERS) == 1


def test_pokedex_entries_carry_an_id_the_gender_gate_can_resolve():
    # Data invariant, not a behavioural regression test: the removed
    # `or target_data.get("num")` fallback was unreachable because pokedex.json
    # carries actual_id/species_id on every entry and `num` on none. Removing it
    # is behaviour-neutral BY CONSTRUCTION, so no test can observe the removal —
    # what is worth pinning is the premise, which would silently stop holding if
    # the bundled pokedex were regenerated in the Smogon `num` schema.
    pokedex = pf._load_pokedex_cache()
    assert pokedex, "pokedex cache is empty"
    assert not [k for k, v in pokedex.items() if v.get("num") is not None]
    for species in (
        "gallade",
        "froslass",
        "wormadam",
        "mothim",
        "vespiquen",
        "salazzle",
    ):
        entry = pokedex[species]
        assert entry.get("actual_id") or entry.get("species_id")


# --------------------------------------------------------------------------- #
# Gender SPLITS the CSV cannot express (pokedex.json `gender` on the forms).
#
# veekun models Espurr -> Meowstic/Meowstic-F and Lechonk -> Oinkologne/
# Oinkologne-F as two FORMS of one species rather than as two gendered
# evolutions, so pokemon_evolution.csv carries no gender_id row for evolved
# species 678 or 916 and the CSV gate sees no split at all. The lowest-evo_id
# tie-break then handed EVERY Espurr the male Meowstic and every Lechonk the
# male Oinkologne — the same failure the CSV gate fixes for Burmy, and
# irreversible once accepted. pokedex.json does carry the split.
#
# pick_random_gender falls through to random.choice(["M", "F"]) for both
# species (neither has a genderRatio or a fixed gender), so ~half of all
# captures hit it.
# --------------------------------------------------------------------------- #
def test_female_espurr_evolves_into_the_female_meowstic_form():
    win = _FakeEvoWindow()
    assert pf.check_evolution_for_pokemon("ind-f", 677, 25, win, gender="F") == 10025
    assert win.calls == [("ind-f", 677, 10025)]


def test_male_espurr_evolves_into_the_male_meowstic_form():
    win = _FakeEvoWindow()
    assert pf.check_evolution_for_pokemon("ind-m", 677, 25, win, gender="M") == 678


def test_female_lechonk_evolves_into_the_female_oinkologne_form():
    win = _FakeEvoWindow()
    assert pf.check_evolution_for_pokemon("ind-f", 915, 18, win, gender="F") == 10254


def test_male_lechonk_evolves_into_the_male_oinkologne_form():
    win = _FakeEvoWindow()
    assert pf.check_evolution_for_pokemon("ind-m", 915, 18, win, gender="M") == 916


def test_split_forms_without_a_gender_keep_historical_behavior():
    # No gender on the record -> no narrowing, lowest evo_id as before.
    win = _FakeEvoWindow()
    assert pf.check_evolution_for_pokemon("ind", 677, 25, win, gender=None) == 678


def test_lone_gendered_target_never_blocks_an_evolution():
    # Bounsweet (761) -> Steenee (762) is a SINGLE target that pokedex.json
    # labels female-only. That is a species property, not an evolution gate:
    # narrowing on it would strand any save whose stored gender disagrees with
    # its own species. Both sexes must still evolve.
    for gender in ("M", "F", None):
        win = _FakeEvoWindow()
        assert pf.check_evolution_for_pokemon("ind", 761, 20, win, gender=gender) == 762


def test_same_gender_siblings_are_not_treated_as_a_split():
    # Tyrogue (236) has three targets, all labelled male-only in pokedex.json.
    # No disagreement -> no narrowing -> the pre-existing choice is preserved.
    without = pf.check_evolution_for_pokemon("ind", 236, 25, _FakeEvoWindow())
    for gender in ("M", "F"):
        assert (
            pf.check_evolution_for_pokemon(
                "ind", 236, 25, _FakeEvoWindow(), gender=gender
            )
            == without
        )


def test_burmy_split_still_resolved_by_the_csv_gate():
    # The CSV gate runs first and already resolves Burmy; the form filter must
    # not disturb it.
    assert (
        pf.check_evolution_for_pokemon("i", 412, 20, _FakeEvoWindow(), gender="M")
        == 414
    )
    assert (
        pf.check_evolution_for_pokemon("i", 412, 20, _FakeEvoWindow(), gender="F")
        == 413
    )


def test_filter_gender_split_forms_semantics():
    # Espurr's pair disagrees -> narrows.
    assert pf.filter_gender_split_forms([678, 10025], "F") == [10025]
    assert pf.filter_gender_split_forms([678, 10025], "M") == [678]
    # Unrecognised / missing gender -> untouched.
    for junk in (None, "Genderless", "N", "", "x"):
        assert pf.filter_gender_split_forms([678, 10025], junk) == [678, 10025]
    # Fewer than two candidates -> untouched, whatever the label says.
    assert pf.filter_gender_split_forms([762], "M") == [762]
    # Siblings that agree -> untouched.
    assert pf.filter_gender_split_forms([106, 107, 237], "F") == [106, 107, 237]
    # Unlabelled siblings survive alongside the matching label.
    assert pf.filter_gender_split_forms([678, 10025, 1], "M") == [678, 1]
    # Never empties the list.
    assert pf.filter_gender_split_forms([678, 10025], "F") != []


def test_pokedex_form_gender_reads_the_bundled_data():
    assert pf._pokedex_form_gender(678) == "M"  # Meowstic
    assert pf._pokedex_form_gender(10025) == "F"  # Meowstic-F
    assert pf._pokedex_form_gender(916) == "M"  # Oinkologne
    assert pf._pokedex_form_gender(10254) == "F"  # Oinkologne-F
    assert pf._pokedex_form_gender(1) is None  # Bulbasaur: no gender field
    for junk in (None, "x", 0, -1, 99999):
        assert pf._pokedex_form_gender(junk) is None


# --------------------------------------------------------------------------- #
# Cache invalidation: the memo built ON TOP of the evolution index.
# --------------------------------------------------------------------------- #
def test_clearing_the_pokedex_caches_also_clears_the_gender_memo():
    """``clear_pokedex_caches`` must invalidate every layer, not just the data.

    ``_evolution_row_gender_id_cached`` answers from ``_poke_evo_index`` (and,
    for form ids >= 10000, from ``_pokedex_cache`` via ``search_pokedex_by_id``).
    Clearing those two while leaving the memo warm keeps serving pre-clear
    verdicts for the rest of the session, so the gate contradicts the very data
    it is supposed to read. ``clear_pokedex_caches`` is the registered
    profile-close hook, so "the rest of the session" spans a profile swap.
    """
    pf._evolution_row_gender_id_cached.cache_clear()
    assert pf._evolution_row_gender_id(416, pf._LEVEL_EVO_TRIGGERS) == 1
    assert pf._evolution_row_gender_id_cached.cache_info().currsize > 0

    pf.clear_pokedex_caches()
    assert pf._evolution_row_gender_id_cached.cache_info().currsize == 0

    # Still correct once rebuilt from the reloaded data.
    assert pf._evolution_row_gender_id(416, pf._LEVEL_EVO_TRIGGERS) == 1
    assert pf._evolution_row_gender_id(475, pf._ITEM_EVO_TRIGGERS) == 2


def test_a_reloaded_csv_changes_the_gender_verdict():
    """The observable consequence of the memo surviving a clear.

    Swap the parsed rows for ones that gate Vespiquen on MALE, clear, and the
    gate must follow the new data. Before the fix the stale ``1`` came straight
    back out of the memo and a female Combee stayed the only one that could
    evolve, whatever the reloaded file said.
    """
    pf.clear_pokedex_caches()
    assert pf._evolution_row_gender_id(416, pf._LEVEL_EVO_TRIGGERS) == 1

    swapped = [
        dict(row, gender_id="2") if row.get("evolved_species_id") == "416" else row
        for row in pf._load_poke_evo_cache()
    ]
    try:
        pf.clear_pokedex_caches()
        pf._poke_evo_cache = swapped
        assert pf._evolution_row_gender_id(416, pf._LEVEL_EVO_TRIGGERS) == 2
    finally:
        pf.clear_pokedex_caches()

    assert pf._evolution_row_gender_id(416, pf._LEVEL_EVO_TRIGGERS) == 1


# --------------------------------------------------------------------------- #
# One gate, one implementation.
# --------------------------------------------------------------------------- #
def test_gender_allows_evolution_semantics():
    # Unknown gender fails open; an ungated species is unrestricted; a gated one
    # matches only its own id, and only within the calling trigger scope.
    assert pf.gender_allows_evolution(416, None, pf._LEVEL_EVO_TRIGGERS) is True
    assert pf.gender_allows_evolution(1, 2, pf._LEVEL_EVO_TRIGGERS) is True
    assert pf.gender_allows_evolution(416, 1, pf._LEVEL_EVO_TRIGGERS) is True
    assert pf.gender_allows_evolution(416, 2, pf._LEVEL_EVO_TRIGGERS) is False
    # Vespiquen's gate lives on a level row, so the item scope must not see it.
    assert pf.gender_allows_evolution(416, 2, pf._ITEM_EVO_TRIGGERS) is True


def test_evolution_target_species_id_prefers_actual_id():
    assert pf.evolution_target_species_id({"actual_id": 10004}) == 10004
    assert pf.evolution_target_species_id({"species_id": 413}) == 413
    assert pf.evolution_target_species_id({"actual_id": 0, "species_id": 413}) == 413
    for junk in (None, {}, [], "nope", {"actual_id": "x"}):
        assert pf.evolution_target_species_id(junk) == 0


def test_evolution_gender_allows_wraps_the_raw_ankimon_gender_string():
    gallade = pf._load_pokedex_cache()["gallade"]
    assert pf.evolution_gender_allows(gallade, "M", pf._ITEM_EVO_TRIGGERS) is True
    assert pf.evolution_gender_allows(gallade, "F", pf._ITEM_EVO_TRIGGERS) is False
    for junk in (None, "Genderless", "", "x"):
        assert pf.evolution_gender_allows(gallade, junk, pf._ITEM_EVO_TRIGGERS) is True


def test_item_and_level_paths_route_through_the_shared_gate(monkeypatch):
    """Both in-module gate sites must call the helper, not a private copy.

    The gate was hand-copied into three places (both paths here and the web
    bag's inline eligibility loop). Copies drift; a test that only checks the
    verdicts agree today cannot see a copy that stops matching tomorrow. Force
    the shared helper to refuse everything and require both call sites to go
    quiet — a surviving copy keeps answering and fails this.
    """
    monkeypatch.setattr(pf, "evolution_gender_allows", lambda *a, **k: False)

    # Item path: Dawn Stone on a male Kirlia is the canonical accept.
    assert pf.check_evolution_by_item(281, _DAWN_STONE_ID, gender="M") is None

    # Level path: a female Combee at 21 is the canonical accept.
    evo_window = mock.MagicMock()
    with mock.patch.object(pf.services, "db", mock.MagicMock()):
        assert (
            pf.check_evolution_for_pokemon("combee-1", 415, 21, evo_window, gender="F")
            is None
        )
    evo_window.ask_pokemon_evo.assert_not_called()
