import os
import sys
import json
import datetime
import sqlite3
import threading
import time
from contextlib import closing
import pytest
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import types

# 1. SETUP CLEAN MOCKS BEFORE ANY IMPORTS
_src = Path(__file__).parent.parent / "src"

def setup_mocks():
    # Mock aqt/anki namespaces
    for name in [
        "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations",
        "aqt.reviewer", "aqt.webview", "aqt.main", "aqt.operations.QueryOp",
        "anki", "anki.hooks", "anki.collection", "anki.models", "anki.notes", "anki.template", "anki.buildinfo"
    ]:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

    # Stub parent packages so relative imports resolve without loading __init__.py
    if "Ankimon" not in sys.modules:
        _mod = types.ModuleType("Ankimon")
        _mod.__path__ = [str(_src / "Ankimon")]
        _mod.__package__ = "Ankimon"
        sys.modules["Ankimon"] = _mod
    else:
        _mod = sys.modules["Ankimon"]
        if not hasattr(_mod, "__path__") or not _mod.__path__:
            _mod.__path__ = [str(_src / "Ankimon")]

    # Load the REAL resources module (not a /tmp mock). This module also runs at
    # collection time; leaving a /tmp-path mock in sys.modules would poison other
    # modules that bind resource paths at import (e.g. business.py caches
    # ``effectiveness_chart_file_path``), breaking unrelated tests like
    # test_cp_formula. The fixture patches ``user_path`` per test for filesystem
    # isolation, so we do not need a mock here.
    _existing_res = sys.modules.get("Ankimon.resources")
    if (
        _existing_res is None
        or isinstance(_existing_res, MagicMock)
        or not hasattr(_existing_res, "effectiveness_chart_file_path")
    ):
        _res_spec = importlib.util.spec_from_file_location(
            "Ankimon.resources", _src / "Ankimon" / "resources.py"
        )
        _resources = importlib.util.module_from_spec(_res_spec)
        sys.modules["Ankimon.resources"] = _resources
        _res_spec.loader.exec_module(_resources)

    if "Ankimon.singletons" not in sys.modules:
        sys.modules["Ankimon.singletons"] = MagicMock()
    if "Ankimon.utils" not in sys.modules:
        sys.modules["Ankimon.utils"] = MagicMock()

    if "Ankimon.pyobj" not in sys.modules:
        _pyobj = types.ModuleType("Ankimon.pyobj")
        _pyobj.__path__ = [str(_src / "Ankimon" / "pyobj")]
        _pyobj.__package__ = "Ankimon.pyobj"
        sys.modules["Ankimon.pyobj"] = _pyobj
    else:
        _pyobj = sys.modules["Ankimon.pyobj"]
        if not hasattr(_pyobj, "__path__") or not _pyobj.__path__:
            _pyobj.__path__ = [str(_src / "Ankimon" / "pyobj")]

setup_mocks()

# Dynamically load modules to avoid importing __init__.py directly
def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, _src / relative_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_db_mod = load_module("Ankimon.pyobj.database_manager", "Ankimon/pyobj/database_manager.py")
_bm_mod = load_module("Ankimon.pyobj.backup_manager", "Ankimon/pyobj/backup_manager.py")

from Ankimon.pyobj.database_manager import AnkimonDB
from Ankimon.pyobj.backup_manager import BackupManager
# The seam registry backup_manager reads its database from (replaces mw.ankimon_db).
from Ankimon.services import services

class MockLogger:
    def log(self, level, msg): pass

