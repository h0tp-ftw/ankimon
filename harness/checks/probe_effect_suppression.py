"""Prove suppression lifetimes through the production bridge and review loop."""

import copy
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
# Keep the Tier-1 path independent of installed desktop libraries.
sys.modules["aqt"] = None
sys.modules["PyQt6"] = None

from harness.driver import Driver


class SuppressionLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        random.seed(869)
        cls.driver = Driver(
            seed={
                "main": {
                    "species": "Mew",
                    "level": 50,
                    "held_item": "shell-bell",
                    "moves": ["Tackle"],
                    "ability": "Synchronize",
                }
            },
            settings_overrides={
                "battle.cards_per_round": 1,
                "battle.review_based_damage": True,
                "battle.automatic_battle": 0,
                "audio.sounds": False,
                "audio.sound_effects": False,
            },
        )
        from Ankimon import battle_loop
        from Ankimon.functions import ankimon_hooks_to_poke_engine as bridge
        from Ankimon.poke_engine.config import ShowdownConfig

        cls.bridge, cls.battle_loop = bridge, battle_loop
        ShowdownConfig.damage_calc_type = "average"

    @classmethod
    def tearDownClass(cls):
        cls.driver.services.db.close()

    def setUp(self):
        self.fresh_battle()

    def fresh_battle(self, foe_move="splash", ability="Synchronize"):
        self.driver.set_enemy(
            species="Mewtwo", level=50, moves=[foe_move], ability="Pressure"
        )
        self.main = self.driver.services.main_pokemon
        self.enemy = self.driver.services.enemy_pokemon
        self.main.hp = self.main.current_hp = 50
        self.main.ability = ability
        self.main.held_item = "shell-bell"
        self.main.attacks = ["Tackle"]
        self.main.volatile_status = set()
        self.enemy.volatile_status = set()
        self.battle_loop._state = self.battle_loop.BattleState()
        tracker = self.driver.services.tracker
        tracker.cards_until_calc_multiplier = 1
        tracker.multiplier_card_ratings_count = {
            grade: 0 for grade in ("again", "hard", "good", "easy")
        }
        self.driver.drain_events()

    def turn(self, user_move, foe_move, state=None, reset=0):
        result = self.bridge.simulate_battle_with_poke_engine(
            self.main,
            self.enemy,
            user_move,
            foe_move,
            reset,
            state,
        )
        self.assertIsNotNone(result)
        errors = [
            event for event in self.driver.drain_events() if event["type"] == "error"
        ]
        self.assertFalse(errors, errors)
        self.assertEqual(self.main.hp, result[1].user.active.hp)
        self.assertEqual(self.main.current_hp, result[1].user.active.hp)
        self.assertEqual(
            self.main.volatile_status, result[1].user.active.volatile_status
        )
        return result

    def test_bridge_preserves_five_turn_suppression_and_restores_shell_bell(self):
        for blocker in ("embargo", "healblock"):
            with self.subTest(blocker=blocker):
                self.fresh_battle()
                state = None
                for turn in range(1, 7):
                    result = self.turn(
                        "tackle", blocker if turn == 1 else "splash", state
                    )
                    state = copy.deepcopy(result[1])
                    self.assertEqual(
                        state.user.active.volatile_status_durations.get(blocker, 0),
                        max(0, 5 - turn),
                    )
                    self.assertEqual(blocker in self.main.volatile_status, turn < 5)
                    self.assertEqual(self.main.hp == 50, turn < 6)
                    self.assertGreater(self.enemy.hp, 0)
                    self.assertEqual(state.user.active.item, "shellbell")

    def test_bridge_psychic_noise_blocks_recover_for_exactly_two_turns(self):
        first = self.turn("recover", "psychicnoise")
        state = first[1]
        self.assertEqual(state.user.active.volatile_status_durations, {"healblock": 1})
        damaged_hp = self.main.hp
        self.assertLess(damaged_hp, 50)
        state = self.turn("recover", "splash", state)[1]
        self.assertEqual(self.main.hp, damaged_hp)
        self.assertNotIn("healblock", self.main.volatile_status)
        self.assertEqual(state.user.active.volatile_status_durations, {})
        self.turn("recover", "splash", state)
        self.assertGreater(self.main.hp, damaged_hp)

    def test_bridge_gastro_acid_enables_klutz_item_without_changing_identity(self):
        self.fresh_battle(ability="Klutz")
        state = self.turn("tackle", "gastroacid")[1]
        self.assertGreater(self.main.hp, 50)
        first_hp = self.main.hp
        state = self.turn("tackle", "splash", state)[1]
        self.assertGreater(self.main.hp, first_hp)
        self.assertEqual(state.user.active.ability, "klutz")
        self.assertEqual(state.user.active.item, "shellbell")
        self.assertIn("gastroacid", self.main.volatile_status)

    def test_new_encounter_reset_clears_flags_and_timers(self):
        state = self.turn("tackle", "healblock")[1]
        self.assertEqual(state.user.active.volatile_status_durations, {"healblock": 4})
        result = self.turn("tackle", "splash", state, reset=1)
        self.assertGreater(self.main.hp, 50)
        self.assertEqual(result[1].user.active.volatile_status_durations, {})
        self.assertNotIn("healblock", self.main.volatile_status)

    def test_real_review_loop_carries_timers_between_card_answers(self):
        for blocker in ("embargo", "healblock"):
            with self.subTest(blocker=blocker):
                self.fresh_battle(foe_move=blocker)
                for turn in range(1, 7):
                    if turn > 1:
                        self.enemy.attacks = ["Splash"]
                    # A failed answer lets the faster wild Pokemon use its move.
                    # Later answers still traverse normal tracker and review logic.
                    events = self.driver.answer("again" if turn == 1 else "good")
                    self.assertFalse(
                        [event for event in events if event["type"] == "error"], events
                    )
                    self.assertEqual(
                        len([event for event in events if event["type"] == "battle"]),
                        1,
                        events,
                    )
                    state = self.battle_loop._state.new_state
                    self.assertEqual(
                        state.user.active.volatile_status_durations.get(blocker, 0),
                        max(0, 5 - turn),
                        events,
                    )
                    self.assertEqual(self.main.hp == 50, turn < 6)
                    self.assertEqual(self.battle_loop._state.mutator_full_reset, 0)
                    self.assertGreater(self.enemy.hp, 0)


if __name__ == "__main__":
    unittest.main()
