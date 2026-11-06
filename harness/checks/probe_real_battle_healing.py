"""Battle healing reaches live HP and SQLite with visible or hidden HUDs (#975)."""

import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from harness.bootstrap import quiet
from harness.real_driver import RealDriver


def main():
    with tempfile.TemporaryDirectory(prefix="ankimon-battle-healing-") as directory:
        os.environ["ANKIMON_SPRITE_CACHE"] = str(Path(directory) / "no-cache")
        with quiet():
            driver = RealDriver(
                user_path=directory,
                first_encounter=False,
                settings_overrides={
                    "battle.cards_per_round": 1,
                    "battle.review_based_damage": False,
                    "controls.allow_to_choose_moves": True,
                    "audio.sounds": False,
                    "audio.sound_effects": False,
                    "gui.pop_up_dialog_message_on_item": False,
                },
            )

        from Ankimon import battle_loop
        from Ankimon.functions.update_main_pokemon import update_main_pokemon
        from harness.fixtures import build_pokemon

        services = driver.services
        db = services.db
        main_pokemon = services.main_pokemon

        def answer(move):
            # Only bypass the modal move picker; real review hooks, the battle
            # engine, Reviewer_Manager and SQLite execute normally.
            with patch.object(services.ui, "choose_move", return_value=move), quiet():
                emitted = driver.answer("again")
            errors = [event for event in emitted if event["type"] == "error"]
            assert not errors, errors

        def assert_hp(expected):
            actual = {
                "live": main_pokemon.hp,
                "current_hp": main_pokemon.current_hp,
                "saved": db.get_main_pokemon()["current_hp"],
                "engine": battle_loop._state.new_state.user.active.hp,
            }
            assert all(hp == expected for hp in actual.values()), (
                hud_mode,
                healing,
                expected,
                actual,
            )
            if hud_mode == 3:
                assert services.reviewer._last_state is None
            else:
                assert services.reviewer._last_state[5] == expected

        for hud_mode in (1, 3):
            driver.set_setting("gui.show_mainpkmn_in_reviewer", hud_mode)
            for healing in ("recover", "leftovers"):
                main_pokemon.update_stats(
                    **build_pokemon(
                        {
                            "species": "Mewtwo",
                            "level": 50,
                            "moves": ["Recover", "Splash"],
                            "hp": 10 if healing == "recover" else 100,
                            "held_item": "leftovers"
                            if healing == "leftovers"
                            else None,
                        }
                    ).to_dict()
                )
                services.enemy_pokemon.update_stats(
                    **build_pokemon(
                        {"species": "Snorlax", "level": 50, "moves": ["Splash"]}
                    ).to_dict()
                )
                assert main_pokemon.max_hp == 181  # Exercise odd maximum HP.
                assert db.save_main_pokemon(main_pokemon.to_dict())
                battle_loop._state.new_state = None
                battle_loop._state.mutator_full_reset = 1
                services.tracker.cards_battle_round = 0

                answer("Recover" if healing == "recover" else "Splash")
                healed_hp = 100 if healing == "recover" else 111
                assert_hp(healed_hp)

                # The next ordinary turn deals fixed damage. Recover must not
                # undo the damage, and Leftovers must add only its new 11 HP.
                services.enemy_pokemon.attacks = ["Seismic Toss"]
                answer("Splash")
                expected = 50 if healing == "recover" else 72
                assert_hp(expected)
                loaded, empty = update_main_pokemon(main_pokemon)
                assert loaded is main_pokemon and not empty
                assert_hp(expected)

        db.close()

    print(
        "probe_real_battle_healing: OK "
        "(Recover, Leftovers, visible/hidden HUD, subsequent damage and reload)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
