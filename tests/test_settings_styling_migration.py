"""Regression tests for the gui.styling_in_reviewer -> gui.hud_styling rename.

The setting moved out of Settings > Styling and into HUD Element Toggles, which
renamed its config key. The config store only ever upserts — ``save_all_config``
is ``INSERT OR REPLACE`` per key and nothing removed rows — so popping the
legacy key from the in-memory dict left its row in the database. Every later
load read that row back, re-ran the migration and pinned ``gui.hud_styling`` to
its pre-migration value: a user who turned Styling off had it back on at the
next Anki start (and one who had it off could never turn it on), and the whole
config was rewritten to the DB on every single load.

These run against the real ``AnkimonDB`` because its persistence semantics are
what the bug lived in. They pin: the stored value carries across exactly once,
the legacy row is gone afterwards, the user's own choice then survives a
restart, a migrated profile stops re-saving on every load, and the new key wins
if a legacy row ever reappears.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_src = Path(__file__).parent.parent / "src"

LEGACY_KEY = "gui.styling_in_reviewer"
NEW_KEY = "gui.hud_styling"


def _install_aqt_stubs(mw):
    """Install fake aqt/PyQt6 modules and a configurable mw singleton."""
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
        "QWidget",
        "QVBoxLayout",
        "QLabel",
        "QLineEdit",
        "QPushButton",
        "QRadioButton",
        "QHBoxLayout",
        "QMainWindow",
        "QScrollArea",
        "QButtonGroup",
        "QComboBox",
        "QMessageBox",
        "QPixmap",
        "QPainter",
        "QPainterPath",
        "Qt",
        "QRectF",
    ):
        setattr(aqt_qt, name, MagicMock())
    sys.modules["aqt.qt"] = aqt_qt


def _load_module(dotted_name, file_path):
    """Load a module from a file path under a specific dotted name."""
    spec = importlib.util.spec_from_file_location(dotted_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated_env(tmp_path):
    """Fresh module namespace + temp user_path for each test."""
    for mod_name in list(sys.modules):
        if mod_name.startswith("Ankimon") or mod_name in (
            "aqt",
            "aqt.qt",
            "aqt.utils",
        ):
            del sys.modules[mod_name]

    user_path = tmp_path / "user_files"
    user_path.mkdir()

    class FakeMw:
        pass

    _install_aqt_stubs(FakeMw())

    ankimon_pkg = types.ModuleType("Ankimon")
    ankimon_pkg.__path__ = [str(_src / "Ankimon")]
    sys.modules["Ankimon"] = ankimon_pkg

    pyobj_pkg = types.ModuleType("Ankimon.pyobj")
    pyobj_pkg.__path__ = [str(_src / "Ankimon" / "pyobj")]
    sys.modules["Ankimon.pyobj"] = pyobj_pkg

    resources_stub = types.ModuleType("Ankimon.resources")
    resources_stub.user_path = user_path
    resources_stub.csv_file_items_cost = tmp_path / "items.csv"
    resources_stub.mypokemon_path = tmp_path / "mypokemon.json"
    resources_stub.mainpokemon_path = tmp_path / "mainpokemon.json"
    resources_stub.items_path = tmp_path / "items.json"
    resources_stub.badges_path = tmp_path / "badges.json"
    resources_stub.team_pokemon_path = tmp_path / "team.json"
    sys.modules["Ankimon.resources"] = resources_stub

    db_mod = _load_module(
        "Ankimon.pyobj.database_manager",
        _src / "Ankimon" / "pyobj" / "database_manager.py",
    )

    # Settings imports ankimon_sync lazily inside load/save_config — stub it so
    # the obfuscation fallback path never drags in aqt.
    sync_stub = types.ModuleType("Ankimon.pyobj.ankimon_sync")

    class _NoSync:
        def _obfuscate_data(self, data):
            return ""

        def _deobfuscate_data(self, s):
            return {}

    sync_stub.AnkimonDataSync = _NoSync
    sys.modules["Ankimon.pyobj.ankimon_sync"] = sync_stub

    settings_mod = _load_module(
        "Ankimon.pyobj.settings",
        _src / "Ankimon" / "pyobj" / "settings.py",
    )

    return {
        "user_path": user_path,
        "db_mod": db_mod,
        "settings_mod": settings_mod,
        "services": settings_mod.services,
    }


def _boot(env, stored=None):
    """Start Ankimon against the shared store, optionally seeding config rows.

    Calling it again simulates an Anki restart: a fresh AnkimonDB and a fresh
    Settings over the same database file.
    """
    db = env["db_mod"].AnkimonDB()
    if stored:
        db.save_all_config(stored)
    env["services"].db = db
    return env["settings_mod"].Settings(), db


@pytest.mark.parametrize("stored_value", [True, False])
def test_legacy_value_carries_across_the_rename(isolated_env, stored_value):
    env = isolated_env
    settings, _ = _boot(env, {LEGACY_KEY: stored_value})
    assert settings.get(NEW_KEY) is stored_value

    stored = env["db_mod"].AnkimonDB().get_all_config()
    assert stored[NEW_KEY] is stored_value, "the migrated value was not persisted"
    assert LEGACY_KEY not in stored


def test_legacy_row_is_deleted_from_the_database(isolated_env):
    env = isolated_env
    _boot(env, {LEGACY_KEY: False})
    stored = env["db_mod"].AnkimonDB().get_all_config()
    assert LEGACY_KEY not in stored, "the renamed key's row outlived the migration"
    assert stored[NEW_KEY] is False


@pytest.mark.parametrize("stored_value, chosen", [(True, False), (False, True)])
def test_user_choice_survives_a_restart(isolated_env, stored_value, chosen):
    """The bug this file exists for: a surviving legacy row re-applied the
    pre-migration value on every load, so the toggle could not be changed."""
    env = isolated_env
    settings, _ = _boot(env, {LEGACY_KEY: stored_value})
    settings.set(NEW_KEY, chosen)

    reloaded, _ = _boot(env)
    assert reloaded.get(NEW_KEY) is chosen


def test_migrated_profile_stops_rewriting_the_config(isolated_env):
    """``modified`` was true on every load while the legacy row survived, so
    ~60 rows plus a commit were rewritten at each startup."""
    env = isolated_env
    settings, db = _boot(env, {LEGACY_KEY: True})
    saves = []
    original_save = db.save_all_config
    db.save_all_config = lambda cfg: saves.append(1) or original_save(cfg)
    settings.load_config()
    assert saves == [], "a migrated profile must not re-save the config on load"


def test_new_key_wins_if_a_legacy_row_reappears(isolated_env):
    """A stale writer (an older build, a restored backup) must not be able to
    reach back and overwrite the current setting."""
    env = isolated_env
    settings, db = _boot(env, {LEGACY_KEY: True})
    settings.set(NEW_KEY, False)
    db.set_config_value(LEGACY_KEY, True)

    reloaded, _ = _boot(env)
    assert reloaded.get(NEW_KEY) is False


def test_clean_profile_defaults_to_styling_on(isolated_env):
    settings, _ = _boot(isolated_env)
    assert settings.get(NEW_KEY) is True
    assert LEGACY_KEY not in settings.config


def test_a_failed_save_leaves_the_legacy_row_alone(isolated_env):
    """``save_config`` swallows a DB write error, so "it returned" is not "it
    persisted". Deleting the legacy row on a failed save would destroy the
    stored value; keeping it just retries the migration on the next load."""
    env = isolated_env
    db = env["db_mod"].AnkimonDB()
    db.set_config_value(LEGACY_KEY, False)
    env["services"].db = db

    def _write_fails(_config):
        raise RuntimeError("database is locked")

    db.save_all_config = _write_fails
    env["settings_mod"].Settings()

    stored = env["db_mod"].AnkimonDB().get_all_config()
    assert stored.get(LEGACY_KEY) is False, (
        "a failed save deleted the only copy of the user's Styling choice"
    )

    recovered, _ = _boot(env)
    assert recovered.get(NEW_KEY) is False
