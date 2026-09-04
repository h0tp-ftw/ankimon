import sys
import types
import pytest
from pathlib import Path
from unittest.mock import MagicMock

_src = Path(__file__).parent.parent / "src"
for _pkg in ("Ankimon", "Ankimon.functions"):
    _mod = types.ModuleType(_pkg)
    _mod.__path__ = [str(_src / _pkg.replace(".", "/"))]
    _mod.__package__ = _pkg
    sys.modules[_pkg] = _mod

if "Ankimon" in sys.modules and "Ankimon.functions" in sys.modules:
    setattr(sys.modules["Ankimon"], "functions", sys.modules["Ankimon.functions"])

sys.modules["aqt"] = MagicMock()
sys.modules["aqt.qt"] = MagicMock()
sys.modules["aqt.utils"] = MagicMock()

from Ankimon.functions.battle_functions import validate_pokemon_status


class MockPokemon:
    def __init__(self, hp, battle_status):
        self.hp = hp
        self.battle_status = battle_status


def test_validate_pokemon_status_fainted_hp_0():
    poke = MockPokemon(0, "fighting")
    assert validate_pokemon_status(poke) == "fainted"


def test_validate_pokemon_status_healed():
    poke = MockPokemon(50, "fainted")
    assert validate_pokemon_status(poke) == "fighting"


def test_validate_pokemon_status_normal():
    poke = MockPokemon(50, "fighting")
    assert validate_pokemon_status(poke) == "fighting"
