"""PR #961: native choices, queued replay writer ordering, and account switches.

Run in a fresh process with PyQt6 and the harness Tier-2 dependencies. All
records live in a temporary profile; only Anki's host/scheduling is simulated.
"""

import contextlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OLD = ["tackle", "growl", "vinewhip", "razorleaf"]
CHOICE = ["tackle", "vinewhip", "poisonpowder", "sleeppowder"]
WANTED = ["tackle", "poisonpowder", "vinewhip", "sleeppowder"]


def run_proof():
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QDialog

    native_exec = QDialog.exec
    from harness.real_driver import RealDriver

    with tempfile.TemporaryDirectory(prefix="pr961-followup-") as directory:
        d = RealDriver(
            user_path=directory,
            first_encounter=False,
            settings_overrides={
                "audio.sounds": False,
                "audio.sound_effects": False,
                "gui.pop_up_dialog_message_on_defeat": False,
                "evolution.friendship_time_enabled": False,
            },
        )
        from harness.fixtures import seed_db, build_pokemon, set_enemy
        from Ankimon.functions import (
            trainer_functions as trainer,
            mobile_sync,
            encounter_functions as encounter,
        )
        from Ankimon.functions.update_main_pokemon import update_main_pokemon
        from Ankimon.core import bind_runtime_globals
        from Ankimon.pyobj.moveset_dialog import MovesetDialog
        from Ankimon.singletons import swap_ankimon_account
        import aqt.operations

        s, db = d.services, d.services.db

        def persisted(iid="holder", path=None):
            with sqlite3.connect(path or db.db_path) as connection:
                row = connection.execute(
                    "SELECT data FROM captured_pokemon WHERE individual_id=?", (iid,)
                ).fetchone()
            return json.loads(row[0]) if row else None

        def setup(moves=OLD, mode="classic"):
            seed_db(
                {
                    "main": {
                        "species": "Pikachu",
                        "level": 50,
                        "individual_id": "active",
                        "moves": ["thunderbolt"],
                    },
                    "team": [
                        {
                            "species": "Bulbasaur",
                            "level": 14,
                            "individual_id": "holder",
                            "moves": moves,
                            "friendship": 70,
                        }
                    ],
                },
                db,
            )
            update_main_pokemon(s.main_pokemon)
            bind_runtime_globals()
            s.settings.set("trainer.xp_share_mode", mode)
            s.settings.set("trainer.xp_share", "holder")
            s.settings.set("controls.allow_to_choose_moves", False)
            holder = db.get_pokemon("holder")
            holder["attacks"] = list(moves)
            holder["everstone"] = True
            db.save_pokemon(holder)
            return (
                int(trainer.find_experience_for_level(holder["growth_rate"], 14, False))
                + 1
            )

        def grant(earned):
            return trainer.xp_share_gain_exp(
                s.logger, s.settings, s.evo_window, "active", earned * 2, "holder"
            )

        def with_modal(operation, chosen, during=lambda: None, cancel=False):
            errors, seen = [], []
            poll = QTimer()

            def exec_modal(dialog):
                timeout = QTimer(dialog)
                timeout.setSingleShot(True)
                timeout.timeout.connect(
                    lambda: (errors.append("native dialog timed out"), dialog.reject())
                )
                timeout.start(10000)
                try:
                    return native_exec(dialog)
                finally:
                    timeout.stop()

            def interact():
                dialogs = [
                    w
                    for w in d.env.app.topLevelWidgets()
                    if isinstance(w, MovesetDialog) and w.isVisible()
                ]
                if not dialogs:
                    return
                poll.stop()
                dialog = dialogs[0]
                try:
                    assert not db._get_connection().in_transaction, (
                        "GUI owns a DB transaction during a modal"
                    )
                    seen.append(True)
                    during()
                    if cancel:
                        dialog.reject()
                        return
                    for box in dialog._checkboxes:
                        box.setChecked(box.property("raw_move") in chosen)
                    assert dialog.confirm_button.isEnabled(), dialog._checked_moves()
                    dialog.confirm_button.click()
                except BaseException as error:
                    errors.append(repr(error))
                    dialog.reject()

            poll.timeout.connect(interact)
            poll.start(1)
            try:
                with patch.object(MovesetDialog, "exec", exec_modal):
                    operation()
            finally:
                poll.stop()
            assert seen == [True] and not errors, (seen, errors)

        try:
            # Equivalent legacy choices render once; no invalid four-checkbox
            # answer can be accepted then silently dropped by the backend.
            earned = setup(["Tackle", "Growl", "tackle", "TACKLE"])
            dialog = MovesetDialog(
                "Bulbasaur",
                ["Tackle", "Growl", "tackle", "TACKLE"],
                ["poisonpowder", "sleeppowder", "Poison Powder"],
            )
            assert len(dialog._checkboxes) == 4
            assert not dialog.confirm_button.isEnabled()
            dialog.movesetSelected()
            assert dialog.selected_moves is None
            dialog.deleteLater()
            with_modal(
                lambda: grant(earned),
                ["Tackle", "Growl", "poisonpowder", "sleeppowder"],
            )
            assert persisted()["attacks"] == [
                "Tackle",
                "Growl",
                "poisonpowder",
                "sleeppowder",
            ]
            print(
                "PASS: equivalent legacy moves display once; native Confirm persists all distinct choices"
            )

            holder = build_pokemon(
                {"species": "Charmander", "level": 3, "individual_id": "holder"}
            ).to_dict()
            holder.update(
                attacks=["Scratch", "Growl", "scratch", "growl"], everstone=True
            )
            db.save_pokemon(holder)
            earned = (
                int(trainer.find_experience_for_level(holder["growth_rate"], 3, False))
                + 1
            )
            with patch.object(
                s.ui,
                "choose_moveset",
                side_effect=AssertionError(
                    "three distinct moves cannot fill a four-choice dialog"
                ),
            ) as prompt:
                grant(earned)
                prompt.assert_not_called()
            assert persisted()["attacks"] == ["Scratch", "Growl", "ember"]
            print(
                "PASS: a short distinct pool auto-learns instead of opening an impossible dialog"
            )

            earned = setup(["tackle", "growl", "vinewhip"])
            d.drain_events()
            with_modal(
                lambda: grant(earned), ["tackle", "growl", "vinewhip", "sleeppowder"]
            )
            tooltips = [
                e["message"] for e in d.drain_events() if e["type"] == "tooltip"
            ]
            assert persisted()["attacks"] == [
                "tackle",
                "growl",
                "vinewhip",
                "sleeppowder",
            ]
            assert any("Sleep Powder" in message for message in tooltips)
            assert not any("Poison Powder" in message for message in tooltips), tooltips
            print("PASS: post-confirmation tooltip names only the committed new move")

            for index, ordering in enumerate(("mobile-first", "desktop-first")):
                earned = setup()
                ready, release, patch_attempt = (
                    threading.Event(),
                    threading.Event(),
                    threading.Event(),
                )
                tasks = []
                original_calc = mobile_sync._calculate_mobile_reward
                original_transaction = db.pokemon_reward_transaction

                def calculate(*args, **kwargs):
                    if (
                        ordering == "mobile-first"
                        and args[0].get("individual_id") == "holder"
                        and threading.current_thread().name.startswith("pr961-mobile")
                    ):
                        ready.set()
                        assert release.wait(5), "desktop never attempted its patch"
                    return original_calc(*args, **kwargs)

                @contextlib.contextmanager
                def transaction(*args, **kwargs):
                    if (
                        ordering == "desktop-first"
                        and args[0] == "holder"
                        and threading.current_thread().name.startswith("pr961-mobile")
                    ):
                        ready.set()
                        assert release.wait(5), "desktop never committed its choice"
                    with original_transaction(*args, **kwargs) as record:
                        yield record

                def trace(statement):
                    if (
                        ordering == "mobile-first"
                        and ready.is_set()
                        and statement == "BEGIN IMMEDIATE"
                    ):
                        patch_attempt.set()
                        release.set()

                with ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="pr961-mobile"
                ) as pool:

                    class WorkerQueryOp:
                        def __init__(self, parent, op, success):
                            self.op, self.success = op, success

                        def without_collection(self):
                            return self

                        def run_in_background(self):
                            tasks.append((pool.submit(self.op, None), self.success))

                    assert (
                        db.queue_mobile_battles(
                            [
                                {
                                    "id": 1760000000000 + index,
                                    "cid": 42,
                                    "ease": 3,
                                    "time": 900,
                                    "type": 1,
                                }
                            ]
                        )
                        == 1
                    )
                    queue_id = db.get_next_pending_mobile_batch()[0]["queue_id"]
                    replay = {
                        "enemy_pokemon": build_pokemon(
                            {"species": "Pidgey", "level": 3}
                        ),
                        "battle_xp": 10,
                        "total_xp": 10,
                        "accumulated_evs": {},
                        "total_trainer_xp": 0,
                        "companion_id": "active",
                        "companion_name": "Pikachu",
                        "companion_level": 50,
                        "review_ids": [queue_id],
                    }

                    def start_worker():
                        result = mobile_sync.commit_replay_outcome(
                            "defeat",
                            replay,
                            db,
                            s.settings,
                            s.trainer_card,
                            s.main_pokemon,
                            s.achievements,
                            s.logger,
                        )
                        assert result["success"], result
                        assert ready.wait(5)

                    conn = db._get_connection()
                    conn._conn.set_trace_callback(trace)
                    try:
                        with (
                            patch.object(aqt.operations, "QueryOp", WorkerQueryOp),
                            patch.object(
                                mobile_sync, "_calculate_mobile_reward", calculate
                            ),
                            patch.object(db, "pokemon_reward_transaction", transaction),
                        ):
                            with_modal(lambda: grant(earned), CHOICE, start_worker)
                            release.set()
                            assert len(tasks) == 1
                            future, success = tasks[0]
                            success(future.result(timeout=10))
                    finally:
                        release.set()
                        conn._conn.set_trace_callback(None)
                if ordering == "mobile-first":
                    assert patch_attempt.is_set(), (
                        "patch did not wait for the earlier writer"
                    )
                saved = persisted()
                assert saved["attacks"] == WANTED, saved
                assert saved["level"] == 15 and saved["xp"] == 6, saved
                assert saved["friendship"] in (72, 73, 74)
                assert db.get_pending_mobile_count() == 0
                assert (
                    db.execute("SELECT COUNT(*) FROM mobile_battle_history").fetchone()[
                        0
                    ]
                    > 0
                )
                print(
                    f"PASS: {ordering} production queued replay preserves both rewards and all confirmed moves"
                )

            destination_path = Path(directory) / "ankimonDEV.db"
            destination = type(db)(s.logger, db_path=destination_path)
            destination.save_main_pokemon(
                build_pokemon(
                    {
                        "species": "Snorlax",
                        "level": 80,
                        "individual_id": "destination",
                        "moves": ["bodyslam"],
                        "xp": 31,
                        "friendship": 100,
                    }
                ).to_dict()
            )
            destination.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('migrated_phase2', 'true')"
            ).close()
            destination._get_connection().commit()
            destination.close()
            for mode, outcome in (
                (mode, outcome)
                for mode in ("classic", "oras")
                for outcome in ("accept", "cancel", "error")
            ):
                earned = setup(mode=mode)
                holder = db.get_pokemon("holder")
                holder["xp"] = earned - 2
                db.save_pokemon(holder)
                before = persisted("destination", destination_path)
                set_enemy(s, d.events, {"species": "Pidgey", "level": 20})
                actual_share = encounter.xp_share_gain_exp

                def share(*args, **kwargs):
                    result = actual_share(*args, **kwargs)
                    if outcome == "error":
                        raise RuntimeError("XP Share raised after switching accounts")
                    return result

                with patch.object(encounter, "xp_share_gain_exp", share):
                    with_modal(
                        lambda: encounter.kill_pokemon(
                            s.main_pokemon,
                            s.enemy_pokemon,
                            s.evo_window,
                            s.logger,
                            s.achievements,
                            s.trainer_card,
                        ),
                        CHOICE,
                        swap_ankimon_account,
                        cancel=outcome == "cancel",
                    )
                assert db.db_path == destination_path
                assert persisted("destination") == before
                assert persisted("holder") is None
                swap_ankimon_account()
                print(
                    f"PASS: real account switch during {mode}/{outcome} modal receives no previous battle rewards"
                )
        finally:
            db.close()
            for widget in d.env.app.topLevelWidgets():
                widget.close()
            d.env.app.processEvents()
    return True


if __name__ == "__main__":
    raise SystemExit(0 if run_proof() else 1)
