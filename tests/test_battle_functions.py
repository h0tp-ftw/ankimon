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


@pytest.mark.parametrize("raw", [None, "", "Fighting", "  FIGHTING  "])
def test_validate_pokemon_status_tolerates_unnormalized_status(raw):
    """PokemonObject.update_stats() writes the database row straight onto the
    attribute, so battle_status can arrive as None or capitalised. battle_loop
    calls this every turn, so it must not raise."""
    assert validate_pokemon_status(MockPokemon(50, raw)) == "fighting"


@pytest.mark.parametrize("raw, expected", [("  PAR  ", "par"), ("Fainted", "fainted")])
def test_validate_pokemon_status_preserves_padded_or_capitalised_real_status(raw, expected):
    """Normalising must not turn a real status into "fighting": a padded "  par  "
    is still paralysed, and a capitalised "Fainted" at 0 HP is still fainted."""
    hp = 0 if expected == "fainted" else 50
    assert validate_pokemon_status(MockPokemon(hp, raw)) == expected


def test_validate_pokemon_status_without_the_attribute():
    class Bare:
        hp = 50

    assert validate_pokemon_status(Bare()) == "fighting"
