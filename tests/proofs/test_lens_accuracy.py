import pytest

# We only run start_session ONCE at the module level!
import harness.headless_env
env = harness.headless_env.start_session()
from Ankimon.poke_engine import constants
from Ankimon.poke_engine.special_effects.moves import modify_move
from Ankimon.poke_engine.objects import Pokemon, Side

class DummyPkmn:
    def __init__(self, item=""):
        self.id = "dummy"
        self.item = item
        self.ability = "dummy"
        self.types = ["normal"]
        self.hp = 100
        self.maxhp = 100

class DummySide:
    def __init__(self, pkmn):
        self.active = pkmn
        self.reserve = {}

def test_widelens_accuracy():
    p1 = DummyPkmn("widelens")
    s1 = DummySide(p1)
    move = {constants.ID: "tackle", constants.ACCURACY: 90, constants.BASE_POWER: 40}
    new_move = modify_move.modify_attack_being_used(s1, move, None, p1, DummyPkmn(), True, None, None)
    assert new_move[constants.ACCURACY] == pytest.approx(99.0)

def test_zoomlens_accuracy():
    p1 = DummyPkmn("zoomlens")
    s1 = DummySide(p1)
    move = {constants.ID: "tackle", constants.ACCURACY: 90, constants.BASE_POWER: 40}
    # first_move = True -> no boost
    new_move_first = modify_move.modify_attack_being_used(s1, move, None, p1, DummyPkmn(), True, None, None)
    assert new_move_first[constants.ACCURACY] == 90

    # first_move = False -> 1.2x boost
    new_move_last = modify_move.modify_attack_being_used(s1, move, None, p1, DummyPkmn(), False, None, None)
    assert new_move_last[constants.ACCURACY] == pytest.approx(108.0)
