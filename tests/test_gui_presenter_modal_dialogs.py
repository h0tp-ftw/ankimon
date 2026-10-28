"""Regression tests for modal move-selection dialogs in ``QtPresenter``."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from conftest import isolated_modules


_SRC = Path(__file__).parent.parent / "src"
_MODULE_NAME = "Ankimon.gui_presenter"


class _DialogCode:
    Accepted = 1


class _QDialog:
    DialogCode = _DialogCode


class _QDialogButtonBox:
    class StandardButton:
        Ok = 1


@pytest.fixture
def presenter_module():
    """Load the real presenter with only its Anki/dialog imports isolated."""
    with isolated_modules("Ankimon", "aqt", "PyQt6"):
        for package_name in ("Ankimon", "Ankimon.classes", "Ankimon.pyobj"):
            package = types.ModuleType(package_name)
            package.__path__ = [str(_SRC / package_name.replace(".", "/"))]
            package.__package__ = package_name
            sys.modules[package_name] = package

        aqt = types.ModuleType("aqt")
        aqt.mw = object()
        sys.modules["aqt"] = aqt

        aqt_qt = types.ModuleType("aqt.qt")
        aqt_qt.QDialog = _QDialog
        aqt_qt.QDialogButtonBox = _QDialogButtonBox
        aqt_qt.QLabel = object
        aqt_qt.QTimer = object
        aqt_qt.QVBoxLayout = object
        sys.modules["aqt.qt"] = aqt_qt

        aqt_utils = types.ModuleType("aqt.utils")
        aqt_utils.showInfo = lambda *_args, **_kwargs: None
        aqt_utils.showWarning = lambda *_args, **_kwargs: None
        sys.modules["aqt.utils"] = aqt_utils

        choose_move = types.ModuleType("Ankimon.classes.choose_move_dialog")
        choose_move.MoveSelectionDialog = object
        sys.modules["Ankimon.classes.choose_move_dialog"] = choose_move

        attack_dialog = types.ModuleType("Ankimon.pyobj.attack_dialog")
        attack_dialog.AttackDialog = object
        sys.modules["Ankimon.pyobj.attack_dialog"] = attack_dialog

        error_handler = types.ModuleType("Ankimon.pyobj.error_handler")
        error_handler.show_warning_with_traceback = lambda *_args, **_kwargs: None
        sys.modules["Ankimon.pyobj.error_handler"] = error_handler

        moveset_dialog = types.ModuleType("Ankimon.pyobj.moveset_dialog")
        moveset_dialog.MovesetDialog = object
        sys.modules["Ankimon.pyobj.moveset_dialog"] = moveset_dialog

        resources = types.ModuleType("Ankimon.resources")
        resources.icon_path = ""
        sys.modules["Ankimon.resources"] = resources

        qt_gui = types.ModuleType("PyQt6.QtGui")
        qt_gui.QIcon = object
        sys.modules["PyQt6.QtGui"] = qt_gui

        spec = importlib.util.spec_from_file_location(
            _MODULE_NAME, _SRC / "Ankimon" / "gui_presenter.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
        yield module


class _DialogProbe:
    """Run queued callbacks only after the simulated modal loop starts."""

    def __init__(self, events, callbacks):
        self.events = events
        self.callbacks = callbacks
        self.selected_move = "tackle"
        self.selected_attack = "growl"

    def show(self):
        self.events.append("show")

    def raise_(self):
        self.events.append("raise")

    def activateWindow(self):
        self.events.append("activate")

    def exec(self):
        self.events.append("exec")
        while self.callbacks:
            self.callbacks.pop(0)()
        return _DialogCode.Accepted

    def deleteLater(self):
        self.events.append("delete")


@pytest.mark.parametrize(
    ("method_name", "dialog_name", "arguments", "expected"),
    [
        ("choose_move", "MoveSelectionDialog", (["tackle"],), "tackle"),
        (
            "choose_attack_to_replace",
            "AttackDialog",
            (["growl"], "tackle"),
            "growl",
        ),
    ],
)
def test_presenter_establishes_modality_before_showing_dialog(
    monkeypatch,
    presenter_module,
    method_name,
    dialog_name,
    arguments,
    expected,
):
    """A foregrounded dialog must already block the other application windows."""
    events = []
    callbacks = []

    class ProbeTimer:
        @staticmethod
        def singleShot(interval, callback):
            assert interval == 0
            events.append("scheduled")
            callbacks.append(callback)

    def make_dialog(*_args, **_kwargs):
        return _DialogProbe(events, callbacks)

    monkeypatch.setattr(presenter_module, dialog_name, make_dialog)
    monkeypatch.setattr(presenter_module, "QTimer", ProbeTimer, raising=False)

    result = getattr(presenter_module.QtPresenter(), method_name)(*arguments)

    assert result == expected
    assert events == ["scheduled", "exec", "raise", "activate", "delete"]
