"""Exercise Backup Manager with real SQLite, Chromium, WebChannel and Qt events.

Anki's host is disposable. QueryOp uses a worker thread and delivers through Qt,
so a status read waiting on import staging must not hold the GUI event loop.
Active-database filtering and queued native cancellation also cross the real
WebChannel boundary, including cancellation failure and dialog destruction.
Run: python -m harness.checks.probe_real_backup_manager
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import threading
import time
from unittest.mock import patch

os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --disable-dev-shm-usage"
)

from PyQt6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtTest import QTest
from PyQt6.QtWebEngineWidgets import QWebEngineView

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from harness.real_env import start_real_session
from harness.checks.probe_real_webengine import _run_javascript, _wait_until


class WorkerQueryOp(QObject):
    completed = pyqtSignal(object, object)
    active = set()
    gate = threading.Event()
    gate.set()

    def __init__(self, *, parent, op, success):
        super().__init__(parent)
        self.op, self.success = op, success
        self.failed = lambda error: (_ for _ in ()).throw(error)
        self.completed.connect(self.deliver)

    def failure(self, callback):
        self.failed = callback
        return self

    def without_collection(self):
        return self

    def run_in_background(self):
        self.active.add(self)

        def run():
            try:
                if not self.gate.wait(10):
                    raise TimeoutError("Controlled worker was not released")
                value = self.op(None)
            except Exception as error:
                self.completed.emit(None, error)
            else:
                self.completed.emit(value, None)

        threading.Thread(target=run, daemon=True).start()

    def deliver(self, value, error):
        self.active.discard(self)
        if error is not None:
            self.failed(error)
        else:
            self.success(value)
        self.deleteLater()


def main():
    session = start_real_session(webengine=True, require_webengine=True)
    from Ankimon.ankimon_items_web import shop_obj
    from Ankimon.singletons import get_items_window
    from Ankimon import save_import
    from Ankimon.pyobj import save_transfer
    import aqt.operations

    aqt.operations.QueryOp = WorkerQueryOp
    manager = session.Ankimon.backup_manager
    db = session.services.db
    # Real saves and summaries, including the permitted maximum-length name.
    db.set_config_value("trainer.name", "W" * 24)
    assert manager.create_backup(
        manual=True, show_notifications=False, context=manager.capture_context()
    )
    window = get_items_window(backup_manager=manager)
    window.load_screen(shop_obj.SCREEN_BACKUP)
    window.resize(1180, 720)
    window.show()
    view = window.webview_backup
    assert isinstance(view, QWebEngineView)
    page = view.page()

    def js(script):
        return _run_javascript(page, script)

    def wait(predicate, message):
        assert _wait_until(predicate), message

    wait(
        lambda: js("document.querySelectorAll('.backup-card').length") >= 1,
        "Backups were not loaded through WebChannel",
    )
    wait(lambda: not WorkerQueryOp.active, "Initial refresh did not finish")

    # A restore row must contain the active database, rather than merely some
    # save mode. Exercise filtering through real WebChannel status responses.
    original_trainers = js(
        "[...document.querySelectorAll('.backup-trainer strong')].map(e => e.textContent)"
    )
    snapshot = pathlib.Path(manager.get_backups()[0]["path"]) / "ankimon.db"
    fixture_directories = []
    for index, databases in enumerate(
        (
            {"ankimon.db": "NormalOnly"},
            {"ankimonDEV.db": "DeveloperOnly"},
            {"ankimon.db": "BothNormal", "ankimonDEV.db": "BothDeveloper"},
            {"custom-save.db": "CustomOnly"},
        )
    ):
        directory = manager.backups_path / f"backup_2000-01-0{index + 1}_00-00-00"
        directory.mkdir()
        fixture_directories.append(directory)
        summary = {"date": f"2000-01-0{index + 1} 00-00-00"}
        for filename, trainer in databases.items():
            shutil.copyfile(snapshot, directory / filename)
            key = "dev_stats" if filename == "ankimonDEV.db" else "normal_stats"
            summary[key] = {"trainer_name": trainer}
        (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    filtering_cases = (
        ("ankimon.db", original_trainers + ["NormalOnly", "BothNormal"]),
        ("ankimonDEV.db", ["DeveloperOnly", "BothDeveloper"]),
        ("custom-save.db", ["CustomOnly"]),
        ("ankimon.db", original_trainers + ["NormalOnly", "BothNormal"]),
    )
    for filename, expected in filtering_cases:
        if db.db_path.name != filename:
            assert db.switch_database(filename)
        js("window.refreshBackups()")
        wait(
            lambda: (
                sorted(
                    js(
                        "[...document.querySelectorAll('.backup-trainer strong')].map(e => e.textContent)"
                    )
                )
                == sorted(expected)
            ),
            f"Backup rows did not match active database {filename}: {expected}",
        )
        wait(lambda: not WorkerQueryOp.active, "Mode filtering refresh did not finish")
    for directory in fixture_directories:
        shutil.rmtree(directory)
    js("window.refreshBackups()")
    wait(
        lambda: (
            js(
                "[...document.querySelectorAll('.backup-trainer strong')].map(e => e.textContent)"
            )
            == original_trainers
        ),
        "Filtering fixtures were not removed from the rendered list",
    )
    wait(lambda: not WorkerQueryOp.active, "Filtering cleanup refresh did not finish")

    # Measure actual Chromium colours, including native pointer hover. Small
    # action labels need 4.5:1 contrast in either state.
    for selector in (".restore-btn", ".delete-btn"):
        for hovered in (False, True):
            point = js(f"""(() => {{
                const r = document.querySelector('{selector}').getBoundingClientRect();
                return {{x: r.x + r.width / 2, y: r.y + r.height / 2}};
            }})()""")
            QTest.mouseMove(
                view.focusProxy(),
                QPoint(int(point["x"]), int(point["y"])) if hovered else QPoint(2, 2),
            )
            QTest.qWait(100)
            contrast = js(f"""(() => {{
                const button = document.querySelector('{selector}');
                const s = getComputedStyle(button);
                const luminance = color => {{
                    const linear = color.match(/[\\d.]+/g).slice(0, 3).map(Number)
                        .map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
                    return linear.reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
                }};
                const values = [luminance(s.color), luminance(s.backgroundColor)].sort((a, b) => a - b);
                return {{ratio: (values[1] + .05) / (values[0] + .05), hovered: button.matches(':hover')}};
            }})()""")
            assert contrast["hovered"] == hovered, (selector, contrast)
            assert contrast["ratio"] >= 4.5, (selector, hovered, contrast)

    # A real status worker queues behind staging. The GUI timer must fire while
    # that lock is still held, not merely once the worker finally releases it.
    held, release = threading.Event(), threading.Event()

    def hold_staging():
        with save_import._STAGING_LOCK:
            held.set()
            assert release.wait(10)

    holder = threading.Thread(target=hold_staging)
    holder.start()
    assert held.wait(5)
    fired = []
    start = time.monotonic()
    QTimer.singleShot(30, lambda: fired.append(time.monotonic() - start))
    try:
        js("window.dispatchEvent(new Event('focus'))")
        wait(lambda: bool(fired), "GUI timer froze behind staging")
        assert fired[0] < 0.5, fired
        assert WorkerQueryOp.active, "Status unexpectedly bypassed the held lock"
        # Repeated focus events coalesce to one worker; an old reply may not
        # replace data from a newer profile/page request.
        js("for(let i=0;i<8;i++) window.dispatchEvent(new Event('focus'))")
        assert len(WorkerQueryOp.active) == 1
    finally:
        release.set()
        holder.join(5)
    wait(lambda: not WorkerQueryOp.active, "Status worker never completed")
    QTest.qWait(250)
    wait(lambda: not WorkerQueryOp.active, "Coalesced refresh never completed")

    for width, zoom in [(1180, 1), (1024, 1), (800, 1), (1180, 2)]:
        view.setZoomFactor(zoom)
        window.resize(width, 720)
        QTest.qWait(100)
        layout = js("""(() => {
            const viewport = document.querySelector('.content-scroll').getBoundingClientRect();
            const buttons = [...document.querySelectorAll('.backup-actions button')].map(b => b.getBoundingClientRect());
            return buttons.every(r => r.left >= viewport.left && r.right <= viewport.right + 1 && r.width > 0);
        })()""")
        assert layout, (width, zoom, "Backup actions overflow the viewport")
    view.setZoomFactor(1)
    window.resize(1180, 720)

    js(
        "document.querySelector('.restore-btn').focus(); document.querySelector('.restore-btn').click()"
    )
    assert js("document.activeElement.id") == "backup-modal-cancel"
    assert js("document.querySelector('.app-container').inert")
    assert js(
        "document.getElementById('backup-modal-message').textContent.includes('W'.repeat(24))"
    )
    for modifier in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
        for _ in range(5):
            QTest.keyClick(view.focusProxy(), Qt.Key.Key_Tab, modifier)
            assert js("document.activeElement.closest('#backup-modal') !== null")
    QTest.keyClick(view.focusProxy(), Qt.Key.Key_Escape)
    # Native Qt key delivery can complete before Chromium handles Escape.
    wait(
        lambda: js(
            "document.getElementById('backup-modal').classList.contains('hidden')"
        ),
        "Escape did not close the backup confirmation",
    )
    assert js("document.activeElement.classList.contains('restore-btn')")

    # Native menu navigation and window hide also retire destructive callbacks.
    for leave in ("navigation", "hide"):
        js("document.querySelector('.delete-btn').click()")
        if leave == "navigation":
            window.load_screen(shop_obj.SCREEN_ITEMS)
            window.load_screen(shop_obj.SCREEN_BACKUP)
        else:
            window.hide()
            window.show()
        wait(
            lambda: js(
                "document.getElementById('backup-modal').classList.contains('hidden')"
            ),
            "Destructive confirmation survived leaving the page",
        )
        js("document.getElementById('backup-modal-confirm').click()")
        assert manager.get_backups(), "Stale confirmation deleted the backup"
    js("window.dispatchEvent(new Event('focus'))")
    QTest.qWait(300)
    wait(lambda: not WorkerQueryOp.active, "Reentry refresh did not finish")

    # Dispatch through the real bridge, then switch Normal -> Developer ->
    # Normal before the worker starts. The unchanged final pathname must not
    # disguise the intervening database generation.
    for action in ("create", "restore"):
        paths_before = {
            row["path"] for row in manager.get_backups(include_inactive=True)
        }
        confirmed = window.backup_bridge._context_token()
        WorkerQueryOp.gate.clear()
        try:
            if action == "create":
                result = window.backup_bridge.createBackup(confirmed)
            else:
                result = window.backup_bridge.restoreBackup(
                    manager.get_backups()[0]["path"], confirmed
                )
            assert result.get("pending"), result
            assert db.switch_database("ankimonDEV.db")
            assert db.switch_database("ankimon.db")
        finally:
            WorkerQueryOp.gate.set()
        wait(lambda: not WorkerQueryOp.active, "Invalidated action did not finish")
        assert not window.backup_bridge._action_active
        assert {
            row["path"] for row in manager.get_backups(include_inactive=True)
        } == paths_before
        for name in ("ankimon.db", "ankimonDEV.db"):
            assert save_import.pending_import_info(db.db_path.parent / name) is None
    js("window.refreshBackups()")
    QTest.qWait(300)
    wait(lambda: not WorkerQueryOp.active, "Account-change refresh did not finish")

    # Real restore through Chromium, with an explicit warning persisted by the
    # staged-save API. Folder integration failure must keep every card and the
    # armed restore's restart/cancellation guidance visible.
    js(
        "document.querySelector('.restore-btn').click(); document.getElementById('backup-modal-confirm').click()"
    )
    wait(
        lambda: js(
            "document.getElementById('backup-status').textContent.includes('Restart') || document.getElementById('backup-status').textContent.includes('restart')"
        ),
        "Restore did not retain restart guidance",
    )
    wait(lambda: not WorkerQueryOp.active, "Restore worker did not finish")
    pending = save_import.pending_import_info(db.db_path)
    assert pending is not None
    save_import.set_pending_import_warning(
        db.db_path,
        "Damaged save preserved for recovery.",
        expected_token=pending["token"],
    )
    js("window.dispatchEvent(new Event('focus'))")
    wait(
        lambda: js(
            "document.getElementById('backup-status').textContent.includes('Damaged save')"
        ),
        "Warning not displayed",
    )
    before = js("document.getElementById('backup-status').textContent")
    cards = js("document.querySelectorAll('.backup-card').length")
    with patch.object(shop_obj.QDesktopServices, "openUrl", return_value=False):
        js("document.getElementById('open-folder-btn').click()")
        wait(
            lambda: js("document.getElementById('toast').classList.contains('error')"),
            "Folder failure not shown",
        )
    assert js("document.querySelectorAll('.backup-card').length") == cards
    assert js("document.getElementById('backup-status').textContent") == before
    assert "Cancel Pending Save Import" in before
    assert save_import.pending_import_info(db.db_path)["token"] == pending["token"]

    # Reserve a native cancellation before its worker can run. A fresh status
    # must disable the Web UI, and even a direct stale-page restore request must
    # refuse while cancellation is queued. Release it, then prepare a new save
    # through Chromium: the older cancellation must not remove that new import.
    cancel_entered, allow_cancel = threading.Event(), threading.Event()
    cancel_targets = save_transfer._cancel_import_targets

    def queued_cancel(targets, validate=None, operation=None):
        cancel_entered.set()
        if not allow_cancel.wait(30):
            raise TimeoutError("Controlled cancellation was not released")
        return cancel_targets(targets, validate, operation)

    with patch.object(save_transfer, "_cancel_import_targets", queued_cancel):
        try:
            save_transfer.cancel_pending_save_import_async(
                manager.capture_context(), notify=window.show_backup_notice
            )
            assert cancel_entered.wait(5), "Cancellation worker did not queue"
            assert save_import.save_operation_busy()
            confirmed = window.backup_bridge._context_token()
            rejected = window.backup_bridge.restoreBackup(
                manager.get_backups()[0]["path"], confirmed
            )
            assert rejected.get("ok") is False and not rejected.get("pending"), rejected
            assert (
                save_import.pending_import_info(db.db_path)["token"] == pending["token"]
            )
            js("window.refreshBackups()")
            wait(
                lambda: js("document.querySelector('.restore-btn').disabled"),
                "Queued native cancellation did not disable restore controls",
            )
            assert js("document.getElementById('manual-backup-btn').disabled")
            js("document.querySelector('.restore-btn').click()")
            assert js(
                "document.getElementById('backup-modal').classList.contains('hidden')"
            )
        finally:
            allow_cancel.set()
        wait(lambda: not WorkerQueryOp.active, "Cancellation did not complete")
    assert not save_import.save_operation_busy()
    assert save_import.pending_import_info(db.db_path) is None
    wait(
        lambda: js(
            "!document.getElementById('backup-modal').classList.contains('hidden')"
        ),
        "Native cancellation did not report its outcome",
    )
    js(
        "document.getElementById('backup-modal-confirm').click(); window.refreshBackups()"
    )
    wait(
        lambda: js("!document.querySelector('.restore-btn').disabled"),
        "Restore controls stayed disabled after cancellation",
    )
    js(
        "document.querySelector('.restore-btn').click(); document.getElementById('backup-modal-confirm').click()"
    )
    wait(
        lambda: save_import.pending_import_info(db.db_path) is not None,
        "A restore after cancellation was not staged",
    )
    wait(lambda: not WorkerQueryOp.active, "New restore did not complete")
    replacement = save_import.pending_import_info(db.db_path)
    assert replacement["token"] != pending["token"]
    QTest.qWait(100)
    assert save_import.pending_import_info(db.db_path)["token"] == replacement["token"]

    # Worker failure must release the reservation and preserve the prepared
    # replacement. If the user navigates away while cancellation is queued,
    # its error must also appear when they return to the cached backup page.
    for navigate_away in (False, True):
        message = f"Cancellation probe failure (navigation={navigate_away})"
        with patch.object(
            save_transfer, "_cancel_import_targets", side_effect=OSError(message)
        ):
            WorkerQueryOp.gate.clear()
            try:
                save_transfer.cancel_pending_save_import_async(
                    manager.capture_context(), notify=window.show_backup_notice
                )
                if navigate_away:
                    window.load_screen(shop_obj.SCREEN_ITEMS)
                    wait(
                        lambda: shop_obj.SCREEN_ITEMS in window.ready_screens,
                        "Items did not load while cancellation was queued",
                    )
            finally:
                WorkerQueryOp.gate.set()
            wait(
                lambda: not WorkerQueryOp.active, "Failed cancellation did not complete"
            )
        assert not save_import.save_operation_busy()
        assert (
            save_import.pending_import_info(db.db_path)["token"] == replacement["token"]
        )
        if navigate_away:
            assert window._pending_backup_notice == (
                f"The pending save import could not be cancelled: {message}",
                True,
            )
            window.load_screen(shop_obj.SCREEN_BACKUP)
        wait(
            lambda: js(
                "!document.getElementById('backup-modal').classList.contains('hidden')"
                f" && document.getElementById('backup-modal-message').textContent.includes({json.dumps(message)})"
            ),
            "Native cancellation failure was not reported on the backup page",
        )
        assert window._pending_backup_notice is None
        js(
            "document.getElementById('backup-modal-confirm').click(); window.refreshBackups()"
        )
        wait(
            lambda: js("!document.querySelector('.restore-btn').disabled"),
            "Restore controls stayed disabled after cancellation failure",
        )
        wait(lambda: not WorkerQueryOp.active, "Failure refresh did not complete")
    screenshot = os.environ.get("ANKIMON_BACKUP_PROBE_SCREENSHOT")
    if screenshot:
        assert view.grab().save(screenshot)

    # Native cancellation belongs to mw rather than the originating dialog. Its
    # worker must finish and release the reservation even if that dialog dies.
    cancel_entered.clear()
    allow_cancel.clear()
    with patch.object(save_transfer, "_cancel_import_targets", queued_cancel):
        try:
            save_transfer.cancel_pending_save_import_async(
                manager.capture_context(), notify=window.show_backup_notice
            )
            assert cancel_entered.wait(5), "Final cancellation worker did not queue"
            destroyed = []
            window.destroyed.connect(lambda: destroyed.append(True))
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert destroyed, "The originating dialog was not destroyed"
        finally:
            allow_cancel.set()
        wait(
            lambda: not WorkerQueryOp.active,
            "Cancellation did not survive dialog deletion",
        )
    assert save_import.pending_import_info(db.db_path) is None
    assert not save_import.save_operation_busy()
    session.app.processEvents()
    print(
        json.dumps(
            {
                "backup_manager": "passed",
                "gui_timer_seconds": fired[0],
                "layout_cases": 4,
                "active_database_filtering_cases": len(filtering_cases),
                "native_cancellation_cases": 4,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
