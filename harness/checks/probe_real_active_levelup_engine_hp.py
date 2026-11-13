"""A level-up outside the defeat path must survive the next review (#975).

Mobile "Resolve All" levels the active companion through
save_main_pokemon_progress without starting a new encounter, so the level-up
heal has to reach the cached engine state or the next turn writes the
pre-level-up HP back.
"""

import os
from pathlib import Path
import tempfile

from harness.bootstrap import quiet
from harness.real_driver import RealDriver


def main():
    with tempfile.TemporaryDirectory(prefix="ankimon-levelup-engine-") as directory:
        os.environ["ANKIMON_SPRITE_CACHE"] = str(Path(directory) / "no-cache")
        with quiet():
            driver = RealDriver(
                user_path=directory,
                first_encounter=False,
                settings_overrides={
                    "battle.cards_per_round": 1,
                    "battle.review_based_damage": True,
                    "gui.show_mainpkmn_in_reviewer": 1,
                    "audio.sounds": False,
                    "audio.sound_effects": False,
                    "gui.pop_up_dialog_message_on_defeat": False,
                },
            )

        from Ankimon import battle_loop
        from Ankimon.functions import encounter_functions
        from harness.fixtures import build_pokemon

        services = driver.services
        db = services.db
        main_pokemon = services.main_pokemon
        main_pokemon.update_stats(
            **build_pokemon(
                {"species": "Mewtwo", "level": 50, "moves": ["Safeguard"]}
            ).to_dict()
        )
        # Seismic Toss always deals the user's level (50) in damage.
        services.enemy_pokemon.update_stats(
            **build_pokemon(
                {"species": "Snorlax", "level": 50, "moves": ["Seismic Toss"]}
            ).to_dict()
        )
        assert db.save_main_pokemon(main_pokemon.to_dict())
        battle_loop._state.new_state = None
        battle_loop._state.mutator_full_reset = 1

        # The encounter's first review may not include an enemy attack.
        for _ in range(6):
            with quiet():
                driver.answer("again")
            if main_pokemon.hp < main_pokemon.max_hp:
                break
        assert main_pokemon.hp < main_pokemon.max_hp, "setup: no damage taken"
        assert battle_loop._state.new_state is not None

        class _NoEvYield:
            ev_yield = {"hp": 0}

        level_before = main_pokemon.level
        with quiet():
            encounter_functions.save_main_pokemon_progress(
                main_pokemon,
                _NoEvYield(),
                200000,
                services.achievements,
                services.logger,
                None,
            )
        assert main_pokemon.level > level_before
        new_max = main_pokemon.max_hp
        engine = battle_loop._state.new_state.user.active
        assert main_pokemon.hp == main_pokemon.current_hp == new_max
        assert db.get_main_pokemon()["current_hp"] == new_max
        assert (engine.hp, engine.maxhp) == (new_max, new_max), (
            engine.hp,
            engine.maxhp,
        )

        with quiet():
            errors = [e for e in driver.answer("again") if e["type"] == "error"]
        assert not errors, errors
        assert main_pokemon.hp == main_pokemon.current_hp == new_max - 50
        assert db.get_main_pokemon()["current_hp"] == new_max - 50
        db.close()

    print("probe_real_active_levelup_engine_hp: OK (heal survives the next turn)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
