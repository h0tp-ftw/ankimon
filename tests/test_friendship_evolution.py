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
# The module under test now resolves ``pokedex_functions`` lazily (for the
# pokedex.json form-aware level/minimumDefeated lookups), so — like the
# singletons stub — the real pokedex_functions we loaded above must be
# re-asserted before every test; other suite modules replace this sys.modules
# entry with a MagicMock, which would otherwise break the pokedex.json lookups.
_POKEDEX_FUNCTIONS_STUB = sys.modules["Ankimon.functions.pokedex_functions"]


@pytest.fixture(autouse=True)
def _reset_settings():
    """Restore our singletons stub and default settings before every test."""
    sys.modules["Ankimon.singletons"] = _SINGLETONS_STUB
    sys.modules["Ankimon.functions.pokedex_functions"] = _POKEDEX_FUNCTIONS_STUB
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
    # Use the real helper captured by the module under test at load time
    # (`fe.evolution_rows_for_evolved_species`) rather than re-importing from
    # the package, whose sys.modules entries other test modules replace with
    # mocks when the whole suite runs.
    rows = fe.evolution_rows_for_evolved_species(700)
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


# --------------------------------------------------------------------------- #
# Friendship evolution via the real pokedex.json / CSV data (added forms)
# --------------------------------------------------------------------------- #
def test_pichu_evolution_readiness():
    # Pichu (172) -> Pikachu (25) is a pure-friendship evolution in the CSV
    # (minimum_happiness, no minimum_level); at high friendship it is ready.
    pokemon = {"id": 172, "friendship": 400, "everstone": False, "level": 5}
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["evolvable"] is True
    assert result["ready"] is True
    assert result["evo_name"] == "Pikachu"


# --------------------------------------------------------------------------- #
# minimumDefeated level evolutions (Pawmo -> Pawmot) via pokedex.json
# --------------------------------------------------------------------------- #
def test_pawmo_minimum_defeated_readiness():
    # Pawmo (922) -> Pawmot (923) requires 100 defeated enemies (pokedex.json
    # evoCondition=minimumDefeated / evoDefeated=100). Below 100: not ready with
    # a "needs to defeat" hint; at/above 100: ready.
    pokemon_not_ready = {
        "id": 922,
        "level": 25,
        "friendship": 50,
        "pokemon_defeated": 50,
        "everstone": False,
    }
    res = fe.evolution_readiness(pokemon_not_ready)
    assert res["evolvable"] is True
    assert res["method"] == "level"
    assert res["ready"] is False
    assert "Needs to defeat 100 enemies to evolve" in res["status_text"]

    pokemon_ready = {
        "id": 922,
        "level": 25,
        "friendship": 50,
        "pokemon_defeated": 100,
        "everstone": False,
    }
    res_ready = fe.evolution_readiness(pokemon_ready)
    assert res_ready["evolvable"] is True
    assert res_ready["ready"] is True
    assert res_ready["evo_name"] == "Pawmot"
    assert "Ready to evolve into Pawmot!" in res_ready["status_text"]


def test_check_friendship_evolution_auto_prompts_pawmo():
    # The victory-time checker queries the DB for the defeat count (via the
    # services.db seam); when it reaches the minimumDefeated milestone it
    # auto-prompts the evolution just like a friendship one.
    from Ankimon.services import services

    fake_db = mock.MagicMock()
    fake_db.get_pokemon = mock.MagicMock(return_value={"pokemon_defeated": 100})
    services.db = fake_db
    try:
        evo_window = _FakeEvoWindow()
        res = fe.check_friendship_evolution_for_pokemon(
            individual_id="some_id",
            pokemon_id=922,
            evo_window=evo_window,
            friendship=50,
            evolution_rejected=False,
        )
        assert res == 923  # Pawmot
        assert evo_window.calls == [("some_id", 922, 923)]
    finally:
        services.db = None


# --------------------------------------------------------------------------- #
# Move-type-gated friendship evolutions (Sylveon's Fairy-move requirement).
#
# The bundled pokemon_evolution.csv carries known_move_type_id=18 ("fairy") on
# both of Sylveon's rows; _select_evolution must honor that gate: Sylveon only
# when the Pokémon knows a Fairy move (and then it outranks Espeon/Umbreon),
# never on missing/unconfirmed movesets.
# --------------------------------------------------------------------------- #
def test_sylveon_row_carries_fairy_move_gate_from_csv():
    by_id = {e.evo_id: e for e in fe.get_friendship_evolutions_for_species(133)}
    assert by_id[700].known_move_type == "fairy"
    assert by_id[196].known_move_type is None
    assert by_id[197].known_move_type is None