@pytest.fixture
def mock_env(tmp_path):
    # Create temp user path and backup path structure
    user_files_dir = tmp_path / "user_files"
    user_files_dir.mkdir()
    addon_dir = tmp_path / "Ankimon"
    addon_dir.mkdir()

    # Mock resources within database_manager and backup_manager namespaces.
    # Also neutralize the interactive/UI helpers bound inside backup_manager:
    # in a full-suite run the real aqt.utils may already be imported, so the
    # module binds the real askUser/showInfo/showWarning (which dereference
    # aqt.mw.app) and the real close_anki. Patch them on the module under
    # test so the tests are deterministic regardless of collection order.
    with patch.object(_db_mod, "user_path", user_files_dir), \
         patch.object(_bm_mod, "user_path", user_files_dir), \
         patch.object(_bm_mod, "addon_dir", addon_dir), \
         patch.object(_bm_mod, "askUser", return_value=True), \
         patch.object(_bm_mod, "showInfo"), \
         patch.object(_bm_mod, "showWarning"), \
         patch.object(_bm_mod, "close_anki"):

        # Instantiate test database manager
        db = AnkimonDB(MockLogger())

        # Set config settings in the database config table
        db.set_config_value("trainer.name", "Red")
        db.set_config_value("trainer.cash", 5000)
        db.set_config_value("trainer.level", 12)

        # Instantiate test backup manager
        settings_mock = MagicMock()
        settings_mock.get.side_effect = lambda k, default=None: {
            "misc.developer_mode": False
        }.get(k, default)

        bm = BackupManager(MockLogger(), settings_mock)

        # Point the service registry's db at our test database. On main the seam
        # replaces exp's direct mw.ankimon_db access, so backup_manager reads
        # services.db instead of a mocked mw.
        with patch.object(services, "db", db):
            try:
                yield bm, db, user_files_dir, addon_dir
            finally:
                db.close()

def test_backup_summary_trainer_info(mock_env):
    bm, db, user_files_dir, addon_dir = mock_env

    # 1. Create a dummy backup directory (no DB files -> live fallback path)
    backup_dir = bm.backups_path / "backup_2026-05-31_23-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 2. Run generate_summary
    summary = bm._generate_summary(backup_dir)

    # 3. Assertions: check that trainer fields are loaded correctly from config (not user_data)
    assert summary["trainer_name"] == "Red"
    assert summary["trainer_cash"] == 5000
    assert summary["trainer_level"] == 12


def _seed_db(db_path, name, cash, level=7):
    """Create a real Ankimon SQLite file with trainer config values."""
    seeded = AnkimonDB(MockLogger(), db_path=db_path)
    seeded.set_config_value("trainer.name", name)
    seeded.set_config_value("trainer.cash", cash)
    seeded.set_config_value("trainer.level", level)
    seeded.close()


def test_dual_db_summary(mock_env):
    bm, db, user_files_dir, addon_dir = mock_env

    # A backup directory that actually contains BOTH database files.
    backup_dir = bm.backups_path / "backup_2026-06-01_10-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    _seed_db(backup_dir / "ankimon.db", "Blue", 999)
    _seed_db(backup_dir / "ankimonDEV.db", "DevGuy", 111)

    summary = bm._generate_summary(backup_dir)

    # Per-database sections are read from each backed-up DB file.
    assert summary["normal_stats"]["trainer_name"] == "Blue"
    assert summary["normal_stats"]["trainer_cash"] == 999
    assert summary["dev_stats"]["trainer_name"] == "DevGuy"
    assert summary["dev_stats"]["trainer_cash"] == 111

    # Active DB is ankimon.db (services.db.db_path.name) -> root mirrors normal_stats.
    assert summary["trainer_name"] == "Blue"
    assert summary["trainer_cash"] == 999


