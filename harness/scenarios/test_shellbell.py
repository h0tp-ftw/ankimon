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
    moves=[{'id': 'tackle', 'disabled': False, 'current_pp': 35}]
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
print("Before move:", state.user.active.hp)

import Ankimon.functions.ankimon_hooks_to_poke_engine

from Ankimon.poke_engine.find_state_instructions import get_all_state_instructions
from Ankimon.poke_engine.objects import StateMutator

mutator = StateMutator(state)
from Ankimon.poke_engine.objects import TransposeInstruction
instruction = TransposeInstruction(1.0, [])

from Ankimon.poke_engine.special_effects.items import modify_attack_being_used
move = {'id': 'tackle', 'category': 'physical', 'basePower': 40, 'type': 'normal', 'flags': {}}
modified_move = modify_attack_being_used.item_modify_attack_being_used('shellbell', move, p1, p2)
print("Modified Move:", modified_move)
print("Drain element in move:", modified_move.get(constants.DRAIN))

from Ankimon.poke_engine.instruction_generator import get_instructions_from_damage

# Call get_instructions_from_damage where actual drain healing happens
results = get_instructions_from_damage(mutator, 'opponent', 16, 100, modified_move, instruction)
for i in results:
    print(i.instructions)
