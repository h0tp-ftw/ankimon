"""Regression test for the settings persistence init-order bug.

The bug: Settings.load_config / set / save_config gate their DB access on the
shared registry handle ``services.db``. If the registry constructs Settings()
before ``services.db`` is populated, the first load_config silently falls
through to defaults and a subsequent set/save skips the DB write — so saved
settings are ignored on startup and overwritten on save.

This test pins the contract: when ``services.db`` is set BEFORE Settings()
is constructed, the DB-persisted config wins over DEFAULT_CONFIG.

(Pre-#492 the gate was ``hasattr(mw, 'ankimon_db')``; the services-registry
refactor moved DB access to ``services.db`` — settings.py:6/87/173/213 — so
this test now wires that seam instead of mw.ankimon_db.)
"""

import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_src = Path(__file__).parent.parent / "src"


def _install_aqt_stubs(mw):
    """Install fake aqt modules and a configurable mw singleton."""
    aqt_stub = types.ModuleType("aqt")
    aqt_stub.mw = mw
    sys.modules["aqt"] = aqt_stub

    aqt_utils = types.ModuleType("aqt.utils")
    aqt_utils.showInfo = lambda *a, **k: None
    aqt_utils.tooltip = lambda *a, **k: None
    aqt_utils.showWarning = lambda *a, **k: None
    sys.modules["aqt.utils"] = aqt_utils

    aqt_qt = types.ModuleType("aqt.qt")
    for name in (
        "QWidget", "QVBoxLayout", "QLabel", "QLineEdit", "QPushButton",
        "QRadioButton", "QHBoxLayout", "QMainWindow", "QScrollArea",
        "QButtonGroup", "QMessageBox", "QPixmap", "QPainter", "QPainterPath",
        "Qt", "QRectF",
    ):
        setattr(aqt_qt, name, MagicMock())
    sys.modules["aqt.qt"] = aqt_qt

    pyqt6 = types.ModuleType("PyQt6")
    pyqt6_widgets = types.ModuleType("PyQt6.QtWidgets")
    for name in (
        "QApplication", "QWidget", "QVBoxLayout", "QLabel", "QLineEdit",
        "QPushButton", "QRadioButton", "QHBoxLayout", "QMainWindow",
        "QScrollArea",
    ):
        setattr(pyqt6_widgets, name, MagicMock())
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtWidgets"] = pyqt6_widgets


