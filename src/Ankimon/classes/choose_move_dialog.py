from functools import partial

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel
from PyQt6.QtGui import QFont, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QEvent, QTimer

from ..functions.pokedex_functions import find_details_move
from ..move_names import format_move_name


# Digit selection exists only for Key_1..Key_9, so one range bounds both the
# QShortcut loop and the keyPressEvent handler, and the two cannot drift apart
# into different limits. An unmodified digit is claimed by the shortcut whether
# it arrives from a real keyboard or from sendEvent(), which reaches
# QApplication::notify and so consults the shortcut map too; keyPressEvent
# handles the digits the shortcut cannot match, Shift+digit above all.
DIGIT_SHORTCUT_COUNT = Qt.Key.Key_9 - Qt.Key.Key_1 + 1

# Ctrl/Alt/Meta chords belong to Anki or another add-on. Shift is deliberately
# NOT in this set: on AZERTY-style layouts the digit row *is* Shift+key, so
# excluding it makes 1-9 unreachable there, and a controller mapper that emits
# its digits with Shift held would silently do nothing.
CHORD_MODIFIERS = (Qt.KeyboardModifier.ControlModifier
                   | Qt.KeyboardModifier.AltModifier
                   | Qt.KeyboardModifier.MetaModifier)


class MoveSelectionDialog(QDialog):
    def __init__(self, mainpokemon_attacks, parent=None):
        """``parent`` should always be the main window: a parentless dialog
        can spawn with no stacking/focus cue on some window managers, so
        exec() blocks the calling turn on a dialog nobody can see."""
        super().__init__(parent)
        self.setWindowTitle("Select a Move")
        self.resize(300, 200)
        self.selected_move = None
        self.mainpokemon_attacks = list(mainpokemon_attacks)
        self.current_selection = 0

        # Close on the next event-loop turn so a controller's synchronous
        # press/release pair finishes before exec() returns to the presenter.
        # Owning the timer lets hide/reject cancel it and destruction clean it up.
        self._accept_timer = QTimer(self)
        self._accept_timer.setSingleShot(True)
        self._accept_timer.timeout.connect(self.accept)

        layout = QVBoxLayout(self)
        if self.mainpokemon_attacks:
            title = ("Press a number or click to select a move.\n"
                     "Use Up/Down, then Enter or Space.")
        else:
            # The usual title names three actions that cannot work here. Say
            # what the dialog is and how to leave it instead.
            title = ("No moves available.\n"
                     "Press Escape or Enter to close.")
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title_label)

        self.move_labels = []
        for index, move in enumerate(self.mainpokemon_attacks):
            if index < DIGIT_SHORTCUT_COUNT:
                shortcut = QShortcut(QKeySequence(str(index + 1)), self)
                # Controller tools that send keys to focusObject() need only
                # window-scoped shortcuts once the presenter activates this modal.
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.setAutoRepeat(False)
                shortcut.activated.connect(partial(self.select_move, index))

            # find_details_move returns None on its exception path; fall back to
            # the raw name rather than raising out of the constructor, which
            # would kill the turn before exec() ever ran.
            move_detail = find_details_move(move) or {}
            move_name = format_move_name(move_detail.get('name', move))
            move_label = QLabel(f"{index + 1}. {move_name}({move_detail.get('basePower', 'Unknown')}): {move_detail.get('shortDesc', 'Unknown')}")
            move_label.setToolTip(f"{move_detail.get('desc', 'No description available')}")
            move_label.setFont(QFont("Arial", 12))
            move_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            # One filter on the dialog, rather than a closure per label: an
            # assigned mousePressEvent holds label -> closure -> dialog -> label
            # alive every turn until the cycle collector gets to it.
            move_label.installEventFilter(self)
            layout.addWidget(move_label)
            self.move_labels.append(move_label)
        self.update_highlight()

    def update_highlight(self):
        """Highlight the currently selected move using the active Qt palette."""
        for i, label in enumerate(self.move_labels):
            if i == self.current_selection:
                label.setStyleSheet("border: 2px solid palette(highlight); border-radius: 4px; "
                                    "background-color: palette(highlight); color: palette(highlighted-text);")
            else:
                label.setStyleSheet("border: 1px solid #ccc; border-radius: 0px; background-color: transparent;")

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and obj in self.move_labels):
            self.select_move(self.move_labels.index(obj))
            return True
        return super().eventFilter(obj, event)

    def select_move(self, index):
        """Latch one choice until the dialog closes; duplicate input is harmless."""
        if (not self.isVisible() or self.selected_move is not None
                or not 0 <= index < len(self.mainpokemon_attacks)):
            return
        self.selected_move = self.mainpokemon_attacks[index]
        self.current_selection = index
        self.update_highlight()
        self._accept_timer.start(0)

    def _index_for_digit(self, key):
        """Map Key_1..Key_9 to a move index, or None when there is no such move."""
        if not Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            return None
        index = key - Qt.Key.Key_1
        return index if index < len(self.mainpokemon_attacks) else None

    def handle_navigation_key(self, key) -> bool:
        """Handle plain keys delivered to this dialog, including synthetic input."""
        if not self.mainpokemon_attacks:
            # Nothing to choose. Close on confirm rather than swallowing it —
            # QDialog's default Return handler would otherwise accept an empty
            # choice, and swallowing leaves Escape as the only way out.
            if key in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Space):
                self.reject()
                return True
            return False
        if self.selected_move is not None:
            return True
        if key == Qt.Key.Key_Up:
            self.current_selection = (self.current_selection - 1) % len(self.mainpokemon_attacks)
            self.update_highlight()
            return True
        if key == Qt.Key.Key_Down:
            self.current_selection = (self.current_selection + 1) % len(self.mainpokemon_attacks)
            self.update_highlight()
            return True
        if key in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Space):
            self.select_move(self.current_selection)
            return True
        move_index = self._index_for_digit(key)
        if move_index is not None:
            self.select_move(move_index)
            return True
        return False

    def keyPressEvent(self, event):
        # Keypad Enter/digits and Shift-qualified digits are ordinary choices;
        # Ctrl/Alt/Meta chords belong to Anki or another add-on. Escape must
        # still cancel a pending choice.
        if event.key() == Qt.Key.Key_Escape:
            super().keyPressEvent(event)
        elif event.modifiers() & CHORD_MODIFIERS:
            event.ignore()
        elif event.isAutoRepeat() and event.key() not in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            event.accept()
        elif self.handle_navigation_key(event.key()):
            event.accept()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        # Reset the highlight too: a reused dialog that paints a row it has not
        # latched turns a bare Enter into last turn's choice.
        self.selected_move = None
        self.current_selection = 0
        self.update_highlight()
        super().showEvent(event)

    def reject(self):
        # Cancelling must not leave a latched move behind for any reader that
        # does not also check the dialog's result code.
        self.selected_move = None
        super().reject()

    def hideEvent(self, event):
        self._accept_timer.stop()
        super().hideEvent(event)
