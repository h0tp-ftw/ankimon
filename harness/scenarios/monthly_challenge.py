"""PR #827 regressions with real Qt modal loops and disposable SQLite saves.

Run ``python -m harness.scenarios.monthly_challenge`` in the Tier-2 environment.
Audio is substituted: these tests exercise dialogs, not native audio devices.
Set ANKIMON_MONTHLY_SCREENSHOTS to an output directory for dialog/sprite screenshots.
"""

import copy
import faulthandler
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
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
    "dialogs",
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
            first_encounter=False,
            neuter_network=False,
            settings_overrides={
                "gui.show_sprites_across_ankimon": False,
                "mobile.enabled": False,
                "misc.ankiweb_sync": False,
            },
        )
    QDialog.exec = lambda self: native_exec(self)
    import Ankimon.pyobj.monthly_challenge as trade
    import Ankimon.pyobj.monthly_challenge_dialogs as dialogs
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
            import Ankimon.menu_buttons as menus

            action = next(
                a
                for a in menus.profile_menu.actions()
                if a.objectName() == "ankimon_monthly_challenge"
            )
            trade.check_and_award_monthly_pokemon(logger)
            action.trigger()
            assert len(queued) == 1
            finish()
            assert db.get_pokemon(iid) is not None
            assert db.get_user_data("monthly_challenge") == 1
            # Reopening an owned challenge reports progress without a new award.
            trade.show_monthly_challenge_dialog.reset_mock()
            with patch.object(d.services.ui, "notify") as notify:
                trade.check_and_award_monthly_pokemon(logger)
                action.trigger()
                assert len(queued) == 1
                finish()
                notify.assert_called_once()
            trade.show_monthly_challenge_dialog.assert_not_called()

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
            import Ankimon.menu_buttons as menus

            action = next(
                a
                for a in menus.profile_menu.actions()
                if a.objectName() == "ankimon_monthly_challenge"
            )
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
                finish()
                assert warnings == ["error"]
                refresh.assert_not_called()
                trade.show_monthly_acceptance_dialog.assert_not_called()
                assert d.services._monthly_challenge_request is None
                conn.execute("DROP TRIGGER fail_monthly_accept").close()
                conn.commit()
                action.trigger()
                finish()
                refresh.assert_called_once()
                trade.show_monthly_acceptance_dialog.assert_called_once()
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

        elif scenario == "sprites":
            path = Path(d.env.user_path) / "wide.gif"
            path.write_bytes(wide_gif())
            for container, size in ((160, 120), (80, 64)):
                with patch.object(dialogs, "get_sprite_path", return_value=str(path)):
                    box = dialogs._build_sprite_box(
                        container, size, payload["pokemon"], True
                    )
                    hidden = dialogs._build_sprite_box(
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

        elif scenario == "dialogs":
            from PyQt6.QtCore import QPoint, QRect
            from PyQt6.QtGui import QTextDocument
            from PyQt6.QtWidgets import QFrame
            from aqt.theme import theme_manager

            description = "\n".join(
                (
                    "Keep <b>literal markup</b>, ampersands & quoted 'instructions' visible while training this month's special Pokémon.",
                    "Review a little each day, then return to the collection to follow your companion's progress as it grows stronger.",
                    "This longer line exercises wrapping across the available space so that every part of the challenge remains readable.",
                    'An <img src="missing.png"> example is text, and the final line must stay visible above both decision buttons.',
                )
            )
            rendered_backgrounds = {}
            completed = []

            def inspect_dialog(theme, kind, button_name=None):
                dialog = QApplication.activeModalWidget()
                assert dialog is not None
                assert dialog.parentWidget() is d.aqt.mw
                assert dialog.findChild(QFrame, "spriteBox") is None
                QApplication.processEvents()
                for label in dialog.findChildren(QLabel):
                    if label.isVisible():
                        rect = QRect(label.mapTo(dialog, QPoint()), label.size())
                        assert dialog.rect().contains(rect), (kind, label.text(), rect)
                        needed = label.heightForWidth(label.width())
                        assert needed <= label.height() + 2, (
                            kind,
                            needed,
                            label.height(),
                        )
                if kind == "decision":
                    label = dialog.findChild(QLabel, "descLabel")
                    assert label is not None and label.wordWrap()
                    rendered = QTextDocument()
                    rendered.setHtml(label.text())
                    assert rendered.toPlainText().replace("\u2028", "\n") == description
                capture = dialog.grab()
                rendered_backgrounds[(theme, kind)] = (
                    capture.toImage().pixelColor(2, 2).lightness()
                )
                output = os.environ.get("ANKIMON_MONTHLY_SCREENSHOTS")
                if output:
                    Path(output).mkdir(parents=True, exist_ok=True)
                    assert capture.save(
                        str(Path(output) / f"monthly-{theme}-{kind}.png")
                    )
                if button_name:
                    press(button_name)
                else:
                    buttons = dialog.findChildren(QPushButton)
                    assert len(buttons) == 1 and buttons[0].isEnabled()
                    buttons[0].click()
                    assert dialog.result() == QDialog.DialogCode.Accepted
                completed.append((theme, kind))

            for theme, night_mode in (("light", False), ("dark", True)):
                with patch.object(theme_manager, "night_mode", night_mode):
                    for accepted, button_name in (
                        (False, "rejectBtn"),
                        (True, "acceptBtn"),
                    ):
                        later(
                            lambda theme=theme, name=button_name: inspect_dialog(
                                theme, "decision", name
                            )
                        )
                        result = dialogs.show_monthly_challenge_dialog(
                            payload["pokemon"], description, parent_window=d.aqt.mw
                        )
                        assert result is accepted
                        kind = "acceptance" if accepted else "rejection"
                        later(
                            lambda theme=theme, kind=kind: inspect_dialog(theme, kind)
                        )
                        confirm = (
                            dialogs.show_monthly_acceptance_dialog
                            if accepted
                            else dialogs.show_monthly_rejection_dialog
                        )
                        confirm(
                            parent_window=d.aqt.mw, challenge_pokemon=payload["pokemon"]
                        )
            assert len(completed) == 8
            for kind in ("decision", "acceptance", "rejection"):
                assert (
                    rendered_backgrounds[("light", kind)]
                    > rendered_backgrounds[("dark", kind)]
                )

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