def test_get_backups_active_db_filtering(mock_env):
    bm, db, user_files_dir, addon_dir = mock_env

    # Backup A: a normal-mode backup (contains ankimon.db).
    a = bm.backups_path / "backup_2026-06-03_08-00-00"
    a.mkdir(parents=True, exist_ok=True)
    (a / "ankimon.db").write_bytes(b"x")
    with open(a / "summary.json", "w", encoding="utf-8") as f:
        json.dump({
            "date": "2026-06-03 08-00-00",
            "normal_stats": {"trainer_name": "Norm"},
            "dev_stats": {"trainer_name": "Dev"},
        }, f)

    # Backup B: a dev-only backup (contains ankimonDEV.db, NOT ankimon.db).
    b = bm.backups_path / "backup_2026-06-03_09-00-00"
    b.mkdir(parents=True, exist_ok=True)
    (b / "ankimonDEV.db").write_bytes(b"x")
    with open(b / "summary.json", "w", encoding="utf-8") as f:
        json.dump({
            "date": "2026-06-03 09-00-00",
            "normal_stats": {"trainer_name": "Norm2"},
            "dev_stats": {"trainer_name": "Dev2"},
        }, f)

    # Active DB is ankimon.db -> only the normal backup is listed.
    backups = bm.get_backups()
    names = {Path(bk["path"]).name for bk in backups}
    assert "backup_2026-06-03_08-00-00" in names
    assert "backup_2026-06-03_09-00-00" not in names

    a_entry = next(bk for bk in backups if Path(bk["path"]).name == "backup_2026-06-03_08-00-00")
    # The active DB's stats section is merged onto the root for the UI.
    assert a_entry["trainer_name"] == "Norm"


def test_restore_only_active_db(mock_env):
    bm, db, user_files_dir, addon_dir = mock_env
    from Ankimon.save_import import cancel_pending_import, pending_import_info

    backup_dir = bm.backups_path / "backup_2026-06-02_09-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    _seed_db(backup_dir / "ankimon.db", "Blue", 999)
    _seed_db(backup_dir / "ankimonDEV.db", "DevGuy", 111)
    with closing(sqlite3.connect(backup_dir / "ankimon.db")) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config VALUES "
            "('leaderboard.api_key', 'private-local-backup-key')"
        )
        conn.commit()

    # Restore is staged: the current runtime keeps its original live DB until
    # a fresh process can safely install the selected backup.
    bm.restore_backup(str(backup_dir))

    assert db.get_config_value("trainer.name") == "Red"
    assert db.get_config_value("trainer.cash") == 5000
    pending = pending_import_info(db.db_path)
    assert pending is not None
    with closing(sqlite3.connect(pending["pending_path"])) as staged:
        assert staged.execute(
            "SELECT value FROM config WHERE key='trainer.name'"
        ).fetchone()[0] == "Blue"
        # Local Backup Manager restores retain this installation's credentials.
        assert staged.execute(
            "SELECT value FROM config WHERE key='leaderboard.api_key'"
        ).fetchone()[0] == "private-local-backup-key"
    assert not (user_files_dir / "ankimonDEV.db").exists()
    assert cancel_pending_import(db.db_path) is True


def test_restore_targets_the_actual_custom_active_path(mock_env, tmp_path):
    bm, _, user_files_dir, _ = mock_env
    from Ankimon.save_import import cancel_pending_import, pending_import_info

    custom_dir = tmp_path / "custom-profile"
    custom_dir.mkdir()
    active = AnkimonDB(MockLogger(), db_path=custom_dir / "profile-save.db")
    active.set_config_value("trainer.name", "Current custom")
    backup_dir = bm.backups_path / "backup_2026-06-02_10-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    _seed_db(backup_dir / "profile-save.db", "Restored custom", 4242)
    try:
        with patch.object(services, "db", active):
            bm.restore_backup(str(backup_dir))
            pending = pending_import_info(active.db_path)
            assert pending is not None
            assert pending["target"] == str(active.db_path.resolve())
            assert not (user_files_dir / active.db_path.name).exists()
            with closing(sqlite3.connect(pending["pending_path"])) as staged:
                assert staged.execute(
                    "SELECT value FROM config WHERE key='trainer.name'"
                ).fetchone()[0] == "Restored custom"
            assert cancel_pending_import(active.db_path) is True
    finally:
        active.close()


def test_restore_rejects_a_corrupt_backup_without_touching_live_save(mock_env):
    bm, db, _, _ = mock_env
    from Ankimon.save_import import pending_import_info

    backup_dir = bm.backups_path / "backup_2026-06-02_11-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / db.db_path.name).write_bytes(b"not a database")

    bm.restore_backup(str(backup_dir))

    assert db.get_config_value("trainer.name") == "Red"
    assert pending_import_info(db.db_path) is None