def test_sylveon_needs_fairy_move_day_stays_espeon():
    # No Fairy move -> Sylveon is gated out; day still offers Espeon.
    pokemon = {
        "id": 133,
        "friendship": 200,
        "everstone": False,
        "attacks": ["tackle", "growl", "tailwhip"],
    }
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["evo_name"] == "Espeon"
    assert result["ready"] is True


def test_sylveon_with_fairy_move_wins_over_espeon():
    # A Fairy move forces Sylveon in-game, so it outranks the time-gated rows.
    pokemon = {
        "id": 133,
        "friendship": 200,
        "everstone": False,
        "attacks": ["moonblast", "tackle"],
    }
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
    assert result["evo_name"] == "Sylveon"
    assert result["evo_id"] == 700
    assert result["ready"] is True


def test_sylveon_with_fairy_move_also_ready_at_night():
    pokemon = {
        "id": 133,
        "friendship": 200,
        "everstone": False,
        "attacks": ["moonblast"],
    }
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 23, 0))
    assert result["evo_name"] == "Sylveon"
    assert result["ready"] is True


def test_no_fairy_move_night_stays_umbreon():
    pokemon = {
        "id": 133,
        "friendship": 200,
        "everstone": False,
        "attacks": ["bite", "tackle"],
    }
    result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 23, 0))
    assert result["evo_name"] == "Umbreon"


def test_missing_attacks_key_never_offers_sylveon():
    # Unknown moveset (no "attacks" key at all) must fail closed: Sylveon is
    # not offered even though its blank-time row would otherwise be eligible.
    for now, expected in (
        (datetime(2024, 1, 1, 9, 0), "Espeon"),
        (datetime(2024, 1, 1, 23, 0), "Umbreon"),
    ):
        result = fe.evolution_readiness(
            {"id": 133, "friendship": 200, "everstone": False}, now=now
        )
        assert result["evo_name"] == expected


def test_auto_prompt_uses_stored_moveset_for_move_gates():
    # The victory-time checker reads the stored attacks through the DB seam;
    # with a Fairy move stored, Sylveon is prompted instead of Espeon.
    from Ankimon.services import services

    fake_db = mock.MagicMock()
    fake_db.get_pokemon = mock.MagicMock(
        return_value={"pokemon_defeated": 0, "attacks": ["Moonblast"]}
    )
    services.db = fake_db
    try:
        evo_window = _FakeEvoWindow()
        res = fe.check_friendship_evolution_for_pokemon(
            individual_id=7,
            pokemon_id=133,
            evo_window=evo_window,
            everstone=False,
            friendship=200,
            now=datetime(2024, 1, 1, 9, 0),
        )
        assert res == 700
        assert evo_window.calls == [(7, 133, 700)]
    finally:
        services.db = None


def test_auto_prompt_without_fairy_move_prompts_time_gated_evo():
    from Ankimon.services import services

    fake_db = mock.MagicMock()
    fake_db.get_pokemon = mock.MagicMock(
        return_value={"pokemon_defeated": 0, "attacks": ["Tackle"]}
    )
    services.db = fake_db
    try:
        evo_window = _FakeEvoWindow()
        res = fe.check_friendship_evolution_for_pokemon(
            individual_id=7,
            pokemon_id=133,
            evo_window=evo_window,
            everstone=False,
            friendship=200,
            now=datetime(2024, 1, 1, 9, 0),
        )
        assert res == 196
        assert evo_window.calls == [(7, 133, 196)]
    finally:
        services.db = None


def test_auto_prompt_caller_attacks_override_stored_moveset():
    # A caller-supplied live moveset wins over the stale DB row.
    from Ankimon.services import services

    fake_db = mock.MagicMock()
    fake_db.get_pokemon = mock.MagicMock(
        return_value={"pokemon_defeated": 0, "attacks": ["Tackle"]}
    )
    services.db = fake_db
    try:
        evo_window = _FakeEvoWindow()
        res = fe.check_friendship_evolution_for_pokemon(
            individual_id=7,
            pokemon_id=133,
            evo_window=evo_window,
            everstone=False,
            friendship=200,
            now=datetime(2024, 1, 1, 9, 0),
            attacks=["Moonblast"],  # just learned this battle/level
        )
        assert res == 700
        assert evo_window.calls == [(7, 133, 700)]
    finally:
        services.db = None


