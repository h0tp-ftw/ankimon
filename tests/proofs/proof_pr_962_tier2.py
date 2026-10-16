"""PR #962: train through real Qt while preserving an ongoing engine battle.

Run with the Tier-2 Python under xvfb-run (or QT_QPA_PLATFORM=offscreen).
Every saved Pokemon, inventory item and SQL trigger belongs to a disposable DB.
"""

import copy
import os
from pathlib import Path
import sys
import tempfile


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def run_proof():
    from PyQt6.QtWidgets import QApplication, QPushButton

    from harness.bootstrap import quiet
    from harness.real_driver import RealDriver

    old_cache = os.environ.get("ANKIMON_SPRITE_CACHE")
    with tempfile.TemporaryDirectory(prefix="ankimon-pr962-proof-") as profile:
        os.environ["ANKIMON_SPRITE_CACHE"] = str(Path(profile) / "unused-cache")
        try:
            with quiet():
                driver = RealDriver(
                    user_path=profile,
                    first_encounter=False,
                    settings_overrides={
                        "battle.cards_per_round": 1,
                        "battle.automatic_battle": 0,
                        "audio.sounds": False,
                        "audio.sound_effects": False,
                        "gui.pop_up_dialog_message_on_item": False,
                        "gui.pop_up_dialog_message_on_defeat": False,
                    },
                )

            from Ankimon import battle_loop
            from Ankimon.functions import ankimon_hooks_to_poke_engine as bridge
            from Ankimon.singletons import get_item_window
            from harness.fixtures import build_pokemon

            db = driver.services.db
            main = driver.services.main_pokemon
            enemy = driver.services.enemy_pokemon
            bag = get_item_window()
            captured_events = []

            def begin_battle(spec):
                main.update_stats(**build_pokemon(spec).to_dict())
                # Serialization omits live combat effects; explicitly start
                # each scenario with an unpoisoned, unboosted companion.
                main.battle_status = "fighting"
                main.volatile_status = set()
                main.reset_bonuses()
                enemy.update_stats(
                    **build_pokemon(
                        {"species": "Blissey", "level": 100, "moves": ["Splash"]}
                    ).to_dict()
                )
                assert db.save_main_pokemon(main.to_dict())
                battle_loop._state.new_state = None
                battle_loop._state.mutator_full_reset = 1
                captured_events.extend(driver.answer(3))
                state = battle_loop._state.new_state
                assert state is not None
                assert battle_loop._state.mutator_full_reset == 0
                return state

            def click_training(item):
                widget = bag.ItemLabel(item, 1, None)
                button = next(
                    child
                    for child in widget.findChildren(QPushButton)
                    if child.text() == "Train Mainpokemon's EVs"
                )
                with quiet():
                    button.click()
                    QApplication.processEvents()
                widget.deleteLater()
                captured_events.extend(driver.drain_events())

            try:
                state = begin_battle(
                    {
                        "species": "Pikachu",
                        "level": 73,
                        "ivs": {
                            "hp": 3,
                            "atk": 7,
                            "def": 11,
                            "spa": 13,
                            "spd": 17,
                            "spe": 19,
                        },
                        "nature": "adamant",
                        "moves": ["Splash"],
                    }
                )
                active = state.user.active
                active.hp = main.hp = main.current_hp = 23
                active.status = main.battle_status = "psn"
                active.attack_boost = main.stat_stages["atk"] = 2
                active.speed_boost = main.stat_stages["spe"] = -1
                active.volatile_status.add("substitute")
                main.volatile_status.add("substitute")
                active.item = None  # A spent held item must stay spent.
                main.held_item = "leftovers"
                state.user.side_conditions["reflect"] = 3
                state.user.wish = (2, 40)
                state.user.future_sight = (2, 50)
                state.weather = "raindance"
                state.field = "electricterrain"
                state.trick_room = True
                saved = main.to_dict()
                saved["proof_metadata"] = {"keep": [1, 2, 3]}
                assert db.save_main_pokemon(saved)
                db.save_item(46, "protein", 1)
                before_live = copy.deepcopy(main.to_dict())
                before_engine = copy.deepcopy(state)
                before_saved = copy.deepcopy(db.get_main_pokemon())

                # Failure must preserve inventory and ALL nested engine fields,
                # including effects hidden in inherited engine slots.
                db.execute(
                    "CREATE TEMP TRIGGER proof_pr962_failure BEFORE UPDATE OF data "
                    "ON captured_pokemon BEGIN SELECT RAISE(ABORT, 'proof failure'); END"
                ).close()
                click_training("protein")
                db.execute("DROP TRIGGER proof_pr962_failure").close()
                db.set_user_data("proof_unrelated_commit", True)
                assert db.get_item("protein")["quantity"] == 1
                assert db.get_main_pokemon() == before_saved
                assert main.to_dict() == before_live
                assert bridge.diff_states(before_engine, state) == []
                assert not db._get_connection().in_transaction

                # Retry the native training action after removing the SQL fault.
                old_intrinsic_attack = main.stats["atk"]
                click_training("protein")
                assert db.get_item("protein") is None
                assert main.ev["atk"] == 10
                assert active.attack == before_engine.user.active.attack + (
                    main.stats["atk"] - old_intrinsic_attack
                )
                assert active.attack > before_engine.user.active.attack
                assert db.get_main_pokemon()["proof_metadata"] == {"keep": [1, 2, 3]}
                assert db.get_main_pokemon()["ev"] == main.ev
                assert state is battle_loop._state.new_state
                assert active is state.user.active
                assert active.status == "psn"
                assert active.attack_boost == 2 and active.speed_boost == -1
                assert active.item is None
                assert "substitute" in active.volatile_status
                assert state.user.side_conditions["reflect"] == 3
                assert state.user.wish == (2, 40)
                assert state.user.future_sight == (2, 50)
                assert state.weather == "raindance"
                assert state.field == "electricterrain" and state.trick_room
                changes = bridge.diff_states(before_engine, state)
                assert {change["key"] for change in changes} == {
                    "user.active.attack",
                    "user.active.evs",
                }, changes
                after_engine = copy.deepcopy(state)
                assert bridge.sync_active_pokemon_stats(state, main)
                assert bridge.diff_states(after_engine, state) == []
                assert main.hp == main.current_hp == active.hp == 23

                # Non-perfect IVs and a nature with rounding must also preserve
                # the engine's live Aegislash Blade form during training.
                state = begin_battle(
                    {
                        "species": "Aegislash",
                        "level": 73,
                        "ivs": 7,
                        "nature": "modest",
                        "moves": ["Shadow Ball"],
                    }
                )
                active = state.user.active
                assert active.id == "aegislash"
                assert (active.attack, active.defense) == (210, 100)
                active.hp = main.hp = main.current_hp = 17
                active.attack_boost = 2
                active.volatile_status.add("substitute")
                state.field = "electricterrain"
                before_engine = copy.deepcopy(state)
                db.save_item(1053, "mighty-candy-xl", 1)
                click_training("mighty-candy-xl")
                assert main.ev["atk"] == 252
                assert active.attack == 252
                assert active.defense == 100
                assert active.hp == 17 and active.attack_boost == 2
                assert state.field == "electricterrain"
                assert "substitute" in active.volatile_status
                assert {
                    change["key"] for change in bridge.diff_states(before_engine, state)
                } == {"user.active.attack", "user.active.evs"}
                main.attacks = ["kingsshield"]
                captured_events.extend(driver.answer(3))
                # A normal review publishes a copied state; training itself
                # must preserve its identity, as asserted above.
                state = battle_loop._state.new_state
                assert state._ankimon_main_individual_id == main.individual_id
                assert state.user.active.attack == 132
                assert state.user.active.evs[1] == 252
                assert battle_loop._state.mutator_full_reset == 0

                # A transformed owner's training updates its HP/EV metadata,
                # while copied offense/defense/speed and fainted HP survive.
                active = state.user.active
                active.volatile_status.add("transform")
                fields = (
                    "attack",
                    "defense",
                    "special_attack",
                    "special_defense",
                    "speed",
                )
                copied_stats = (401, 503, 607, 709, 811)
                for name, value in zip(fields, copied_stats):
                    setattr(active, name, value)
                active.hp = main.hp = main.current_hp = 0
                main.battle_status = "fainted"
                db.save_main_pokemon(main.to_dict())
                old_maxhp = active.maxhp
                db.save_item(45, "hp-up", 1)
                click_training("hp-up")
                assert tuple(getattr(active, name) for name in fields) == copied_stats
                assert active.maxhp > old_maxhp
                assert main.max_hp == active.maxhp
                assert main.hp == main.current_hp == active.hp == 0
                assert main.battle_status == "fainted"
                assert db.get_item("hp-up") is None
                assert db.get_main_pokemon()["hp"] == 0

                assert not [
                    event for event in captured_events if event["type"] == "error"
                ]
                assert any(event["type"] == "battle" for event in captured_events)
                print(
                    "PR #962 Tier 2 PASS: native training SQL rollback/retry; "
                    "all nested combat state preserved; idempotent publication; "
                    "low-IV Aegislash live-form rounding; transformed/fainted HP."
                )
                return True
            finally:
                bag.close()
                db.close()
                driver.env.app.processEvents()
        finally:
            if old_cache is None:
                os.environ.pop("ANKIMON_SPRITE_CACHE", None)
            else:
                os.environ["ANKIMON_SPRITE_CACHE"] = old_cache


if __name__ == "__main__":
    raise SystemExit(0 if run_proof() else 1)
