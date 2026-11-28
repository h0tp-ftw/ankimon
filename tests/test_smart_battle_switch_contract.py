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
    # Decide the switch without depending on the random wild Pokemon.
    sts.matchup_score = lambda pkmn, enemy: 1.0
    sts.pick_best = lambda cands, enemy, current_id=None, **kw: target
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


def test_level_balancing_never_pins_the_team_on_a_member_that_cannot_attack():
    # A Splash-only Magikarp far below the rest used to set the level floor,
    # so it was sent out at every encounter and could never win.
    _run("""
        from harness.fixtures import build_pokemon
        karp = build_pokemon({"species": "Magikarp", "level": 10, "moves": ["splash"]})
        s.db.save_pokemon(karp.to_dict())
        s.db.save_team([{"individual_id": i} for i in (PIKA, karp.individual_id, GOLEM)])
        s.settings.set("battle.team_level_gap", 5)
        driver.set_enemy(species="Rattata", level=40, moves=["tackle"])
        sts.smart_switch_for_new_enemy(main, s.enemy_pokemon, s.settings)
        assert main.individual_id != karp.individual_id, main.name
    """)


def test_switch_does_not_copy_the_outgoing_nature_onto_a_sparse_row():
    # Monthly-challenge, trade and fossil rows (and old migrated catches) can
    # lack nature/evolution_rejected. The incoming Pokemon is built on a copy
    # of the outgoing one, so those keys used to inherit Pikachu's values and
    # be saved into Golem's row for good.
    _run("""
        main.nature = "adamant"
        main.evolution_rejected = True
        s.db.save_main_pokemon(main.to_dict())
        sparse = {k: v for k, v in row(GOLEM).items()
                  if k not in ("nature", "evolution_rejected")}
        s.db.save_pokemon(sparse)
        assert "nature" not in row(GOLEM) and "evolution_rejected" not in row(GOLEM)
        force_switch_to(GOLEM)
        driver.encounter()

        assert main.individual_id == GOLEM
        assert (main.nature, main.evolution_rejected) == ("serious", False)
        assert row(GOLEM)["nature"] == "serious"
        assert row(GOLEM)["evolution_rejected"] is False
        assert (row(PIKA)["nature"], row(PIKA)["evolution_rejected"]) == ("adamant", True)
    """)


def test_a_lost_swap_turn_skips_the_smart_move_choice():
    # A swap mid-battle costs the next attack; Smart must not pick a move for
    # that round, and must pick again on the round after.
    _run("""
        force_switch_to(None)
        driver.set_enemy(species="Blissey", level=100, moves=["splash"])
        driver.answer("good")
        picks = []
        real_choose = battle_loop.choose_best_move
        def counting_choose(*a, **k):
            picks.append(1)
            return real_choose(*a, **k)
        battle_loop.choose_best_move = counting_choose

        battle_loop.mark_swap_turn_lost()
        assert battle_loop._state.swap_turn_pending
        lost = [e for e in driver.answer("good") if e["type"] == "battle"]
        assert [r["user_move"] for r in lost] == ["splash"] and picks == [], (lost, picks)
        after = [e for e in driver.answer("good") if e["type"] == "battle"]
        assert [r["user_move"] for r in after] == ["thunderbolt"] and picks == [1], after
    """)


def test_team_ids_are_compared_as_strings():
    # A non-str individual id on the live main must still match its team slot,
    # or the live main would be reloaded from its (stale) row and scored twice.
    _run("""
        import uuid
        main.individual_id = uuid.UUID(PIKA)
        seen = []
        sts.pick_best = lambda cands, enemy, current_id=None, **kw: seen.append(
            (current_id, [(i, p is main) for i, p in cands])) or None
        sts.matchup_score = lambda pkmn, enemy: 1.0
        driver.encounter()
        current_id, cands = seen[0]
        assert current_id == PIKA, seen
        assert [is_live for i, is_live in cands if i == PIKA] == [True], seen
    """)


def test_switch_does_not_pass_the_outgoing_status_to_the_incoming_pokemon():
    # battle_status is not saved with a team row, so the copy used to keep
    # the outgoing Pokemon's paralysis.
    _run("""
        main.battle_status = "par"
        force_switch_to(GOLEM)
        driver.encounter()
        assert main.individual_id == GOLEM
        assert main.battle_status == "fighting", main.battle_status
    """)


_LEVEL_ROLL = """
from harness.driver import Driver
SMART = {smart}
driver = Driver(
    seed={{
        "main": {{"species": "Dragonite", "level": 80, "moves": ["outrage", "dragonclaw"]}},
        "team": [{{"species": "Pikachu", "level": 20, "moves": ["thunderbolt", "quickattack"]}},
                 {{"species": "Bulbasaur", "level": 21, "moves": ["vinewhip", "tackle"]}}],
    }},
    first_encounter=False,
    settings_overrides={{"battle.smart_battle": SMART, "battle.team_level_gap": 5}},
)
s = driver.services
driver.encounter()
print("ENCOUNTER", s.enemy_pokemon.level, s.main_pokemon.name, s.main_pokemon.level)
"""


def _first_encounter(smart):
    result = subprocess.run(
        [sys.executable, "-c", _LEVEL_ROLL.format(smart=smart)],
        cwd=_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    line = [l for l in result.stdout.splitlines() if l.startswith("ENCOUNTER")][-1]
    _, enemy_level, lead, lead_level = line.split()
    return int(enemy_level), lead, int(lead_level)


def test_wild_level_is_rolled_for_the_pokemon_that_will_fight_it():
    # Dragonite Lv80 is benched by level balancing (gap 5) in favour of the
    # Lv20-21 members; the wild Pokemon used to be rolled at Lv77-83 anyway.
    for _ in range(3):
        enemy_level, lead, lead_level = _first_encounter(smart=True)
        assert lead in ("Pikachu", "Bulbasaur"), lead
        assert enemy_level <= lead_level + 5 + 3, (enemy_level, lead, lead_level)
    # Smart Battle off: unchanged, rolled at the active Pokemon's level.
    enemy_level, lead, _ = _first_encounter(smart=False)
    assert lead == "Dragonite" and enemy_level >= 77 - 5, enemy_level
