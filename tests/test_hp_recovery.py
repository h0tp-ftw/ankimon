"""Healing items scale with max HP, and wounded Pokemon regenerate while resting."""

import importlib
from pathlib import Path
import subprocess
import sys
import textwrap
import threading

import pytest

from conftest import isolated_modules

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from Ankimon.functions.hp_recovery import (  # noqa: E402
    HEAL_ITEMS,
    REGEN_FULL_HEAL_SECONDS,
    heal_item_amount,
    heal_item_summary,
    keep_rest_clock,
    regenerate,
)

HOUR = 60 * 60


# --------------------------------------------------------------------------- #
# Healing items
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "item, max_hp, expected",
    [
        ("potion", 40, 20),  # the classic flat amount is the floor
        ("potion", 300, 75),
        ("super-potion", 300, 150),
        ("hyper-potion", 300, 240),
        ("max-potion", 300, 1000),
        ("full-restore", 700, 1000),
        ("potion", 301, 76),  # rounds up
    ],
)
def test_heal_items_restore_a_share_of_max_hp(item, max_hp, expected):
    assert heal_item_amount(item, max_hp) == expected


def test_stronger_items_never_heal_less():
    by_strength = sorted(HEAL_ITEMS, key=lambda name: HEAL_ITEMS[name])
    for max_hp in (12, 45, 150, 400, 714):
        amounts = [heal_item_amount(name, max_hp) for name in by_strength]
        assert amounts == sorted(amounts), (max_hp, amounts)


def test_an_unknown_item_heals_only_the_flat_amount_it_was_given():
    assert heal_item_amount("mystery-tonic", 300, flat=45) == 45
    assert heal_item_amount("mystery-tonic", 300) == 0


def test_heal_item_summary_states_the_rule():
    assert heal_item_summary("potion") == "Restores 25% of max HP (at least 20 HP)."
    assert heal_item_summary("max-potion") == "Restores all HP."
    assert heal_item_summary("poke-ball") == ""


# --------------------------------------------------------------------------- #
# Heal on Swap setting
# --------------------------------------------------------------------------- #
def test_heal_on_swap_is_off_by_default_and_shown_in_the_settings_screen():
    import importlib.util
    import json

    # Read from disk: other suite modules leave mocks under these import names.
    addon = _SRC / "Ankimon"
    spec = importlib.util.spec_from_file_location(
        "_settings_schema_under_test",
        addon / "ankimon_items_web" / "settings_schema.py",
    )
    schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schema)
    defaults = (addon / "pyobj" / "settings.py").read_text(encoding="utf-8")
    names = json.loads(
        (addon / "lang" / "setting_name.json").read_text(encoding="utf-8")
    )
    descriptions = json.loads(
        (addon / "lang" / "setting_description.json").read_text(encoding="utf-8")
    )

    assert '"battle.heal_on_swap": False,' in defaults
    assert names["battle.heal_on_swap"] == "Heal on Swap"
    assert "fully heals" in descriptions["battle.heal_on_swap"]
    battle = next(group for group in schema.GROUPS if group["label"] == "Battle")
    listed = [name for sub in battle["subgroups"] for name in sub["settings"]]
    assert "Heal on Swap" in listed


def test_swap_heals_only_for_an_explicit_true():
    from Ankimon.functions.hp_recovery import swap_heals

    class _Settings:
        def __init__(self, value):
            self.value = value

        def get(self, key, default=None):
            return self.value if key == "battle.heal_on_swap" else default

    assert swap_heals(_Settings(True)) is True
    assert swap_heals(_Settings(False)) is False
    assert swap_heals(_Settings("yes")) is False
    assert swap_heals(None) is False


# --------------------------------------------------------------------------- #
# Regeneration rule
# --------------------------------------------------------------------------- #
def test_half_the_full_heal_time_restores_half_of_max_hp():
    hp, regen_at = regenerate(10, 200, 1000.0, 1000.0 + REGEN_FULL_HEAL_SECONDS / 2)
    assert hp == 110
    assert regen_at == pytest.approx(1000.0 + REGEN_FULL_HEAL_SECONDS / 2)


def test_a_full_rest_heals_completely_and_banks_nothing():
    now = 5000.0 + 3 * REGEN_FULL_HEAL_SECONDS
    assert regenerate(1, 200, 5000.0, now) == (200, now)


@pytest.mark.parametrize("regen_at", [None, "yesterday", True, float("nan")])
def test_a_missing_clock_starts_now_without_a_gain(regen_at):
    assert regenerate(10, 200, regen_at, 9000.0) == (10, 9000.0)


def test_a_clock_in_the_future_restarts_without_a_gain():
    assert regenerate(10, 200, 9999.0, 9000.0) == (10, 9000.0)


def test_a_fainted_pokemon_does_not_regenerate():
    assert regenerate(0, 200, 0.0, 10 * HOUR) == (0, 10 * HOUR)


def test_time_at_full_hp_is_not_banked():
    assert regenerate(200, 200, 0.0, 10 * HOUR) == (200, 10 * HOUR)


