"""
gui_presenter.py — the Qt implementation of the UI presenter port.

Production wires ``services.ui`` to a ``QtPresenter`` so the few interactive
moments in the core logic (choose a move, overwrite a move when learning a 5th,
report an error) show the real dialogs. The headless agent harness keeps the
default :class:`Ankimon.ui_port.HeadlessPresenter` instead.

This module imports aqt/PyQt6 and the dialog classes, so it is GUI-only and must
never be imported headless. See :mod:`Ankimon.ui_port` for the contract.
"""

from __future__ import annotations

from aqt import mw
from aqt.qt import QDialog, QTimer
from aqt.utils import showInfo, showWarning

from .classes.choose_move_dialog import MoveSelectionDialog
from .pyobj.attack_dialog import AttackDialog
from .pyobj.error_handler import show_warning_with_traceback


class QtPresenter:
    """Shows real Qt dialogs for the UI-port interactions."""

    def choose_move(self, attacks):
        # Parent to mw and force it to the front: a parentless QDialog can
        # spawn behind the main Anki window (or the Ankimon battle popup)
        # with no taskbar/focus cue on some window managers — exec() still
        # blocks the calling turn on it, so the battle silently freezes with
        # nothing in the logs, since nothing actually failed; the dialog is
        # just invisible. (finding: turns stopped advancing despite correct
        # review answers, with controls.allow_to_choose_moves enabled.)
        dialog = MoveSelectionDialog(list(attacks), parent=mw)
        # Let exec() establish modality and show the dialog first, then raise
        # and activate it on the next event-loop iteration so it cannot appear
        # behind the Anki/battle windows.
        QTimer.singleShot(
            0,
            lambda: (
                dialog.raise_(),
                dialog.activateWindow(),
            ),
        )
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_move:
                return dialog.selected_move
            return None
        finally:
            dialog.deleteLater()

    def choose_attack_to_replace(self, attacks, new_attack):
        dialog = AttackDialog(list(attacks), new_attack, parent=mw)
        QTimer.singleShot(
            0,
            lambda: (
                dialog.raise_(),
                dialog.activateWindow(),
            ),
        )
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                return dialog.selected_attack
            return None
        finally:
            dialog.deleteLater()

    def notify(self, level, message):
        if level == "warning":
            showWarning(str(message))
        else:
            showInfo(str(message))

    def warn(self, message):
        showWarning(str(message))

    def report_error(self, exception, message=""):
        show_warning_with_traceback(exception=exception, message=message)