@pytest.mark.parametrize("close_error", [RuntimeError("shutdown failed"), None])
def test_restore_stays_pending_when_real_close_helper_cannot_exit(mock_env, close_error):
    """Use the real shutdown helper so swallowed exceptions cannot hide a warning."""
    bm, db, _, _ = mock_env
    from Ankimon.save_import import cancel_pending_import, pending_import_info
    from Ankimon.utils import close_anki

    backup_dir = bm.backups_path / "backup_to_restore"
    backup_dir.mkdir()
    _seed_db(backup_dir / "ankimon.db", "Restored", 42)

    def close():
        if close_error is not None:
            raise close_error
        # Anki can refuse to close when the user chooses Keep Editing.
        return False

    aqt = types.ModuleType("aqt")
    aqt.mw = types.SimpleNamespace(close=close)
    try:
        with patch.dict(sys.modules, {"aqt": aqt}), \
             patch.object(_bm_mod, "close_anki", close_anki), \
             patch.object(_bm_mod, "showWarning") as warning:
            bm.restore_backup(str(backup_dir))

        assert pending_import_info(db.db_path) is not None
        assert db.get_config_value("trainer.name") == "Red"
        if close_error is not None:
            assert warning.call_count == 1
            message = warning.call_args.args[0]
            assert "shutdown failed" in message
            assert "current save is still active" in message
            assert "restore remains pending" in message
        else:
            warning.assert_not_called()
    finally:
        cancel_pending_import(db.db_path)


@pytest.mark.parametrize("filename", ["ankimon.db", "ankimonDEV.db"])
def test_backup_contains_committed_wal_while_reader_blocks_checkpoint(mock_env, filename):
    """Copying the main file after a busy checkpoint loses committed cash."""
    bm, db, user_files_dir, _ = mock_env
    source = user_files_dir / filename
    if filename == "ankimonDEV.db":
        _seed_db(source, "Dev", 5000)
    db.execute("PRAGMA busy_timeout=1")

    with closing(sqlite3.connect(source, timeout=0.01)) as writer, \
         closing(sqlite3.connect(source)) as reader:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        reader.execute("BEGIN")
        assert reader.execute(
            "SELECT value FROM config WHERE key='trainer.cash'"
        ).fetchone()[0] == "5000"
        writer.execute("UPDATE config SET value='12345' WHERE key='trainer.cash'")
        writer.commit()
        busy, pages, checkpointed = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert busy == 1 and pages > checkpointed

        assert bm.create_backup(required_file=filename) is True
        backup = next(bm.backups_path.glob("backup_*")) / filename
        # Read only the published file, without allowing a companion WAL to
        # supply missing pages: this is what a restored backup can recover.
        with closing(sqlite3.connect(backup.as_uri() + "?immutable=1", uri=True)) as saved:
            assert saved.execute(
                "SELECT value FROM config WHERE key='trainer.cash'"
            ).fetchone()[0] == "12345"
        reader.rollback()


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("filename", ["ankimon.db", "profile-save.db"])
def test_backup_uses_active_database_at_custom_path(mock_env, tmp_path, manual, filename):
    """A same-named default save must never stand in for the active profile."""
    bm, _, _, _ = mock_env
    custom_dir = tmp_path / "profile with spaces #?"
    custom_dir.mkdir()
    active = AnkimonDB(MockLogger(), db_path=custom_dir / filename)
    active.set_config_value("trainer.cash", 76543)
    try:
        with patch.object(services, "db", active):
            assert bm.create_backup(manual=manual, required_file=filename) is True
        backup = next(bm.backups_path.glob("backup_*")) / filename
        with closing(sqlite3.connect(backup)) as saved:
            assert saved.execute(
                "SELECT value FROM config WHERE key='trainer.cash'"
            ).fetchone()[0] == "76543"
    finally:
        active.close()


