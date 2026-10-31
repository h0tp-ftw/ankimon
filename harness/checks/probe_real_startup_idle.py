"""Check startup scheduling with real Anki workers and native Qt progress dialogs.

Requires an installed ``aqt`` package and its Qt dependencies, beyond the usual
Tier-2 environment. Run in a fresh process with:

    QT_QPA_PLATFORM=offscreen python -m harness.checks.probe_real_startup_idle

The window and collection identity are disposable stand-ins; TaskManager,
QueryOp, ProgressManager, QApplication and modal dialogs are real Anki/Qt.
No profile, user data, network requests or full add-on import are needed.
The ``probe_real_`` prefix excludes this optional probe from the Tier-1 gate.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import weakref
from pathlib import Path
from types import ModuleType

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import aqt
from aqt.progress import ProgressManager
from aqt.qt import QApplication, QDialog, QMainWindow, Qt
from aqt.taskman import TaskManager


class Window(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.col = object()
        self._background_op_count = 0
        self.taskman = TaskManager(self)
        self.progress = ProgressManager(self)

    def weakref(self):
        return weakref.proxy(self)

    def _increase_background_ops(self):
        self._background_op_count += 1

    def _decrease_background_ops(self):
        self._background_op_count -= 1

    def inMainThread(self):
        return threading.current_thread() is threading.main_thread()


def main():
    app = QApplication.instance() or QApplication([])
    mw = Window(app)
    aqt.mw = mw
    mw.show()

    # Import only the production scheduling helper and service registry. The
    # package stub prevents a full add-on startup from using any real profile.
    root = Path(__file__).resolve().parents[2]
    package = ModuleType("Ankimon")
    package.__path__ = [str(root / "src" / "Ankimon")]
    sys.modules["Ankimon"] = package
    from Ankimon.background_ops import call_when_anki_idle, run_query
    from Ankimon.services import services

    def pump_until(predicate, message, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError(message)
            app.processEvents()
            time.sleep(0.005)

    def pump_for(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)

    entered, release = threading.Event(), threading.Event()

    def collection_work():
        entered.set()
        assert release.wait(15), "controlled collection worker was not released"

    blocker = mw.taskman.run_in_background(collection_work, uses_collection=True)
    try:
        assert entered.wait(1), "controlled collection worker did not start"
        results, failures, ui = [], [], []
        mw.progress.start(label="Checking", immediate=True)

        def completed(value):
            results.append(value)
            call_when_anki_idle(
                lambda: ui.append(("ready", mw.inMainThread())),
                "startup",
                profile_bound=False,
            )

        run_query(
            lambda: ("finished", not mw.inMainThread()), completed, failures.append
        )
        pump_until(
            lambda: results or failures,
            "collection-free work blocked behind the collection executor",
        )
        assert results == [("finished", True)] and not failures
        assert mw._background_op_count == 0
        assert mw.progress.busy() and not ui
        pump_until(lambda: mw.progress._win.isVisible(), "native progress did not show")
        assert QApplication.activeModalWidget() is mw.progress._win

        # Global startup must survive closing/replacing the Anki collection
        # while progress is active, then complete on the main thread.
        mw.col = None
        pump_for(0.12)
        assert not ui
        mw.col = object()
        mw.progress.finish()
        pump_until(lambda: ui, "startup never resumed after native progress ended")
        assert ui == [("ready", True)]
        assert mw.progress.busy() == 0

        # Other native modal windows also hold automatic UI until closure.
        modal = QDialog(mw)
        modal.setWindowModality(Qt.WindowModality.ApplicationModal)
        modal.show()
        pump_until(
            lambda: QApplication.activeModalWidget() is modal,
            "test modal never became active",
        )
        call_when_anki_idle(
            lambda: ui.append(("modal-clear", mw.inMainThread())),
            "automatic profile dialog",
        )
        pump_for(0.12)
        assert len(ui) == 1
        modal.hide()
        pump_until(
            lambda: len(ui) == 2, "automatic UI did not resume after modal closed"
        )
        assert ui[-1] == ("modal-clear", True)

        # Profile-specific requests still cancel when their collection changes.
        cancelled = []
        mw.progress.start(label="Checking", immediate=True)
        call_when_anki_idle(
            lambda: ui.append(("stale", True)),
            "profile owned",
            on_cancel=lambda: cancelled.append(True),
        )
        mw.col = object()
        pump_until(lambda: cancelled, "profile callback was not cancelled")
        mw.progress.finish()
        pump_until(lambda: not mw.progress.busy(), "progress cleanup did not finish")
        assert len(ui) == 2
        assert services._idle_callbacks == set()
    finally:
        release.set()
        blocker.result(timeout=1)
        mw.taskman._collection_executor.shutdown()
        mw.taskman._no_collection_executor.shutdown()
        mw.close()

    print(
        "probe_real_startup_idle: PASS — real Anki executor independence, "
        "native progress/modal deferral, profile-switch continuation, "
        "main-thread delivery and profile-bound cancellation"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
