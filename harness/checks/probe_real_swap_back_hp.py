"""Swapping away and straight back keeps the HP and rest a Pokemon is owed (#975).

The engine keeps the active Pokemon's HP between rounds and writes it back
after each one. Swapping A -> B -> A with no battle round in between returns to
an engine state still bound to A, so the HP A comes back with (rest credited on
the bench, or the classic heal-on-swap) must reach the engine, or the next
round shows the old HP again and saves it.

A swap-in must also leave the rest clock to the save. Restarting it throws away
the part of an HP the Pokemon has already rested for, so a team rotated faster
than one HP of rest would never regenerate.

Both ways of switching are covered: the team-cycle key and the PC's
"pick as main".
"""

from datetime import datetime
import os
from pathlib import Path
import tempfile

from harness.clock import advance, install_clock

# Before Ankimon is imported, so rest can be measured in hours, instantly.
install_clock(datetime(2026, 6, 1, 9, 0, 0))

from harness.bootstrap import quiet  # noqa: E402
from harness.real_driver import RealDriver  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="ankimon-swap-back-") as directory:
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
        from Ankimon.functions.hp_recovery import (
            REGEN_FULL_HEAL_SECONDS,
            current_timestamp,
        )
        from harness.fixtures import build_pokemon

        services = driver.services
        db = services.db
        main_pokemon = services.main_pokemon
        main_pokemon.update_stats(
            **build_pokemon(
                {"species": "Snorlax", "level": 50, "moves": ["Splash"]}
            ).to_dict()
        )
        assert db.save_main_pokemon(main_pokemon.to_dict())
        snorlax_id = str(main_pokemon.individual_id)
        max_hp = int(main_pokemon.max_hp)
        partner = build_pokemon(
            {"species": "Blissey", "level": 50, "moves": ["Splash"]}
        )
        db.save_pokemon(partner.to_dict())
        partner_id = str(partner.individual_id)
        db.save_team([{"individual_id": snorlax_id}, {"individual_id": partner_id}])
        pc = services.pokemon_pc
        if pc is None:
            from Ankimon.singletons import pokemon_pc as pc

        def cycle():
            with quiet():
                reviewer_ui.cycle_team_pokemon()
            driver.drain_events()

        def pick(individual_id):
            with quiet():
                pc.pick_as_main_pokemon({"individual_id": individual_id})
            driver.drain_events()

        # name -> (leave Snorlax for the partner, come back to Snorlax)
        switches = {
            "team-cycle key": (cycle, cycle),
            "PC pick": (lambda: pick(partner_id), lambda: pick(snorlax_id)),
        }

        def saved(individual_id=snorlax_id):
            return db.get_pokemon(individual_id)

        def wound():
            """A fresh foe hits Snorlax once, binding the engine state to it."""
            main_pokemon.hp = main_pokemon.current_hp = max_hp
            services.enemy_pokemon.update_stats(
                **build_pokemon(
                    {"species": "Machamp", "level": 40, "moves": ["Karate Chop"]}
                ).to_dict()
            )
            battle_loop._state.new_state = None
            battle_loop._state.mutator_full_reset = 1
            battle_loop._state.swap_turn_pending = False
            for _ in range(3):  # the first card of a session draws no attack
                with quiet():
                    driver.answer("again")  # a wrong answer lets the foe attack
                if main_pokemon.hp < max_hp:
                    break
            assert str(main_pokemon.individual_id) == snorlax_id
            assert 0 < main_pokemon.hp < max_hp, main_pokemon.hp
            assert saved()["current_hp"] == main_pokemon.hp
            return int(main_pokemon.hp)

        def fight_on(expected, why):
            """One more round in which nobody attacks must not move Snorlax's HP."""
            with quiet():
                events = driver.answer("good")
            assert not [e for e in events if e["type"] == "error"], events
            assert main_pokemon.hp == main_pokemon.current_hp == expected, (
                why,
                main_pokemon.hp,
                expected,
            )
            assert saved()["current_hp"] == expected, (why, saved()["current_hp"])

        for name, (leave, come_back) in switches.items():
            # Rest credited on the bench survives the next round.
            hurt = wound()
            leave()
            assert str(main_pokemon.individual_id) == partner_id, name
            advance(hours=2)
            come_back()
            assert str(main_pokemon.individual_id) == snorlax_id, name
            rested = hurt + max_hp // 4
            assert abs(main_pokemon.hp - rested) <= 1, (name, main_pokemon.hp, rested)
            fight_on(int(main_pokemon.hp), f"{name}: bench rest was undone")

            # So does the classic heal-on-swap.
            driver.set_setting("battle.heal_on_swap", True)
            wound()
            leave()
            come_back()
            assert main_pokemon.hp == max_hp, (name, main_pokemon.hp)
            fight_on(max_hp, f"{name}: heal on swap was undone")
            driver.set_setting("battle.heal_on_swap", False)

            # A swap-in leaves the rest clock alone, so short stints add up.
            hurt = wound()
            clock = saved()["hp_regen_at"]
            seconds_per_hp = REGEN_FULL_HEAL_SECONDS / max_hp
            stint = int(seconds_per_hp * 0.45)  # no single stint earns an HP
            leave()
            advance(seconds=stint)
            come_back()
            assert saved()["current_hp"] == hurt, name
            assert saved()["hp_regen_at"] == clock, (
                f"{name}: the swap-in restarted the rest clock",
                saved()["hp_regen_at"] - clock,
            )
            for _ in range(4):
                advance(seconds=stint)
                leave()
                advance(seconds=stint)
                come_back()
            owed = int((current_timestamp() - clock) / seconds_per_hp)
            assert owed >= 3, owed
            assert main_pokemon.hp == saved()["current_hp"] == hurt + owed, (
                f"{name}: rest was lost across swaps",
                main_pokemon.hp,
                hurt + owed,
            )

        # With regeneration off, a swap-in settles the rest unhealed. The
        # settled clock must be the one saved, or switching regeneration back
        # on would credit the time it was off.
        driver.set_setting("battle.hp_regeneration", False)
        wound()
        assert db.update_pokemon_current_hp(partner_id, 100)
        advance(hours=3)
        for name, (leave, come_back) in switches.items():
            leave()
            assert str(main_pokemon.individual_id) == partner_id, name
            assert main_pokemon.hp == saved(partner_id)["current_hp"] == 100, name
            assert saved(partner_id)["hp_regen_at"] == current_timestamp(), name
            come_back()
            advance(hours=3)
        driver.set_setting("battle.hp_regeneration", True)
        db.close()

    print(
        "probe_real_swap_back_hp: OK (bench rest, heal on swap and the rest "
        "clock survive a swap away and back, by cycle key and PC)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
