"""Tests for ``Ankimon.functions.friendship_evolution``.

The friendship/time-of-day evolution module is normally imported as part of the
full Ankimon add-on, which requires Anki (``aqt``) to be importable and the
``singletons.settings_obj`` to be bound at runtime. Neither is available in this
headless CI environment, so this test module reproduces the loading strategy
already used by ``tests/test_encounter_functions.py``:

* ``tests/conftest.py`` registers lightweight stub packages for ``Ankimon`` and
  ``Ankimon.functions`` so that the modules' *relative* imports resolve against
  the real source tree without executing ``Ankimon/__init__.py``.
* Before loading the module under test we replace ``aqt`` / ``aqt.qt`` /
  ``aqt.utils`` and ``Ankimon.pyobj.error_handler`` (a transitive dependency of
  ``pokedex_functions``) with ``MagicMock`` objects in ``sys.modules``.
* We install a *fake* ``Ankimon.singletons`` exposing a mutable ``settings_obj``
  so each test can drive the day/night clock and the friendship toggle.
* ``pokedex_functions`` and ``resources`` are loaded *for real* so the bundled
  ``pokemon_evolution.csv`` / ``pokemon.csv`` lookups exercise the genuine data
  (Eevee -> Espeon/Umbreon, Golbat -> Crobat, ...).

The module is loaded once at import time via
``importlib.util.spec_from_file_location`` and shared by every test. Time of day
is always supplied explicitly via ``now=datetime(...)`` so the tests never depend
on the real wall clock.
"""

import importlib.util
import sys
import unittest.mock as mock
from datetime import datetime
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"


class _FakeSettings:
    """Minimal stand-in for ``settings_obj`` backed by a mutable dict.

    Only ``get(key, default)`` is exercised by the module under test. Tests can
    mutate ``.values`` directly to flip toggles (e.g. the friendship master
    switch) or shift the day/night boundary hours.
    """

    def __init__(self):
        self.values = {
            "evolution.day_start_hour": 6,
            "evolution.night_start_hour": 18,
            "evolution.timezone_auto": True,
            "evolution.timezone_offset": 0.0,
            "evolution.friendship_time_enabled": True,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


def _load_friendship_evolution():
    """Load the module under test against the real data and stubbed deps.

    Returns a tuple of ``(module, fake_settings)``. ``fake_settings`` is the same
    instance referenced by ``module``'s lazy ``settings_obj`` import, so mutating
    it from a test changes the module's view of the settings.
    """
    # Stub Anki + the error handler that pokedex_functions imports at module top.
    sys.modules["aqt"] = mock.MagicMock()
    sys.modules["aqt.qt"] = mock.MagicMock()
    sys.modules["aqt.utils"] = mock.MagicMock()
    sys.modules["Ankimon.pyobj.error_handler"] = mock.MagicMock()

    # Fake singletons exposing a mutable settings_obj.
    fake_settings = _FakeSettings()
    singletons_stub = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("Ankimon.singletons", loader=None)
    )
    singletons_stub.settings_obj = fake_settings
    sys.modules["Ankimon.singletons"] = singletons_stub

    # Load resources + pokedex_functions FOR REAL so the bundled CSV lookups work.
    # We overwrite any entries left by other test modules (e.g.
    # ``test_encounter_functions`` registers these as MagicMocks); pokedex_functions
    # imports the path constants from ``..resources`` at module top, so the real
    # ``resources`` must be installed *before* pokedex_functions is executed.
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

    # Load the module under test; its relative imports resolve via conftest's
    # Ankimon / Ankimon.functions stub packages and the real pokedex_functions.
    fe_spec = importlib.util.spec_from_file_location(
        "Ankimon.functions.friendship_evolution",
        _SRC / "Ankimon" / "functions" / "friendship_evolution.py",
    )
    module = importlib.util.module_from_spec(fe_spec)
    sys.modules["Ankimon.functions.friendship_evolution"] = module
    fe_spec.loader.exec_module(module)
    return module, fake_settings


fe, settings = _load_friendship_evolution()

