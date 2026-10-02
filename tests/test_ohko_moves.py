import pytest
from unittest.mock import MagicMock
import sys

# Mock Anki dependencies so we can import Ankimon
sys.modules['aqt'] = MagicMock()
sys.modules['anki'] = MagicMock()
sys.modules['PyQt6'] = MagicMock()
sys.modules['PyQt6.QtCore'] = MagicMock()
sys.modules['PyQt6.QtGui'] = MagicMock()
sys.modules['PyQt6.QtWidgets'] = MagicMock()

from Ankimon.functions.ankimon_hooks_to_poke_engine import _install_ohko_moves
from Ankimon.poke_engine.damage_calculator import SPECIAL_LOGIC_MOVES

def test_ohko_move_logic():
    # Install our hooks
    _install_ohko_moves()

    # Create mock attacker and defender
    attacker = MagicMock()
    attacker.level = 50
    attacker.pokemon_id = 1 # Bulbasaur

    defender = MagicMock()
    defender.level = 50
    defender.hp = 100
    defender.pokemon_id = 4 # Charmander
    defender.types = ["fire"] # Neutral to normal, ground, etc. in gen 1

    # Test 1: Equal levels, neutral typing -> Should return [defender.hp] (100)
    guillotine_func = SPECIAL_LOGIC_MOVES["guillotine"]
    damage = guillotine_func(attacker, defender)
    assert damage == [100]

    # Test 2: Attacker level < Defender level -> Should return [0]
    attacker.level = 49
    damage = guillotine_func(attacker, defender)
    assert damage == [0]

    # Test 3: Attacker level > Defender level -> Should return [100]
    attacker.level = 51
    damage = guillotine_func(attacker, defender)
    assert damage == [100]

    # Test 4: Immunity (type effectiveness == 0) -> Should return [0]
    attacker.level = 50
    defender.types = ["flying"] # Flying is immune to Ground (Fissure)

    # Test Fissure
    fissure_func = SPECIAL_LOGIC_MOVES["fissure"]
    damage = fissure_func(attacker, defender)
    assert damage == [0]

    # Test Ghost immune to Normal (Guillotine)
    defender.types = ["ghost"]
    damage = guillotine_func(attacker, defender)
    assert damage == [0]
