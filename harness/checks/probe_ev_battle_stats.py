"""Real-engine regressions for training during an ongoing encounter (#959)."""

import copy
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.modules["aqt"] = None
sys.modules["PyQt6"] = None

from harness.driver import Driver
from harness.fixtures import build_pokemon


class EVBattleStatsTests(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory(prefix="ankimon_ev_battle_")
        self.addCleanup(self.profile.cleanup)
        self.driver = Driver(
            user_path=self.profile.name,
            seed={"main": {"species": "Pikachu", "level": 100, "moves": ["Tackle"]}},
            settings_overrides={
                "battle.cards_per_round": 1,
                "battle.automatic_battle": 0,
                "audio.sound_effects": False,
            },
            first_encounter=False,
        )
        self.addCleanup(self.driver.services.db.close)
        self.main = self.driver.services.main_pokemon
        self.driver.set_enemy(species="Blissey", level=100, moves=["Splash"])
        self.driver.answer("good")
        from Ankimon import battle_loop
        from Ankimon.functions import ankimon_hooks_to_poke_engine as bridge

        self.loop = battle_loop
        self.bridge = bridge
        self.state = self.loop._state.new_state
        self.assertIsNotNone(self.state)
        self.assertEqual(self.loop._state.mutator_full_reset, 0)

    def train(self, **evs):
        self.main.update_stats(ev={**self.main.ev, **evs})
        return self.bridge.sync_active_pokemon_stats(self.state, self.main)

    def test_training_reaches_next_review_without_resetting_combat(self):
        active = self.state.user.active
        user_side = self.state.user
        enemy = self.state.opponent.active
        moves = active.moves
        active.hp = self.main.hp = self.main.current_hp = 100
        active.status = "brn"
        active.attack_boost = 2
        active.defense_boost = -1
        active.volatile_status.add("substitute")
        active.item = None  # An item already spent in this battle stays spent.
        self.main.held_item = "Leftovers"
        self.state.user.side_conditions["reflect"] = 3
        self.state.user.wish = (2, 40)
        self.state.user.future_sight = (2, 50)
        self.state.weather = "raindance"
        self.state.field = "electricterrain"
        self.state.trick_room = True
        before = copy.deepcopy(self.state)

        self.assertEqual(active.attack, 146)
        self.assertTrue(self.train(atk=252, hp=252))
        self.assertIs(self.state.user, user_side)
        self.assertIs(self.state.user.active, active)
        self.assertIs(self.state.opponent.active, enemy)
        self.assertIs(active.moves, moves)
        self.assertEqual(active.attack, 209)
        self.assertEqual(active.maxhp, 274)
        self.assertEqual(active.evs, (252, 252, 0, 0, 0, 0))
        for field in (
            "hp",
            "status",
            "attack_boost",
            "defense_boost",
            "volatile_status",
            "item",
            "types",
            "moves",
            "terastallized",
        ):
            self.assertEqual(getattr(active, field), getattr(before.user.active, field))
        for field in ("side_conditions", "wish", "future_sight"):
            self.assertEqual(getattr(user_side, field), getattr(before.user, field))
        for field in ("weather", "field", "trick_room"):
            self.assertEqual(getattr(self.state, field), getattr(before, field))

        # A repeated notification must not apply the EV gain a second time.
        self.assertTrue(self.bridge.sync_active_pokemon_stats(self.state, self.main))
        self.assertEqual(active.attack, 209)

        seen = []
        original = self.bridge.get_all_state_instructions

        def observe(mutator, *args):
            seen.append(
                (mutator.state.user.active.attack, mutator.state.user.active.maxhp)
            )
            return original(mutator, *args)

        self.bridge.get_all_state_instructions = observe
        try:
            result = self.driver.answer("good")
        finally:
            self.bridge.get_all_state_instructions = original
        self.assertEqual(seen, [(209, 274)])
        self.assertTrue(any(event["type"] == "battle" for event in result), result)
        self.assertFalse(any(event["type"] == "error" for event in result), result)
        self.assertEqual(self.loop._state.mutator_full_reset, 0)

    def test_same_species_other_individual_cannot_update_stale_state(self):
        other = build_pokemon({"species": "Pikachu", "level": 100})
        other.update_stats(ev={**other.ev, "atk": 252})
        before = repr(self.state)
        self.assertFalse(self.bridge.sync_active_pokemon_stats(self.state, other))
        self.assertEqual(repr(self.state), before)

    def test_next_reviewer_rounds_report_inherited_combat_state_changes(self):
        before = copy.deepcopy(self.state)
        self.assertTrue(self.train(atk=252))
        training_changes = self.bridge.diff_states(before, self.state)
        self.assertIn("user.active.attack", {c["key"] for c in training_changes})
        self.assertFalse(
            any(c["key"].startswith("_ankimon_") for c in training_changes)
        )

        reported = []
        original = self.loop.simulate_battle_with_poke_engine

        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            reported.append({change["key"]: change for change in result[5]})
            return result

        self.loop.simulate_battle_with_poke_engine = observe
        try:
            for move, key, expected_after in (
                ("tackle", "opponent.active.hp", None),
                ("glare", "opponent.active.status", "par"),
                ("swordsdance", "user.active.attack_boost", 2),
                ("defog", "field", None),
                ("sunnyday", "weather", "sunnyday"),
            ):
                with self.subTest(move=move):
                    self.main.attacks = [move]
                    if move == "defog":
                        self.loop._state.new_state.field = "electricterrain"
                    events = self.driver.answer("good")
                    self.assertFalse(any(e["type"] == "error" for e in events), events)
                    self.assertIn(key, reported[-1])
                    change = reported[-1][key]
                    if key.endswith(".hp"):
                        self.assertLess(change["after"], change["before"])
                    else:
                        self.assertEqual(change["after"], expected_after)
        finally:
            self.loop.simulate_battle_with_poke_engine = original

    def test_switching_same_species_rebinds_engine_owner(self):
        old_individual_id = self.main.individual_id
        self.main.individual_id = "another-pikachu"
        self.driver.answer("good")
        self.state = self.loop._state.new_state
        self.assertNotEqual(self.state._ankimon_main_individual_id, old_individual_id)
        self.assertEqual(
            self.state._ankimon_main_individual_id, self.main.individual_id
        )
        self.assertTrue(self.train(atk=252))
        self.assertEqual(self.state.user.active.attack, 209)

    def test_transform_keeps_copied_non_hp_stats(self):
        active = self.state.user.active
        active.volatile_status.add("transform")
        active.attack = 400
        active.defense = 500
        active.special_attack = 600
        active.special_defense = 700
        active.speed = 800
        active.hp = 0
        active.status = "fainted"
        self.assertTrue(self.train(atk=252, hp=252))
        self.assertEqual(
            (
                active.attack,
                active.defense,
                active.special_attack,
                active.special_defense,
                active.speed,
            ),
            (400, 500, 600, 700, 800),
        )
        self.assertEqual(active.hp, 0)
        self.assertEqual(active.maxhp, 274)
        self.assertEqual(active.status, "fainted")

    def test_training_preserves_live_stance_and_its_nature_rounding(self):
        replacement = build_pokemon(
            {
                "species": "Aegislash",
                "level": 73,
                "nature": "modest",
                "moves": ["Shadow Ball"],
            }
        )
        self.main.update_stats(**replacement.to_dict())
        self.driver.answer("good")
        self.state = self.loop._state.new_state
        active = self.state.user.active
        self.assertEqual(active.id, "aegislash")
        # The engine retains Shield's id, but this damaging move selected Blade.
        self.assertEqual((active.attack, active.defense), (210, 100))
        self.assertEqual(self.main.stats["atk"], 90)
        self.assertTrue(self.train(atk=252))
        self.assertEqual((active.attack, active.defense), (252, 100))
        self.assertEqual(self.main.stats["atk"], 131)
        self.main.attacks = ["kingsshield"]
        self.driver.answer("good")
        self.state = self.loop._state.new_state
        self.assertEqual(self.state.user.active.attack, 132)
        self.assertEqual(self.state.user.active.evs, (0, 252, 0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