@pytest.mark.parametrize("contents", [b"not a database", b""])
def test_backup_rejects_invalid_required_database(mock_env, contents):
    """A file's existence alone cannot establish a recoverable backup."""
    bm, _, user_files_dir, _ = mock_env
    (user_files_dir / "ankimonDEV.db").write_bytes(contents)

    assert bm.create_backup(required_file="ankimonDEV.db") is False
    assert not list(bm.backups_path.iterdir())


def _seed_prior_backups(bm):
    backups = {}
    for index in range(5):
        backup_dir = bm.backups_path / f"backup_prior_{index}"
        backup_dir.mkdir()
        database = backup_dir / "ankimonDEV.db"
        _seed_db(database, f"Prior {index}", index)
        backups[database] = database.read_bytes()
        (backup_dir / "summary.json").write_text(json.dumps({
            "date": f"Prior {index}",
            "dev_stats": {"trainer_name": f"Prior {index}", "trainer_cash": index},
        }), encoding="utf-8")
        modified = time.time() - (5 - index) * 60
        os.utime(backup_dir, (modified, modified))
    return backups


@pytest.mark.parametrize("manual", [False, True])
def test_failed_required_backup_preserves_all_five_prior_backups(mock_env, manual):
    """Neither an empty attempt nor another mode's snapshot may evict recovery data."""
    bm, db, user_files_dir, _ = mock_env
    prior = _seed_prior_backups(bm)
    source = user_files_dir / "ankimonDEV.db"
    source.write_bytes(b"not a database")

    with patch.object(db, "db_path", source):
        assert bm.create_backup(manual=manual) is False

    assert set(bm.backups_path.iterdir()) == {path.parent for path in prior}
    assert all(path.read_bytes() == content for path, content in prior.items())


def test_locked_required_backup_preserves_all_five_prior_backups(mock_env):
    bm, _, user_files_dir, _ = mock_env
    prior = _seed_prior_backups(bm)
    source = user_files_dir / "ankimonDEV.db"
    _seed_db(source, "Locked", 10)
    snapshot = bm._snapshot_database

    def quick_snapshot(source_path, destination_path):
        return snapshot(source_path, destination_path, timeout=0.05)

    with closing(sqlite3.connect(source, check_same_thread=False)) as locker:
        locker.execute("PRAGMA journal_mode=DELETE")
        locker.execute("BEGIN EXCLUSIVE")
        release = threading.Timer(1.0, locker.rollback)
        release.start()
        try:
            with patch.object(bm, "_snapshot_database", side_effect=quick_snapshot):
                assert bm.create_backup(required_file="ankimonDEV.db") is False
        finally:
            release.cancel()
            release.join()
            locker.rollback()

    assert set(bm.backups_path.iterdir()) == {path.parent for path in prior}
    assert all(path.read_bytes() == content for path, content in prior.items())


def test_incomplete_backup_survives_failed_removal_without_evicting_valid_backups(mock_env):
    """A leftover failed attempt must not occupy a retention slot on the next success."""
    bm, db, user_files_dir, _ = mock_env
    prior = _seed_prior_backups(bm)
    source = user_files_dir / "ankimonDEV.db"
    source.write_bytes(b"not a database")
    now = datetime.datetime.now()

    with patch.object(_bm_mod.datetime, "datetime", wraps=datetime.datetime) as clock, \
         patch.object(db, "db_path", source):
        clock.now.return_value = now
        with patch.object(_bm_mod.shutil, "rmtree", side_effect=PermissionError("file locked")):
            assert bm.create_backup() is False

        assert all(path.read_bytes() == content for path, content in prior.items())
        failed_directories = set(bm.backups_path.iterdir()) - {path.parent for path in prior}
        assert len(failed_directories) == 1

        source.unlink()
        _seed_db(source, "Next success", 100)
        clock.now.return_value = now + datetime.timedelta(seconds=1)
        assert bm.create_backup() is True

        # Only the oldest of the five valid copies should rotate out. The
        # unreadable attempt must neither count nor be listed as a backup.
        surviving_prior = list(prior.items())[1:]
        assert all(path.exists() for path, _ in surviving_prior)
        assert all(path.read_bytes() == content for path, content in surviving_prior)
        assert len(bm.get_backups()) == 5
        assert not {Path(backup["path"]) for backup in bm.get_backups()} & failed_directories

    # The failed attempt captured the other mode, which must not make it
    # visible when that mode is active either.
    assert not {Path(backup["path"]) for backup in bm.get_backups()} & failed_directories