def test_move_type_name_resolves_known_ids_and_degrades_on_junk():
    assert fe._move_type_name(18) == "fairy"
    assert fe._move_type_name("18") == "fairy"  # CSV cells arrive as strings
    assert fe._move_type_name(None) is None
    assert fe._move_type_name("junk") is None
    assert fe._move_type_name(9999) is None


# --------------------------------------------------------------------------- #
# Victory-path I/O discipline + quiet move-type resolution.
#
# Coding guideline: "No synchronous disk I/O in the review path." The
# victory-time checker must therefore run entirely on caller-supplied state
# (pokemon_defeated / attacks) — no services.db.get_pokemon call at all — and
# its move-type lookups must never route through find_details_move, whose
# unknown-move fallback returns "tackle" data and pops a warning dialog via
# services.ui.warn (a modal showWarning under QtPresenter) mid-review.
# --------------------------------------------------------------------------- #
def test_auto_prompt_with_full_caller_state_never_touches_db():
    from Ankimon.services import services

    fake_db = mock.MagicMock()
    fake_db.get_pokemon = mock.MagicMock(
        side_effect=AssertionError("victory path performed a DB read")
    )
    services.db = fake_db
    try:
        evo_window = _FakeEvoWindow()
        res = fe.check_friendship_evolution_for_pokemon(
            individual_id="some_id",
            pokemon_id=922,
            evo_window=evo_window,
            everstone=False,
            friendship=50,
            evolution_rejected=False,
            attacks=["Tackle"],
            pokemon_defeated=100,  # Pawmot milestone reached this battle
        )
        assert res == 923
        assert evo_window.calls == [("some_id", 922, 923)]
        fake_db.get_pokemon.assert_not_called()
    finally:
        services.db = None


def test_auto_prompt_no_level_up_victory_does_not_raise():
    # Regression for the unbound-`attacks` crash class: a friendship-ready
    # Pokémon winning a battle that grants NO level-up reaches the checker with
    # attacks=None; it must fall back to the stored moveset instead of raising
    # UnboundLocalError (which used to abort the final save).
    from Ankimon.services import services

    fake_db = mock.MagicMock()
    fake_db.get_pokemon = mock.MagicMock(
        return_value={"pokemon_defeated": 0, "attacks": ["Moonblast"]}
    )
    services.db = fake_db
    try:
        evo_window = _FakeEvoWindow()
        res = fe.check_friendship_evolution_for_pokemon(
            individual_id=7,
            pokemon_id=133,
            evo_window=evo_window,
            everstone=False,
            friendship=200,
            now=datetime(2024, 1, 1, 9, 0),
            # attacks omitted entirely: zero level-ups => caller has no fresh list
        )
        assert res == 700
        assert evo_window.calls == [(7, 133, 700)]
    finally:
        services.db = None


def test_move_type_of_is_quiet_and_accurate():
    # Known move resolves to its real type from the moves.json cache...
    assert fe._move_type_of("Moonblast") == "fairy"
    assert fe._move_type_of("moonblast") == "fairy"
    assert fe._move_type_of("Leaf Blade") == "grass"

    # ...unknown/junk names resolve to None WITHOUT touching services.ui
    # (no tackle fallback, no warning popup)...
    class _BoomUI:
        def warn(self, message):
            raise AssertionError(f"ui.warn called with {message!r}")

    from Ankimon.services import services

    real_ui = services.ui
    services.ui = _BoomUI()
    try:
        assert fe._move_type_of("Not A Real Move") is None
        assert fe._move_type_of("") is None
        assert fe._move_type_of(None) is None
        assert fe._move_type_of(12345) is None
        # ...and an unknown move contributes NO type evidence, so Sylveon's
        # gate stays unmet instead of being satisfied by phantom "tackle".
        types = fe._known_move_types({"attacks": ["Not A Real Move"]})
        assert "tackle" not in types and types == frozenset()
    finally:
        services.ui = real_ui


