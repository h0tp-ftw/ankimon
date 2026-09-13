"""Real-state regressions for synchronous level-up stats and XP Share (#648)."""

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


class LevelupStatsTests(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory(prefix="ankimon_levelup_")
        self.addCleanup(self.profile.cleanup)
        self.driver = Driver(
            user_path=self.profile.name,
            seed={"main": {"species": "Pikachu", "level": 50, "hp": 20}},
            settings_overrides={
                "misc.remove_level_cap": False,
                "gui.pop_up_dialog_message_on_defeat": False,
                "audio.sound_effects": False,
            },
            first_encounter=False,
        )
        self.s = self.driver.services
        self.addCleanup(self.s.db.close)
        self.mp = self.s.main_pokemon
        self.mp.hp = self.mp.current_hp = 20
        self.s.db.save_main_pokemon(self.mp.to_dict())

    def xp_for_levels(self, count):
        from Ankimon.functions.pokemon_functions import find_experience_for_level

        return 1 + sum(
            int(find_experience_for_level(self.mp.growth_rate, level, False))
            for level in range(self.mp.level, self.mp.level + count)
        )

    def assert_live_stats(self, level, hp_ev, original_hp=20):
        self.assertEqual(self.mp.level, level)
        self.assertEqual(self.mp.ev["hp"], hp_ev)
        expected_hp = 10 + level + ((2 * 35 + 31 + hp_ev // 4) * level // 100)
        self.assertEqual(self.mp.max_hp, expected_hp)
        self.assertEqual(self.mp.to_engine_format()["maxhp"], expected_hp)
        self.assertEqual(self.mp.hp, original_hp, "Recalculation must not heal")
        self.assertEqual(self.mp.current_hp, original_hp)
        row = self.s.db.get_main_pokemon()
        self.assertEqual(row["level"], level)
        self.assertEqual(row["ev"], self.mp.ev)
        self.assertEqual(row["stats"], self.mp.stats)
        self.assertEqual(row["base_stats"], self.mp.base_stats)

    def desktop_award(self, xp, hp_ev):
        from Ankimon.functions.encounter_functions import save_main_pokemon_progress

        self.s.enemy_pokemon.ev_yield = {"hp": hp_ev}
        save_main_pokemon_progress(
            self.mp, self.s.enemy_pokemon, xp, self.s.achievements,
            self.s.logger, self.s.evo_window,
        )

    def test_desktop_levelup_refreshes_before_observers(self):
        seen = []
        self.driver.events.enable(sink=lambda e: seen.append(
            (self.mp.level, self.mp.max_hp)
        ) if e["type"] == "levelup" else None)
        self.desktop_award(self.xp_for_levels(2), 0)
        self.assertEqual(seen, [(51, 112), (52, 114)])
        self.assert_live_stats(52, 0)

    def test_desktop_levelup_includes_new_evs(self):
        self.desktop_award(self.xp_for_levels(2), 8)
        self.assert_live_stats(52, 8)

    def test_desktop_ev_only_gain_refreshes_stats(self):
        self.desktop_award(1, 8)
        self.assert_live_stats(50, 8)

    def test_desktop_capped_fainted_pokemon_stays_fainted(self):
        self.mp.update_stats(level=100, hp=0, current_hp=0, battle_status="fainted")
        self.s.db.save_main_pokemon(self.mp.to_dict())
        self.desktop_award(1000, 8)
        self.assert_live_stats(100, 8, original_hp=0)
        self.assertEqual(self.mp.battle_status, "fainted")

    def mobile_award(self, xp, hp_ev, companion_id=None):
        from Ankimon.functions.mobile_sync import _attribute_xp_and_evs_to_companion
        from Ankimon import utils

        previous = getattr(utils, "in_bulk_resolve", False)
        utils.in_bulk_resolve = True
        try:
            _attribute_xp_and_evs_to_companion(
                companion_id or self.mp.individual_id, xp, {"hp": hp_ev},
                self.s.settings, db=self.s.db, logger=self.s.logger,
            )
        finally:
            utils.in_bulk_resolve = previous

    def test_mobile_levelup_includes_new_evs(self):
        self.mobile_award(self.xp_for_levels(2), 8)
        self.assert_live_stats(52, 8)

    def test_mobile_ev_only_gain_refreshes_stats(self):
        self.mobile_award(0, 8)
        self.assert_live_stats(50, 8)

    def test_mobile_inactive_companion_does_not_mutate_main(self):
        companion = build_pokemon({"species": "Pikachu", "level": 50})
        self.s.db.save_pokemon(companion.to_dict())
        before = copy.deepcopy(self.mp.to_dict())
        self.mobile_award(self.xp_for_levels(2), 8, companion.individual_id)
        self.assertEqual(self.mp.to_dict(), before)
        saved = self.s.db.get_pokemon(companion.individual_id)
        self.assertEqual(saved["level"], 52)
        self.assertEqual(saved["ev"]["hp"], 8)
        self.assertEqual(saved["stats"]["hp"], 115)

    def test_defeat_excludes_active_pokemon_from_xp_share(self):
        from Ankimon.functions.encounter_functions import kill_pokemon

        self.driver.set_enemy(species="Chansey", level=100)
        self.mp.xp = self.xp_for_levels(1) - 2
        before = copy.deepcopy(self.mp.to_dict())
        self.s.db.save_main_pokemon(before)
        self.s.settings.set("trainer.xp_share", None)
        kill_pokemon(self.mp, self.s.enemy_pokemon, self.s.evo_window,
                     self.s.logger, self.s.achievements, self.s.trainer_card)
        expected = (self.mp.level, self.mp.xp)
        self.mp.update_stats(**before)
        self.s.db.save_main_pokemon(before)
        self.s.settings.set("trainer.xp_share", self.mp.individual_id)
        kill_pokemon(self.mp, self.s.enemy_pokemon, self.s.evo_window,
                     self.s.logger, self.s.achievements, self.s.trainer_card)
        self.assertEqual((self.mp.level, self.mp.xp), expected)

    def test_xp_share_still_rewards_another_individual_of_same_species(self):
        from Ankimon.functions.trainer_functions import xp_share_gain_exp

        companion = build_pokemon({"species": "Pikachu", "level": 50})
        self.s.db.save_pokemon(companion.to_dict())
        before = copy.deepcopy(self.mp.to_dict())
        xp = 2 * self.xp_for_levels(1)
        remaining = xp_share_gain_exp(
            self.s.logger, self.s.settings, self.s.evo_window,
            self.mp.individual_id, xp, companion.individual_id,
        )
        self.assertEqual(remaining, xp // 2)
        self.assertEqual(self.s.db.get_pokemon(companion.individual_id)["level"], 51)
        self.assertEqual(self.mp.to_dict(), before)


if __name__ == "__main__":
    unittest.main()
