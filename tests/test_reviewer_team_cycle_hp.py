"""Regression tests for ``reviewer_ui.cycle_team_pokemon`` HP handling.

Cycling used to finish with ``main_pokemon.hp = main_pokemon.max_hp`` -- a free
full heal on every press of the team-cycle hotkey.  Deleting that line alone is
not enough: ``PokemonObject.update_stats`` only overwrites the attributes the
incoming record actually carries, so a ``current_hp``-only (or ``hp``-only) row
kept the *outgoing* Pokemon's value in the other field, and ``to_dict()`` then
persisted the split record.

The switch must resolve HP exactly like the launch-time loader
(``update_main_pokemon._apply_loaded_hp``): ``current_hp`` first, then ``hp``,
then max HP, written to both fields.  ``reviewer_ui``, ``update_main_pokemon``
and ``pokemon_obj`` are loaded for real; only Qt/aqt and unrelated siblings are
stubbed (mirroring ``test_main_pokemon_switch_preserves_state.py``).  Runs
Qt-free in the Tier-1 env.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"

# The cycled-to Pokemon: level 50, 31 HP IVs -> 181 max HP, so any wounded value
# (or a value leaked from the outgoing Pokemon) is unmistakable.
_INCOMING_BASE_STATS = {
    "hp": 106,
    "atk": 110,
    "def": 90,
    "spa": 154,
    "spd": 90,
    "spe": 130,
}
_INCOMING_MAX_HP = 181
_OUTGOING_HP = 18


class _MockResources:
    """Path-returning stand-in for ``Ankimon.resources`` (any attr -> /tmp/<name>)."""

    pokedex_path = _SRC / "Ankimon" / "data_files" / "pokedex.json"

    def __getattr__(self, name):
        return Path("/tmp") / name


class _FakeSettings:
    def get(self, key, default=None):
        return 3 if key == "controls.team_cycle_count" else default


class _FakeDB:
    """The ``services.db`` seam: a two-member team, one stored row, recorded saves."""

    def __init__(self, incoming_row):
        self._row = incoming_row
        self.saved_main = []

    def get_team(self):
        return [{"individual_id": "outgoing-uuid"}, {"individual_id": "incoming-uuid"}]

    def get_pokemon(self, individual_id):
        assert individual_id == "incoming-uuid"
        return dict(self._row)

    def save_main_pokemon(self, data):
        self.saved_main.append(data)


def _make_module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _force_load(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def team_cycle():
    """Load the real ``reviewer_ui`` + HP loader + ``PokemonObject`` on stubs."""
    saved_modules = sys.modules.copy()

    services = types.SimpleNamespace(db=None, settings=_FakeSettings())

    # to_dict() lazily computes CP via ``..business``; a functional stub keeps the
    # round-trip cheap and independent of the CP formula.
    business = MagicMock()
    business.pokemon_go_raw_stats.return_value = (100, 100, 100)
    business.calculate_pokemon_go_cp.return_value = 500

    singletons = types.ModuleType("Ankimon.singletons")
    for attr in (
        "enemy_pokemon",
        "main_pokemon",
        "ankimon_tracker_obj",
        "get_test_window",
        "get_evo_window",
        "logger",
        "achievements",
        "trainer_card",
        "reviewer_obj",
    ):
        setattr(singletons, attr, MagicMock())

    for pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
        mod = types.ModuleType(pkg)
        mod.__path__ = [str(_SRC / pkg.replace(".", "/"))]
        mod.__package__ = pkg
        sys.modules[pkg] = mod

    sys.modules.update(
        {
            "aqt": MagicMock(),
            "aqt.utils": MagicMock(),
            "aqt.reviewer": MagicMock(),
            "anki": MagicMock(),
            "anki.hooks": MagicMock(),
            "Ankimon.services": _make_module("Ankimon.services", services=services),
            "Ankimon.business": business,
            "Ankimon.resources": _MockResources(),
            "Ankimon.singletons": singletons,
            "Ankimon.functions.pokedex_functions": _make_module(
                "Ankimon.functions.pokedex_functions",
                search_pokedex_by_id=lambda pkmn_id: "mewtwo",
                search_pokedex=lambda name, key: dict(_INCOMING_BASE_STATS),
            ),
            "Ankimon.functions.encounter_functions": MagicMock(),
            "Ankimon.texts": MagicMock(),
            "Ankimon.utils": MagicMock(),
        }
    )

    try:
        pokemon_obj = _force_load(
            "Ankimon.pyobj.pokemon_obj", _SRC / "Ankimon" / "pyobj" / "pokemon_obj.py"
        )
        _force_load(
            "Ankimon.functions.update_main_pokemon",
            _SRC / "Ankimon" / "functions" / "update_main_pokemon.py",
        )
        ui = _force_load("Ankimon.reviewer_ui", _SRC / "Ankimon" / "reviewer_ui.py")
        yield ui, services, pokemon_obj.PokemonObject
    finally:
        for name in [name for name in sys.modules if name not in saved_modules]:
            del sys.modules[name]
        sys.modules.update(saved_modules)


def _incoming_row(**hp_fields):
    """The stored row of the team member being cycled to, minus its HP keys."""
    row = {
        "id": 150,
        "name": "mewtwo",
        "level": 50,
        "ability": "Pressure",
        "type": ["Psychic"],
        "gender": "N",
        "growth_rate": "slow",
        "base_experience": 306,
        "attacks": ["psychic"],
        "ev": {k: 0 for k in _INCOMING_BASE_STATS},
        "iv": {k: 31 for k in _INCOMING_BASE_STATS},
        "shiny": False,
        "individual_id": "incoming-uuid",
        "tier": "Legendary",
        "captured_date": None,
    }
    row.update(hp_fields)
    return row


def _cycle(team_cycle, **hp_fields):
    """Cycle from a wounded Pikachu to the stored row; return (main, saved row)."""
    ui, services, PokemonObject = team_cycle
    outgoing = PokemonObject(
        name="pikachu",
        id=25,
        shiny=False,
        level=30,
        ability="Static",
        type=["Electric"],
        gender="M",
        growth_rate="medium",
        captured_date=None,
        tier="Normal",
        individual_id="outgoing-uuid",
        base_stats={"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
        hp=_OUTGOING_HP,
        current_hp=_OUTGOING_HP,
    )
    assert outgoing.hp == outgoing.current_hp == _OUTGOING_HP

    db = _FakeDB(_incoming_row(**hp_fields))
    services.db = db
    ui.main_pokemon = outgoing
    ui.reviewer_obj = MagicMock()
    ui.tooltip = MagicMock()

    ui.cycle_team_pokemon()

    # Reached the success path, not one of the error tooltips.
    ui.tooltip.assert_called_once()
    assert ui.tooltip.call_args.args[0].startswith("Switched to")
    ui.reviewer_obj.refresh_hud.assert_called_once()
    assert db.saved_main, "the cycled-to Pokemon must be persisted as main"
    return outgoing, db.saved_main[-1]


@pytest.mark.parametrize(
    ("hp_fields", "expected_hp"),
    [
        pytest.param({"hp": 7, "current_hp": 7}, 7, id="both-fields-wounded"),
        pytest.param({"current_hp": 42}, 42, id="current_hp-only"),
        pytest.param({"hp": 42}, 42, id="hp-only"),
        # save_main_pokemon_progress / evolution_window refresh only current_hp.
        pytest.param({"hp": 181, "current_hp": 12}, 12, id="stale-hp-fresh-current_hp"),
        pytest.param(
            {"hp": "garbage", "current_hp": None}, _INCOMING_MAX_HP, id="malformed"
        ),
        pytest.param({}, _INCOMING_MAX_HP, id="missing"),
    ],
)
def test_cycle_resolves_incoming_hp_like_the_loader(team_cycle, hp_fields, expected_hp):
    main, saved = _cycle(team_cycle, **hp_fields)

    assert main.individual_id == "incoming-uuid"
    assert main.max_hp == _INCOMING_MAX_HP
    assert main.hp == main.current_hp == expected_hp
    assert saved["hp"] == saved["current_hp"] == expected_hp
