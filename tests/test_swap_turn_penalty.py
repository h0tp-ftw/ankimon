"""Swapping the active Pokemon mid-battle costs it the next attack (#975)."""

from pathlib import Path
import subprocess
import sys
import textwrap


_ROOT = Path(__file__).resolve().parents[1]
_BOOT = """
from harness.driver import Driver
from harness.fixtures import build_pokemon
driver = Driver(
    seed={"main": {"species": "Eevee", "level": 30}},
    first_encounter=False,
    settings_overrides={
        "battle.cards_per_round": 1,
        "battle.review_based_damage": False,
        "controls.allow_to_choose_moves": False,
        "gui.pop_up_dialog_message_on_defeat": False,
        "audio.sound_effects": False,
        "audio.battle_sounds": False,
    },
)
s = driver.services
from Ankimon import battle_loop
main = s.main_pokemon
main.attacks = ["tackle"]
s.db.save_main_pokemon(main.to_dict())
# A bulky foe that survives a few hits and never attacks back.
driver.set_enemy(species="Blissey", level=100, moves=["Splash"])
# One round in: the battle is under way, so a swap from here costs a turn.
driver.answer("good")
driver.drain_events()

def battle_rounds(events):
    return [e for e in events if e["type"] == "battle"]
"""


def _run(code):
    result = subprocess.run(
        [sys.executable, "-c", _BOOT + textwrap.dedent(code)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_swap_skips_exactly_the_next_attack():
    _run("""
        battle_loop.mark_swap_turn_lost()
        assert battle_loop._state.swap_turn_pending

        first = battle_rounds(driver.answer("good"))
        assert [r["user_move"] for r in first] == ["splash"], first
        assert not battle_loop._state.swap_turn_pending

        second = battle_rounds(driver.answer("good"))
        assert [r["user_move"] for r in second] == ["tackle"], second
    """)


def test_new_encounter_clears_a_pending_penalty():
    _run("""
        battle_loop.mark_swap_turn_lost()
        driver.encounter()
        assert not battle_loop._state.swap_turn_pending
    """)


def test_no_penalty_without_a_live_wild_pokemon():
    _run("""
        s.enemy_pokemon.hp = 0
        battle_loop.mark_swap_turn_lost()
        assert not battle_loop._state.swap_turn_pending
    """)


def test_choosing_the_lead_before_the_first_round_is_free():
    _run("""
        driver.encounter()
        assert s.enemy_pokemon.hp > 0
        battle_loop.mark_swap_turn_lost()
        assert not battle_loop._state.swap_turn_pending

        first = battle_rounds(driver.answer("good"))
        assert [r["user_move"] for r in first] == ["tackle"], first
    """)


def test_resaving_the_active_companion_during_a_double_faint_is_not_a_switch():
    _run("""
        from Ankimon.ankimon_profile_web.profile_data import ProfileData

        other = build_pokemon({"species": "Pikachu", "level": 20})
        s.db.save_pokemon(other.to_dict())
        main_id = str(main.individual_id)
        conn = s.db._get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated_phase2', 'true')"
        )
        conn.commit()
        profile = ProfileData(None, None, s.settings, s.logger)

        # Both fainted in manual mode: the main's faint waits on the enemy choice.
        main.hp = main.current_hp = 0
        battle_loop._main_faint_deferred = True
        battle_loop._main_faint_context = (main, main.individual_id)

        result = profile.handle_save_team(
            [main_id, str(other.individual_id)], None, main_id
        )
        assert result["ok"], result
        assert main.hp == main.current_hp == 0
        assert battle_loop._main_faint_deferred
        assert [row["individual_id"] for row in s.db.get_team()] == [
            main_id,
            str(other.individual_id),
        ]
    """)


def test_team_select_companion_change_costs_a_turn_but_resave_does_not():
    _run("""
        from Ankimon.ankimon_profile_web.profile_data import ProfileData

        other = build_pokemon({"species": "Pikachu", "level": 20})
        s.db.save_pokemon(other.to_dict())
        main_id = str(main.individual_id)
        other_id = str(other.individual_id)
        # Real installs are migrated; the headless profile starts unmigrated,
        # which would make update_main_pokemon fall back to the legacy JSON.
        conn = s.db._get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated_phase2', 'true')"
        )
        conn.commit()
        profile = ProfileData(None, None, s.settings, s.logger)

        result = profile.handle_save_team([main_id, other_id], None, main_id)
        assert result["ok"], result
        assert not battle_loop._state.swap_turn_pending

        result = profile.handle_save_team([main_id, other_id], None, other_id)
        assert result["ok"], result
        assert str(s.main_pokemon.individual_id) == other_id
        assert battle_loop._state.swap_turn_pending
    """)
