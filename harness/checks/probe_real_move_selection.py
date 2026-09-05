"""Real-Qt regressions for controller input and move-dialog lifetime.

Run with ``python -m harness.checks.probe_real_move_selection``. Unlike the
ordinary Tier-2 boot probe, this restores the real blocking exec for
MoveSelectionDialog alone, to exercise real modal loops and the production
presenter's deferred deletion, and puts the harness stub back afterwards.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PyQt6.QtGui import QKeyEvent, QShortcut
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout

from harness.real_env import start_real_session

_REAL_EXEC = QDialog.exec
_ENV = None
MOVES = ["tackle", "growl", "scratch", "ember"]


def setUpModule():
    global _ENV
    _ENV = start_real_session()
    # real_env stubs QDialog.exec process-wide so a real boot can never hang on
    # a modal. Restore the blocking loop for this dialog alone -- restoring it
    # globally would leave any later dialog (an error box, a first-run prompt)
    # able to block until CI kills the job.
    # The lambda is load-bearing, not a stray wrapper: assigning the captured
    # sip method straight back (on either class) makes dialog.exec() raise
    # "first argument of unbound method must have type 'QDialog'", because the
    # unbound descriptor does not rebind when reassigned as a class attribute.
    from Ankimon.classes.choose_move_dialog import MoveSelectionDialog
    MoveSelectionDialog.exec = lambda self: _REAL_EXEC(self)


def tearDownModule():
    from Ankimon.classes.choose_move_dialog import MoveSelectionDialog
    # Drop the override so the inherited non-blocking stub applies again.
    del MoveSelectionDialog.exec


class MoveSelectionTests(unittest.TestCase):
    def setUp(self):
        from Ankimon.classes.choose_move_dialog import MoveSelectionDialog

        self.dialog_type = MoveSelectionDialog
        self.widgets = []
        self.qt_errors = []
        self.old_excepthook = sys.excepthook
        sys.excepthook = lambda kind, value, tb: self.qt_errors.append(value)

    def tearDown(self):
        for widget in reversed(self.widgets):
            if not sip.isdeleted(widget):
                widget.close()
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _ENV.app.processEvents()
        sys.excepthook = self.old_excepthook
        self.assertFalse(self.qt_errors, self.qt_errors)

    def dialog(self, moves=MOVES):
        dialog = self.dialog_type(moves, parent=_ENV.aqt.mw)
        self.widgets.append(dialog)
        dialog.setModal(True)
        dialog.show()
        dialog.activateWindow()
        dialog.setFocus()
        _ENV.app.processEvents()
        return dialog

    def key(self, target, key, modifiers=Qt.KeyboardModifier.NoModifier):
        # sendEvent(None, ...) segfaults rather than raising, so a runner whose
        # offscreen platform has not established focus yet must fail as an
        # assertion instead of taking the whole job down with signal 11.
        self.assertIsNotNone(target, "no target for the synthetic key event")
        # The same synchronous sendEvent press/release pair as Contanki.
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QCoreApplication.sendEvent(target, QKeyEvent(kind, key, modifiers))

    def test_numeric_press_release_selects_each_move(self):
        for index, key in enumerate((Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4)):
            with self.subTest(index=index):
                dialog = self.dialog()
                self.key(_ENV.app.focusObject(), key)
                QTest.qWait(80)
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertEqual(dialog.selected_move, MOVES[index])
                self.assertFalse(dialog.isVisible())

    def test_navigation_wraps_and_confirms(self):
        for confirm in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            with self.subTest(confirm=confirm):
                dialog = self.dialog()
                self.key(dialog, Qt.Key.Key_Up)
                self.assertEqual(dialog.current_selection, 3)
                self.key(dialog, Qt.Key.Key_Down)
                self.assertEqual(dialog.current_selection, 0)
                self.key(dialog, Qt.Key.Key_Down)
                self.key(dialog, confirm)
                QTest.qWait(80)
                self.assertEqual(dialog.selected_move, "growl")
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_first_selection_wins_during_deferred_close(self):
        dialog = self.dialog()
        self.key(dialog, Qt.Key.Key_2)
        self.key(dialog, Qt.Key.Key_4)
        self.key(dialog, Qt.Key.Key_Return)
        QTest.qWait(80)
        self.assertEqual(dialog.selected_move, "growl")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_escape_cancels_pending_acceptance(self):
        dialog = self.dialog()
        finished = []
        dialog.finished.connect(finished.append)
        self.key(dialog, Qt.Key.Key_2)
        self.key(dialog, Qt.Key.Key_Escape)
        QTest.qWait(80)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(finished, [QDialog.DialogCode.Rejected])

    def test_modified_keys_do_not_select_or_navigate(self):
        # Shift is deliberately absent: see test_shift_qualified_keys_select.
        for modifiers in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.AltModifier,
                          Qt.KeyboardModifier.MetaModifier):
            with self.subTest(modifiers=modifiers):
                dialog = self.dialog()
                self.key(dialog, Qt.Key.Key_Down, modifiers)
                self.key(dialog, Qt.Key.Key_2, modifiers)
                QTest.qWait(80)
                self.assertEqual(dialog.current_selection, 0)
                self.assertTrue(dialog.isVisible())
                dialog.reject()

    def test_nested_modal_receives_its_own_keys(self):
        dialog = self.dialog()
        child = QDialog(dialog)
        self.widgets.append(child)
        layout = QVBoxLayout(child)
        edit = QLineEdit(child)
        layout.addWidget(edit)
        child.setModal(True)
        child.show()
        child.activateWindow()
        edit.setFocus()
        _ENV.app.processEvents()
        self.key(edit, Qt.Key.Key_Down)
        QTest.keyClicks(edit, "2 ")
        QTest.qWait(80)
        self.assertEqual(edit.text(), "2 ")
        self.assertEqual(dialog.current_selection, 0)
        self.assertTrue(dialog.isVisible())

    def test_empty_moves_can_be_cancelled(self):
        dialog = self.dialog([])
        for key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_1):
            self.key(dialog, key)
        self.assertIsNone(dialog.selected_move)
        self.assertTrue(dialog.isVisible())
        # Confirm has to dismiss it: with no rows the dialog can neither select
        # nor explain itself, and swallowing Enter leaves only Escape and the
        # window X to close a modal that is blocking the turn.
        self.key(dialog, Qt.Key.Key_Return)
        QTest.qWait(20)
        self.assertIsNone(dialog.selected_move)
        self.assertFalse(dialog.isVisible())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_shift_qualified_keys_select_and_navigate(self):
        # AZERTY-style layouts reach the digit row only with Shift held, and a
        # controller mapper may emit its digits the same way.
        shift = Qt.KeyboardModifier.ShiftModifier
        dialog = self.dialog()
        self.key(dialog, Qt.Key.Key_2, shift)
        QTest.qWait(20)
        self.assertEqual(dialog.selected_move, "growl")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog = self.dialog()
        self.key(dialog, Qt.Key.Key_Down, shift)
        self.assertEqual(dialog.current_selection, 1)
        self.key(dialog, Qt.Key.Key_Return, shift)
        QTest.qWait(20)
        self.assertEqual(dialog.selected_move, "growl")

    def test_digit_shortcut_activates_the_matching_move(self):
        # sendEvent() bypasses Qt's shortcut map, so every other digit test
        # here reaches keyPressEvent and never the QShortcut wiring.
        dialog = self.dialog()
        shortcuts = dialog.findChildren(QShortcut)
        self.assertEqual([s.key().toString() for s in shortcuts], ["1", "2", "3", "4"])
        shortcuts[1].activated.emit()
        QTest.qWait(20)
        self.assertEqual(dialog.selected_move, "growl")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_reject_clears_a_latched_move(self):
        # A reader that does not also check result() must not see a cancelled
        # choice as a real one.
        dialog = self.dialog()
        self.key(dialog, Qt.Key.Key_2)
        self.key(dialog, Qt.Key.Key_Escape)
        QTest.qWait(80)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertIsNone(dialog.selected_move)

    def test_reopen_resets_the_highlight(self):
        dialog = self.dialog()
        self.key(dialog, Qt.Key.Key_Down)
        self.key(dialog, Qt.Key.Key_Down)
        self.assertEqual(dialog.current_selection, 2)
        dialog.hide()
        dialog.show()
        dialog.activateWindow()
        dialog.setFocus()
        _ENV.app.processEvents()
        # A row painted as selected but not latched would make a bare Enter
        # confirm the previous turn's choice.
        self.assertEqual(dialog.current_selection, 0)
        self.key(dialog, Qt.Key.Key_Return)
        QTest.qWait(20)
        self.assertEqual(dialog.selected_move, "tackle")

    def test_presenter_skips_the_dialog_with_no_moves(self):
        from Ankimon.gui_presenter import QtPresenter

        self.assertIsNone(QtPresenter().choose_move([]))
        self.assertIsNone(QApplication.activeModalWidget())

    def test_mouse_selects_move(self):
        dialog = self.dialog()
        QTest.mouseClick(dialog.move_labels[2], Qt.MouseButton.LeftButton)
        QTest.qWait(80)
        self.assertEqual(dialog.selected_move, "scratch")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_invalid_number_does_not_close_dialog(self):
        dialog = self.dialog(["tackle"])
        self.key(dialog, Qt.Key.Key_9)
        QTest.qWait(20)
        self.assertIsNone(dialog.selected_move)
        self.assertTrue(dialog.isVisible())

    def test_keypad_number_and_enter(self):
        dialog = self.dialog()
        self.key(dialog, Qt.Key.Key_2, Qt.KeyboardModifier.KeypadModifier)
        QTest.qWait(20)
        self.assertEqual(dialog.selected_move, "growl")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog = self.dialog()
        self.key(dialog, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
        QTest.qWait(20)
        self.assertEqual(dialog.selected_move, "tackle")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_repeated_confirmation_does_not_choose_a_move(self):
        dialog = self.dialog()
        QCoreApplication.sendEvent(dialog, QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier, "", True))
        QTest.qWait(20)
        self.assertIsNone(dialog.selected_move)
        self.assertTrue(dialog.isVisible())

    def test_hide_cancels_pending_accept_and_reopen_allows_new_choice(self):
        dialog = self.dialog()
        finished = []
        dialog.finished.connect(finished.append)
        self.key(dialog, Qt.Key.Key_1)
        dialog.hide()
        QTest.qWait(80)
        self.assertEqual(finished, [])
        dialog.show()
        dialog.activateWindow()
        dialog.setFocus()
        _ENV.app.processEvents()
        self.key(dialog, Qt.Key.Key_3)
        QTest.qWait(20)
        self.assertEqual(dialog.selected_move, "scratch")
        self.assertEqual(finished, [QDialog.DialogCode.Accepted])

    def test_hidden_dialog_does_not_consume_other_window_keys(self):
        dialog = self.dialog()
        dialog.reject()
        other = QDialog(_ENV.aqt.mw)
        self.widgets.append(other)
        layout = QVBoxLayout(other)
        edit = QLineEdit(other)
        layout.addWidget(edit)
        other.show()
        other.activateWindow()
        edit.setFocus()
        _ENV.app.processEvents()
        QTest.keyClicks(edit, "1234 ")
        self.assertEqual(edit.text(), "1234 ")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_destroying_dialog_cancels_queued_acceptance(self):
        dialog = self.dialog()
        finished = []
        dialog.finished.connect(finished.append)
        self.key(dialog, Qt.Key.Key_2)
        sip.delete(dialog)
        QTest.qWait(80)
        self.assertEqual(finished, [])

    def test_right_click_does_not_choose_a_move(self):
        dialog = self.dialog()
        QTest.mouseClick(dialog.move_labels[2], Qt.MouseButton.RightButton)
        QTest.qWait(20)
        self.assertIsNone(dialog.selected_move)
        self.assertTrue(dialog.isVisible())

    def test_presenter_real_modal_loop_and_deletion(self):
        from Ankimon.gui_presenter import QtPresenter

        dialogs = []
        errors = []
        def controller_input():
            try:
                dialog = QApplication.activeModalWidget()
                self.assertIsInstance(dialog, self.dialog_type)
                dialogs.append(dialog)
                # Re-read focusObject between events, exactly as Contanki does.
                for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                    target = _ENV.app.focusObject()
                    self.assertIsNotNone(target)
                    QCoreApplication.sendEvent(target, QKeyEvent(kind, Qt.Key.Key_3,
                                                               Qt.KeyboardModifier.NoModifier))
            except Exception as exc:
                errors.append(exc)
                modal = QApplication.activeModalWidget()
                if modal:
                    modal.reject()

        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: QApplication.activeModalWidget().reject()
                                if QApplication.activeModalWidget() else None)
        timeout.start(2000)
        QTimer.singleShot(20, controller_input)
        move = QtPresenter().choose_move(MOVES)
        timeout.stop()
        self.assertFalse(errors, errors)
        self.assertEqual(move, "scratch")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(dialogs, "controller input never reached a modal dialog")
        self.assertTrue(sip.isdeleted(dialogs[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
