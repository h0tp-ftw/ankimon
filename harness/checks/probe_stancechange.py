"""Real-engine regressions for reversible Stance Change (#773).

Outcome exploration must not turn current HP into max HP, including when a
faster opponent's miss, flinch or paralysis creates multiple branches.
"""
import copy
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
# This is a Tier-1 probe even under a Python with Qt installed.
sys.modules["aqt"] = None
sys.modules["PyQt6"] = None

from harness.bootstrap import bootstrap

_PROFILE = tempfile.TemporaryDirectory(prefix="ankimon_stancechange_")
bootstrap(_PROFILE.name)

from Ankimon.functions import ankimon_hooks_to_poke_engine as bridge
from Ankimon.poke_engine.battle import Pokemon as BattlePokemon
from Ankimon.poke_engine.config import ShowdownConfig
from Ankimon.poke_engine.data import all_move_json
from Ankimon.poke_engine.special_effects.abilities import before_move


def make_state(form="aegislashshield", side="user", hp=100):
    def pokemon(name):
        obj = bridge.Pokemon.from_state_pokemon_dict(BattlePokemon(name, 50).to_dict())
        obj.types = [t.lower() for t in obj.types]
        obj.hp = obj.maxhp = 300
        return obj

    attacker = pokemon("aegislashblade" if form == "aegislashblade" else "aegislash")
    attacker.id = form
    attacker.ability = "stancechange"
    attacker.hp = hp
    attacker.speed = 100
    defender = pokemon("mew")
    defender.speed = 200
    user, opponent = (attacker, defender) if side == "user" else (defender, attacker)
    return bridge.State(bridge.reset_side(user), bridge.reset_side(opponent), None, None, False)


def stats(pokemon):
    return (pokemon.id, pokemon.hp, pokemon.maxhp, pokemon.attack, pokemon.defense,
            pokemon.special_attack, pokemon.special_defense, pokemon.speed)


class StanceChangeTests(unittest.TestCase):
    def test_every_outcome_preserves_max_hp_and_reverses_cleanly(self):
        ShowdownConfig.damage_calc_type = "average"
        for form in ("aegislashshield", "aegislash", "aegislashblade"):
            for side in ("user", "opponent"):
                for hp in (100, 300):
                    for opposing_move in ("thunderbolt", "airslash"):
                        with self.subTest(form=form, side=side, hp=hp, move=opposing_move):
                            state = make_state(form, side, hp)
                            initial = copy.deepcopy(state)
                            active = getattr(state, side).active
                            expected = stats(active)
                            moves = ("shadowball", opposing_move)
                            if side == "opponent":
                                moves = moves[::-1]
                            outcomes = bridge.get_all_state_instructions(
                                bridge.StateMutator(state), *moves
                            )
                            self.assertGreater(len(outcomes), 1)
                            self.assertEqual(stats(active), expected,
                                             "Exploring outcomes mutated the input battler")
                            changed_stance = False
                            for outcome in outcomes:
                                mutator = bridge.StateMutator(copy.deepcopy(initial))
                                mutator.apply(outcome.instructions)
                                result = getattr(mutator.state, side).active
                                self.assertEqual(result.maxhp, 300)
                                self.assertEqual(result.id, form)
                                self.assertLessEqual(result.hp, result.maxhp)
                                changed_stance |= any(
                                    i[0] == "change_stats" and i[1] == side
                                    for i in outcome.instructions
                                )
                                mutator.reverse(outcome.instructions)
                                self.assertEqual(stats(getattr(mutator.state, side).active), expected)
                            self.assertTrue(changed_stance)

    def test_attack_then_kings_shield_changes_stats_without_changing_hp(self):
        for form in ("aegislashshield", "aegislash", "aegislashblade"):
            with self.subTest(form=form):
                state = make_state(form)
                attacker, defender = state.user.active, state.opponent.active
                for move, blade in (("shadowball", True), ("kingsshield", False)) * 2:
                    instructions = before_move.stancechange(
                        state, "user", all_move_json[move], attacker, defender
                    )
                    self.assertTrue(instructions)
                    bridge.StateMutator(state).apply(instructions)
                    self.assertEqual(attacker.id, form)
                    self.assertEqual((attacker.hp, attacker.maxhp), (100, 300))
                    self.assertEqual(attacker.attack > attacker.defense, blade)
                self.assertIsNone(before_move.stancechange(
                    state, "user", all_move_json["swordsdance"], attacker, defender
                ))


if __name__ == "__main__":
    unittest.main()
