from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel
from PyQt6.QtGui import QFont, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer

from ..functions.pokedex_functions import find_details_move
from ..move_names import format_move_name


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
        title_label = QLabel("Press a number or click to select a move.\n"
                             "Use Up/Down, then Enter or Space.")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title_label)

        self.move_labels = []
        for index, move in enumerate(self.mainpokemon_attacks):
            if index < 9:
                shortcut = QShortcut(QKeySequence(str(index + 1)), self)
                # Controller tools that send keys to focusObject() need only
                # window-scoped shortcuts once the presenter activates this modal.
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.setAutoRepeat(False)
                shortcut.activated.connect(self.create_shortcut_handler(index))

            move_detail = find_details_move(move)
            move_name = format_move_name(move_detail.get('name', move))
            move_label = QLabel(f"{index + 1}. {move_name}({move_detail.get('basePower', 'Unknown')}): {move_detail.get('shortDesc', 'Unknown')}")
            move_label.setToolTip(f"{move_detail.get('desc', 'No description available')}")
            move_label.setFont(QFont("Arial", 12))
            move_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            move_label.mousePressEvent = self.create_mouse_press_handler(index)
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

    def create_mouse_press_handler(self, index):
        def handle_mouse_press(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.select_move(index)
        return handle_mouse_press

    def create_shortcut_handler(self, index):
        def handle_shortcut():
            self.select_move(index)
        return handle_shortcut

    def select_move(self, index):
        """Latch one choice until the dialog closes; duplicate input is harmless."""
        if (not self.isVisible() or self.selected_move is not None
                or not 0 <= index < len(self.mainpokemon_attacks)):
            return
        self.selected_move = self.mainpokemon_attacks[index]
        self.current_selection = index
        self.update_highlight()
        self._accept_timer.start(0)

    def handle_navigation_key(self, key) -> bool:
        """Handle plain keys delivered to this dialog, including synthetic input."""
        if not self.mainpokemon_attacks:
            # Do not let QDialog's default Return handler accept an empty choice.
            return key in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Space)
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
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            move_index = key - Qt.Key.Key_1
            if move_index < len(self.mainpokemon_attacks):
                self.select_move(move_index)
                return True
        return False

    def keyPressEvent(self, event):
        # Keypad Enter/digits are ordinary choices; Ctrl/Alt/Meta/Shift chords
        # belong to Anki or another add-on. Escape must still cancel a pending choice.
        if event.key() == Qt.Key.Key_Escape:
            super().keyPressEvent(event)
        elif event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            event.ignore()
        elif event.isAutoRepeat() and event.key() not in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            event.accept()
        elif self.handle_navigation_key(event.key()):
            event.accept()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        self.selected_move = None
        super().showEvent(event)

    def hideEvent(self, event):
        self._accept_timer.stop()
        super().hideEvent(event)
