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


class _Holder:
    """Enough of a PokemonObject for the effect messages to name it."""

    def __init__(self, name, held_item=None):
        self.display_name = name
        self.name = name
        self.held_item = held_item


def _effects(changes, main=None, enemy=None):
    from Ankimon.functions.battle_functions import _process_battle_effects

    return _process_battle_effects(
        [], None, main_pokemon=main, enemy_pokemon=enemy, changes=changes,
    )


def test_a_spent_held_item_is_announced_with_the_name_the_player_knows():
    """The engine id has the hyphen stripped; the holder still has the real one.

    Without this the only thing a Cell Battery holder sees is an unattributed
    stat change, and every screen that shows the held item still shows it.
    """
    messages = _effects(
        [
            {"key": "user.active.attack_boost", "before": 0, "after": 1},
            {"key": "user.active.item", "before": "cellbattery", "after": None},
        ],
        main=_Holder("Snorlax", held_item="cell-battery"),
    )

    assert any("Cell Battery" in message and "Snorlax" in message
               for message in messages), messages


def test_an_item_arriving_is_not_reported_as_one_being_used_up():
    """Thief, Trick and a switch-in all change this key the other way."""
    messages = _effects(
        [{"key": "opponent.active.item", "before": None, "after": "leftovers"}],
        enemy=_Holder("Rattata"),
    )

    assert not any("used up" in message for message in messages), messages


def test_a_spent_item_is_named_from_the_engine_id_when_the_holder_has_moved_on():
    messages = _effects(
        [{"key": "opponent.active.item", "before": "absorbbulb", "after": None}],
        enemy=_Holder("Rattata", held_item="oran-berry"),
    )

    assert any("Absorbbulb" in message for message in messages), messages
