"""A committed potion must survive the next ordinary review turn (#975)."""

import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from harness.bootstrap import quiet
from harness.real_driver import RealDriver


def main():
    with tempfile.TemporaryDirectory(prefix="ankimon-potion-hp-") as directory:
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
                    "gui.pop_up_dialog_message_on_item": False,
                },
            )

        from Ankimon import battle_loop
        from Ankimon.singletons import get_item_window
        from harness.fixtures import build_pokemon

        services = driver.services
        db = services.db
        main_pokemon = services.main_pokemon
        main_pokemon.update_stats(
            **build_pokemon(
                {"species": "Mewtwo", "level": 50, "moves": ["Safeguard"]}
            ).to_dict()
        )
        services.enemy_pokemon.update_stats(
            **build_pokemon(
                {"species": "Snorlax", "level": 50, "moves": ["Seismic Toss"]}
            ).to_dict()
        )
        assert db.save_main_pokemon(main_pokemon.to_dict())
        db.add_item("potion", 1)
        battle_loop._state.new_state = None
        battle_loop._state.mutator_full_reset = 1

        # Ordinary review hooks determine the multiplier; allow its warm-up turn.
        errors = []
        for _ in range(5):
            with quiet():
                errors.extend(e for e in driver.answer("again") if e["type"] == "error")
            if main_pokemon.hp < main_pokemon.max_hp:
                break
        assert not errors, errors
        before_hp = main_pokemon.hp
        assert before_hp == main_pokemon.max_hp - 50

        with quiet():
            bag = get_item_window()

        # A background reward may acquire the writer lock just as the real
        # turn repaints. Its refused HP save must not let the potion heal from
        # the previous turn's larger saved HP, or consume stock while busy.
        blocker = sqlite3.connect(str(db.db_path))
        persist_hp = db.update_pokemon_current_hp

        def persist_while_busy(*args):
            if not blocker.in_transaction:
                blocker.execute("BEGIN IMMEDIATE")
            return persist_hp(*args)

        try:
            with (
                patch.object(db, "update_pokemon_current_hp", persist_while_busy),
                quiet(),
            ):
                errors = [e for e in driver.answer("again") if e["type"] == "error"]
            assert not errors, errors
            assert main_pokemon.hp == before_hp - 50
            assert db.get_main_pokemon()["current_hp"] == before_hp
            with quiet():
                refused = bag.dispatch_use("potion")
            assert not refused["ok"], refused
            assert main_pokemon.hp == before_hp - 50
            assert db.get_item("potion")["quantity"] == 1
        finally:
            blocker.rollback()
            blocker.close()
        before_hp = main_pokemon.hp

        with quiet():
            result = bag.dispatch_use("potion")
        assert result["ok"], result
        healed_hp = before_hp + 20
        assert main_pokemon.hp == main_pokemon.current_hp == healed_hp
        assert db.get_main_pokemon()["current_hp"] == healed_hp
        assert battle_loop._state.new_state.user.active.hp == healed_hp
        assert db.get_item("potion") is None

        with quiet():
            errors = [e for e in driver.answer("again") if e["type"] == "error"]
        assert not errors, errors
        assert main_pokemon.hp == main_pokemon.current_hp == healed_hp - 50
        assert db.get_main_pokemon()["current_hp"] == healed_hp - 50
        bag.close()
        db.close()

    print(
        "probe_real_potion_hp: OK (real bag, review hooks, engine HP, one item spent)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
