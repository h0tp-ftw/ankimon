"""Smart Battle's move scorer agrees with what the engine actually does.

Real poke_engine, real Pokemon built from the game's pokedex (via the headless
harness), no stubs: each case pins a matchup where the damage calculator alone
predicts damage that the battle engine never deals.
"""

from pathlib import Path
import subprocess
import sys
import textwrap


_ROOT = Path(__file__).resolve().parents[1]
_BOOT = """
from harness.driver import Driver
driver = Driver(first_encounter=False)
from harness.fixtures import build_pokemon
from Ankimon.functions.smart_move_selection import choose_best_move, score_moves

def mon(species, moves, level=50, **kw):
    return build_pokemon({"species": species, "level": level, "moves": moves, **kw})

def scores(attacker, defender, enemy_move="splash"):
    return {m: s for m, s, _ in score_moves(attacker, defender, enemy_move)}
"""


def _run(code):
    result = subprocess.run(
        [sys.executable, "-c", _BOOT + textwrap.dedent(code)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_sucker_punch_is_not_picked_against_a_target_that_is_not_attacking():
    # The wild Pokemon uses Splash on every correct answer, and Sucker Punch
    # fails against a target that is not attacking.
    _run("""
        absol = mon("Absol", ["suckerpunch", "slash"])
        snorlax = mon("Snorlax", ["tackle"])
        assert scores(absol, snorlax, "splash")["suckerpunch"] == 0
        assert choose_best_move(absol, snorlax, "splash", fallback="suckerpunch") == "slash"
        # Against an attacking target it works, and it is the stronger move.
        assert scores(absol, snorlax, "tackle")["suckerpunch"] > 0
        assert choose_best_move(absol, snorlax, "tackle", fallback="slash") == "suckerpunch"
    """)


def test_electric_moves_score_nothing_into_ground_types():
    # The engine freezes Electric-into-Ground (0 damage); the calculator alone
    # reports 1/8, enough to beat a resisted Quick Attack.
    _run("""
        pikachu = mon("Pikachu", ["thunderbolt", "quickattack", "thunderwave", "growl"])
        golem = mon("Golem", ["tackle"])
        assert scores(pikachu, golem)["thunderbolt"] == 0
        assert choose_best_move(pikachu, golem, fallback="thunderbolt") == "quickattack"
        # Non-Ground targets are unaffected.
        gyarados = mon("Gyarados", ["tackle"])
        assert choose_best_move(pikachu, gyarados, fallback="quickattack") == "thunderbolt"
    """)


def test_sound_moves_are_not_picked_against_soundproof():
    _run("""
        exploud = mon("Exploud", ["hypervoice", "bite"])
        voltorb = mon("Voltorb", ["tackle"], ability="soundproof")
        assert scores(exploud, voltorb)["hypervoice"] == 0
        assert choose_best_move(exploud, voltorb, fallback="hypervoice") == "bite"
        # Without Soundproof, Hyper Voice (STAB, 90 power) is the better move.
        plain = mon("Voltorb", ["tackle"], ability="static")
        assert choose_best_move(exploud, plain, fallback="bite") == "hypervoice"
    """)


def test_half_hp_recoil_moves_are_never_picked():
    # Like Mind Blown: 50% of max HP per use, so every-turn use faints the user.
    _run("""
        venusaur = mon("Venusaur", ["chloroblast", "tackle"])
        aggron = mon("Aggron", ["steelbeam", "tackle"])
        rattata = mon("Rattata", ["tackle"])
        assert choose_best_move(venusaur, rattata, fallback="chloroblast") == "tackle"
        assert choose_best_move(aggron, rattata, fallback="steelbeam") == "tackle"
    """)
