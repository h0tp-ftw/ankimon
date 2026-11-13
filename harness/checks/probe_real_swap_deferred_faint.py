"""Swapping out during a deferred double faint keeps the faint heal (#975).

In manual mode a double faint defers the main's heal until the player picks
catch/defeat. A swap cancels that deferral, so the outgoing Pokemon must be
saved at full HP rather than the 0 HP the HUD persisted, or swapping it back
in makes it faint again and throws away the current encounter.
"""

import os
from pathlib import Path
import tempfile

from harness.bootstrap import quiet
from harness.real_driver import RealDriver


def main():
    with tempfile.TemporaryDirectory(prefix="ankimon-swap-faint-") as directory:
        os.environ["ANKIMON_SPRITE_CACHE"] = str(Path(directory) / "no-cache")
        with quiet():
            driver = RealDriver(
                user_path=directory,
                first_encounter=False,
                settings_overrides={
                    "battle.cards_per_round": 1,
                    "battle.review_based_damage": False,
                    "battle.automatic_battle": 0,
                    "controls.allow_to_choose_moves": False,
                    "gui.show_mainpkmn_in_reviewer": 1,
                    "audio.sounds": False,
                    "audio.sound_effects": False,
                    "gui.pop_up_dialog_message_on_defeat": False,
                    "controls.team_cycle_count": 3,
                },
            )

        from Ankimon import battle_loop, reviewer_ui
        from harness.fixtures import build_pokemon

        services = driver.services
        db = services.db
        main_pokemon = services.main_pokemon
        main_pokemon.update_stats(
            **build_pokemon(
                {"species": "Electrode", "level": 50, "moves": ["Explosion"]}
            ).to_dict()
        )
        assert db.save_main_pokemon(main_pokemon.to_dict())
        electrode_id = str(main_pokemon.individual_id)
        electrode_max = main_pokemon.max_hp
        partner = build_pokemon(
            {"species": "Pikachu", "level": 50, "moves": ["Tackle"]}
        )
        db.save_pokemon(partner.to_dict())
        db.save_team(
            [
                {"individual_id": electrode_id},
                {"individual_id": str(partner.individual_id)},
            ]
        )
        services.enemy_pokemon.update_stats(
            **build_pokemon(
                {"species": "Magikarp", "level": 5, "moves": ["Splash"]}
            ).to_dict()
        )
        battle_loop._state.new_state = None
        battle_loop._state.mutator_full_reset = 1

        # Explosion knocks out both sides: the main's heal is deferred.
        with quiet():
            driver.answer("good")
        assert main_pokemon.hp == 0 and services.enemy_pokemon.hp == 0
        assert battle_loop._main_faint_deferred

        with quiet():
            reviewer_ui.cycle_team_pokemon()
        assert str(main_pokemon.individual_id) == str(partner.individual_id)
        assert not battle_loop._main_faint_deferred
        assert db.get_pokemon(electrode_id)["current_hp"] == electrode_max

        with quiet():
            driver.defeat()
            reviewer_ui.cycle_team_pokemon()
        assert str(main_pokemon.individual_id) == electrode_id
        assert main_pokemon.hp == electrode_max
        encounter_token = getattr(
            services.enemy_pokemon, "_ankimon_encounter_token", None
        )

        with quiet():
            events = driver.answer("good")
        types = [e["type"] for e in events]
        assert "error" not in types, events
        assert "faint" not in types, types
        assert (
            getattr(services.enemy_pokemon, "_ankimon_encounter_token", None)
            is encounter_token
        ), "the encounter was thrown away"
        db.close()

    print("probe_real_swap_deferred_faint: OK (outgoing Pokemon keeps its faint heal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