def test_frequent_ticks_add_up_to_one_long_rest():
    hp, regen_at = 10, 0.0
    for tick in range(1, 241):  # a tick a minute for four hours
        hp, regen_at = regenerate(hp, 200, regen_at, tick * 60.0)
    assert hp == regenerate(10, 200, 0.0, 4 * HOUR)[0] == 110


def test_keep_rest_clock_follows_current_hp(monkeypatch):
    import Ankimon.functions.hp_recovery as recovery

    monkeypatch.setattr(recovery, "current_timestamp", lambda: 7000.0)

    wounded = {"current_hp": 40}
    keep_rest_clock(wounded, 200)
    assert wounded["hp_regen_at"] == 7000.0

    resting = {"current_hp": 40, "hp_regen_at": 1000.0}
    keep_rest_clock(resting, 200)
    assert resting["hp_regen_at"] == 1000.0, "a rest under way is not restarted"

    healthy = {"current_hp": 200, "hp_regen_at": 1000.0}
    keep_rest_clock(healthy, 200)
    assert "hp_regen_at" not in healthy

    fainted = {"current_hp": 0}
    keep_rest_clock(fainted, 200)
    assert "hp_regen_at" not in fainted


# --------------------------------------------------------------------------- #
# Saved rows
# --------------------------------------------------------------------------- #
@pytest.fixture
def rest_db(tmp_path, monkeypatch):
    """A real AnkimonDB holding a level 50 Pikachu: max HP 102."""
    with isolated_modules(
        "aqt", extra=("Ankimon.resources", "Ankimon.pyobj.database_manager")
    ):
        sys.modules["aqt"] = None
        module = importlib.import_module("Ankimon.pyobj.database_manager")
        monkeypatch.setattr(
            module,
            "_is_main_thread",
            lambda: threading.current_thread() is threading.main_thread(),
        )
        db = module.AnkimonDB(db_path=tmp_path / "rest.db")
        for individual_id, is_main in (("main", True), ("benched", False)):
            record = {
                "individual_id": individual_id,
                "id": 25,
                "name": "pikachu",
                "level": 50,
                "base_stats": {"hp": 35},
                "current_hp": 102,
            }
            (db.save_main_pokemon if is_main else db.save_pokemon)(record)
        try:
            yield db
        finally:
            db.close()


def _wound(db, individual_id, hp, regen_at):
    row = db.get_pokemon(individual_id)
    row.update(current_hp=hp, hp_regen_at=regen_at)
    db.save_pokemon(row)


def test_the_rest_clock_starts_when_a_pokemon_is_first_hurt(rest_db, monkeypatch):
    recovery = importlib.import_module("Ankimon.functions.hp_recovery")
    monkeypatch.setattr(recovery, "current_timestamp", lambda: 1000.0)
    assert "hp_regen_at" not in rest_db.get_pokemon("main")

    assert rest_db.update_pokemon_current_hp("main", 60)
    assert rest_db.get_pokemon("main")["hp_regen_at"] == 1000.0

    # Further damage does not restart a rest that is already under way.
    monkeypatch.setattr(recovery, "current_timestamp", lambda: 2000.0)
    assert rest_db.update_pokemon_current_hp("main", 40)
    assert rest_db.get_pokemon("main")["hp_regen_at"] == 1000.0

    # Back at full HP and hurt again: a new rest starts.
    assert rest_db.update_pokemon_current_hp("main", 102)
    monkeypatch.setattr(recovery, "current_timestamp", lambda: 3000.0)
    assert rest_db.update_pokemon_current_hp("main", 90)
    assert rest_db.get_pokemon("main")["hp_regen_at"] == 3000.0


def test_rest_is_credited_to_the_saved_row(rest_db):
    _wound(rest_db, "benched", 20, 1000.0)

    gained = rest_db.regenerate_pokemon_hp("benched", now=1000.0 + 4 * HOUR)

    assert gained == {"benched": 51}  # half of 102 in half the full-heal time
    assert rest_db.get_pokemon("benched")["current_hp"] == 71
    # The same instant credits nothing twice.
    assert rest_db.regenerate_pokemon_hp("benched", now=1000.0 + 4 * HOUR) == {}
    assert rest_db.regenerate_pokemon_hp("benched", now=1000.0 + 9 * HOUR) == {
        "benched": 31
    }
    healed = rest_db.get_pokemon("benched")
    assert healed["current_hp"] == 102
    assert "hp_regen_at" not in healed


def test_a_healthy_pokemon_never_keeps_a_rest_clock(rest_db):
    """Otherwise its next wound would be credited with time spent at full HP."""
    row = rest_db.get_pokemon("benched")
    row.update(current_hp=102, hp_regen_at=1000.0)
    rest_db.save_pokemon(row)

    assert "hp_regen_at" not in rest_db.get_pokemon("benched")


def test_a_sweep_credits_every_wounded_pokemon_and_leaves_the_rest_alone(rest_db):
    _wound(rest_db, "benched", 20, 1000.0)
    healthy_before = rest_db.get_pokemon("main")

    assert rest_db.regenerate_pokemon_hp(now=1000.0 + 2 * HOUR) == {"benched": 25}

    assert rest_db.get_pokemon("benched")["current_hp"] == 45
    assert rest_db.get_pokemon("main") == healthy_before


