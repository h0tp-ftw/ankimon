"""PR 892: permanent catch history through capture, release, evolution and trade.

Runs the real catch path and SQLite database in a throwaway profile. The separate
Tier-2 ``probe_real_caught_history`` verifies the visible reviewer Pokeball.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.driver import Driver


def run_proof():
    with tempfile.TemporaryDirectory(prefix="ankimon-caught-history-") as profile:
        driver = Driver(
            user_path=profile,
            seed={"main": {"id": 1, "level": 10}},
            first_encounter=False,
        )
        from Ankimon.functions import encounter_functions as encounters
        from Ankimon.pyobj.database_manager import AnkimonDB
        from Ankimon.utils import load_collected_pokemon_ids
        from harness.fixtures import build_pokemon

        db = driver.services.db
        try:
            assert load_collected_pokemon_ids() == {1}
            driver.set_enemy(id=25, level=5)
            driver.services.enemy_pokemon.hp = 0
            events = driver.catch()
            assert any(
                event["type"] == "catch" and event["id"] == 25 for event in events
            )
            assert not any(event["type"] == "error" for event in events), events
            captured = next(p for p in db.get_all_pokemon() if p["id"] == 25)
            assert load_collected_pokemon_ids() == {1, 25}

            # Release must affect the box without erasing the catch, immediately
            # and after reopening the same SQLite file.
            assert db.add_to_history(captured)
            assert db.delete_pokemon(captured["individual_id"])
            assert db.get_all_pokemon_ids() == {1}
            assert load_collected_pokemon_ids() == {1, 25}

            # Seeing a species alone never counts as collecting it.
            db.set_user_data("pokedex_seen", sorted(db.get_seen_ids() | {150}))
            assert 150 not in load_collected_pokemon_ids()

            # Evolution replaces the owned record; both stages remain caught.
            main = db.get_main_pokemon()
            evolved = build_pokemon({"id": 2, "level": 16}).to_dict()
            evolved["individual_id"] = main["individual_id"]
            assert db.save_main_pokemon(evolved)
            driver.services.main_pokemon.update_stats(**evolved)
            assert db.get_all_pokemon_ids() == {2}
            assert {1, 2} <= load_collected_pokemon_ids()

            # Import/catch and trade use different DB writers. Neither may need
            # an application restart before its species unlocks are available.
            form = build_pokemon({"id": 10034, "level": 40}).to_dict()
            assert db.save_pokemon(form)
            assert 10034 in load_collected_pokemon_ids()
            assert 6 not in load_collected_pokemon_ids(), (
                "Forms must retain their exact IDs"
            )
            assert not encounters._player_owns_base_form(
                10034, load_collected_pokemon_ids()
            )
            traded = build_pokemon({"id": 133, "level": 5}).to_dict()
            assert db.replace_pokemon(traded, form["individual_id"])
            assert {10034, 133} <= load_collected_pokemon_ids()
            rejected = build_pokemon({"id": 151, "level": 5}).to_dict()
            assert not db.replace_pokemon(rejected, "missing-individual")
            assert 151 not in load_collected_pokemon_ids()
            assert db.delete_pokemon(traded["individual_id"])
            expected = {1, 2, 25, 10034, 133}
            assert load_collected_pokemon_ids() == expected

            # The startup/battle set predates the trade. Catch-if-uncollected
            # must consult durable history, and defeat this released species.
            assert 133 not in driver.env.collected_ids
            driver.set_setting("battle.automatic_battle", 3)
            driver.set_setting("battle.auto_catch_wishlist", [])
            driver.set_enemy(id=133, level=5, shiny=False)
            state = driver.services
            state.enemy_pokemon.hp = 0
            state.tracker.faint_processed = False
            driver.drain_events()
            encounters.handle_enemy_faint(
                state.main_pokemon,
                state.enemy_pokemon,
                driver.env.collected_ids,
                state.test_window,
                state.evo_window,
                state.reviewer,
                state.logger,
                state.achievements,
            )
            resolved = driver.drain_events()
            assert any(e["type"] == "defeat" and e["id"] == 133 for e in resolved), (
                resolved
            )
            assert not any(e["type"] in {"catch", "error"} for e in resolved), resolved
            assert db.get_all_pokemon_ids() == {2}

            # Released base species still unlock their Mega forms; a released
            # prerequisite likewise unlocks its dependent legendary encounter.
            assert not encounters._meets_prerequisites(
                150, load_collected_pokemon_ids()
            )
            for species_id in (6, 151):
                acquired = build_pokemon({"id": species_id, "level": 40}).to_dict()
                assert db.save_pokemon(acquired)
                assert db.add_to_history(acquired)
                assert db.delete_pokemon(acquired["individual_id"])
            expected.update({6, 151})
            assert encounters._player_owns_base_form(
                10034, load_collected_pokemon_ids()
            )
            assert encounters._meets_prerequisites(150, load_collected_pokemon_ids())
            # Consumers can alter their snapshots without corrupting history.
            snapshot = db.get_caught_ids()
            snapshot.clear()
            assert load_collected_pokemon_ids() == expected

            path = db.db_path
            assert db.close()
            db = AnkimonDB(db_path=path)
            driver.services.db = db
            assert db.get_all_pokemon_ids() == {2}
            assert load_collected_pokemon_ids() == expected
            assert 150 in db.get_seen_ids()
        finally:
            db.close()
    print(
        "probe_caught_history: release/reopen, evolution, trade, form unlocks and auto-catch OK"
    )
    return True


if __name__ == "__main__":
    run_proof()