# --------------------------------------------------------------------------- #
# Move-type gates must be VISIBLE, not just enforced.
#
# Filtering Sylveon out of the candidate list makes it correct but invisible: a
# high-friendship Eevee just shows Espeon/Umbreon and nothing ever tells the
# player the third branch exists or what it wants. evolution_readiness now
# reports the gate alongside the choice.
# --------------------------------------------------------------------------- #
def _eevee(friendship, attacks):
    return {
        "id": 133,
        "friendship": friendship,
        "everstone": False,
        "pokemon_defeated": 0,
        "attacks": attacks,
    }


def test_readiness_reports_the_hidden_sylveon_gate():
    # The structured keys are the contract; status_text is one rendering of them.
    # (Note the details panel only draws status_text while ready is False — see
    # test_readiness_hint_also_shows_while_still_gaining_friendship for the
    # variant a user actually reads.)
    result = fe.evolution_readiness(
        _eevee(220, ["Tackle", "Swift"]), now=datetime(2024, 1, 1, 9, 0)
    )
    assert result["evo_name"] == "Espeon"
    assert result["ready"] is True
    # The chosen evolution has no unmet gate of its own...
    assert result["required_move_type"] is None
    # ...but the branch the moveset is hiding is named, with its requirement.
    assert result["gated_alternatives"] == (("Sylveon", "fairy"),)
    assert "Sylveon needs a Fairy-type move" in result["status_text"]


def test_readiness_hint_also_shows_while_still_gaining_friendship():
    # This is the branch the details panel actually renders (it draws
    # status_text only when ready is False), and it covers the whole 0..159
    # friendship climb — i.e. the entire window in which the player can still
    # choose to teach a Fairy move.
    result = fe.evolution_readiness(
        _eevee(100, ["Tackle"]), now=datetime(2024, 1, 1, 9, 0)
    )
    assert result["ready"] is False
    assert "60 friendship to evolve into Espeon" in result["status_text"]
    assert "Sylveon needs a Fairy-type move" in result["status_text"]


def test_readiness_hint_disappears_once_the_gate_is_met():
    result = fe.evolution_readiness(
        _eevee(220, ["Baby-Doll Eyes"]), now=datetime(2024, 1, 1, 9, 0)
    )
    assert result["evo_name"] == "Sylveon"
    assert result["gated_alternatives"] == ()
    assert result["required_move_type"] is None
    assert result["status_text"] == "Ready to evolve into Sylveon!"


def test_readiness_keys_exist_on_every_path():
    # Consumers (PC box overlays, the details panel) read the dict positionally
    # by key; every return path must carry the same shape.
    for pokemon in (
        _eevee(220, ["Tackle"]),  # friendship path
        {"id": 1, "level": 20, "friendship": 0, "everstone": False},  # level path
        {"id": 999999, "level": 5},  # not evolvable
        {"id": None},  # missing id
    ):
        result = fe.evolution_readiness(pokemon, now=datetime(2024, 1, 1, 9, 0))
        assert "required_move_type" in result
        assert "gated_alternatives" in result


# --------------------------------------------------------------------------- #
# The all-rows-gated fallback in _select_evolution.
#
# No bundled species reaches it (Eevee, the only species with gated rows, also
# has ungated ones), so these drive it directly. The property that matters is
# fail-closed: the branch hands back a representative purely so the UI has
# something to name, and that representative must never be reported as ready.
# The first two tests pin its identity so selection stays predictable.
# --------------------------------------------------------------------------- #
def _gated(evo_id, name, move_type, time_of_day=None):
    return fe.FriendshipEvolution(
        evo_id=evo_id,
        evo_name=name,
        min_happiness=160,
        time_of_day=time_of_day,
        known_move_type=move_type,
    )


def test_all_gated_fallback_returns_the_lowest_id_representative():
    evos = (_gated(700, "Sylveon", "fairy"), _gated(701, "Ghosteon", "ghost"))
    chosen = fe._select_evolution(evos, "day", frozenset())
    assert chosen.evo_name == "Sylveon"  # lowest evo_id; nothing here is reachable


def test_satisfied_gate_still_wins_when_one_is_met():
    evos = (_gated(700, "Sylveon", "fairy"), _gated(701, "Ghosteon", "ghost"))
    chosen = fe._select_evolution(evos, "day", frozenset({"ghost"}))
    assert chosen.evo_name == "Ghosteon"