# The fake singletons module that owns ``settings``. It must be re-asserted into
# ``sys.modules`` before every test (see ``_reset_settings``): the module under
# test imports ``settings_obj`` *lazily* inside each function, and other test
# modules in the suite (e.g. ``test_encounter_functions``) replace
# ``Ankimon.singletons`` with a ``MagicMock`` at import time. Without restoring
# our stub, those lazy imports would resolve to the mock and break the clock.
_SINGLETONS_STUB = sys.modules["Ankimon.singletons"]


@pytest.fixture(autouse=True)
def _reset_settings():
    """Restore our singletons stub and default settings before every test."""
    sys.modules["Ankimon.singletons"] = _SINGLETONS_STUB
    # #492 moved the code from singletons.settings_obj to the services registry, so
    # point services.settings at the same fake (the module reads it lazily per call).
    from Ankimon.services import services
    services.settings = settings
    settings.values.update(
        {
            "evolution.day_start_hour": 6,
            "evolution.night_start_hour": 18,
            "evolution.timezone_auto": True,
            "evolution.timezone_offset": 0.0,
            "evolution.friendship_time_enabled": True,
        }
    )
    yield


# --------------------------------------------------------------------------- #
# get_time_of_day
# --------------------------------------------------------------------------- #
def test_get_time_of_day_day_at_morning():
    assert fe.get_time_of_day(datetime(2024, 1, 1, 9, 0)) == "day"


def test_get_time_of_day_night_at_evening():
    assert fe.get_time_of_day(datetime(2024, 1, 1, 23, 0)) == "night"


def test_get_time_of_day_night_spans_midnight():
    # 02:00 is before the day window starts -> still "night".
    assert fe.get_time_of_day(datetime(2024, 1, 1, 2, 0)) == "night"


def test_get_time_of_day_boundaries():
    # Day window is [day_start, night_start): 06:00 is day, 18:00 is night.
    assert fe.get_time_of_day(datetime(2024, 1, 1, 6, 0)) == "day"
    assert fe.get_time_of_day(datetime(2024, 1, 1, 18, 0)) == "night"


# --------------------------------------------------------------------------- #
# _format_utc_offset
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "offset, expected",
    [
        (0, "UTC+0"),
        (-5, "UTC-5"),
        (1, "UTC+1"),
        (5.5, "UTC+5:30"),
        (5.75, "UTC+5:45"),
    ],
)
def test_format_utc_offset(offset, expected):
    assert fe._format_utc_offset(offset) == expected


# --------------------------------------------------------------------------- #
# get_friendship_evolutions_for_species (real bundled data)
# --------------------------------------------------------------------------- #
def test_eevee_friendship_evolutions():
    evos = fe.get_friendship_evolutions_for_species(133)  # Eevee
    # Entries are FriendshipEvolution NamedTuples (attribute access works).
    assert all(isinstance(e, fe.FriendshipEvolution) for e in evos)

    by_id = {e.evo_id: e for e in evos}
    assert 196 in by_id and 197 in by_id

    espeon = by_id[196]
    assert espeon.evo_name == "Espeon"
    assert espeon.time_of_day == "day"

    umbreon = by_id[197]
    assert umbreon.evo_name == "Umbreon"
    assert umbreon.time_of_day == "night"


def test_golbat_friendship_evolution_no_time():
    evos = fe.get_friendship_evolutions_for_species(42)  # Golbat
    assert all(isinstance(e, fe.FriendshipEvolution) for e in evos)
    by_id = {e.evo_id: e for e in evos}
    assert 169 in by_id  # Crobat
    assert by_id[169].evo_name == "Crobat"
    assert by_id[169].time_of_day is None


def test_species_without_friendship_evolution_is_empty():
    # Bulbasaur evolves by level, not friendship -> no friendship evolutions.
    assert fe.get_friendship_evolutions_for_species(1) == ()


# --------------------------------------------------------------------------- #
# get_level_evolutions_for_species (real bundled data)
# --------------------------------------------------------------------------- #
def test_charmander_level_evolution():
    evos = fe.get_level_evolutions_for_species(4)  # Charmander -> Charmeleon @16
    assert all(isinstance(e, fe.LevelEvolution) for e in evos)
    by_id = {e.evo_id: e for e in evos}
    assert 5 in by_id  # Charmeleon
    assert by_id[5].evo_name == "Charmeleon"
    assert by_id[5].min_level == 16


