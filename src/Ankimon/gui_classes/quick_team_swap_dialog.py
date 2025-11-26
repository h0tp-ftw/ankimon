import os
import json
import uuid
import base64
import traceback
from collections import defaultdict
from typing import Optional, Callable

from aqt import mw
from aqt.qt import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QShortcut,
    QKeySequence,
    Qt,
    QGridLayout,
    QToolButton,
    QIcon,
    QPixmap,
    QSize,
)
from aqt.utils import tooltip

from ..pyobj.test_window import TestWindow
from ..pyobj.reviewer_obj import Reviewer_Manager
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.collection_dialog import MainPokemon
from ..functions.pokedex_functions import search_pokedex, search_pokedex_by_id
from ..resources import mainpokemon_path
from ..resources import mypokemon_path, team_pokemon_path
from ..functions.sprite_functions import get_sprite_path
from ..utils import png_to_base64
from .overview_team import load_pokemon_team


"""Quick team swap dialog.

This module provides a compact, keyboard-first dialog to let the user
quickly pick a Pokémon from their current team. It is intentionally
    lightweight and designed to be invoked via a single global shortcut

Design goals and behavior
- Minimal UI: a simple grid of up to six buttons (3 columns × 2 rows).
- Keyboard-first: pressing digits `1`..`6` selects the corresponding
    Pokémon slot. `Esc` cancels. Buttons are clickable as an alternative.
- Non-destructive by default: selection is reported through the
    `perform_quick_swap` hook which is a placeholder you should replace
    with application-specific swap/reorder/persist logic.
- Sprite rendering: the dialog attempts to load sprite images using
    `get_sprite_path()` from the addon and will embed or display them if
    available (matching the overview appearance).

Extension points
- `perform_quick_swap(selected_index: int)`: replace this function with
    your own logic to perform the actual swap/reorder and persist changes
    to `mypokemon_path` as needed.
- The shortcut at the bottom of this file can be changed to a different
    key sequence or removed entirely if you prefer to trigger the dialog
    from another UI element.

Notes
- All file IO and image loading is performed synchronously when the
    dialog is created. The dialog reads `mypokemon_path` directly from disk
    so it shows the most recent saved data.
- This module imports `aqt` and other Anki internals and should be run
    within Anki's Python environment. Static analyzers outside Anki may
    report unresolved imports for `aqt` which is expected.
"""