@pytest.mark.parametrize("prefix", ["backup_", ".backup_"])
def test_failed_backup_mkdir_preserves_existing_same_timestamp_directory(mock_env, prefix):
    bm, _, _, _ = mock_env
    now = datetime.datetime.now()
    backup_dir = bm.backups_path / f"{prefix}{now:%Y-%m-%d_%H-%M-%S}"
    backup_dir.mkdir()
    database = backup_dir / "ankimon.db"
    _seed_db(database, "Existing", 123)
    original = database.read_bytes()
    # Even an expired existing backup must survive a failed creation attempt.
    expired = time.time() - 30 * 24 * 3600
    os.utime(backup_dir, (expired, expired))

    with patch.object(_bm_mod.datetime, "datetime", wraps=datetime.datetime) as clock:
        clock.now.return_value = now
        assert bm.create_backup() is False

    assert database.read_bytes() == original


def test_failed_other_mode_snapshot_keeps_successful_required_backup(mock_env):
    bm, _, user_files_dir, _ = mock_env
    (user_files_dir / "ankimonDEV.db").write_bytes(b"not a database")

    assert bm.create_backup(required_file="ankimon.db") is True

    backup_dir = next(bm.backups_path.glob("backup_*"))
    with closing(sqlite3.connect(backup_dir / "ankimon.db")) as saved:
        assert saved.execute(
            "SELECT value FROM config WHERE key='trainer.cash'"
        ).fetchone()[0] == "5000"
    assert not (backup_dir / "ankimonDEV.db").exists()


def test_snapshot_timeout_does_not_publish_partial_backup(mock_env, tmp_path):
    """A locked source must stop within its deadline and leave no usable backup."""
    bm, _, _, _ = mock_env
    source = tmp_path / "locked.db"
    destination = tmp_path / "saved.db"
    _seed_db(source, "Locked", 10)
    with closing(sqlite3.connect(source, check_same_thread=False)) as locker:
        locker.execute("PRAGMA journal_mode=DELETE")
        locker.execute("BEGIN EXCLUSIVE")
        # Release even if a regression removes the timeout, so a failed test
        # cannot hang the test runner indefinitely inside sqlite3.backup().
        release = threading.Timer(1.0, locker.rollback)
        release.start()
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError):
                bm._snapshot_database(source, destination, timeout=0.05)
            assert time.monotonic() - started < 0.75
        finally:
            release.cancel()
            release.join()
            locker.rollback()
    assert not destination.exists()
    assert not list(tmp_path.glob(".snapshot-*"))


def test_snapshot_missing_source_is_not_created(mock_env, tmp_path):
    bm, _, _, _ = mock_env
    source = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        bm._snapshot_database(source, tmp_path / "saved.db")
    assert not source.exists()
    assert not (tmp_path / "saved.db").exists()
    assert not list(tmp_path.glob(".snapshot-*"))


def test_backup_rejects_corruption_that_sqlite_can_copy(mock_env):
    """Online backup copies pages without verifying their logical consistency."""
    bm, _, user_files_dir, _ = mock_env
    source = user_files_dir / "ankimonDEV.db"
    with closing(sqlite3.connect(source)) as conn:
        conn.executescript(
            "CREATE TABLE captured_pokemon(id INTEGER, data TEXT);"
            "CREATE TABLE scratch(data BLOB);"
            "INSERT INTO scratch VALUES (zeroblob(10000));"
            "DROP TABLE scratch;"
        )
    damaged = bytearray(source.read_bytes())
    # The freelist still has pages, but its SQLite header count falsely says
    # zero. Online backup succeeds; quick_check detects the inconsistency.
    damaged[36:40] = (0).to_bytes(4, "big")
    source.write_bytes(damaged)

    assert bm.create_backup(required_file="ankimonDEV.db") is False
    assert not list(bm.backups_path.iterdir())
    assert source.read_bytes() == damaged


