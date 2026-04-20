import os
import sys
import importlib
import pkgutil
import traceback
import pytest
from unittest.mock import MagicMock

# Import aqt modules BEFORE QApplication is instantiated by pytest-qt.
try:
    import aqt
    import aqt.qt
except ImportError:
    pass

# Add the 'src' directory to the Python path to allow for absolute imports of Ankimon
ANKIMON_SRC_PARENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src")
)
if ANKIMON_SRC_PARENT_DIR not in sys.path:
    sys.path.insert(0, ANKIMON_SRC_PARENT_DIR)


def test_ankimon_initialization(qapp):
    """
    Dynamically loads the Ankimon add-on to catch real AttributeError or ImportError
    that happen at module level, such as failing to register GUI hooks properly.
    `qapp` is a pytest-qt fixture that provides a QApplication instance, allowing
    real PyQt6 widgets to be instantiated without crashing.
    """
    from PyQt6.QtWidgets import QMainWindow

    # Other tests (e.g. test_encounter_functions) may install MagicMock stubs
    # for aqt and Ankimon packages in sys.modules.  conftest.py also installs
    # lightweight stubs for Ankimon and Ankimon.functions.
    # This test needs the *real* packages, so purge all stubs first.
    for _stub in [k for k in list(sys.modules) if k.startswith("Ankimon")]:
        del sys.modules[_stub]
    for _aqt_key in [k for k in list(sys.modules) if k.startswith("aqt")]:
        if isinstance(sys.modules[_aqt_key], MagicMock):
            del sys.modules[_aqt_key]
    import aqt
    import aqt.qt

    # Instead of a MagicMock which Qt rejects for parent classes, use a real QWidget
    # as the mock main window.
    class MockMainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.pm = MagicMock()
            self.pm.name = "test_profile"
            self.form = MagicMock()
            self.addonManager = MagicMock()
            # settings_obj is accessed at import time by modules like
            # gui_classes/overview_team.py. Return False for all settings
            # so hook-registration code paths short-circuit.
            self.settings_obj = MagicMock()
            self.settings_obj.get = MagicMock(return_value=False)
            # col is accessed by ankimon_tracker.get_total_reviews() which
            # calls re.search on mw.col.studied_today() — needs a string.
            self.col = MagicMock()
            self.col.studied_today.return_value = "Studied 0 cards today"

        def _increase_background_ops(self):
            pass

        def _decrease_background_ops(self):
            pass

    aqt.mw = MockMainWindow()

    # Mocking QThreadPool and query execution because we don't want background threads to actually run in the test
    aqt.mw.taskman = MagicMock()

    # Track errors
    errors = []

    try:
        # Import the main __init__ to simulate Anki loading the add-on
        import Ankimon
    except Exception as e:
        error_msg = f"Failed to load Ankimon/__init__.py:\n{traceback.format_exc()}"
        errors.append(error_msg)

    # Walk packages and catch remaining errors
    package = sys.modules.get("Ankimon")
    if package:
        prefix = package.__name__ + "."

        ignore_modules = [
            "pypresence",
            "Ankimon.poke_engine.data.scripts",
            "Ankimon.poke_engine.data.mods",
            "Ankimon.gui_classes.backup_manager_dialog",
            "Ankimon.gui_classes.overview_team",
            "Ankimon.gui_classes.pokemon_details",
            "Ankimon.menu_buttons",
            "Ankimon.poke_engine.ankimon_hooks_to_poke_engine",
            "Ankimon.singletons",
        ]

        for importer, modname, ispkg in pkgutil.walk_packages(package.__path__, prefix):
            if any(ignored in modname for ignored in ignore_modules):
                continue

            try:
                importlib.import_module(modname)
            except Exception as e:
                error_msg = f"Failed to dynamically import module {modname}:\n{traceback.format_exc()}"
                errors.append(error_msg)

    if errors:
        error_text = "\n".join(errors)
        pytest.fail(
            f"Integrity check failed with {len(errors)} error(s):\n{error_text}"
        )