def _load_team() -> list:
    """Load and return the list of Pokémon from `mypokemon_path`.

    The team is stored as JSON on disk and this helper reads the file and
    returns the parsed list. This function is intentionally tolerant and
    will return an empty list when the file is missing or cannot be parsed.

    Returns:
        list: A list of Pokémon dictionaries as stored in `mypokemon_path`.
              If the file cannot be read or parsed an empty list is
              returned. The function does not raise exceptions.
    """
    # Prefer to load the saved team order from `team_pokemon_path` and then
    # resolve those `individual_id` values against the full `mypokemon_path`.
    try:
        # Try read the team (order) file
        if os.path.exists(team_pokemon_path):
            with open(team_pokemon_path, "r", encoding="utf-8") as fh:
                team_entries = json.load(fh)

            # collect individual ids in order
            individual_ids = [e.get("individual_id") for e in team_entries if e.get("individual_id") is not None]

            # load all stored pokemon and index by individual_id
            try:
                all_pok = load_pokemon_team()
            except Exception:
                # fallback to direct read if overview loader unavailable
                if os.path.exists(mypokemon_path):
                    with open(mypokemon_path, "r", encoding="utf-8") as fh2:
                        all_pok = json.load(fh2)
                else:
                    all_pok = []

            by_ind = {p.get("individual_id"): p for p in all_pok if p.get("individual_id") is not None}

            # build ordered team based on team file; skip missing entries
            ordered = [by_ind.get(ind) for ind in individual_ids]
            ordered = [p for p in ordered if p]
            if ordered:
                return ordered

        # If no ordered team found, fall back to the overview loader or raw file
        try:
            return load_pokemon_team()
        except Exception:
            if not os.path.exists(mypokemon_path):
                return []
            with open(mypokemon_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        return []


class QuickTeamSwapDialog(QDialog):
    """Dialog that presents the current team as clickable sprite buttons.

    The dialog displays up to six team members (the first six entries
    returned by `_load_team()`). Each team member is represented by a
    `QToolButton` that shows a scaled sprite icon and a text label. Users
    can either click a button or press a digit key (`1`..`6`) to choose
    the corresponding slot.

    Attributes:
        selected_index (Optional[int]): The zero-based index of the
            selected team slot after the dialog closes. `None` means the
            dialog was cancelled or no valid selection was made.

    Behavior and usage:
        - Construct the dialog with `dlg = QuickTeamSwapDialog(mw)`.
        - Invoke it modally with `dlg.exec_()` to block until selection.
        - After the dialog returns, inspect `dlg.selected_index` and take
          application-specific action (or use the convenience
          `show_quick_team_swap_dialog()` function which calls
          `perform_quick_swap()` automatically).

    Threading / blocking:
        This dialog performs file I/O and image loading on the calling
        (GUI) thread when constructed. For very large teams or slow image
        sources consider moving image loading to a background thread and
        updating icons asynchronously.
    """
    def __init__(
        self,
        parent=None,
        main_pokemon=None,
        logger=None,
        translator=None,
        reviewer_obj=None,
        test_window=None,
    ):
        """Initialize the dialog UI and load team entries.

        Args:
            parent: Optional parent widget. When `None` the Anki main window
                `mw` is used as the parent so the dialog appears centered on
                the main window.

        Side effects:
            - Reads `mypokemon_path` synchronously to populate the team list.
            - Creates Qt widgets and loads sprite images (may access disk).
        """

        super().__init__(parent or mw)
        self.setWindowTitle("Quick Team Swap")
        self.setModal(True)
        self.resize(360, 240)

        # Public attribute populated when the dialog closes. Use the value
        # to perform application-specific actions.
        self.selected_index: Optional[int] = None

        # This attribute will be set when the user selects a Pokémon.
        self.main_pokemon_function_callback = None
        # Optional runtime objects that may be provided by the caller
        # (for example from `singletons` in `__init__.py`). They are
        # forwarded into the shared `MainPokemon` implementation when
        # a selection is made.
        self.main_pokemon = main_pokemon
        self.logger = logger
        self.translator = translator
        self.reviewer_obj = reviewer_obj
        self.test_window = test_window

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Press 1-6 or click a button to pick a Pokémon from your current team"))

        # grid of buttons (3 columns x 2 rows)
        grid = QGridLayout()
        layout.addLayout(grid)

        # populate the list with the current team
        self._team = _load_team()

        # helper to create a QPixmap from a path or base64 data-uri
        def _pixmap_from_src(src):
            if not src:
                return QPixmap()

            # data-uri
            if isinstance(src, str) and src.startswith("data:image/png;base64,"):
                try:
                    import base64

                    data = base64.b64decode(src.split(",", 1)[1])
                    pix = QPixmap()
                    pix.loadFromData(data)
                    return pix
                except Exception:
                    return QPixmap()
            # file path (strip file:// if present)
            if src.startswith("file://"):
                src = src[7:]
            if os.path.exists(src):
                return QPixmap(src)
            return QPixmap()

        # create up to 6 buttons
        for i, p in enumerate(self._team[:6]):
            name = p.get("nickname") or p.get("name") or "Unknown"
            sprite_path = get_sprite_path("front", "png", p.get("id", 132), p.get("shiny", False), p.get("gender", "M"))
            # try to embed as base64 via overview helper, fall back to file path
            sprite_src = png_to_base64(sprite_path) or sprite_path

            pix = _pixmap_from_src(sprite_src)

            btn = QToolButton(self)
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            if not pix.isNull():
                icon = QIcon(pix.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                btn.setIcon(icon)
                btn.setIconSize(QSize(96, 96))
            btn.setText(f"{i+1}. {name}")
            btn.setFixedSize(120, 130)
            # basic styling to mimic the overview sprite panel
            btn.setStyleSheet(
                "QToolButton{background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #3a3a3a, stop:1 #2b2b2b); color: #fff; border-radius:8px;}"
            )

            def _on_click(index=i, pokemon=p):
                # Resolve and set the main_pokemon callback before closing
                self.selected_index = index
                try:
                    self._set_main_callback(pokemon)
                except Exception:
                    pass
                self.accept()

            btn.clicked.connect(_on_click)

            row = i // 3
            col = i % 3
            grid.addWidget(btn, row, col)

        # if no team entries, show a placeholder label
        if len(self._team) == 0:
            layout.addWidget(QLabel("(no Pokémon found in team)"))

    def keyPressEvent(self, event):
        """Handle keyboard input while the dialog is focused.

        Supported keys:
            - `1`..`6`: choose the corresponding team slot (if present).
            - `Esc`: cancel the dialog.

        The method maps digit keys to zero-based indices and closes the
        dialog by calling `accept()` when a valid selection is made. The
        selected index is available later as `dlg.selected_index`.

        Args:
            event: QKeyEvent provided by Qt.
        """
        key = event.key()
        # handle Esc to cancel
        if key == Qt.Key_Escape:
            self.selected_index = None
            return super().keyPressEvent(event)

        # Map number keys to 0-based indexes
        mapping = {
            Qt.Key_1: 0,
            Qt.Key_2: 1,
            Qt.Key_3: 2,
            Qt.Key_4: 3,
            Qt.Key_5: 4,
            Qt.Key_6: 5,
        }

        if key in mapping:
            idx = mapping[key]
            if idx < len(self._team):
                self.selected_index = idx
                # set callback from the selected entry then accept
                try:
                        self._set_main_callback(self._team[idx])
                except Exception:
                    pass
                # accept the selection immediately
                self.accept()
                return

        # default handling
        return super().keyPressEvent(event)

    def _set_main_callback(self, pokemon_record: dict):
        """Prepare and invoke the MainPokemon callback for the given record.

        This method centralizes error handling so UI callers (click/key)
        remain simple. It will attach the selected index to the record
        and call the shared `MainPokemon` implementation from
        `pyobj.collection_dialog` with the runtime objects provided at
        construction time (if any).
        """
        try:
            # Ensure we have a mutable dict (some loaders may provide objects)
            p = dict(pokemon_record) if pokemon_record is not None else {}
            # Attach selected index for downstream behaviour
            p["_selected_index"] = getattr(self, "selected_index", None)
            self._invoke_main_pokemon(p)
        except Exception as e:
            tb = traceback.format_exc()
            try:
                tooltip(f"Failed to open Pokémon view: {e}")
            except Exception:
                pass
            if getattr(self, "logger", None):
                try:
                    self.logger.error(tb)
                except Exception:
                    print(tb)
            else:
                print(tb)

    def _invoke_main_pokemon(self, full_pokemon_record: dict):
        """Call the shared MainPokemon(...) implementation.

        Arguments are forwarded as: (pokemon_record, main_pokemon, logger,
        translator, reviewer_obj, test_window). Failures are surfaced to
        the user via tooltip and logged when possible.
        """
        try:
            from ..pyobj.collection_dialog import MainPokemon

            # Provide safe fallback stubs for required runtime objects
            logger = getattr(self, "logger", None)
            translator = getattr(self, "translator", None)
            reviewer_obj = getattr(self, "reviewer_obj", None)
            test_window = getattr(self, "test_window", None)

            class _LoggerStub:
                def log(self, level, msg):
                    try:
                        print(f"[logger] {level}: {msg}")
                    except Exception:
                        pass

                def error(self, msg):
                    try:
                        print(f"[logger][error] {msg}")
                    except Exception:
                        pass

                def log_and_showinfo(self, level, msg):
                    try:
                        print(f"[logger] {level}: {msg}")
                    except Exception:
                        pass

            class _TranslatorStub:
                def translate(self, key, **kwargs):
                    # Simple placeholder: return key or formatted string
                    try:
                        return key.format(**kwargs) if kwargs else key
                    except Exception:
                        return key

            class _ReviewerStub:
                def update_life_bar(self, reviewer, a, b):
                    return None

            # Fill missing objects with stubs to avoid attribute errors
            if logger is None:
                logger = _LoggerStub()
            if translator is None:
                translator = _TranslatorStub()
            if reviewer_obj is None:
                reviewer_obj = _ReviewerStub()
            # test_window is optional; if missing, leave as None

            class _MainStub:
                def __init__(self, record):
                    self.name = record.get('name', '')
                    self.level = record.get('level', 5)
                    self.id = record.get('id', 0)
                    self.individual_id = record.get('individual_id', str(uuid.uuid4()))

                def to_dict(self):
                    return {
                        'name': self.name,
                        'level': self.level,
                        'id': self.id,
                        'individual_id': self.individual_id,
                    }

            MainPokemon(
                full_pokemon_record,
                getattr(self, "main_pokemon", None) or _MainStub(full_pokemon_record),
                logger,
                translator,
                reviewer_obj,
                test_window,
            )
        except Exception as e:
            tb = traceback.format_exc()
            try:
                tooltip(f"Error invoking main view: {e}")
            except Exception:
                pass
            if getattr(self, "logger", None):
                try:
                    self.logger.error(tb)
                except Exception:
                    print(tb)
            else:
                print(tb)


def perform_quick_swap(selected_index: int):
    """Hook invoked when the user selects a Pokémon from the dialog.

    This function is intentionally a lightweight placeholder. It is
    called by `show_quick_team_swap_dialog()` when the dialog returns
    with a valid selection. Replace or extend this function with the
    concrete behavior you want when a slot is chosen (persisting changes,
    reordering the team, opening a confirmation dialog, etc.).

    Args:
        selected_index: zero-based index of the chosen team slot. If the
            index is out of range the function will show an information
            popup and return without making changes.

    Side effects:
        - Shows a short tooltip notification by default. Replace with
          your own logic to perform the swap and save the team file.
    """

    team = _load_team()
    if selected_index is None or selected_index >= len(team):
        tooltip("No Pokémon selected or index out of range.")
        return

    p = team[selected_index]
    name = p.get("nickname") or p.get("name") or "Unknown"
    # Default behavior: notify the user. Replace this with your swap logic.
    tooltip(f"Selected Pokémon slot {selected_index + 1}: {name}")

    # Note: the dialog already invokes the shared MainPokemon via its
    # callback when a selection is made. This placeholder only notifies
    # the user; if you want to call MainPokemon here, pass the full
    # required runtime objects (main_pokemon, logger, translator, reviewer_obj, test_window).


def show_quick_team_swap_dialog(
    main_pokemon=None,
    logger=None,
    translator=None,
    reviewer_obj=None,
    test_window=None,
):
    """Show the quick swap dialog and return the selected index or None.

    This convenience wrapper forwards optional context objects into the
    dialog constructor. If `MainPokemonClass` is provided the dialog will
    instantiate it (via the callback) automatically when a selection is
    made.

    Args (keyword-only):
        MainPokemonClass: Optional callable/class used to instantiate the
            main Pokémon view/widget. When provided the dialog will set
            `dlg.main_pokemon_function_callback` to call
            `MainPokemonClass(_pokemon_data, main_pokemon, logger,
            translator, reviewer_obj, test_window)` and will invoke it
            immediately on selection.
        main_pokemon, logger, translator, reviewer_obj, test_window:
            Optional objects forwarded into the constructor call above.

    Returns:
        Optional[int]: the zero-based index chosen by the user, or `None`.
    """

    dlg = QuickTeamSwapDialog(parent=mw, main_pokemon=main_pokemon, logger=logger, translator=translator, reviewer_obj=reviewer_obj, test_window=test_window)
    dlg.exec_()
    if dlg.selected_index is not None:
        perform_quick_swap(dlg.selected_index)
        return dlg.selected_index
    return None


# Register a global shortcut on the Anki main window to open the dialog.
# Avoid registering more than once when the module is reloaded.
if not hasattr(mw, "_quick_team_swap_shortcut_registered"):
    try:
        # Use a two-step key sequence: press Tab, then P.
        # Qt represents multi-step sequences with a comma-separated string.
        shortcut = QShortcut(QKeySequence("Tab, P"), mw)

        def _open_quick_swap():
            # small debug hint to confirm the shortcut fired
            try:
                tooltip("Opening Quick Team Swap...")
            except Exception:
                pass
            # Construct the dialog with real runtime objects when possible.
            try:
                from ..singletons import main_pokemon, logger as _logger, translator as _translator, reviewer_obj as _reviewer_obj, test_window as _test_window
            except Exception:
                # Fallback to mw-provided logger/translator if singletons unavailable
                main_pokemon = None
                try:
                    _logger = getattr(mw, 'logger', None)
                except Exception:
                    _logger = None
                try:
                    _translator = getattr(mw, 'translator', None)
                except Exception:
                    _translator = None
                _reviewer_obj = None
                _test_window = None

            dlg = QuickTeamSwapDialog(parent=mw, main_pokemon=main_pokemon, logger=_logger, translator=_translator, reviewer_obj=_reviewer_obj, test_window=_test_window)
            try:
                dlg.exec_()
            except Exception as e:
                try:
                    tooltip(f"Failed to open Quick Team Swap: {e}")
                except Exception:
                    pass

        shortcut.activated.connect(_open_quick_swap)
        mw._quick_team_swap_shortcut_registered = True
    except Exception:
        # If something goes wrong when creating the shortcut (unlikely),
        # silently ignore to avoid breaking Anki on import.
        mw._quick_team_swap_shortcut_registered = False

# (The `MainPokemon` implementation lives in `pyobj.collection_dialog`.
# This file should call that shared function rather than re-defining it.)