def test_custom_active_filename_summary_describes_its_snapshot(mock_env, tmp_path):
    bm, _, _, _ = mock_env
    active = AnkimonDB(MockLogger(), db_path=tmp_path / "profile.db")
    active.set_config_value("trainer.name", "Custom profile")
    active.set_config_value("trainer.cash", 54321)
    try:
        with patch.object(services, "db", active):
            assert bm.create_backup() is True
            active.set_config_value("trainer.cash", 1)
            summary = bm.get_backups()[0]
            assert summary["trainer_name"] == "Custom profile"
            assert summary["trainer_cash"] == 54321
    finally:
        active.close()


def test_shutdown_backup_spends_one_budget_across_both_databases(mock_env, monkeypatch):
    """Two locked saves must not each hold the close for a full timeout."""
    bm, _, user_files_dir, _ = mock_env
    _seed_db(user_files_dir / "ankimonDEV.db", "Dev", 1)
    clock = [100.0]
    monkeypatch.setattr(_bm_mod.time, "monotonic", lambda: clock[0])
    attempts = []

    def exhaust(source_path, destination_path, timeout=30.0):
        # A locked source spends everything it is given, then fails.
        attempts.append((Path(source_path).name, timeout))
        clock[0] += timeout
        raise TimeoutError("Timed out taking a database backup")

    monkeypatch.setattr(bm, "_snapshot_database", exhaust)
    bm.on_anki_close()

    # The active save goes first and consumes the whole shutdown budget; the
    # companion database is refused rather than given a second full timeout.
    assert [name for name, _ in attempts] == ["ankimon.db"]
    assert attempts[0][1] == pytest.approx(bm.SHUTDOWN_BACKUP_BUDGET)
    assert clock[0] - 100.0 <= bm.SHUTDOWN_BACKUP_BUDGET

    # A pre-overwrite backup is not a shutdown: every file keeps the per-file
    # default, and the call shape stays positional for callers that patch it.
    attempts.clear()
    assert bm.create_backup(required_file="ankimon.db") is False
    assert [name for name, _ in attempts] == ["ankimon.db", "ankimonDEV.db"]
    assert {timeout for _, timeout in attempts} == {30.0}


class _LockedLiveDatabase:
    """A live handle with the accessors the backup summary reads.

    Behaves like ``AnkimonDB`` towards ``backup_manager``, but every read opens
    a real connection to a genuinely locked file, so its own busy timeout —
    not a mocked clock — is what the shutdown budget has to keep out.
    """

    def __init__(self, path: Path, timeout: float):
        self.db_path = path
        self._timeout = timeout
        self.live_reads = 0

    def _read(self, statement, *parameters):
        self.live_reads += 1
        with closing(sqlite3.connect(self.db_path, timeout=self._timeout)) as conn:
            return conn.execute(statement, parameters).fetchone()

    def get_stats(self):
        return {"pokemon": self._read("SELECT COUNT(*) FROM captured_pokemon")[0], "items": 0}

    def get_config_value(self, key, default=None):
        row = self._read("SELECT value FROM config WHERE key=?", key)
        return row[0] if row else default

    def get_main_pokemon(self):
        return None

    def close(self):
        pass


