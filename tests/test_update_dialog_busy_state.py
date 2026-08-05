"""Focused Tier-1 coverage for the updater dialog's busy-state controls."""

import importlib.util
import sys
import types
from pathlib import Path


_SRC = Path(__file__).parent.parent / "src"


def _load_update_dialog():
    module_names = (
        "aqt",
        "aqt.operations",
        "aqt.qt",
        "aqt.theme",
        "Ankimon",
        "Ankimon.pyobj",
        "Ankimon.pyobj.update_dialog",
        "Ankimon.pyobj.update_manager",
        "Ankimon.resources",
    )
    saved = {name: sys.modules.get(name) for name in module_names}
    try:
        aqt = types.ModuleType("aqt")
        aqt.mw = None
        sys.modules["aqt"] = aqt

        operations = types.ModuleType("aqt.operations")
        operations.QueryOp = object
        sys.modules["aqt.operations"] = operations

        qt = types.ModuleType("aqt.qt")
        qt.Qt = object
        for name in (
            "QDialog",
            "QVBoxLayout",
            "QHBoxLayout",
            "QLabel",
            "QPushButton",
            "QComboBox",
            "QProgressBar",
            "QTabWidget",
            "QWidget",
            "QMessageBox",
            "QGroupBox",
            "QFrame",
            "QSizePolicy",
            "QSpacerItem",
            "QTextBrowser",
            "QCheckBox",
        ):
            setattr(qt, name, type(name, (), {}))
        sys.modules["aqt.qt"] = qt

        theme = types.ModuleType("aqt.theme")
        theme.theme_manager = types.SimpleNamespace(night_mode=False)
        sys.modules["aqt.theme"] = theme

        for name, path in (
            ("Ankimon", _SRC / "Ankimon"),
            ("Ankimon.pyobj", _SRC / "Ankimon" / "pyobj"),
        ):
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            package.__package__ = name
            sys.modules[name] = package

        update_manager = types.ModuleType("Ankimon.pyobj.update_manager")
        for name in (
            "fetch_releases",
            "fetch_tags",
            "fetch_branches",
            "fetch_open_prs",
            "apply_update",
            "_download_zip_to_temp",
            "_download_branch_zip",
            "_download_pr_zip",
            "read_update_state",
            "fetch_branch_sha",
        ):
            setattr(update_manager, name, lambda *args, **kwargs: None)
        sys.modules["Ankimon.pyobj.update_manager"] = update_manager

        resources = types.ModuleType("Ankimon.resources")
        resources.addon_ver = "test"
        resources.IS_EXPERIMENTAL_BUILD = False
        sys.modules["Ankimon.resources"] = resources

        spec = importlib.util.spec_from_file_location(
            "Ankimon.pyobj.update_dialog",
            _SRC / "Ankimon" / "pyobj" / "update_dialog.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


update_dialog = _load_update_dialog()


class _Control:
    def __init__(self, enabled=True):
        self.enabled = enabled

    def isEnabled(self):
        return self.enabled

    def setEnabled(self, enabled):
        self.enabled = enabled


class _Progress:
    def __init__(self):
        self.visible = False
        self.value = None

    def setVisible(self, visible):
        self.visible = visible

    def setValue(self, value):
        self.value = value


class _Label:
    def __init__(self):
        self.text = "Working..."

    def setText(self, text):
        self.text = text


def test_busy_state_disables_all_actions_and_restores_prior_state():
    dialog = types.SimpleNamespace(
        progress_bar=_Progress(),
        status_label=_Label(),
        brrr_update_btn=_Control(True),
        update_latest_btn=_Control(False),
        release_btn=_Control(True),
        dev_install_btn=_Control(True),
        _busy_action_states=None,
    )
    buttons = (
        dialog.brrr_update_btn,
        dialog.update_latest_btn,
        dialog.release_btn,
        dialog.dev_install_btn,
    )

    update_dialog.UpdateDialog._set_busy(dialog, True)

    assert dialog.progress_bar.visible is True
    assert dialog.progress_bar.value == 0
    assert all(button.enabled is False for button in buttons)

    update_dialog.UpdateDialog._set_busy(dialog, False)

    assert dialog.progress_bar.visible is False
    assert [button.enabled for button in buttons] == [True, False, True, True]
    assert dialog.status_label.text == ""
