"""Tests for move-based (levelMove) evolution timing in ``check_evolution_for_pokemon``.

Regression coverage for the "evolution checked before new moves are learned" bug
(fixed alongside PRs #706/#744/#785): a Pokémon that learns its required move on
a level-up must be offered the evolution on that very event, and the check must
prefer the caller's fresh moveset over the stale DB row.

Loading strategy mirrors ``tests/test_friendship_evolution.py``:

* conftest.py registers lightweight stub packages so relative imports resolve.
* Anki/aqt modules and the error handler are stubbed with MagicMocks.
* ``resources`` + ``pokedex_functions`` are loaded FOR REAL so the bundled
  pokedex.json / CSV data drive every lookup (real Bonsly -> Sudowoodo chain).
* ``services.db`` is a fake returning a controllable stored-moveset, so tests can
  prove the fresh ``current_attacks`` argument wins over stale stored data.

Time of day is pinned by freezing ``services.settings`` with an explicit offset
clock (``timezone_auto=False``), which ``pokedex_functions.get_time_of_day``
reads through the services seam.
"""

import importlib.util
import sys
import unittest.mock as mock
from datetime import datetime
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"


class _FakeSettings:
    """Minimal stand-in for ``settings_obj`` backed by a mutable dict."""

    def __init__(self):
        self.values = {
            "misc.active_region": None,
            "evolution.day_start_hour": 6,
            "evolution.night_start_hour": 18,
            "evolution.timezone_auto": False,
            # Fixed-offset clock: get_time_of_day() reads datetime.now(tz) with
            # offset 0, so tests pin "day"/"night" by passing now= explicitly —
            # but check_evolution_for_pokemon reads the clock itself. The fixed
            # offset keeps it deterministic for the test run's wall time only
            # via the _freeze_clock helper below.
            "evolution.timezone_offset": 0.0,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


class _FakeEvoWindow:
    """Records ``ask_pokemon_evo`` invocations for assertions."""

    def __init__(self):
        self.calls = []

    def ask_pokemon_evo(self, individual_id, pokemon_id, evo_id):
        self.calls.append((individual_id, pokemon_id, evo_id))


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
    return pokedex_functions, fake_settings


pf, settings = _load_pf()

_SINGLETONS_STUB = sys.modules["Ankimon.singletons"]
_POKEDEX_FUNCTIONS_STUB = sys.modules["Ankimon.functions.pokedex_functions"]

# A fake DB whose get_pokemon returns the "stale stored moveset".
STORED_ATTACKS = ["Tackle"]


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Restore stubs, pin the clock to day, and wire the fake DB seam."""
    from Ankimon.services import services

    sys.modules["Ankimon.singletons"] = _SINGLETONS_STUB
    sys.modules["Ankimon.functions.pokedex_functions"] = _POKEDEX_FUNCTIONS_STUB
    # monkeypatch restores the real registry attributes after each test, so
    # later tests can never inherit this module's fake settings/db (the direct
    # assignments used before leaked across the suite and made it order-bound).
    monkeypatch.setattr(services, "settings", settings)
    settings.values.update(
        {
            "misc.active_region": None,
            "evolution.day_start_hour": 6,
            "evolution.night_start_hour": 18,
            "evolution.friendship_time_enabled": True,
        }
    )

    fake_db = mock.MagicMock()
    fake_db.get_pokemon = mock.MagicMock(return_value={"attacks": list(STORED_ATTACKS)})
    monkeypatch.setattr(services, "db", fake_db)

    # Freeze pokedex_functions' clock: 09:00 local == "day" deterministically.
    frozen_now = datetime(2024, 1, 1, 9, 0)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return tz.fromtimestamp(frozen_now.timestamp())
            return frozen_now

    monkeypatch.setattr(pf, "datetime", _FrozenDatetime, raising=False)
    yield


# --------------------------------------------------------------------------- #
# Real data chain: Bonsly (438) -> Sudowoodo (185) requires levelMove Mimic.
# --------------------------------------------------------------------------- #
def test_level_move_evo_fires_on_fresh_attacks_same_level():
    """Learning Mimic THIS level must trigger the offer immediately (#706 bug).

    The stored DB moveset is stale (no Mimic); the fresh current_attacks carry
    it. Before the fix the level-evo check ran before attack learning and read
    only the DB, so the offer came one level late or never.
    """
    evo_window = _FakeEvoWindow()
    result = pf.check_evolution_for_pokemon(
        individual_id="ind-1",
        pokemon_id=438,  # Bonsly
        level=17,
        evo_window=evo_window,
        everstone=False,
        evolution_rejected=False,
        current_attacks=["Tackle", "Mimic"],  # just learned this level
    )
    assert result == 185  # Sudowoodo
    assert evo_window.calls == [("ind-1", 438, 185)]


def test_level_move_evo_uses_stored_attacks_when_no_fresh_list():
    """Without a fresh list the check falls back to the stored moveset."""
    evo_window = _FakeEvoWindow()
    result = pf.check_evolution_for_pokemon(
        individual_id="ind-1",
        pokemon_id=438,
        level=17,
        evo_window=evo_window,
        everstone=False,
        evolution_rejected=False,
        current_attacks=None,
    )
    # Stored moveset is ["Tackle"] -> no Mimic -> fail closed, no offer.
    assert result is None
    assert evo_window.calls == []
    # And the DB seam was actually consulted.
    from Ankimon.services import services

    services.db.get_pokemon.assert_called_with("ind-1")


def test_level_move_evo_empty_fresh_list_beats_stored_data():
    """An empty fresh list must NOT silently fall back to stored data.

    The caller said "this is the current moveset"; treating [] as unknown would
    let a stale DB row (that may already contain the move) fire the evolution
    on unconfirmed data.
    """
    from Ankimon.services import services

    # Stored row KNOWS Mimic; the fresh list says it was replaced away.
    services.db.get_pokemon = mock.MagicMock(return_value={"attacks": ["Mimic"]})
    evo_window = _FakeEvoWindow()
    result = pf.check_evolution_for_pokemon(
        individual_id="ind-1",
        pokemon_id=438,
        level=17,
        evo_window=evo_window,
        everstone=False,
        evolution_rejected=False,
        current_attacks=["Tackle", "Rock Throw"],
    )
    assert result is None
    assert evo_window.calls == []


def test_level_move_evo_still_blocked_by_everstone_and_rejection():
    """The new parameter must not bypass existing suppressors."""
    for kwargs in (
        {"everstone": True},
        {"evolution_rejected": True},
    ):
        evo_window = _FakeEvoWindow()
        result = pf.check_evolution_for_pokemon(
            individual_id="ind-1",
            pokemon_id=438,
            level=17,
            evo_window=evo_window,
            current_attacks=["Tackle", "Mimic"],
            **kwargs,
        )
        assert result is None
        assert evo_window.calls == []


def test_plain_level_evo_unaffected_by_moveset():
    """A plain level-up evolver still evolves regardless of attacks."""
    evo_window = _FakeEvoWindow()
    result = pf.check_evolution_for_pokemon(
        individual_id="ind-2",
        pokemon_id=4,  # Charmander -> Charmeleon at Lv16 (plain level)
        level=20,
        evo_window=evo_window,
        everstone=False,
        evolution_rejected=False,
        current_attacks=["Scratch"],
    )
    assert result == 5
    assert evo_window.calls == [("ind-2", 4, 5)]
