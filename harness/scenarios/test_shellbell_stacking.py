import math
from harness.headless_env import start_session
env = start_session()

from Ankimon.poke_engine import constants
from Ankimon.poke_engine.battle import Battle
from Ankimon.poke_engine.objects import Pokemon, State, Side

p1 = Pokemon(
    identifier='bulbasaur',
    level=50,
    types=['grass', 'poison'],
    hp=100,
    maxhp=100,
    ability='overgrow',
    item='shellbell',
    attack=100,
    defense=100,
    special_attack=100,
    special_defense=100,
    speed=150,
    moves=[{'id': 'gigadrain', 'disabled': False, 'current_pp': 35}]
)

p2 = Pokemon(
    identifier='charmander',
    level=50,
    types=['fire'],
    hp=100,
    maxhp=100,
    ability='blaze',
    item='',
    attack=100,
    defense=100,
    special_attack=100,
    special_defense=100,
    speed=100,
    moves=[{'id': 'tackle', 'disabled': False, 'current_pp': 35}]
)

from collections import defaultdict
user = Side(p1, {}, (0, 0), defaultdict(int), (0, 0))
opponent = Side(p2, {}, (0, 0), defaultdict(int), (0, 0))

state = State(user, opponent, None, None, False)
state.user.active.hp = 10

import Ankimon.functions.ankimon_hooks_to_poke_engine

from Ankimon.poke_engine.special_effects.items import modify_attack_being_used
move = {'id': 'gigadrain', 'category': 'special', 'basePower': 75, 'type': 'grass', 'drain': [1, 2], 'flags': {}}
modified = modify_attack_being_used.item_modify_attack_being_used('shellbell', move, p1, p2)
print("Modified Move:", modified)
