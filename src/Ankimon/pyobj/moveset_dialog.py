from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
)

import re

from ..functions.pokedex_functions import _load_moves_cache
from ..move_names import format_move_description
from ..utils import format_move_name

MAX_MOVES = 4


def _lookup_move(attack):
    """Fetch a move record by raw id without any fallback or warning.

    ``find_details_move`` substitutes Tackle (and raises a UI warning) for an
    unknown id, which a tooltip must never do, so this reads the move table
    directly.

    Parameters
    ----------
    attack : str
        Raw move id, e.g. ``"sleeppowder"``.

    Returns
    -------
    dict or None
        The move record, or None when the id is not in the table.
    """
    key = re.sub(r"[^a-z0-9]", "", str(attack).lower())
    try:
        return _load_moves_cache().get(key)
    except Exception:
        return None


def _move_tooltip(attack):
    """Build the hover text describing what a move does.

    Parameters
    ----------
    attack : str
        Raw move id.

    Returns
    -------
    str
        Multi-line summary: name, type and category, power / accuracy / PP,
        then the short description. Falls back to a "details unavailable"
        line rather than raising or warning, so a tooltip can never
        interrupt the dialog.
    """
    name = format_move_name(attack)
    move = _lookup_move(attack)
    if not move:
        return f"{name}\n(details unavailable)"
    power = move.get("basePower") or "—"
    accuracy = move.get("accuracy")
    # Showdown stores "always hits" as True; bool must be tested before int.
    accuracy = "—" if isinstance(accuracy, bool) or accuracy is None else f"{accuracy}%"
    description = format_move_description(attack, move.get("shortDesc") or "")
    return (
        f"{name} — {move.get('type', '?')} · {move.get('category', '?')}\n"
        f"Power {power} · Accuracy {accuracy} · PP {move.get('pp', '?')}\n"
        f"{description}"
    )


class MovesetDialog(QDialog):
    """Pick up to 4 moves from the current set plus every newly learnable one.

    Shown once per Pokemon per battle when an XP Share recipient has levelled
    into more moves than it has free slots. A single checklist replaces one
    AttackDialog per new move, which gets tedious when a low-level recipient
    jumps several levels off one win. Moves left unchecked are not lost: the
    Remember Attacks window in Pokemon details can teach them later.

    Parameters
    ----------
    pokemon_name : str
        Display name of the Pokemon learning the moves.
    attacks : Sequence[str]
        Raw move ids the Pokemon currently knows; pre-checked.
    new_attacks : Sequence[str]
        Raw move ids newly available from the levels just gained; unchecked.
    parent : QWidget, optional
        Should always be a real window: a parentless dialog can spawn with
        no stacking/focus cue on some window managers, so ``exec()`` blocks
        the calling flow on a dialog nobody can see.

    Attributes
    ----------
    selected_moves : list of str or None
        Raw move ids the user confirmed, or None until confirmed.
    """

    def __init__(self, pokemon_name, attacks, new_attacks, parent=None):
        super().__init__(parent)
        self.pokemon_name = pokemon_name
        self.attacks = list(attacks)
        self.new_attacks = list(new_attacks)
        self.selected_moves = None
        self._checkboxes = []
        self.initUI()

    def initUI(self):
        """Build the checklist: current moves pre-checked, new moves unchecked."""
        display_name = str(self.pokemon_name).capitalize()
        self.setWindowTitle(f"{display_name} wants to learn new moves")
        layout = QVBoxLayout()
        layout.addWidget(
            QLabel(
                f"{display_name} can learn "
                f"{', '.join(format_move_name(a) for a in self.new_attacks)}.\n"
                f"Choose exactly {MAX_MOVES} moves to keep. Unchecked moves can be "
                "re-learned later via Remember Attacks."
            )
        )
        layout.addWidget(QLabel("Current moves:"))
        for attack in self.attacks:
            self._add_checkbox(layout, attack, checked=True)
        layout.addWidget(QLabel("New moves:"))
        for attack in self.new_attacks:
            self._add_checkbox(layout, attack, checked=False)

        self.count_label = QLabel()
        layout.addWidget(self.count_label)

        buttons = QHBoxLayout()
        self.confirm_button = QPushButton("Confirm")
        self.confirm_button.clicked.connect(self.movesetSelected)
        keep_button = QPushButton("Keep current moves")
        keep_button.clicked.connect(self.reject)
        buttons.addWidget(self.confirm_button)
        buttons.addWidget(keep_button)
        layout.addLayout(buttons)
        self.setLayout(layout)
        self._refresh_count()

    def _add_checkbox(self, layout, attack, checked):
        """Add one move checkbox to ``layout`` and track it.

        Parameters
        ----------
        layout : QLayout
            Layout the checkbox is appended to.
        attack : str
            Raw move id; shown human-readable but stored raw on the widget so
            the returned moveset can be saved as-is.
        checked : bool
            Initial checked state.
        """
        box = QCheckBox(format_move_name(attack))
        box.setProperty("raw_move", attack)
        box.setToolTip(_move_tooltip(attack))
        box.setChecked(checked)
        box.toggled.connect(self._refresh_count)
        layout.addWidget(box)
        self._checkboxes.append(box)

    def _checked_moves(self):
        """Return the raw move ids currently checked, in display order.

        Returns
        -------
        list of str
        """
        return [box.property("raw_move") for box in self._checkboxes if box.isChecked()]

    def _refresh_count(self):
        """Update the counter label and gate Confirm on exactly MAX_MOVES checked."""
        count = len(self._checked_moves())
        self.count_label.setText(f"{count}/{MAX_MOVES} moves selected")
        # Exactly four, like the games: the dialog only opens on a full set,
        # so the player can never accidentally confirm their way down to fewer.
        self.confirm_button.setEnabled(count == MAX_MOVES)

    def movesetSelected(self):
        """Store the checked moves in ``selected_moves`` and accept the dialog."""
        self.selected_moves = self._checked_moves()
        self.accept()