def test_all_gated_fallback_is_never_reported_ready(monkeypatch):
    # Friendship and time both satisfied — only the move gate is unmet. Without
    # folding move_ok into `ready` this path would fail OPEN and offer a
    # gate-locked evolution.
    monkeypatch.setattr(
        fe,
        "get_friendship_evolutions_for_species",
        lambda species_id: (_gated(700, "Sylveon", "fairy"),),
    )
    result = fe.evolution_readiness(
        _eevee(400, ["Tackle"]), now=datetime(2024, 1, 1, 9, 0)
    )
    assert result["evolvable"] is True
    assert result["ready"] is False, "gate-locked evolution reported as ready"
    assert result["required_move_type"] == "fairy"
    assert result["status_text"] == ("Needs a Fairy-type move to evolve into Sylveon")


def test_all_gated_fallback_becomes_ready_once_the_move_is_known(monkeypatch):
    monkeypatch.setattr(
        fe,
        "get_friendship_evolutions_for_species",
        lambda species_id: (_gated(700, "Sylveon", "fairy"),),
    )
    result = fe.evolution_readiness(
        _eevee(400, ["Moonblast"]), now=datetime(2024, 1, 1, 9, 0)
    )
    assert result["ready"] is True
    assert result["required_move_type"] is None
    assert result["status_text"] == "Ready to evolve into Sylveon!"


# --------------------------------------------------------------------------- #
# CSV gender_id gates on the MANUAL level path (_level_readiness)
#
# check_evolution_for_pokemon gained the gate on the automatic level-up path,
# but evolution_readiness feeds the PC box's "Evolve now" button, its ✨ badge
# and the details-panel status line. Without the same gate there, the manual
# path is a way around it: a male Combee could still be evolved into Vespiquen,
# and a male Burmy was offered Wormadam (lowest evo_id) rather than Mothim.
# Real bundled data throughout — Burmy 412 -> Wormadam 413 (F) / Mothim 414 (M),
# Combee 415 -> Vespiquen 416 (F), Salandit 757 -> Salazzle 758 (F).
# --------------------------------------------------------------------------- #
_NOON = datetime(2024, 1, 1, 12, 0)


def _lvl(species_id, level, gender=None):
    pkmn = {"id": species_id, "level": level, "attacks": []}
    if gender is not None:
        pkmn["gender"] = gender
    return pkmn


def test_male_burmy_is_offered_mothim_not_wormadam():
    result = fe.evolution_readiness(_lvl(412, 25, "M"), now=_NOON)
    assert result["evo_id"] == 414
    assert result["ready"] is True
    assert result["status_text"] == "Ready to evolve into Mothim!"


def test_female_burmy_is_offered_wormadam():
    result = fe.evolution_readiness(_lvl(412, 25, "F"), now=_NOON)
    assert result["evo_id"] == 413
    assert result["ready"] is True


def test_burmy_without_gender_keeps_historical_behavior():
    # No gender on the record -> no gate (fail open, matching the item and
    # automatic level paths), so the lowest-id target is still chosen.
    result = fe.evolution_readiness(_lvl(412, 25), now=_NOON)
    assert result["evo_id"] == 413
    assert result["ready"] is True


def test_male_combee_is_never_ready_for_vespiquen():
    result = fe.evolution_readiness(_lvl(415, 25, "M"), now=_NOON)
    assert result["evolvable"] is True
    assert result["ready"] is False, "manual path bypassed the gender gate"
    assert result["status_text"] == "Needs to be Female to evolve into Vespiquen"


def test_female_combee_is_ready_for_vespiquen():
    result = fe.evolution_readiness(_lvl(415, 25, "F"), now=_NOON)
    assert result["evo_id"] == 416
    assert result["ready"] is True


def test_male_salandit_is_never_ready_for_salazzle():
    result = fe.evolution_readiness(_lvl(757, 40, "M"), now=_NOON)
    assert result["ready"] is False
    assert result["status_text"] == "Needs to be Female to evolve into Salazzle"


def test_gender_gate_outranks_the_level_line():
    # Below the evolve level AND the wrong gender: the immutable requirement is
    # the one worth showing, since no amount of levelling unblocks it.
    result = fe.evolution_readiness(_lvl(415, 5, "M"), now=_NOON)
    assert result["ready"] is False
    assert result["status_text"] == "Needs to be Female to evolve into Vespiquen"