def test_failed_shutdown_backup_does_not_read_the_locked_live_database(mock_env, monkeypatch, tmp_path):
    """The shutdown budget bounds the WHOLE call, summary generation included.

    A summary is only ever published alongside a verified snapshot, so when the
    required snapshot times out there is nothing to describe. Generating one
    anyway sends ``_generate_summary`` down its live-database fallback and into
    the same lock the deadline just gave up on, for a second full timeout.
    """
    bm, _, _, _ = mock_env
    source = tmp_path / "profile" / "ankimon.db"
    source.parent.mkdir()
    _seed_db(source, "Locked", 7)
    with closing(sqlite3.connect(source)) as setup:
        # A rollback journal is what makes one writer exclude every reader,
        # including the read-only connection the snapshot opens.
        setup.execute("PRAGMA journal_mode=DELETE")

    live = _LockedLiveDatabase(source, timeout=2.0)
    monkeypatch.setattr(services, "db", live)
    monkeypatch.setattr(bm, "SHUTDOWN_BACKUP_BUDGET", 0.1)

    with closing(sqlite3.connect(source, check_same_thread=False)) as locker:
        locker.execute("BEGIN EXCLUSIVE")
        # Release even if a regression removes the bound, so a failing test
        # cannot wedge the runner inside a multi-second busy wait.
        release = threading.Timer(10.0, locker.rollback)
        release.start()
        started = time.monotonic()
        try:
            bm.on_anki_close()
            elapsed = time.monotonic() - started
        finally:
            release.cancel()
            release.join()
            locker.rollback()

    assert live.live_reads == 0
    assert elapsed < 1.0
    assert not list(bm.backups_path.glob("backup_*"))
    assert not list(bm.backups_path.glob(".backup_*"))


def _fake_backups(bm, count):
    """Published-looking backup directories, oldest first and all within the
    age limit, so only the MAX_BACKUPS count rule decides what goes."""
    made = []
    now = time.time()
    for index in range(count):
        directory = bm.backups_path / f"backup_2020-01-01_00-00-{index:02d}"
        directory.mkdir()
        stamp = now - (count - index)
        os.utime(directory, (stamp, stamp))
        made.append(directory)
    return made


def test_retention_failure_cannot_abort_anki_closing(mock_env, monkeypatch):
    """profile_will_close does not catch what its handlers raise.

    Retention runs after a backup has already been published, and deleting an
    old directory can fail for reasons that have nothing to do with it -- a
    Windows lock, an antivirus scan, a permission change. Letting that escape
    would take Anki's close down over housekeeping, and throw away the backup
    the call had just made.
    """
    bm, _, _, _ = mock_env
    _fake_backups(bm, bm.MAX_BACKUPS + 3)

    def refuse(path, *args, **kwargs):
        raise OSError("simulated lock on an old backup")

    monkeypatch.setattr(_bm_mod.shutil, "rmtree", refuse)

    bm.on_anki_close()

    published = [p for p in bm.backups_path.iterdir()
                 if p.name.startswith("backup_") and (p / "ankimon.db").is_file()]
    assert len(published) == 1, "the shutdown backup was lost to a retention failure"
    assert len(list(bm.backups_path.glob("backup_*"))) == bm.MAX_BACKUPS + 4


def test_retention_does_not_spend_a_shutdown_budget_it_no_longer_has(mock_env, monkeypatch):
    """Deleting directories is not work to do while the user waits to exit."""
    bm, _, _, _ = mock_env
    _fake_backups(bm, bm.MAX_BACKUPS + 3)
    removed = []
    monkeypatch.setattr(_bm_mod.shutil, "rmtree", lambda path, *a, **k: removed.append(Path(path)))

    clock = [500.0]
    monkeypatch.setattr(_bm_mod.time, "monotonic", lambda: clock[0])
    bm.cleanup_backups(deadline=clock[0] - 1)
    assert removed == []

    # With budget left it does the same work as before.
    bm.cleanup_backups(deadline=clock[0] + 60)
    assert len(removed) == 3


def test_an_abandoned_staging_directory_is_swept_once_it_is_stale(mock_env, monkeypatch):
    """Nothing else can remove it: listing and retention both filter on backup_."""
    bm, _, _, _ = mock_env
    stale = bm.backups_path / ".backup_2020-01-01_00-00-00"
    stale.mkdir()
    (stale / "ankimon.db").write_bytes(b"a whole save copy")
    os.utime(stale, (1_600_000_000, 1_600_000_000))
    live = bm.backups_path / ".backup_2020-01-01_00-00-01"
    live.mkdir()

    bm.cleanup_backups()

    assert not stale.exists()
    assert live.is_dir(), "a staging directory an attempt may still be using was removed"