def test_friendship_evolver_has_no_level_evolution():
    # Eevee's Espeon/Umbreon are friendship evos (positive minimum_happiness),
    # so they must NOT be double-counted as level-up evolutions.
    assert fe.get_level_evolutions_for_species(133) == ()  # Eevee


# --------------------------------------------------------------------------- #
# evolution_readiness (real bundled data)
# --------------------------------------------------------------------------- #
def test_readiness_eevee_ready_to_espeon_by_day():
    pokemon = {"id": 133, "friendship": 160, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["evolvable"] is True
    assert result["ready"] is True
    assert result["evo_name"] == "Espeon"
    assert result["bar_max"] == 160


def test_readiness_eevee_ready_to_umbreon_by_night():
    pokemon = {"id": 133, "friendship": 160, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 23, 0))
    assert result["ready"] is True
    assert result["evo_name"] == "Umbreon"


def test_readiness_eevee_not_enough_friendship():
    pokemon = {"id": 133, "friendship": 100, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is False
    assert result["friendship_remaining"] == 60


def test_readiness_everstone_blocks_and_mentions_everstone():
    pokemon = {"id": 133, "friendship": 160, "everstone": True}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is False
    assert "everstone" in result["status_text"].lower()


def test_readiness_non_evolving_species():
    # Use a species with no evolution at all so the level fallback also misses.
    # Tauros (128) is a single-stage Pokémon.
    pokemon = {"id": 128, "friendship": 250, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["evolvable"] is False
    assert result["method"] is None
    assert result["bar_max"] == fe.MAX_FRIENDSHIP == 400


# --------------------------------------------------------------------------- #
# evolution_readiness — level-up evolutions (real bundled data)
# --------------------------------------------------------------------------- #
def test_readiness_level_evolver_ready_at_level():
    # Charmander caught/raised above its evolve level (e.g. rejected earlier).
    pokemon = {"id": 4, "level": 20, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["evolvable"] is True
    assert result["ready"] is True
    assert result["method"] == "level"
    assert result["evo_name"] == "Charmeleon"
    assert result["evo_id"] == 5
    assert result["required_time"] is None  # no wait badge for level evos
    assert result["status_text"] == "Ready to evolve into Charmeleon!"


def test_readiness_level_evolver_not_ready_below_level():
    pokemon = {"id": 4, "level": 5, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["evolvable"] is True
    assert result["ready"] is False
    assert result["method"] == "level"
    assert "Lv16" in result["status_text"]


def test_readiness_level_evolver_rejected_still_ready():
    # evolution_rejected must NOT block readiness (manual button still shows).
    pokemon = {"id": 4, "level": 20, "everstone": False, "evolution_rejected": True}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is True
    assert result["method"] == "level"
    assert result["status_text"] == "Evolution rejected — tap Evolve now to override"


def test_readiness_level_evolver_everstone_blocks():
    pokemon = {"id": 4, "level": 20, "everstone": True}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is False
    assert result["method"] == "level"
    assert "everstone" in result["status_text"].lower()


def test_level_ready_pokemon_not_auto_prompted_by_friendship_checker():
    # The friendship auto-prompt must only fire for method == "friendship".
    evo_window = _FakeEvoWindow()
    result = fe.check_friendship_evolution_for_pokemon(
        individual_id=3,
        pokemon_id=4,  # Charmander — a level evolver
        evo_window=evo_window,
        everstone=False,
        friendship=0,
        now=datetime(2024, 1, 1, 9, 0),
    )
    assert result is None
    assert evo_window.calls == []


# --------------------------------------------------------------------------- #
# check_friendship_evolution_for_pokemon
# --------------------------------------------------------------------------- #
class _FakeEvoWindow:
    """Records ``ask_pokemon_evo`` invocations for assertions."""

    def __init__(self):
        self.calls = []

    def ask_pokemon_evo(self, individual_id, pokemon_id, evo_id):
        self.calls.append((individual_id, pokemon_id, evo_id))


def test_check_triggers_evolution_when_ready():
    evo_window = _FakeEvoWindow()
    result = fe.check_friendship_evolution_for_pokemon(
        individual_id=7,
        pokemon_id=133,  # Eevee
        evo_window=evo_window,
        everstone=False,
        friendship=160,
        now=datetime(2024, 1, 1, 9, 0),  # day -> Espeon
    )
    assert result == 196
    assert evo_window.calls == [(7, 133, 196)]


def test_check_returns_none_when_disabled():
    settings.values["evolution.friendship_time_enabled"] = False
    evo_window = _FakeEvoWindow()
    result = fe.check_friendship_evolution_for_pokemon(
        individual_id=7,
        pokemon_id=133,
        evo_window=evo_window,
        everstone=False,
        friendship=160,
        now=datetime(2024, 1, 1, 9, 0),
    )
    assert result is None
    assert evo_window.calls == []


def test_check_returns_none_with_everstone():
    evo_window = _FakeEvoWindow()
    result = fe.check_friendship_evolution_for_pokemon(
        individual_id=7,
        pokemon_id=133,
        evo_window=evo_window,
        everstone=True,
        friendship=160,
        now=datetime(2024, 1, 1, 9, 0),
    )
    assert result is None
    assert evo_window.calls == []


# --------------------------------------------------------------------------- #
# get_time_of_day — defensive boundary handling (corrupt / hand-edited config)
# --------------------------------------------------------------------------- #
def test_get_time_of_day_string_hour_bounds_do_not_crash():
    # day/night bounds are advanced config that isn't surfaced in the UI, so a
    # hand-edited config can store them as strings. Comparing str < int used to
    # raise TypeError on the hot PC-render path; they must now be coerced.
    settings.values["evolution.day_start_hour"] = "6"
    settings.values["evolution.night_start_hour"] = "18"
    assert fe.get_time_of_day(datetime(2024, 1, 1, 9, 0)) == "day"
    assert fe.get_time_of_day(datetime(2024, 1, 1, 23, 0)) == "night"


def test_get_time_of_day_none_hour_bound_falls_back_to_default():
    settings.values["evolution.day_start_hour"] = None  # cleared/corrupt value
    # Falls back to the default day_start (6), so 09:00 is still day.
    assert fe.get_time_of_day(datetime(2024, 1, 1, 9, 0)) == "day"


def test_get_time_of_day_junk_hour_bound_falls_back_to_default():
    settings.values["evolution.night_start_hour"] = "evening"
    # Junk night_start falls back to the default (18): 12:00 day, 20:00 night.
    assert fe.get_time_of_day(datetime(2024, 1, 1, 12, 0)) == "day"
    assert fe.get_time_of_day(datetime(2024, 1, 1, 20, 0)) == "night"


def test_get_time_of_day_misconfigured_day_after_night_is_always_night():
    # day_start >= night_start is a degenerate (empty) day window; it must yield
    # "night" everywhere rather than raising.
    settings.values["evolution.day_start_hour"] = 18
    settings.values["evolution.night_start_hour"] = 6
    assert fe.get_time_of_day(datetime(2024, 1, 1, 12, 0)) == "night"
    assert fe.get_time_of_day(datetime(2024, 1, 1, 0, 0)) == "night"


def test_coerce_hour_clamps_out_of_range():
    assert fe._coerce_hour(30, 6) == 23
    assert fe._coerce_hour(-3, 6) == 0
    assert fe._coerce_hour("9", 6) == 9
    assert fe._coerce_hour(None, 6) == 6
    assert fe._coerce_hour("nonsense", 18) == 18


# --------------------------------------------------------------------------- #
# evolution_readiness — defensive coercion of stats from the DB
# --------------------------------------------------------------------------- #
def test_readiness_friendship_as_string_does_not_crash():
    # json_extract can hand back a JSON string ("160") for friendship; the
    # arithmetic against the threshold must not raise.
    pokemon = {"id": 133, "friendship": "160", "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is True
    assert result["friendship_remaining"] == 0
    assert result["current_friendship"] == 160


def test_readiness_level_as_string_does_not_crash():
    pokemon = {"id": 4, "level": "20", "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is True
    assert result["method"] == "level"
    assert result["status_text"] == "Ready to evolve into Charmeleon!"


def test_readiness_friendship_as_float_is_supported():
    pokemon = {"id": 133, "friendship": 160.0, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is True
    assert result["current_friendship"] == 160


def test_readiness_friendship_none_defaults_to_zero():
    pokemon = {"id": 133, "friendship": None, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["current_friendship"] == 0
    assert result["friendship_remaining"] == 160
    assert result["ready"] is False


def test_readiness_junk_friendship_defaults_to_zero():
    # A non-numeric friendship value degrades to 0 rather than raising.
    pokemon = {"id": 133, "friendship": "lots", "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["current_friendship"] == 0
    assert result["ready"] is False


def test_readiness_missing_friendship_and_level_keys():
    # A stub DB dict that predates the friendship/level keys must still work via
    # the documented defaults (friendship=0, level=1).
    result = fe.evolution_readiness({"id": 4}, now=datetime(2024, 1, 1, 9, 0))
    assert result["method"] == "level"  # Charmander still a level evolver
    assert result["ready"] is False  # level defaults to 1
    assert result["current_friendship"] == 0


def test_readiness_bad_pid_is_not_evolvable():
    # A malformed non-numeric id can't match the integer CSV ids; treat it like
    # a missing id (not evolvable) instead of raising on int("abc").
    result = fe.evolution_readiness({"id": "abc", "friendship": 200})
    assert result["evolvable"] is False
    assert result["method"] is None


def test_readiness_object_with_string_attrs_does_not_crash():
    class _Mon:
        id = 133
        friendship = "160"
        everstone = False
        level = "1"

    result = fe.evolution_readiness(_Mon(), now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is True
    assert result["current_friendship"] == 160


# --------------------------------------------------------------------------- #
# _select_evolution fallback — single time-gated friendship evolver at off-time
# --------------------------------------------------------------------------- #
def test_readiness_riolu_waiting_for_day_at_night():
    # Riolu (447) -> Lucario (448) is a day-only friendship evolution. At night
    # with friendship met, readiness must surface the time-of-day wait (this is
    # the _select_evolution branch Eevee never hits, since Eevee always has a
    # blank-time Sylveon row eligible at any hour).
    pokemon = {"id": 447, "friendship": 250, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 23, 0))
    assert result["evolvable"] is True
    assert result["ready"] is False
    assert result["required_time"] == "day"
    assert result["time_ok"] is False
    assert result["friendship_remaining"] == 0
    assert "waiting for Day" in result["status_text"]


def test_readiness_riolu_ready_to_lucario_by_day():
    pokemon = {"id": 447, "friendship": 250, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is True
    assert result["evo_name"] == "Lucario"


def test_readiness_snom_waiting_for_night_at_day():
    # Snom (872) -> Frosmoth (873) is night-only; mirror of the Riolu case.
    pokemon = {"id": 872, "friendship": 250, "everstone": False}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["ready"] is False
    assert result["required_time"] == "night"
    assert "waiting for Night" in result["status_text"]


# --------------------------------------------------------------------------- #
# Eevee — three friendship evolutions (day / night / blank-time Sylveon)
# --------------------------------------------------------------------------- #
def test_eevee_has_three_friendship_evolutions_including_sylveon():
    evos = fe.get_friendship_evolutions_for_species(133)
    by_id = {e.evo_id: e for e in evos}
    # Espeon (day), Umbreon (night), Sylveon (no time requirement).
    assert by_id[196].time_of_day == "day"
    assert by_id[197].time_of_day == "night"
    assert 700 in by_id and by_id[700].evo_name == "Sylveon"
    assert by_id[700].time_of_day is None


def test_eevee_prefers_time_gated_evo_over_blank_sylveon():
    # With Espeon(day), Umbreon(night) and Sylveon(blank) all eligible-ish, the
    # time-gated match wins over the blank-time Sylveon at the matching hour.
    day = fe.evolution_readiness(
        {"id": 133, "friendship": 200, "everstone": False},
        now=datetime(2024, 1, 1, 9, 0),
    )
    assert day["evo_name"] == "Espeon"
    night = fe.evolution_readiness(
        {"id": 133, "friendship": 200, "everstone": False},
        now=datetime(2024, 1, 1, 23, 0),
    )
    assert night["evo_name"] == "Umbreon"


# --------------------------------------------------------------------------- #
# Multi-row CSV handling — an evolved species can span several method rows.
# Regression: a first-match read dropped friendship rows that weren't listed
# first (Sylveon's blank row precedes its friendship row; Persian's level row
# precedes its friendship row).
# --------------------------------------------------------------------------- #
def test_sylveon_friendship_evolution_is_found_despite_leading_blank_row():
    # Sylveon (700) has a blank row *before* its minimum_happiness row in the
    # CSV; a first-match read would skip it entirely. Sylveon has no level-up
    # row at all, so without this it would be unreachable from the manual UI.
    evos = fe.get_friendship_evolutions_for_species(133)
    assert 700 in {e.evo_id for e in evos}


def test_meowth_dual_route_stays_level_only():
    # Persian (53) is reachable from Meowth (52) by level-28 *and* by a friendship
    # row in the CSV (the data conflates Kantonian + Alolan Meowth onto one id).
    # To avoid silently changing a classic level-up evolution, a dual-route
    # species is treated as level-only: it must NOT appear as a friendship
    # evolution, and must still appear as a level evolution.
    fr = {e.evo_id: e for e in fe.get_friendship_evolutions_for_species(52)}
    lv = {e.evo_id: e for e in fe.get_level_evolutions_for_species(52)}
    assert 53 not in fr
    assert lv[53].min_level == 28


def test_meowth_readiness_uses_level_route():
    # Because Meowth -> Persian is also a level-up evolution, evolution_readiness
    # reports the level method (not friendship) even with high friendship.
    result = fe.evolution_readiness(
        {"id": 52, "friendship": 200, "level": 30, "everstone": False},
        now=datetime(2024, 1, 1, 9, 0),
    )
    assert result["method"] == "level"
    assert result["evo_name"] == "Persian"
    assert result["ready"] is True


def test_rows_for_key_in_table_returns_all_matching_rows():
    # The underlying helper must return *every* row for an evolved species, not
    # just the first (this is what fixes the Sylveon/Persian first-match bug).
    # Use the real function/path captured by the module under test at load time
    # (`fe.rows_for_key_in_table` / `fe.poke_evo_path`) rather than re-importing
    # from the package, whose sys.modules entries other test modules replace with
    # mocks when the whole suite runs.
    rows = fe.rows_for_key_in_table("evolved_species_id", 700, fe.poke_evo_path)
    assert len(rows) >= 2
    # Exactly one of Sylveon's rows carries the friendship requirement.
    happiness = [r.get("minimum_happiness") for r in rows]
    assert "160" in happiness


# --------------------------------------------------------------------------- #
# lru_cache — cached tuples are shared and the entries are immutable
# --------------------------------------------------------------------------- #
def test_friendship_cache_returns_same_immutable_object():
    first = fe.get_friendship_evolutions_for_species(133)
    second = fe.get_friendship_evolutions_for_species(133)
    # Same object identity -> cached, no per-call CSV re-read.
    assert first is second
    # Entries are immutable NamedTuples: attempting to mutate raises.
    with pytest.raises(AttributeError):
        first[0].min_happiness = 1


def test_level_cache_returns_same_immutable_object():
    first = fe.get_level_evolutions_for_species(4)
    second = fe.get_level_evolutions_for_species(4)
    assert first is second
    with pytest.raises(AttributeError):
        first[0].min_level = 1


# --------------------------------------------------------------------------- #
# current_time_label — manual time zone offset rendering
# --------------------------------------------------------------------------- #
def test_current_time_label_includes_offset_when_manual_tz():
    settings.values["evolution.timezone_auto"] = False
    settings.values["evolution.timezone_offset"] = -5
    label = fe.current_time_label(datetime(2024, 1, 1, 9, 0))
    assert "Day" in label
    assert "UTC-5" in label


def test_current_time_label_bad_offset_does_not_crash():
    settings.values["evolution.timezone_auto"] = False
    settings.values["evolution.timezone_offset"] = "not-a-number"
    # Bad offset must not raise; the offset suffix is simply omitted.
    label = fe.current_time_label(datetime(2024, 1, 1, 9, 0))
    assert "Day" in label
    assert "UTC" not in label