def test_a_wounded_row_without_a_clock_starts_resting_without_a_gain(rest_db):
    row = rest_db.get_pokemon("benched")
    row["current_hp"] = 20
    rest_db.save_pokemon(row)

    assert rest_db.regenerate_pokemon_hp(now=5000.0) == {}
    saved = rest_db.get_pokemon("benched")
    assert (saved["current_hp"], saved["hp_regen_at"]) == (20, 5000.0)
    assert rest_db.regenerate_pokemon_hp(now=5000.0 + 8 * HOUR) == {"benched": 82}


def test_a_fainted_row_is_left_alone(rest_db):
    _wound(rest_db, "benched", 0, 1000.0)
    before = rest_db.get_pokemon("benched")

    assert rest_db.regenerate_pokemon_hp(now=1000.0 + 8 * HOUR) == {}
    assert rest_db.get_pokemon("benched") == before


def test_regeneration_refuses_a_callers_open_transaction(rest_db):
    _wound(rest_db, "benched", 20, 1000.0)
    raw = rest_db._get_connection()._conn
    raw.execute("INSERT INTO metadata (key, value) VALUES ('pending', 'value')")
    try:
        assert rest_db.regenerate_pokemon_hp(now=1000.0 + 8 * HOUR) == {}
        assert raw.in_transaction
    finally:
        raw.rollback()
    assert rest_db.get_pokemon("benched")["current_hp"] == 20


# --------------------------------------------------------------------------- #
# In play (headless harness with a controlled clock)
# --------------------------------------------------------------------------- #
_ROOT = Path(__file__).resolve().parents[1]
_BOOT = """
from datetime import datetime
from harness.driver import Driver
from harness.fixtures import build_pokemon
driver = Driver(
    seed={"main": {"species": "Snorlax", "level": 50}},
    first_encounter=False,
    clock_start=datetime(2026, 6, 1, 12, 0),
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
main.attacks = ["splash"]
s.db.save_main_pokemon(main.to_dict())
main_id = str(main.individual_id)
# A foe that never hurts back, so only rest can change the main's HP.
driver.set_enemy(species="Blissey", level=100, moves=["Splash"])
driver.answer("good")
driver.drain_events()

def hurt(hp):
    main.hp = main.current_hp = hp
    assert s.db.update_pokemon_current_hp(main_id, hp)
    battle_loop.sync_active_pokemon_hp(battle_loop._state.new_state, main)
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


def test_the_active_pokemon_regenerates_between_rounds():
    _run("""
        hurt(10)
        driver.advance_time(hours=4)
        events = driver.answer("good")

        expected = 10 + main.max_hp // 2
        assert abs(main.hp - expected) <= 1, (main.hp, expected)
        assert main.hp == main.current_hp
        assert [e["gained"] for e in events if e["type"] == "regen"] == [main.hp - 10]
        # The engine fights on with the regenerated HP instead of writing 10 back.
        driver.answer("good")
        assert abs(main.hp - expected) <= 1, main.hp
        assert not [e for e in events if e["type"] == "error"]
    """)


def test_a_night_away_fully_heals_and_a_few_cards_do_not():
    _run("""
        hurt(10)
        for _ in range(5):
            driver.answer("good")
        assert main.hp == 10

        driver.advance_time(hours=9)
        driver.answer("good")
        assert main.hp == main.max_hp
    """)


def test_a_fainted_active_pokemon_does_not_regenerate():
    _run("""
        hurt(0)
        driver.advance_time(hours=9)
        battle_loop._credit_rest_to_active(main, battle_loop._state.new_state)
        assert main.hp == 0
    """)


def test_a_wound_first_saved_by_a_defeat_starts_resting():
    """The last card of a session can hurt the main and end the fight at once."""
    _run("""
        driver.set_enemy(species="Magikarp", level=1, moves=["Splash"])
        assert "hp_regen_at" not in s.db.get_pokemon(main_id)
        main.hp = main.current_hp = 50  # hurt in the final round, not yet saved
        s.enemy_pokemon.hp = s.enemy_pokemon.current_hp = 0
        driver.defeat()

        saved = s.db.get_pokemon(main_id)
        assert saved["current_hp"] == 50 and "hp_regen_at" in saved, saved
        driver.advance_time(hours=9)
        assert s.db.regenerate_pokemon_hp() == {main_id: main.max_hp - 50}
    """)


def test_a_benched_pokemon_has_rested_by_the_time_it_is_loaded():
    _run("""
        from Ankimon.functions.update_main_pokemon import update_main_pokemon

        conn = s.db._get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated_phase2', 'true')"
        )
        conn.commit()
        hurt(10)
        driver.advance_time(hours=2)

        update_main_pokemon(main)

        assert main.hp == 10 + main.max_hp // 4
        assert s.db.get_pokemon(main_id)["current_hp"] == main.hp
    """)
