from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QScrollArea
from PyQt6.QtCore import Qt

from ..utils import format_move_name

class AttackDialog(QDialog):
    def __init__(self, attacks, new_attack, parent=None):
        """``parent`` should always be a real window: a parentless dialog
        can spawn with no stacking/focus cue on some window managers, so
        exec() blocks the calling flow on a dialog nobody can see."""
        super().__init__(parent)
        self.attacks = attacks
        self.new_attack = new_attack
        self.selected_attack = None
        self.initUI()

    def initUI(self):
        # Display human-readable move names (e.g. "Thunderbolt") while the
        # dialog still returns the RAW move id in ``self.selected_attack`` so
        # callers can index the real ``attacks`` list. The raw move is stashed
        # on each button via a dynamic property and read back in attackSelected.
        new_attack_display = format_move_name(self.new_attack)
        self.setWindowTitle(
            f"Select which Attack to Replace with {new_attack_display}"
        )
        layout = QVBoxLayout()
        layout.addWidget(
            QLabel(f"Select which Attack to Replace with {new_attack_display}")
        )
        for attack in self.attacks:
            button = QPushButton(format_move_name(attack))
            button.setProperty("raw_move", attack)
            button.clicked.connect(self.attackSelected)
            layout.addWidget(button)
        reject_button = QPushButton("Reject Attack")
        reject_button.clicked.connect(self.attackNoneSelected)
        layout.addWidget(reject_button)
        self.setLayout(layout)

    def attackSelected(self):
        sender = self.sender()
        # Return the RAW move id (not the formatted label) so callers can index
        # the underlying ``attacks`` list correctly.
        self.selected_attack = sender.property("raw_move")
        self.accept()

    def attackNoneSelected(self):
        sender = self.sender()
        self.selected_attack = sender.text()
        self.reject()
