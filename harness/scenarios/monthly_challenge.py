"""PR #827 regressions with real Qt modal loops and disposable SQLite saves.

Run ``python -m harness.scenarios.monthly_challenge`` in the Tier-2 environment.
Audio is substituted: these tests exercise dialogs, not native audio devices.
Set ANKIMON_MONTHLY_SCREENSHOTS to an output directory for sprite screenshots.
"""

import copy
import faulthandler
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCENARIOS = (
    "overlap",
    "profile",
    "warning_switch",
    "refresh",
    "reclaim",
    "atomic_failure",
    "stale_decision",
    "sprites",
    "pending_learn_reject",
    "pending_learn_close",
    "pending_tm_reject",
    "pending_tm_close",
    "pending_tm_replacement_reject",
    "missing_move_row",
    "tm_progress_accept",
    "tm_progress_cancel",
    "tm_moves_changed",
)


def wide_gif():
    """143x24 two-colour GIF; clear each LZW pixel to keep three-bit codes."""
    codes = [code for _ in range(143 * 24) for code in (4, 1)] + [5]
    bits = sum(code << (3 * i) for i, code in enumerate(codes))
    data = bits.to_bytes((len(codes) * 3 + 7) // 8, "little")
    blocks = b"".join(
        bytes([len(data[i : i + 255])]) + data[i : i + 255]
        for i in range(0, len(data), 255)
    )
    size = struct.pack("<HH", 143, 24)
    return (
        b"GIF89a"
        + size
        + b"\x80\0\0\0\0\0\xd2\x32\x5a"
        + b","
        + b"\0" * 4
        + size
        + b"\0\x02"
        + blocks
        + b"\0;"
    )


def monthly_action():
    """Locate the live Monthly Challenge menu action.

    The action lives at ``mw.pokemenu > Profile > Monthly Challenge``. Since the
    menu is built dynamically at startup and the action is registered with
    objectName "ankimon_monthly_challenge", we retrieve it by searching through
    the profile_menu actions.
    """
    import Ankimon.menu_buttons as menu_buttons

    profile_menu = menu_buttons.profile_menu

    for action in profile_menu.actions():
        if action.objectName() == "ankimon_monthly_challenge":
            return action

    raise AssertionError("Monthly Challenge action is not found in profile_menu")


def run(scenario):
    from PyQt6.QtWidgets import QApplication, QDialog, QPushButton, QLabel
    from PyQt6.QtCore import QTimer
    import PyQt6.QtMultimedia as audio

    for name in ("QSoundEffect", "QAudioOutput", "QMediaPlayer"):
        setattr(audio, name, MagicMock)
    native_exec = QDialog.exec
    from harness.bootstrap import quiet
    from harness.real_driver import RealDriver
    import requests

    def offline(*args, **kwargs):
        raise requests.exceptions.ConnectionError(
            "monthly regression: controlled network"
        )

    with (
        patch.object(requests, "get", side_effect=offline),
        patch.object(requests, "post", side_effect=offline),
        quiet(),
    ):
        d = RealDriver(
            first_run=True,
            first_encounter=False,
            neuter_network=False,
            settings_overrides={
                "gui.show_sprites_across_ankimon": False,
                "mobile.enabled": False,
                "misc.ankiweb_sync": False,
            },
        )
    QDialog.exec = lambda self: native_exec(self)
    import Ankimon.pyobj.pokemon_trade as trade
    import Ankimon.profile_hooks as profiles

    db = d.services.db
    logger = SimpleNamespace(
        log=lambda level, message: print(level, message, flush=True)
    )
    profiles.logger = logger
    db.set_user_data("rate_this", True)
    root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (root / "assets/challenges/monthly_challenges.json").read_text()
    )[-1]
    # English month names independent of the process locale.
    months = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    now = datetime.now()
    payload["month"] = f"{months[now.month - 1]} {now.year}"
    iid = payload["pokemon"]["individual_id"]
    response = SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: [copy.deepcopy(payload)]
    )
    queued = []
    d.aqt.mw.taskman.run_in_background = lambda task, done: queued.append((task, done))
    timer_errors = []
    seen = []

    def later(action):
        def checked():
            try:
                action()
            except BaseException as error:
                timer_errors.append(error)
                dialog = QApplication.activeModalWidget()
                if dialog:
                    dialog.reject()

        QTimer.singleShot(0, checked)

    def press(name):
        dialog = QApplication.activeModalWidget()
        assert dialog is not None
        button = dialog.findChild(QPushButton, name)
        assert button is not None, (dialog.windowTitle(), name)
        button.click()

    def press_text(text):
        dialog = QApplication.activeModalWidget()
        assert dialog is not None
        button = next(
            (
                button
                for button in dialog.findChildren(QPushButton)
                if button.text() == text
            ),
            None,
        )
        assert button is not None, (dialog.windowTitle(), text)
        button.click()

    def schedule_manual_acceptance():
        def accept():
            later(lambda: press_text("Let's go!"))
            press("acceptBtn")

        later(accept)

    def finish():
        task, done = queued.pop(0)
        # Database access on this thread would violate the fetch-only contract.
        db_methods = (
            "get_user_data",
            "get_pokemon",
            "set_monthly_challenge_state",
            "save_pokemon",
        )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for name in db_methods:
                stack.enter_context(
                    patch.object(
                        db, name, side_effect=AssertionError("worker touched DB")
                    )
                )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(task)
                assert future.result(timeout=5) is not None
        done(future)
        # Automatic results now leave the worker-completion stack and wait for
        # idle Anki. Deliver the real Qt timer (including its modal loops)
        # before inspecting the award; explicit menu requests finish directly.
        deadline = time.monotonic() + 5
        while getattr(d.services, "_monthly_challenge_request", None) is not None:
            QApplication.processEvents()
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "Monthly challenge result did not finish while Anki was idle"
                )
            time.sleep(0.001)

    def close_notice(fn):
        def wrapped(*args, **kwargs):
            later(lambda: QApplication.activeModalWidget().accept())
            return fn(*args, **kwargs)

        return wrapped

    native_decision = trade.show_monthly_challenge_dialog

    def decision(*args, **kwargs):
        seen.append("decision")
        later(lambda: press("acceptBtn"))
        return native_decision(*args, **kwargs)

    with (
        patch.object(trade.requests, "get", return_value=response),
        patch.object(trade, "show_monthly_challenge_dialog", side_effect=decision),
        patch.object(
            trade,
            "show_monthly_acceptance_dialog",
            side_effect=close_notice(trade.show_monthly_acceptance_dialog),
        ),
        patch.object(
            trade,
            "show_monthly_rejection_dialog",
            side_effect=close_notice(trade.show_monthly_rejection_dialog),
        ),
    ):
        if scenario == "overlap":

            def nested(*args, **kwargs):
                seen.append("decision")

                def request_again():
                    trade.check_and_award_monthly_pokemon(logger)
                    assert not queued, "nested dialog started another request"
                    press("acceptBtn")

                later(request_again)
                return native_decision(*args, **kwargs)

            trade.show_monthly_challenge_dialog.side_effect = nested
            trade.check_and_award_monthly_pokemon(logger)
            trade.check_and_award_monthly_pokemon(logger)
            assert len(queued) == 1
            finish()
            assert seen == ["decision"]
            assert db.get_pokemon(iid) is not None
            assert db.get_user_data("monthly_challenge") == 1

        elif scenario == "profile":
            handler = profiles._on_profile_did_open(True)
            with (
                patch.object(profiles, "show_tip_of_the_day"),
                patch.object(profiles, "setup_ankimon_sync_hooks"),
            ):
                handler()
                old_done = queued.pop()[1]
                old_col = d.aqt.mw.col
                d.aqt.mw.col = SimpleNamespace(path="other.anki2", db=old_col.db)
                handler()
                new_done = queued.pop()[1]
                ready = Future()
                ready.set_result(True)
                old_done(ready)
                assert not queued
                new_done(ready)
                assert len(queued) == 1
                finish()
            assert seen == ["decision"]

        elif scenario in ("warning_switch", "refresh"):
            from Ankimon.singletons import get_pokemon_pc

            pc = get_pokemon_pc()
            original = db.db_path.name
            db.switch_database("monthly-B.db")
            db.set_monthly_challenge_state("keep-B", 2)
            db.switch_database(original)
            native_warning = trade.show_warning_with_traceback
            warnings = []

            def warning(*args, **kwargs):
                def close_error():
                    warnings.append("error")
                    assert db.get_pokemon(iid) is not None
                    assert db.get_user_data("monthly_challenge") == 1
                    if scenario == "warning_switch":
                        db.switch_database("monthly-B.db")
                    press("ok")

                later(close_error)
                return native_warning(*args, **kwargs)

            import Ankimon.pyobj.error_handler as errors

            with (
                patch.object(
                    pc,
                    "refresh_pokemon_grid",
                    side_effect=RuntimeError("injected refresh error"),
                ),
                patch.object(trade, "show_warning_with_traceback", side_effect=warning),
                patch.object(
                    errors,
                    "load_error_images",
                    return_value={"path": "", "credit": "", "url": ""},
                ),
            ):
                trade.check_and_award_monthly_pokemon(logger)
                finish()
            assert warnings == ["error"]
            if scenario == "warning_switch":
                assert db.get_user_data("monthly_challenge_id") == "keep-B"
                assert db.get_user_data("monthly_challenge") == 2
                assert db.get_pokemon(iid) is None
                trade.show_monthly_acceptance_dialog.assert_not_called()
                db.switch_database(original)
            assert db.get_pokemon(iid) is not None
            assert db.get_user_data("monthly_challenge") == 1

        elif scenario == "reclaim":

            def reject(*args, **kwargs):
                later(lambda: press("rejectBtn"))
                return native_decision(*args, **kwargs)

            trade.show_monthly_challenge_dialog.side_effect = reject
            trade.check_and_award_monthly_pokemon(logger)
            finish()
            assert db.get_user_data("monthly_challenge") == 2
            trade.show_monthly_challenge_dialog.reset_mock()
            trade.check_and_award_monthly_pokemon(logger)
            finish()
            trade.show_monthly_challenge_dialog.assert_not_called()
            trade.show_monthly_challenge_dialog.side_effect = decision
            action = monthly_action()
            trade.check_and_award_monthly_pokemon(logger)
            schedule_manual_acceptance()
            action.trigger()
            assert len(queued) == 1
            finish()
            assert db.get_pokemon(iid) is not None
            assert db.get_user_data("monthly_challenge") == 1
            # Reopening an owned challenge reports progress without a new award.
            trade.show_monthly_challenge_dialog.reset_mock()
            trade.check_and_award_monthly_pokemon(logger)
            action.trigger()
            assert len(queued) == 1

            def close_owned_progress():
                dialog = QApplication.activeModalWidget()
                assert dialog is not None
                details = dialog.findChild(QLabel, "descLabel")
                assert details is not None and "Pokémon Defeated:" in details.text()
                accept_button = dialog.findChild(QPushButton, "acceptBtn")
                reject_button = dialog.findChild(QPushButton, "rejectBtn")
                assert accept_button is not None and not accept_button.isEnabled()
                assert reject_button is not None and reject_button.isEnabled()
                press_text("Close")

            later(close_owned_progress)
            finish()
            trade.show_monthly_challenge_dialog.assert_not_called()
            assert db.get_pokemon(iid) is not None
            assert db.get_user_data("monthly_challenge") == 1

        elif scenario == "atomic_failure":
            db.set_monthly_challenge_state(iid, 2)
            conn = db._get_connection()
            conn.execute("""CREATE TRIGGER fail_monthly_accept BEFORE INSERT ON user_data
                            WHEN NEW.key = 'monthly_challenge' AND NEW.value = '1'
                            BEGIN SELECT RAISE(ABORT, 'injected monthly state failure'); END""").close()
            conn.commit()
            native_warning = trade.show_warning_with_traceback
            warnings = []

            def warning(*args, **kwargs):
                def close_error():
                    warnings.append("error")
                    assert db.get_pokemon(iid) is None
                    assert db.get_user_data("monthly_challenge") == 2
                    assert not conn.in_transaction
                    press("ok")

                later(close_error)
                return native_warning(*args, **kwargs)

            import Ankimon.pyobj.error_handler as errors

            action = monthly_action()
            with (
                patch.object(trade, "show_warning_with_traceback", side_effect=warning),
                patch.object(
                    errors,
                    "load_error_images",
                    return_value={"path": "", "credit": "", "url": ""},
                ),
                patch.object(trade, "_refresh_collection") as refresh,
            ):
                action.trigger()
                schedule_manual_acceptance()
                finish()
                assert warnings == ["error"]
                refresh.assert_not_called()
                trade.show_monthly_acceptance_dialog.assert_not_called()
                assert d.services._monthly_challenge_request is None
                conn.execute("DROP TRIGGER fail_monthly_accept").close()
                conn.commit()
                action.trigger()
                schedule_manual_acceptance()
                finish()
                refresh.assert_called_once()
                trade.show_monthly_acceptance_dialog.assert_not_called()
            assert db.get_pokemon(iid) is not None
            assert db.get_user_data("monthly_challenge") == 1

        elif scenario == "stale_decision":

            def external_award(*args, **kwargs):
                def change_and_reject():
                    owned = trade.create_monthly_challenge_pokemon(payload["pokemon"])
                    owned["level"] = 70
                    db.save_pokemon(owned)
                    db.set_monthly_challenge_state(iid, 1)
                    press("rejectBtn")

                later(change_and_reject)
                return native_decision(*args, **kwargs)

            trade.show_monthly_challenge_dialog.side_effect = external_award
            trade.check_and_award_monthly_pokemon(logger)
            finish()
            assert db.get_pokemon(iid)["level"] == 70
            assert db.get_user_data("monthly_challenge") == 1

        elif scenario.startswith(("pending_", "tm_")) or scenario == "missing_move_row":
            from harness.fixtures import build_pokemon, seed_db
            from Ankimon.functions.update_main_pokemon import update_main_pokemon
            from Ankimon.pyobj.move_picker import MovePickerDialog
            from Ankimon.utils import is_alive

            pc = d.services.pokemon_pc
            captured = {}

            def open_editor(four_moves=False):
                moves = ["thundershock", "quickattack"]
                if four_moves:
                    moves += ["tailwhip", "growl"]
                seed_db(
                    {
                        "main": {
                            "species": "Bulbasaur",
                            "level": 10,
                            "individual_id": "monthly-picker-backup",
                        },
                        "box": [
                            {
                                "species": "Pikachu",
                                "level": 50,
                                "individual_id": iid,
                                "moves": moves,
                            }
                        ],
                    },
                    db,
                )
                update_main_pokemon(d.services.main_pokemon)
                db.set_monthly_challenge_state(iid, 1)
                pc.show()
                pc.refresh_gui()
                pc.show_pokemon_details({"individual_id": iid})
                QApplication.processEvents()
                manager = pc.move_manager
                assert manager.isVisible()
                assert manager.slots[0]["learn_btn"].isVisible()
                return manager, moves

            def choose_move(picker):
                for row in range(picker.table.rowCount()):
                    picker.table.setCurrentCell(row, 0)
                    if picker.learn_btn.isEnabled():
                        break
                assert picker.learn_btn.isEnabled(), "Fixture needs an unknown move"
                captured["move"] = picker.get_selected_move()
                picker.learn_btn.click()

            # PyQt routes signal exceptions through sys.excepthook, separately
            # from exceptions in our timers. Both must fail the scenario.
            with patch.object(
                sys,
                "excepthook",
                side_effect=lambda kind, error, tb: timer_errors.append(error),
            ):
                if scenario == "missing_move_row":
                    for edit in ("learn", "forget", "cached_save"):
                        manager, _ = open_editor()
                        if edit == "learn":

                            def remove_and_learn():
                                picker = QApplication.activeModalWidget()
                                assert isinstance(picker, MovePickerDialog)
                                assert db.reject_monthly_challenge(iid)
                                # No PC refresh: the save itself must revalidate
                                # the row even when the editor remains cached.
                                choose_move(picker)

                            later(remove_and_learn)
                            manager.slots[0]["learn_btn"].click()
                        elif edit == "forget":
                            manager.slots[0]["forget_btn"].click()
                            QApplication.processEvents()
                            yes = next(
                                button
                                for button in manager.findChildren(QPushButton)
                                if button.text() == "Yes" and button.isVisible()
                            )
                            assert db.reject_monthly_challenge(iid)
                            yes.click()
                        else:
                            save = manager.save_fn
                            stale_data = copy.deepcopy(manager.full_data)
                            assert db.reject_monthly_challenge(iid)
                            save(stale_data)
                        assert not timer_errors, timer_errors
                        assert db.get_pokemon(iid) is None, (
                            f"Stale {edit} recreated the deleted row"
                        )
                        assert db.get_user_data("monthly_challenge") == 2
                elif scenario.startswith("tm_"):
                    from Ankimon.pyobj.attack_dialog import AttackDialog

                    manager, before = open_editor(four_moves=True)
                    assert db.add_item("protect", 1, {"type": "TM"})
                    changed_moves = ["thunderbolt", *before[1:]]

                    def update_during_replacement():
                        dialog = QApplication.activeModalWidget()
                        assert isinstance(dialog, AttackDialog) and dialog.isVisible()
                        fresh = db.get_pokemon(iid)
                        fresh.update(level=51, xp=123, nickname="Fresh progress")
                        if scenario == "tm_moves_changed":
                            fresh["attacks"] = changed_moves
                        assert db.save_pokemon(fresh)
                        if scenario == "tm_progress_cancel":
                            press_text("Reject Attack")
                        else:
                            button = next(
                                button
                                for button in dialog.findChildren(QPushButton)
                                if button.property("raw_move") == before[0]
                            )
                            button.click()

                    def choose_tm():
                        picker = QApplication.activeModalWidget()
                        assert isinstance(picker, MovePickerDialog)
                        later(update_during_replacement)
                        choose_move(picker)

                    later(choose_tm)
                    manager.tm_btn.click()
                    assert not timer_errors, timer_errors
                    assert captured["move"] == "protect"
                    saved = db.get_pokemon(iid)
                    assert saved["level"] == 51, (
                        "TM replacement overwrote newer progress"
                    )
                    assert saved["xp"] == 123 and saved["nickname"] == "Fresh progress"
                    if scenario == "tm_progress_accept":
                        expected = ["protect", *before[1:]]
                    elif scenario == "tm_moves_changed":
                        expected = changed_moves
                    else:
                        expected = before
                    assert saved["attacks"] == expected
                else:
                    is_tm = scenario.startswith("pending_tm_")
                    replacement = scenario == "pending_tm_replacement_reject"
                    rejecting = scenario.endswith("reject")
                    manager, before = open_editor(four_moves=replacement)
                    payload["pokemon"] = build_pokemon(
                        {
                            "species": "Pikachu",
                            "level": 1,
                            "individual_id": iid,
                        }
                    ).to_dict()
                    if is_tm:
                        assert db.add_item("protect", 1, {"type": "TM"})

                    def reject_monthly():
                        later(lambda: press_text("Yes"))
                        press("rejectBtn")

                    def complete_fetch():
                        modal = QApplication.activeModalWidget()
                        if replacement:
                            from Ankimon.gui_classes.pokemon_details import AttackDialog

                            assert isinstance(modal, AttackDialog)
                        else:
                            assert isinstance(modal, MovePickerDialog)
                        captured["modal"] = modal
                        assert modal.isVisible(), (
                            "Fetch must complete during the move dialog"
                        )
                        later(
                            reject_monthly if rejecting else lambda: press_text("Close")
                        )
                        finish()
                        if rejecting:
                            assert db.get_pokemon(iid) is None
                            assert db.get_user_data("monthly_challenge") == 2
                            assert pc._selected_individual_id is None
                            assert not is_alive(manager) or not manager.isVisible()
                            assert not is_alive(modal) or not modal.isVisible(), (
                                "Rejection left a move dialog open"
                            )
                            if is_alive(modal):
                                assert modal.result() == QDialog.DialogCode.Rejected
                        else:
                            assert db.get_pokemon(iid)["attacks"] == before
                            assert db.get_user_data("monthly_challenge") == 1
                            assert QApplication.activeModalWidget() is modal
                            assert modal.isVisible() and manager.isVisible()
                            choose_move(modal)

                    def enter_replacement():
                        picker = QApplication.activeModalWidget()
                        assert isinstance(picker, MovePickerDialog)
                        # The next timer runs in the real four-move replacement
                        # dialog, after accepting the first TM picker.
                        later(complete_fetch)
                        choose_move(picker)

                    monthly_action().trigger()
                    assert len(queued) == 1
                    later(enter_replacement if replacement else complete_fetch)
                    if is_tm:
                        manager.tm_btn.click()
                    else:
                        manager.slots[0]["learn_btn"].click()
                    assert not timer_errors, timer_errors
                    if rejecting:
                        assert db.get_pokemon(iid) is None, (
                            "A move dialog resurrected the rejected reward"
                        )
                        assert db.get_user_data("monthly_challenge") == 2
                    else:
                        expected = (
                            before + [captured["move"]]
                            if is_tm
                            else [captured["move"], *before[1:]]
                        )
                        assert db.get_pokemon(iid)["attacks"] == expected
                        assert db.get_user_data("monthly_challenge") == 1

        elif scenario == "sprites":
            import Ankimon.functions.sprite_functions as sprites

            path = Path(d.env.user_path) / "wide.gif"
            path.write_bytes(wide_gif())
            for container, size in ((160, 120), (80, 64)):
                with patch.object(sprites, "get_sprite_path", return_value=str(path)):
                    box = trade._build_sprite_box(
                        container, size, payload["pokemon"], True
                    )
                    hidden = trade._build_sprite_box(
                        container, size, payload["pokemon"], False
                    )
                box.show()
                QApplication.processEvents()
                label = box.findChild(QLabel)
                movie = label.movie()
                assert movie is not None and movie.isValid()
                movie.jumpToFrame(0)
                frame = movie.currentPixmap()
                print("sprite", size, frame.width(), frame.height(), flush=True)
                assert (
                    frame.width() == size and abs(frame.height() - 24 * size / 143) <= 1
                )
                assert movie.parent() is label
                assert hidden.findChild(QLabel).movie() is None
                output = os.environ.get("ANKIMON_MONTHLY_SCREENSHOTS")
                if output:
                    Path(output).mkdir(parents=True, exist_ok=True)
                    assert box.grab().save(str(Path(output) / f"monthly-{size}.png"))
                box.close()
                hidden.close()

    assert not timer_errors, timer_errors
    assert getattr(d.services, "_monthly_challenge_request", None) is None
    print(f"PASS monthly_challenge: {scenario}", flush=True)


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import PyQt6.QtWidgets
    except ImportError:
        print("NO_QT: monthly challenge requires the Tier-2 environment")
        return 77
    if len(sys.argv) == 1:
        for scenario in SCENARIOS:
            result = subprocess.run(
                [sys.executable, "-m", "harness.scenarios.monthly_challenge", scenario],
                timeout=45,
            )
            if result.returncode:
                return result.returncode
        return 0
    scenario = sys.argv[1]
    if scenario not in SCENARIOS:
        raise ValueError(scenario)
    faulthandler.dump_traceback_later(30, exit=True)
    run(scenario)
    faulthandler.cancel_dump_traceback_later()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
