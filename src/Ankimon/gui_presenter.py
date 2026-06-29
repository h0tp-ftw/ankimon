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

from aqt.qt import QDialog
from aqt.utils import showInfo, showWarning

from .classes.choose_move_dialog import MoveSelectionDialog
from .pyobj.attack_dialog import AttackDialog
from .pyobj.error_handler import show_warning_with_traceback


class QtPresenter:
    """Shows real Qt dialogs for the UI-port interactions."""

    def choose_move(self, attacks):
        dialog = MoveSelectionDialog(list(attacks))
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_move:
            return dialog.selected_move
        return None

    def choose_attack_to_replace(self, attacks, new_attack):
        dialog = AttackDialog(list(attacks), new_attack)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.selected_attack
        return None

    def notify(self, level, message):
        if level == "warning":
            showWarning(str(message))
        else:
            showInfo(str(message))

    def warn(self, message):
        showWarning(str(message))

    def report_error(self, exception, message=""):
        show_warning_with_traceback(exception=exception, message=message)