def test_ungendered_species_unaffected_by_the_gate():
    # A plain level evolution must behave identically for either gender.
    baseline = fe.evolution_readiness(_lvl(1, 20), now=_NOON)
    for gender in ("M", "F", "N", "Genderless", ""):
        result = fe.evolution_readiness(_lvl(1, 20, gender), now=_NOON)
        assert result["evo_id"] == baseline["evo_id"]
        assert result["ready"] == baseline["ready"]
        assert result["status_text"] == baseline["status_text"]


def test_level_gender_gate_helper_fails_open_without_a_gender():
    # Vespiquen (416) is female-gated in the CSV.
    assert fe._level_gender_gate(416, None) is True
    assert fe._level_gender_gate(416, 1) is True
    assert fe._level_gender_gate(416, 2) is False
    # Ivysaur (2) carries no gate at all.
    assert fe._level_gender_gate(2, 2) is True


def test_pokemon_gender_reads_dicts_and_objects():
    assert fe._pokemon_gender({"gender": "F"}) == "F"
    assert fe._pokemon_gender({}) is None
    assert fe._pokemon_gender(None) is None

    class _Obj:
        gender = "M"

    assert fe._pokemon_gender(_Obj()) == "M"


# --------------------------------------------------------------------------- #
# The settings seam may be unbound (early boot, profile swap in flight, a
# partially-wired headless run). Every read in this module is a preference with
# a default, and both entry points sit on paths that otherwise degrade rather
# than raise — so `services.settings is None` must not turn into AttributeError.
# `pokedex_functions.get_time_of_day` already makes this exact check; this
# module did not.
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_settings():
    from Ankimon.services import services

    previous = services.settings
    services.settings = None
    yield
    services.settings = previous


def test_get_time_of_day_survives_an_unbound_settings_seam(no_settings):
    # Defaults are day_start=6 / night_start=18.
    assert fe.get_time_of_day(datetime(2024, 1, 1, 9, 0)) == "day"
    assert fe.get_time_of_day(datetime(2024, 1, 1, 23, 0)) == "night"


def test_evolution_readiness_survives_an_unbound_settings_seam(no_settings):
    result = fe.evolution_readiness(
        {"id": 133, "level": 20, "friendship": 200, "attacks": ["Tackle"]},
        now=datetime(2024, 1, 1, 9, 0),
    )
    assert result["evolvable"] is True
    assert result["evo_name"] == "Espeon"


def test_current_time_label_survives_an_unbound_settings_seam(no_settings):
    assert "Day" in fe.current_time_label(datetime(2024, 1, 1, 9, 0))


def test_check_friendship_evolution_survives_an_unbound_settings_seam(no_settings):
    # The toggle defaults to True, so the checker proceeds instead of raising.
    class _Win:
        def __init__(self):
            self.calls = []

        def ask_pokemon_evo(self, *args):
            self.calls.append(args)

    win = _Win()
    result = fe.check_friendship_evolution_for_pokemon(
        "iid",
        133,
        win,
        friendship=200,
        attacks=["Tackle"],
        pokemon_defeated=0,
        now=datetime(2024, 1, 1, 9, 0),
    )
    assert result == 196  # Espeon
    assert win.calls == [("iid", 133, 196)]


def test_settings_helper_falls_back_to_defaults(no_settings):
    assert fe._settings().get("evolution.day_start_hour", 6) == 6
    assert fe._settings().get("anything.at.all") is None


# --------------------------------------------------------------------------- #
# The same gender split, on the MANUAL readiness path. The two paths must agree:
# a female Espurr the automatic level-up offers Meowstic-F must not be offered
# the male Meowstic by the PC box's "Evolve now" button.
# --------------------------------------------------------------------------- #
def test_female_espurr_readiness_offers_the_female_meowstic_form():
    result = fe.evolution_readiness(_lvl(677, 30, "F"), now=_NOON)
    assert result["evo_id"] == 10025
    assert result["ready"] is True
    assert result["status_text"] == "Ready to evolve into Meowstic-F!"


def test_male_espurr_readiness_offers_the_male_meowstic_form():
    assert fe.evolution_readiness(_lvl(677, 30, "M"), now=_NOON)["evo_id"] == 678


def test_female_lechonk_readiness_offers_the_female_oinkologne_form():
    assert fe.evolution_readiness(_lvl(915, 25, "F"), now=_NOON)["evo_id"] == 10254


