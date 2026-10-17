"""Protect EV item use from real background mobile reward publication (#962)."""

import copy
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace


def main():
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print("NO_QT: EV/mobile concurrency proof requires PyQt6")
        return 77

    from harness.bootstrap import quiet
    from harness.real_driver import RealDriver

    with tempfile.TemporaryDirectory(prefix="ankimon-ev-sync-") as directory:
        os.environ["ANKIMON_SPRITE_CACHE"] = str(Path(directory) / "no-cache")
        with quiet():
            driver = RealDriver(
                user_path=directory,
                first_encounter=False,
                settings_overrides={
                    "audio.sounds": False,
                    "audio.sound_effects": False,
                    "gui.pop_up_dialog_message_on_item": False,
                    "gui.pop_up_dialog_message_on_defeat": False,
                },
            )

        from Ankimon import utils
        from Ankimon.functions.encounter_functions import save_main_pokemon_progress
        from Ankimon.functions.mobile_sync import _mobile_sync_lock
        from Ankimon.singletons import get_item_window
        from harness.fixtures import build_pokemon

        services = driver.services
        db = services.db
        companion = services.main_pokemon
        companion.update_stats(
            **build_pokemon(
                {"species": "Pikachu", "level": 50, "moves": ["Splash"], "hp": 20}
            ).to_dict()
        )
        companion.battle_status = "par"
        companion.stat_stages["atk"] = 2
        companion.volatile_status.add("substitute")
        stored = companion.to_dict()
        stored["custom_save_metadata"] = {"retained": [1, 2]}
        # Older records may contain combat fields that to_dict() now omits.
        # They must not replace the ongoing battle's live status or boosts.
        stored["battle_status"] = "fighting"
        stored["stat_stages"] = {"atk": 0}
        stored["volatile_status"] = []
        db.save_main_pokemon(stored)
        db.save_item(64, "protein", 1)
        bag = get_item_window()
        before_live = copy.deepcopy(companion.to_dict())
        before_saved = copy.deepcopy(db.get_main_pokemon())
        before_stock = copy.deepcopy(db.get_item("protein"))
        worker_ready = threading.Event()
        finish_rewards = threading.Event()
        errors = []

        def resolve_mobile_rewards():
            conn = None
            try:
                # Production bulk resolution holds this lock through its
                # transaction and publishes progress onto the same singleton.
                with _mobile_sync_lock:
                    conn = db._get_connection()
                    conn._disable_commit = True
                    utils.in_bulk_resolve = True
                    with conn:
                        conn.execute("UPDATE metadata SET value = value").close()
                        worker_ready.set()
                        assert finish_rewards.wait(5), "bag use never finished"
                        save_main_pokemon_progress(
                            companion,
                            SimpleNamespace(ev_yield={"atk": 3}),
                            1,
                            services.achievements,
                            services.logger,
                            None,
                        )
            except BaseException as exc:
                errors.append(exc)
            finally:
                if conn is not None:
                    conn._disable_commit = False
                utils.in_bulk_resolve = False

        worker = threading.Thread(target=resolve_mobile_rewards)
        worker.start()
        try:
            assert worker_ready.wait(5), "mobile worker never acquired its lock"
            with quiet():
                result = bag.dispatch_use("protein")
            assert not result["ok"]
            assert companion.to_dict() == before_live
            assert db.get_main_pokemon() == before_saved
            assert db.get_item("protein") == before_stock
        finally:
            finish_rewards.set()
            worker.join(5)

        assert not worker.is_alive()
        assert errors == [], errors
        rewarded = db.get_main_pokemon()
        assert rewarded["ev"]["atk"] == companion.ev["atk"] == 3
        assert rewarded["xp"] == companion.xp == 1
        assert rewarded["pokemon_defeated"] == companion.pokemon_defeated == 1
        assert rewarded["friendship"] == companion.friendship > 0
        assert db.get_item("protein") == before_stock

        # Disk HP can lag an ongoing fight. Retrying must preserve the live
        # value while applying the item to the committed mobile EV baseline.
        companion.hp = companion.current_hp = 12
        with quiet():
            assert bag.dispatch_use("protein")["ok"]
        trained = db.get_main_pokemon()
        assert trained["ev"]["atk"] == companion.ev["atk"] == 13
        for key in ("xp", "friendship", "pokemon_defeated"):
            assert trained[key] == rewarded[key] == getattr(companion, key)
        assert trained["custom_save_metadata"] == {"retained": [1, 2]}
        assert (
            trained["current_hp"]
            == companion.hp
            == companion.current_hp
            == 12
        )
        assert companion.battle_status == "par"
        assert companion.stat_stages["atk"] == 2
        assert "substitute" in companion.volatile_status
        assert db.get_item("protein") is None
        assert not db._get_connection().in_transaction
        bag.close()
        QApplication.processEvents()
        db.close()

    print("probe_real_ev_item_sync: OK (busy refusal, mobile rewards, retry, live HP)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