def _load_module(dotted_name, file_path):
    """Load a module from a file path under a specific dotted name."""
    spec = importlib.util.spec_from_file_location(dotted_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Fresh module namespace + temp user_path for each test."""
    # Wipe any cached Ankimon modules so each test gets a fresh import graph.
    for mod_name in list(sys.modules):
        if mod_name.startswith("Ankimon") or mod_name in ("aqt", "aqt.qt", "aqt.utils"):
            del sys.modules[mod_name]

    user_path = tmp_path / "user_files"
    user_path.mkdir()

    mw = MagicMock()
    # Critical: simulate Anki's real mw — it does NOT have ankimon_db by default.
    # MagicMock auto-creates attrs on access, so we override hasattr behavior by
    # using a real object that only has what we explicitly set.
    class FakeMw:
        pass
    real_mw = FakeMw()
    _install_aqt_stubs(real_mw)

    # Build a minimal Ankimon package namespace.
    ankimon_pkg = types.ModuleType("Ankimon")
    ankimon_pkg.__path__ = [str(_src / "Ankimon")]
    sys.modules["Ankimon"] = ankimon_pkg

    pyobj_pkg = types.ModuleType("Ankimon.pyobj")
    pyobj_pkg.__path__ = [str(_src / "Ankimon" / "pyobj")]
    sys.modules["Ankimon.pyobj"] = pyobj_pkg

    # Stub Ankimon.resources with a user_path pointing at our tmp dir.
    resources_stub = types.ModuleType("Ankimon.resources")
    resources_stub.user_path = user_path
    resources_stub.csv_file_items_cost = tmp_path / "items.csv"
    resources_stub.mypokemon_path = tmp_path / "mypokemon.json"
    resources_stub.mainpokemon_path = tmp_path / "mainpokemon.json"
    resources_stub.items_path = tmp_path / "items.json"
    resources_stub.badges_path = tmp_path / "badges.json"
    resources_stub.team_pokemon_path = tmp_path / "team.json"
    sys.modules["Ankimon.resources"] = resources_stub

    # Load database_manager from disk under its real dotted name.
    db_mod = _load_module(
        "Ankimon.pyobj.database_manager",
        _src / "Ankimon" / "pyobj" / "database_manager.py",
    )

    # Settings imports ankimon_sync lazily inside load_config/save_config —
    # stub it so the obfuscation fallback path doesn't blow up.
    sync_stub = types.ModuleType("Ankimon.pyobj.ankimon_sync")
    class _NoSync:
        def _obfuscate_data(self, data): return ""
        def _deobfuscate_data(self, s): return {}
    sync_stub.AnkimonDataSync = _NoSync
    sys.modules["Ankimon.pyobj.ankimon_sync"] = sync_stub

    settings_mod = _load_module(
        "Ankimon.pyobj.settings",
        _src / "Ankimon" / "pyobj" / "settings.py",
    )

    return {
        "mw": real_mw,
        "user_path": user_path,
        "db_mod": db_mod,
        "settings_mod": settings_mod,
        # The live registry instance that settings.py imported (`from ..services
        # import services`). Setting .db on it is exactly how the app wires the seam.
        "services": settings_mod.services,
    }


def test_settings_reads_db_when_services_db_is_set_first(isolated_env):
    """
    THE FIX:  services.db is populated BEFORE Settings() is constructed.
    Settings.__init__ → load_config sees the DB and returns persisted values.
    """
    env = isolated_env
    db = env["db_mod"].AnkimonDB()

    # Persist a non-default value (gen9 default is False; we save True).
    db.save_all_config({"misc.gen9": True, "controls.allow_to_choose_moves": True})
    assert db.has_config()

    # Simulate the FIXED registry order: services.db is set FIRST.
    env["services"].db = db

    settings = env["settings_mod"].Settings()

    assert settings.get("misc.gen9") is True, (
        "Settings.load_config did not read misc.gen9=True from the DB — "
        "the init-order fix is regressing."
    )
    assert settings.get("controls.allow_to_choose_moves") is True, (
        "Settings.load_config did not read allow_to_choose_moves=True from DB"
    )


def test_settings_falls_through_to_defaults_without_services_db(isolated_env):
    """
    THE BUG (pre-fix):  Settings() runs before services.db is populated.
    Settings.load_config hits the `services.db is not None` gate, falls through,
    returns defaults even though the DB has saved values.

    This test pins the bug so a future regression of the init ordering will be
    caught: if Settings() is constructed before services.db is assigned, the
    integration breaks in exactly this way.
    """
    env = isolated_env
    db = env["db_mod"].AnkimonDB()
    db.save_all_config({"misc.gen9": True, "controls.allow_to_choose_moves": True})

    # Simulate the OLD broken order: services.db is NOT populated yet.
    assert env["services"].db is None

    settings = env["settings_mod"].Settings()

    # The persisted True values are ignored; DEFAULT_CONFIG wins.
    assert settings.get("misc.gen9") is False, (
        "Without mw.ankimon_db set, load_config should fall through to "
        "DEFAULT_CONFIG (which has misc.gen9=False). If this assertion fails, "
        "the DB-access gate in settings.py has changed and the regression "
        "test premise needs updating."
    )
    assert settings.get("controls.allow_to_choose_moves") is False, (
        "DEFAULT_CONFIG has allow_to_choose_moves=False; the bug path returned "
        "something else."
    )

    # And confirm the DB was NOT touched on the save_config path either —
    # the persisted row should be intact.
    persisted = db.get_all_config()
    assert persisted["misc.gen9"] is True, (
        "The DB row should still hold misc.gen9=True; load_config should not "
        "have rewritten it (the gate skips the write too)."
    )


def test_setting_save_persists_to_db_when_gate_open(isolated_env):
    """End-to-end: after the fix, set() actually persists to the DB so the
    next session can read it back. This is the user-visible contract."""
    env = isolated_env
    db = env["db_mod"].AnkimonDB()
    env["services"].db = db

    settings = env["settings_mod"].Settings()
    settings.set("misc.gen9", True)
    settings.set("controls.allow_to_choose_moves", True)

    # Re-read from the DB through a fresh AnkimonDB instance (simulates restart).
    db2 = env["db_mod"].AnkimonDB()
    cfg = db2.get_all_config()
    assert cfg["misc.gen9"] is True
    assert cfg["controls.allow_to_choose_moves"] is True
