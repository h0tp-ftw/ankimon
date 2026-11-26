"""Smart Battle's automatic switch keeps the #975 swap contract.

The switch at each new wild encounter must treat HP exactly like the Cycle
Team hotkey: save the outgoing Pokemon's live HP, credit the incoming one's
bench rest, honour the classic heal-on-swap rule, and cost no turn (the
encounter has not started). These run the real new_pokemon() path against a
real database through the headless harness.
"""

from pathlib import Path
import subprocess
import sys
import textwrap


_ROOT = Path(__file__).resolve().parents[1]
_BOOT = """
import datetime
from harness.driver import Driver

HEAL_ON_SWAP = {heal_on_swap}
driver = Driver(
    seed={{
        "main": {{"species": "Pikachu", "level": 40, "moves": ["thunderbolt"],
                  "held_item": "light-ball"}},
        "team": [{{"species": "Golem", "level": 40, "moves": ["earthquake"]}}],
    }},
    first_encounter=False,
    clock_start=datetime.datetime(2026, 1, 1, 12, 0, 0),
    settings_overrides={{
        "battle.cards_per_round": 1,
        "battle.smart_battle": True,
        "battle.team_level_gap": 0,
        "battle.heal_on_swap": HEAL_ON_SWAP,
        "gui.pop_up_dialog_message_on_defeat": False,
        "audio.sound_effects": False,
        "audio.battle_sounds": False,
    }},
)
s = driver.services
from Ankimon import battle_loop
import Ankimon.functions.smart_team_switch as sts
main = s.main_pokemon
ids = {{s.db.get_pokemon(e["individual_id"])["name"]: e["individual_id"]
        for e in s.db.get_team()}}
PIKA, GOLEM = ids["Pikachu"], ids["Golem"]
assert main.individual_id == PIKA

def row(iid):
    return s.db.get_pokemon(iid)

def force_switch_to(target):
    sts.pick_best = lambda cands, enemy, current_id=None, margin=1.15: target
"""


def _run(code, heal_on_swap=False):
    script = _BOOT.format(heal_on_swap=heal_on_swap) + textwrap.dedent(code)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_switch_saves_outgoing_hp_and_credits_incoming_rest():
    _run("""
        golem_max = 126
        assert s.db.update_pokemon_current_hp(GOLEM, 40)  # starts its rest clock
        main.hp = main.current_hp = 30                    # live damage, unsaved
        driver.advance_time(hours=4)                      # half a full rest
        force_switch_to(GOLEM)
        driver.encounter()

        assert main.individual_id == GOLEM and main.name == "Golem"
        assert main.held_item is None                     # not Pikachu's Light Ball
        assert row(PIKA)["current_hp"] == 30              # outgoing live HP kept
        assert row(PIKA)["held_item"] == "light-ball"
        # 4h of an 8h full rest = +63 of 126, credited as it comes off the bench.
        assert main.current_hp == row(GOLEM)["current_hp"] == 40 + golem_max // 2
        assert s.db.get_main_pokemon()["individual_id"] == GOLEM
        # Leading a fresh encounter is free: no lost turn.
        assert not battle_loop._state.swap_turn_pending
    """)


def test_heal_on_swap_heals_both_pokemon():
    _run(
        """
        assert s.db.update_pokemon_current_hp(GOLEM, 40)
        pika_max = main.max_hp
        main.hp = main.current_hp = 30
        force_switch_to(GOLEM)
        driver.encounter()

        assert main.individual_id == GOLEM
        assert main.current_hp == main.max_hp == row(GOLEM)["current_hp"]
        assert row(PIKA)["current_hp"] == pika_max
        """,
        heal_on_swap=True,
    )


def test_no_switch_leaves_every_row_and_the_active_pokemon_alone():
    _run("""
        import copy
        assert s.db.update_pokemon_current_hp(GOLEM, 40)
        main.hp = main.current_hp = 30
        before_rows = {i: row(i) for i in (PIKA, GOLEM)}
        keys = ("individual_id", "name", "level", "attacks", "held_item",
                "current_hp", "max_hp", "ev", "iv", "nature", "ability")
        before_main = {k: copy.deepcopy(getattr(main, k, None)) for k in keys}
        force_switch_to(None)
        driver.encounter()

        assert {k: getattr(main, k, None) for k in keys} == before_main
        assert row(GOLEM) == before_rows[GOLEM]
        assert s.db.get_main_pokemon()["individual_id"] == PIKA
    """)


def test_smart_battle_picks_the_super_effective_move():
    _run("""
        main.attacks = ["quickattack", "thunderbolt"]
        s.db.save_main_pokemon(main.to_dict())
        force_switch_to(None)
        driver.set_enemy(species="Gyarados", level=40, moves=["splash"])
        rounds = [e for e in driver.answer("good") if e["type"] == "battle"]
        assert rounds and rounds[0]["user_move"] == "thunderbolt", rounds
    """)
