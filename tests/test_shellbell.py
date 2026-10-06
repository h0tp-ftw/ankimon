from Ankimon.poke_engine import constants
from Ankimon.poke_engine.battle import Battle
from Ankimon.poke_engine.objects import Pokemon, State, Side

from Ankimon.functions.ankimon_hooks_to_poke_engine import _install_shell_bell
_install_shell_bell()

def test_shellbell_applies_heal_to_damaging_moves():
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

    from Ankimon.poke_engine.special_effects.items import modify_attack_being_used
    move = {'id': 'tackle', 'category': 'physical', 'basePower': 40, 'type': 'normal', 'flags': {}}

    modified = modify_attack_being_used.item_modify_attack_being_used('shellbell', move, p1, p2)
    assert constants.DRAIN in modified
    assert modified[constants.DRAIN] == [1, 8]

def test_shellbell_stacks_with_draining_moves():
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

    from Ankimon.poke_engine.special_effects.items import modify_attack_being_used
    move = {'id': 'gigadrain', 'category': 'special', 'basePower': 75, 'type': 'grass', 'drain': [1, 2], 'flags': {}}

    modified = modify_attack_being_used.item_modify_attack_being_used('shellbell', move, p1, p2)
    assert constants.DRAIN in modified
    assert modified[constants.DRAIN] == [625, 1000]
