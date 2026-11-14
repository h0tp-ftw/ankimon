"""Real battle recovery and committed potion updates must coexist (#975)."""

from pathlib import Path
import subprocess
import sys
import textwrap


_ROOT = Path(__file__).resolve().parents[1]
_BOOT = """
import random
from harness.driver import Driver
from harness.fixtures import build_pokemon
driver = Driver(
    first_encounter=False,
    settings_overrides={"battle.review_based_damage": False},
)
from Ankimon.functions import ankimon_hooks_to_poke_engine as bridge
from Ankimon.poke_engine import constants

def battle(user, opponent, user_move, opponent_move, state=None):
    random.seed(0)
    return bridge.simulate_battle_with_poke_engine(
        user, opponent, user_move, opponent_move, int(state is None), state
    )
"""


def _run(code):
    result = subprocess.run(
        [sys.executable, "-c", _BOOT + textwrap.dedent(code)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_recover_heals_before_later_hp_based_damage():
    _run("""
        user = build_pokemon({"species": "Mewtwo", "level": 50,
                              "moves": ["Recover"], "hp": 10})
        opponent = build_pokemon({"species": "Raticate", "level": 50,
                                  "moves": ["Super Fang"]})
        result = battle(user, opponent, "recover", "superfang")
        # The faster Recover must affect Super Fang's calculation in the same
        # turn, not just add its healing after damage has already been decided.
        healed_hp = 10 + user.max_hp / 2
        expected_hp = int(healed_hp - int(healed_hp / 2))
        assert user.hp == user.current_hp == result[1].user.active.hp == expected_hp
        assert [i[2] for i in result[0]["instructions"]
                if i[0] == constants.MUTATOR_HEAL and i[1] == constants.USER] == [user.max_hp / 2]
        assert [i[2] for i in result[0]["instructions"]
                if i[0] == constants.MUTATOR_DAMAGE and i[1] == constants.USER] == [int(healed_hp / 2)]
    """)


def test_rest_restores_full_hp_as_well_as_putting_user_to_sleep():
    _run("""
        user = build_pokemon({"species": "Mewtwo", "level": 50,
                              "moves": ["Rest"], "hp": 10})
        opponent = build_pokemon({"species": "Raticate", "level": 50,
                                  "moves": ["Splash"]})
        result = battle(user, opponent, "rest", "splash")
        assert user.hp == user.current_hp == result[1].user.active.hp == user.max_hp
        assert result[1].user.active.status == constants.SLEEP
        assert [i[2] for i in result[0]["instructions"]
                if i[0] == constants.MUTATOR_HEAL and i[1] == constants.USER] == [user.max_hp - 10]
    """)


def test_draining_attack_recovers_hp_from_damage_dealt():
    _run("""
        user = build_pokemon({"species": "Mewtwo", "level": 50,
                              "moves": ["Drain Punch"], "hp": 10})
        opponent = build_pokemon({"species": "Snorlax", "level": 50,
                                  "moves": ["Splash"]})
        opponent_hp = opponent.hp
        result = battle(user, opponent, "drainpunch", "splash")
        damage = opponent_hp - opponent.hp
        assert damage > 0
        recovered = min(int(damage / 2), user.max_hp - 10)
        assert user.hp == user.current_hp == result[1].user.active.hp == 10 + recovered
        assert [i[2] for i in result[0]["instructions"]
                if i[0] == constants.MUTATOR_HEAL and i[1] == constants.USER] == [recovered]
    """)


def test_pain_split_can_heal_either_side():
    _run("""
        # Pain Split uses negative damage to heal its target, but a heal
        # instruction to recover the user's HP. Exercise both representations.
        for user_hp, opponent_hp in ((10, 100), (100, 10)):
            for user_uses_pain_split in (False, True):
                user_move = "painsplit" if user_uses_pain_split else "safeguard"
                opponent_move = "safeguard" if user_uses_pain_split else "painsplit"
                user = build_pokemon({"species": "Mewtwo", "level": 50,
                                      "moves": [user_move], "hp": user_hp})
                opponent = build_pokemon({"species": "Misdreavus", "level": 50,
                                          "moves": [opponent_move], "hp": opponent_hp})
                result = battle(user, opponent, user_move, opponent_move)
                assert user.hp == user.current_hp == result[1].user.active.hp == 55
                assert opponent.hp == opponent.current_hp == result[1].opponent.active.hp == 55
    """)


def test_opponent_recovery_remains_unchanged():
    _run("""
        user = build_pokemon({"species": "Raticate", "level": 50,
                              "moves": ["Super Fang"]})
        opponent = build_pokemon({"species": "Mewtwo", "level": 50,
                                  "moves": ["Recover"], "hp": 10})
        result = battle(user, opponent, "superfang", "recover")
        healed_hp = 10 + opponent.max_hp / 2
        assert opponent.hp == int(healed_hp - int(healed_hp / 2))
        assert any(i[0] == constants.MUTATOR_HEAL and
                   i[1] == constants.OPPONENT and i[2] > 0
                   for i in result[0]["instructions"])
    """)


def test_odd_max_recovery_publishes_integer_hp_for_the_next_turn():
    _run("""
        for recovering_side in (constants.USER, constants.OPPONENT):
            recovering = build_pokemon({"species": "Mewtwo", "level": 50,
                                        "moves": ["Recover", "Splash"], "hp": 10})
            attacker = build_pokemon({"species": "Raticate", "level": 50,
                                     "moves": ["Super Fang", "Splash"]})
            assert recovering.max_hp == 181
            if recovering_side == constants.USER:
                user, opponent = recovering, attacker
                first_moves = ("recover", "splash")
                next_moves = ("splash", "superfang")
            else:
                user, opponent = attacker, recovering
                first_moves = ("splash", "recover")
                next_moves = ("superfang", "splash")
            result = battle(user, opponent, *first_moves)
            active = getattr(result[1], recovering_side).active
            # The engine emits a native 90.5 HP heal, but persistent Ankimon
            # health is integer. Publish the same value into every live copy.
            assert recovering.hp == recovering.current_hp == active.hp == 100
            assert type(active.hp) is int
            assert [i[2] for i in result[0]["instructions"]
                    if i[0] == constants.MUTATOR_HEAL and i[1] == recovering_side] == [90.5]

            # Reuse the actual returned battle state, as an ordinary later
            # card does. A stale half-HP must not survive into that next turn.
            result = battle(user, opponent, *next_moves, state=result[1])
            active = getattr(result[1], recovering_side).active
            assert recovering.hp == recovering.current_hp == active.hp == 50
            assert type(active.hp) is int
    """)


def test_recovery_caps_both_sides_at_integer_max_hp():
    _run("""
        user = build_pokemon({"species": "Mewtwo", "level": 50,
                              "moves": ["Recover"]})
        opponent = build_pokemon({"species": "Mewtwo", "level": 50,
                                  "moves": ["Recover"]})
        for pokemon in (user, opponent):
            pokemon.hp = pokemon.current_hp = pokemon.max_hp - 1
        result = battle(user, opponent, "recover", "recover")
        for pokemon, side in ((user, result[1].user), (opponent, result[1].opponent)):
            assert pokemon.hp == pokemon.current_hp == side.active.hp == pokemon.max_hp
            assert type(side.active.hp) is int
        # Recover at full HP cannot create an over-max persisted value.
        result = battle(user, opponent, "recover", "recover", state=result[1])
        for pokemon, side in ((user, result[1].user), (opponent, result[1].opponent)):
            assert pokemon.hp == pokemon.current_hp == side.active.hp == pokemon.max_hp
    """)


def test_leftovers_recovers_hp_before_end_of_turn_damage():
    _run("""
        for starting_hp, status in ((10, "fighting"), (1, constants.POISON)):
            user = build_pokemon({"species": "Mewtwo", "level": 50,
                                  "moves": ["Safeguard"], "hp": starting_hp,
                                  "held_item": "leftovers"})
            user.battle_status = status
            opponent = build_pokemon({"species": "Raticate", "level": 50,
                                      "moves": ["Tail Whip"]})
            result = battle(user, opponent, "safeguard", "tailwhip")
            recovered = round(user.max_hp / 16)
            assert [i[2] for i in result[0]["instructions"]
                    if i[0] == constants.MUTATOR_HEAL and i[1] == constants.USER] == [recovered]
            if status == constants.POISON:
                # Poison must see the HP that Leftovers just restored.
                assert user.hp == user.current_hp == result[1].user.active.hp == 0
                assert [i[2] for i in result[0]["instructions"]
                        if i[0] == constants.MUTATOR_DAMAGE and i[1] == constants.USER] == [starting_hp + recovered]
            else:
                assert user.hp == user.current_hp == result[1].user.active.hp == starting_hp + recovered
    """)


def test_poison_heal_recovers_hp_instead_of_poison_damage():
    _run("""
        user = build_pokemon({"species": "Gliscor", "level": 50,
                              "moves": ["Protect"], "hp": 10,
                              "ability": "Poison Heal"})
        user.battle_status = constants.POISON
        opponent = build_pokemon({"species": "Raticate", "level": 50,
                                  "moves": ["Splash"]})
        result = battle(user, opponent, "protect", "splash")
        assert user.hp == user.current_hp == result[1].user.active.hp == 10 + round(user.max_hp / 8)
        assert result[1].user.active.status == constants.POISON
        assert not any(i[0] == constants.MUTATOR_DAMAGE and i[1] == constants.USER
                       for i in result[0]["instructions"])
    """)


def test_potion_hp_sync_preserves_battle_state_and_checks_individual():
    _run("""
        user = build_pokemon({"species": "Mewtwo", "level": 50,
                              "moves": ["Recover"], "hp": 10})
        opponent = build_pokemon({"species": "Raticate", "level": 50,
                                  "moves": ["Super Fang"]})
        state = battle(user, opponent, "recover", "superfang")[1]
        active = state.user.active
        active.attack_boost = 3
        active.volatile_status.add("substitute")
        state.weather = "rain"
        state.trick_room = True
        old_hp = active.hp
        user.current_hp = user.hp = 25
        individual_id = user.individual_id
        user.individual_id = "another-individual"
        assert not bridge.sync_active_pokemon_hp(state, user)
        assert active.hp == old_hp
        user.individual_id = individual_id
        assert bridge.sync_active_pokemon_hp(state, user)
        assert active.hp == 25 and active.maxhp == user.max_hp
        assert state.user.active is active
        assert active.attack_boost == 3 and "substitute" in active.volatile_status
        assert state.weather == "rain" and state.trick_room is True
    """)
