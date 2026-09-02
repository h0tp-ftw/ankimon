import sys
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel
from PyQt6.QtGui import QFont, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QEvent, QObject
from ..functions.pokedex_functions import find_details_move
from ..move_names import format_move_name
import random

class MoveSelectionDialog(QDialog):
    def __init__(self, mainpokemon_attacks, parent=None):
        """``parent`` should always be the main window: a parentless dialog
        can spawn with no stacking/focus cue on some window managers, so
        exec() blocks the calling turn on a dialog nobody can see."""
        super().__init__(parent)

        # Dialog settings
        self.setWindowTitle("Select a Move")
        self.resize(300, 200)
        self.selected_move = random.choice(mainpokemon_attacks)
        self.mainpokemon_attacks = mainpokemon_attacks

        # Create and set layout
        layout = QVBoxLayout()
        self.setLayout(layout)

        # Add a title label
        title_label = QLabel("Press a number (1-4) or click to select a move:")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title_label)

        self.current_selection = 0

        # Add labels for each move
        self.move_labels = []
        for index, move in enumerate(mainpokemon_attacks):
            # Bind global QShortcut for this move index (1-based)
            if index < 9:
                shortcut_key = str(index + 1)
                shortcut = QShortcut(QKeySequence(shortcut_key), self)
                shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
                shortcut.activated.connect(self.create_shortcut_handler(index))

            move_detail = find_details_move(move)
            move_name = format_move_name(move_detail.get('name', move))
            move_label = QLabel(f"{index + 1}. {move_name}({move_detail.get('basePower', 'Unknown')}): {move_detail.get('shortDesc', 'Unknown')}")
            move_label.setToolTip(f"{move_detail.get('desc', 'No description available')}")
            move_label.setFont(QFont("Arial", 12))
            move_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            move_label.mousePressEvent = self.create_mouse_press_handler(index)
            move_label.setFixedHeight(20)  # Example fixed height for thinner labels
            layout.addWidget(move_label)
            self.move_labels.append(move_label)

        self.update_highlight()

    def update_highlight(self):
        """Highlight the currently selected move."""
        for i, label in enumerate(self.move_labels):
            if i == self.current_selection:
                label.setStyleSheet("border: 2px solid #0078D7; border-radius: 4px; background-color: #E5F1FB; color: black;")
            else:
                label.setStyleSheet("border: 1px solid #ccc; border-radius: 0px; background-color: transparent;")

    def create_mouse_press_handler(self, index):
        def handle_mouse_press(event):
            self.select_move(index)
        return handle_mouse_press

    def create_shortcut_handler(self, index):
        def handle_shortcut():
            self.select_move(index)
        return handle_shortcut

    def select_move(self, index):
        """Handle move selection and close the dialog."""
        self.selected_move = self.mainpokemon_attacks[index]
        # Defers the accept call to let the Qt event loop pump any remaining
        # rogue QKeyEvents (e.g. from Contanki) before the window is destroyed.
        from aqt.qt import QTimer
        QTimer.singleShot(50, self.accept)

    def handle_navigation_key(self, key) -> bool:
        """Handle arrow keys and enter for controller navigation. Returns True if consumed."""
        if key == Qt.Key.Key_Up:
            self.current_selection = (self.current_selection - 1) % len(self.mainpokemon_attacks)
            self.update_highlight()
            return True
        elif key == Qt.Key.Key_Down:
            self.current_selection = (self.current_selection + 1) % len(self.mainpokemon_attacks)
            self.update_highlight()
            return True
        elif key in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Space):
            self.select_move(self.current_selection)
            return True
        elif Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            move_index = key - Qt.Key.Key_1
            if 0 <= move_index < len(self.mainpokemon_attacks):
                self.select_move(move_index)
                return True
        return False

    def keyPressEvent(self, event):
        """Fallback: handle keyboard shortcuts if focused normally."""
        if self.handle_navigation_key(event.key()):
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Global intercept for all key presses in the application."""
        if event.type() == QEvent.Type.KeyPress:
            if self.handle_navigation_key(event.key()):
                return True # Consume event
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)