def test_male_lechonk_readiness_offers_the_male_oinkologne_form():
    assert fe.evolution_readiness(_lvl(915, 25, "M"), now=_NOON)["evo_id"] == 916


def test_split_forms_without_a_gender_keep_the_lowest_id():
    assert fe.evolution_readiness(_lvl(677, 30), now=_NOON)["evo_id"] == 678


def test_lone_gendered_target_stays_ready_for_either_gender():
    # Bounsweet -> Steenee: a single female-labelled target is a species
    # property, not a gate. Narrowing on it would strand a save whose stored
    # gender disagrees with its own species.
    for gender in ("M", "F", None):
        result = fe.evolution_readiness(_lvl(761, 25, gender), now=_NOON)
        assert result["evo_id"] == 762
        assert result["ready"] is True, gender


def test_form_split_never_flips_gender_ok_or_the_status_line():
    # The form filter narrows the choice; it must not feed `ready`/`required_
    # gender`, which stay driven by the CSV gate alone.
    for species_id, level in ((677, 30), (915, 25)):
        for gender in ("M", "F"):
            result = fe.evolution_readiness(_lvl(species_id, level, gender), now=_NOON)
            assert result["ready"] is True
            assert "Needs to be" not in result["status_text"]


def test_manual_and_automatic_paths_agree_on_the_split():
    """The regression this whole gate exists to prevent: two paths, one answer."""
    from Ankimon.functions import pokedex_functions as _pf

    class _Win:
        def ask_pokemon_evo(self, *args):
            pass

    for species_id, level in ((677, 25), (915, 18), (412, 20)):
        for gender in ("M", "F"):
            manual = fe.evolution_readiness(_lvl(species_id, level, gender), now=_NOON)[
                "evo_id"
            ]
            auto = _pf.check_evolution_for_pokemon(
                "iid", species_id, level, _Win(), gender=gender, current_attacks=[]
            )
            assert manual == auto, (species_id, gender, manual, auto)


def test_unlabelled_gender_gate_never_reads_as_ready(monkeypatch):
    """``status_text`` must follow ``gender_ok``, not the label lookup.

    ``_GENDER_LABELS`` only knows the veekun ids 1 and 2. Nothing in the bundled
    CSV carries anything else, but the file is regenerated from upstream, and a
    gender_id the labels don't cover used to leave ``ready`` False while the
    branch below cheerfully rendered "Ready to evolve into ..." — a status line
    contradicting the disabled button beside it. Simulate that by making both the
    gate and the label lookup answer from an unlabelled id.
    """
    from Ankimon.functions import pokedex_functions as _pf

    monkeypatch.setattr(_pf, "_evolution_row_gender_id", lambda *a, **k: 3)
    monkeypatch.setattr(fe, "_evolution_row_gender_id", lambda *a, **k: 3)

    result = fe.evolution_readiness(_lvl(1, 20, "M"), now=_NOON)

    assert result["ready"] is False
    assert "Ready to evolve" not in result["status_text"]
    assert "gender requirement" in result["status_text"]
    assert "Ivysaur" in result["status_text"]


def test_labelled_gender_gate_still_names_the_gender(monkeypatch):
    # The unlabelled fallback must not swallow the informative line.
    result = fe.evolution_readiness(_lvl(415, 21, "M"), now=_NOON)
    assert result["ready"] is False
    assert result["status_text"] == "Needs to be Female to evolve into Vespiquen"


def test_level_gender_gate_delegates_to_the_shared_helper(monkeypatch):
    """One gate, one implementation — the PC path must not keep a private copy.

    ``check_evolution_for_pokemon`` (automatic) and ``_level_readiness`` (the
    PC's manual "Evolve now") have to answer identically or the button becomes a
    way around the gate. Force the shared helper to refuse and require the manual
    path to follow; a re-inlined copy would keep saying yes.
    """
    from Ankimon.functions import pokedex_functions as _pf

    monkeypatch.setattr(_pf, "gender_allows_evolution", lambda *a, **k: False)
    monkeypatch.setattr(fe, "gender_allows_evolution", lambda *a, **k: False)

    assert fe._level_gender_gate(416, 1) is False
    assert fe.evolution_readiness(_lvl(1, 20, "M"), now=_NOON)["ready"] is False
