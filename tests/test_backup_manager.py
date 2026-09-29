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

    # BackupManager anchors backups_path at mw.pm.profileFolder(). Point that at
    # a real per-test folder: without it, Path(MagicMock(...)) stringifies to a
    # junk path shared by every manager in the session, so backups created by
    # other tests appear in this one's listing (and vice versa).
    profile_folder = tmp_path / "anki-profile"
    profile_folder.mkdir(parents=True, exist_ok=True)

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
         patch.object(_bm_mod, "close_anki"), \
         patch("aqt.mw.pm.profileFolder", return_value=str(profile_folder)):

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
             patch.object(services, "ui", MagicMock()) as ui:
            bm.restore_backup(str(backup_dir))

        warning = ui.warn
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
        with patch.object(_bm_mod.BackupManager, "_remove_tree",
                          side_effect=PermissionError("file locked")):
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
    # A colliding staging directory is another attempt's and stays fresh: one
    # past the stale age was abandoned, and a failed attempt sweeps it too.
    if prefix == "backup_":
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


def test_snapshot_of_a_database_that_is_not_a_save_is_refused(mock_env, tmp_path):
    """A readable SQLite file without captured_pokemon must not be published."""
    bm, _, _, _ = mock_env
    source = tmp_path / "other.db"
    destination = tmp_path / "saved.db"
    with closing(sqlite3.connect(source)) as conn:
        conn.executescript("CREATE TABLE notes(id INTEGER, data TEXT);")
    with pytest.raises(ValueError):
        bm._snapshot_database(source, destination)
    assert not list(tmp_path.glob("saved.db*"))
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


@pytest.mark.parametrize("active_name", ["ankimon.db", "ankimonDEV.db"])
def test_shutdown_backup_spends_one_budget_across_both_databases(mock_env, monkeypatch, active_name):
    """Two locked saves must not each hold the close for a full timeout."""
    bm, db, user_files_dir, _ = mock_env
    _seed_db(user_files_dir / "ankimonDEV.db", "Dev", 1)
    # FILES_TO_BACKUP lists ankimon.db first anyway, so only a developer-mode
    # active save shows that the active save really is the one tried first.
    monkeypatch.setattr(db, "db_path", user_files_dir / active_name)
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
    assert [name for name, _ in attempts] == [active_name]
    assert attempts[0][1] == pytest.approx(bm.SHUTDOWN_BACKUP_BUDGET)
    assert clock[0] - 100.0 <= bm.SHUTDOWN_BACKUP_BUDGET

    # A backup outside shutdown has no deadline: every file keeps the per-file
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

    rename = Path.rename

    def refuse(path, *args, **kwargs):
        if path.name.startswith("backup_2020"):
            raise OSError("simulated lock on an old backup")
        return rename(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", refuse)

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
    monkeypatch.setattr(_bm_mod.BackupManager, "_remove_tree",
                        lambda self, path, deadline: removed.append(Path(path)) or True)

    clock = [500.0]
    monkeypatch.setattr(_bm_mod.time, "monotonic", lambda: clock[0])
    bm.cleanup_backups(deadline=clock[0] - 1)
    assert removed == []
    assert not list(bm.backups_path.glob(".discard_*"))

    # With budget left it does the same work as before.
    bm.cleanup_backups(deadline=clock[0] + 60)
    assert len(removed) == 3


def test_a_backup_that_cannot_be_removed_never_costs_one_the_policy_keeps(mock_env, monkeypatch):
    """Only removals that were due are attempted, and a failure adds none.

    The oldest backup is locked and the next one is not. Stopping at the lock
    would let one stuck directory grow the folder without bound; carrying on
    removes only what lay outside the newest MAX_BACKUPS anyway.
    """
    bm, _, _, _ = mock_env
    made = _fake_backups(bm, bm.MAX_BACKUPS + 2)
    rename = Path.rename

    def oldest_locked(path, *args, **kwargs):
        if path == made[0]:
            raise PermissionError("simulated lock on the oldest backup")
        return rename(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", oldest_locked)
    bm.cleanup_backups()

    assert made[0].is_dir(), "the locked backup should simply remain"
    assert not made[1].exists()
    assert all(path.is_dir() for path in made[2:])
    assert not list(bm.backups_path.glob(".discard_*"))


def test_a_removal_that_fails_part_way_cannot_pose_as_the_newest_backup(mock_env):
    """Deleting inside a directory restamps its mtime, and retention sorts by mtime.

    Emptied in place, what a failed removal leaves behind would sort as the
    newest backup, and the next pass would evict a good one to keep it.
    """
    bm, _, _, _ = mock_env
    made = _fake_backups(bm, bm.MAX_BACKUPS + 1)
    for name in ("ankimon.db", "ankimonDEV.db", "summary.json"):
        (made[0] / name).write_bytes(b"x")
    stamp = time.time() - 3600
    os.utime(made[0], (stamp, stamp))
    unlink, calls = os.unlink, []

    def second_file_locked(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            raise PermissionError("simulated lock on one file of the oldest backup")
        return unlink(path, *args, **kwargs)

    with patch.object(os, "unlink", side_effect=second_file_locked):
        bm.cleanup_backups()
    assert len(calls) == 2, "the removal was not cut short part-way"

    newest = bm.backups_path / "backup_2020-01-01_00-01-00"
    newest.mkdir()
    bm.cleanup_backups()

    kept = sorted(path.name for path in bm.backups_path.glob("backup_*"))
    assert kept == sorted([path.name for path in made[2:]] + [newest.name])


def test_a_removal_stops_between_entries_once_the_shutdown_budget_is_spent(mock_env):
    """shutil.rmtree cannot be interrupted; one entry at a time can."""
    bm, _, _, _ = mock_env
    doomed = bm.backups_path / "backup_2020-01-01_00-00-00"
    doomed.mkdir()
    for name in ("ankimon.db", "ankimonDEV.db", "summary.json"):
        (doomed / name).write_bytes(b"x")
    clock = [0.0]
    unlink = os.unlink

    def slow_disk(path, *args, **kwargs):
        clock[0] += 10  # every deletion outlasts the whole budget
        return unlink(path, *args, **kwargs)

    with patch.object(_bm_mod.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(os, "unlink", side_effect=slow_disk):
        assert bm._discard(doomed, "old backup", deadline=5.0) is False

    # Out of the listing at once, one entry gone, the rest left for later.
    assert not doomed.exists()
    [leftover] = bm.backups_path.glob(".discard_*")
    assert len(list(leftover.iterdir())) == 2
    bm.cleanup_backups()
    assert not leftover.exists()


def test_a_removal_that_starts_after_the_budget_does_not_list_the_directory(mock_env):
    """iterdir reads the whole listing before it yields the first entry.

    So a deadline checked only inside the loop is checked after that read.
    """
    bm, _, _, _ = mock_env
    doomed = bm.backups_path / ".discard_00000000_backup_2020-01-01_00-00-00"
    doomed.mkdir(parents=True)
    (doomed / "ankimon.db").write_bytes(b"x")

    with patch.object(_bm_mod.time, "monotonic", return_value=10.0), \
         patch.object(Path, "iterdir", side_effect=AssertionError("listed after the deadline")):
        assert bm._remove_tree(doomed, deadline=5.0) is False
        assert (doomed / "ankimon.db").exists()
        # An empty one needs no listing, so it still goes.
        (doomed / "ankimon.db").unlink()
        assert bm._remove_tree(doomed, deadline=5.0) is True
    assert not doomed.exists()


def test_restore_over_an_import_of_unknown_state_claims_neither_answer(mock_env, monkeypatch):
    """A locked save gives no answer in time; neither confident message is safe."""
    bm, db, _, _ = mock_env
    from Ankimon import save_import

    backup_dir = bm.backups_path / "backup_2026-06-02_12-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    _seed_db(backup_dir / "ankimon.db", "Blue", 999)
    bm.restore_backup(str(backup_dir))
    assert save_import.pending_import_info(db.db_path) is not None
    monkeypatch.setattr(save_import, "pending_import_is_installed", lambda target: None)
    try:
        with patch.object(services, "ui", MagicMock()) as ui:
            bm.restore_backup(str(backup_dir))
        message = ui.warn.call_args.args[0]
        assert "could not tell whether" in message
        assert "will install at the next" not in message
        assert "is the save you are playing" not in message
    finally:
        save_import.cancel_pending_import(db.db_path)


def test_restore_over_an_already_installed_import_says_nothing_installs_twice(mock_env, monkeypatch):
    """Import's notice says so, and the same install path holds for a restore."""
    bm, db, _, _ = mock_env
    from Ankimon import save_import

    backup_dir = bm.backups_path / "backup_2026-06-02_13-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    _seed_db(backup_dir / "ankimon.db", "Blue", 999)
    bm.restore_backup(str(backup_dir))
    assert save_import.pending_import_info(db.db_path) is not None
    monkeypatch.setattr(save_import, "pending_import_is_installed", lambda target: True)
    try:
        with patch.object(services, "ui", MagicMock()) as ui:
            bm.restore_backup(str(backup_dir))
        message = ui.warn.call_args.args[0]
        assert "ALREADY installed" in message
        assert "Nothing will be installed a second time." in message
        assert "will install at the next" not in message
    finally:
        save_import.cancel_pending_import(db.db_path)


def test_a_manual_delete_that_fails_part_way_cannot_pose_as_the_newest_backup(mock_env):
    """The Delete button had the same restamped-remains problem as retention."""
    bm, _, _, _ = mock_env
    made = _fake_backups(bm, bm.MAX_BACKUPS)
    for name in ("ankimon.db", "ankimonDEV.db", "summary.json"):
        (made[1] / name).write_bytes(b"x")
    unlink, calls = os.unlink, []

    def second_file_locked(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            raise PermissionError("simulated lock on one file of the backup")
        return unlink(path, *args, **kwargs)

    with patch.object(os, "unlink", side_effect=second_file_locked), \
         patch.object(_bm_mod, "showInfo"), patch.object(_bm_mod, "showWarning"):
        bm.delete_backup(str(made[1]))
    assert len(calls) == 2, "the removal was not cut short part-way"

    newest = bm.backups_path / "backup_2020-01-01_00-01-00"
    newest.mkdir()
    bm.cleanup_backups()

    kept = sorted(path.name for path in bm.backups_path.glob("backup_*"))
    assert kept == sorted([made[0].name] + [path.name for path in made[2:]] + [newest.name])
    assert not list(bm.backups_path.glob(".discard_*")), "retention should finish the removal"


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_retention_removes_a_linked_backup_without_emptying_what_it_points_at(mock_env, tmp_path):
    """shutil.rmtree refuses a link; walking one deletes files outside the folder."""
    bm, _, _, _ = mock_env
    elsewhere = tmp_path / "backup-on-another-drive"
    elsewhere.mkdir()
    (elsewhere / "ankimon.db").write_bytes(b"a save the user moved and linked back")
    stamp = time.time() - 3600
    os.utime(elsewhere, (stamp, stamp))
    made = _fake_backups(bm, bm.MAX_BACKUPS)
    link = bm.backups_path / "backup_2019-01-01_00-00-00"
    link.symlink_to(elsewhere, target_is_directory=True)

    bm.cleanup_backups()

    assert (elsewhere / "ankimon.db").is_file()
    assert not link.exists() and not link.is_symlink()
    assert all(path.is_dir() for path in made)
    assert not list(bm.backups_path.glob(".discard_*"))


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
@pytest.mark.parametrize("route", ["retention", "delete", "entry"])
def test_a_junction_is_removed_as_a_link_on_pythons_without_isjunction(mock_env, tmp_path, monkeypatch, route):
    """Before Python 3.12 only the reparse tag os.lstat reports gives a junction away.

    Those Pythons have no os.path.isjunction, and Path.is_symlink answers False
    for a junction. A symlink stands in for one here, found by its target
    because retention and Delete rename it before removing it.
    """
    bm, _, _, _ = mock_env
    elsewhere = tmp_path / "backup-on-another-drive"
    elsewhere.mkdir()
    (elsewhere / "ankimon.db").write_bytes(b"a save the user moved and linked back")
    stamp = time.time() - 3600
    os.utime(elsewhere, (stamp, stamp))
    made = _fake_backups(bm, bm.MAX_BACKUPS)
    doomed = bm.backups_path / ".discard_0badf00d_backup_2019-01-01_00-00-01"
    if route == "entry":
        doomed.mkdir()
        link = doomed / "linked"
    else:
        link = bm.backups_path / "backup_2019-01-01_00-00-00"
    link.symlink_to(elsewhere, target_is_directory=True)

    def is_junction(path):
        try:
            return Path(os.readlink(path)) == elsewhere
        except OSError:
            return False

    real_lstat, real_is_symlink, real_iterdir = os.lstat, Path.is_symlink, Path.iterdir
    listed = []

    def windows_lstat(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        if not is_junction(path):
            return result
        return types.SimpleNamespace(st_mode=result.st_mode, st_reparse_tag=0xA0000003)

    def recording_iterdir(self):
        listed.append((self, is_junction(self)))
        return real_iterdir(self)

    monkeypatch.delattr(os.path, "isjunction", raising=False)
    monkeypatch.setattr(os, "lstat", windows_lstat)
    monkeypatch.setattr(Path, "is_symlink", lambda self: real_is_symlink(self) and not is_junction(self))
    monkeypatch.setattr(Path, "iterdir", recording_iterdir)

    if route == "retention":
        bm.cleanup_backups()
    elif route == "delete":
        bm.delete_backup(str(link))
    else:
        assert bm._remove_tree(doomed, None)

    assert (elsewhere / "ankimon.db").is_file()
    assert [path for path, junction in listed if junction] == []
    assert not os.path.lexists(link)
    assert all(path.is_dir() for path in made)
    assert not list(bm.backups_path.glob(".discard_*"))


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


def test_a_failed_backup_still_sweeps_leftovers_but_keeps_every_backup(mock_env, monkeypatch):
    """A run of failed backups on a locked folder must not pile up save copies.

    Each failure whose own removal is locked out too leaves a staging directory
    holding a save, and the sweep that removes those ran only after a success.
    Retention still waits for one: evicting old backups while none replace them
    would leave the user with nothing.
    """
    bm, _, _, _ = mock_env
    stale = bm.backups_path / ".backup_2020-01-01_00-00-00"
    stale.mkdir()
    (stale / "ankimon.db").write_bytes(b"a whole save copy")
    os.utime(stale, (1_600_000_000, 1_600_000_000))
    discarded = bm.backups_path / ".discard_0badf00d_backup_2020-01-01_00-00-01"
    discarded.mkdir()
    (discarded / "ankimon.db").write_bytes(b"what a locked removal left")
    made = _fake_backups(bm, bm.MAX_BACKUPS + 2)
    expired = time.time() - (bm.MAX_BACKUP_AGE_DAYS + 7) * 24 * 3600
    for directory in made[:2]:
        os.utime(directory, (expired, expired))

    def locked_partial_copy(source_path, destination_path, timeout=30.0):
        destination_path.write_bytes(b"part of a save copy")
        raise OSError("simulated lock on the save")

    unlink = Path.unlink

    def locked(path, *args, **kwargs):
        # Only this attempt's own copy: the sweep renames what it removes first.
        if path.parent.name.startswith(".backup_"):
            raise PermissionError("simulated antivirus lock on the partial copy")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    with patch.object(bm, "_snapshot_database", side_effect=locked_partial_copy):
        assert bm.create_backup() is False

    assert not stale.exists()
    assert not list(bm.backups_path.glob(".discard_*"))
    (attempt,) = bm.backups_path.glob(".backup_*")
    assert (attempt / "ankimon.db").is_file(), "the sweep took the attempt still in flight"
    assert sorted(bm.backups_path.glob("backup_*")) == made


def test_a_restore_close_failure_notice_that_cannot_be_shown_does_not_escape(mock_env):
    """The restore is staged before Anki is asked to close.

    Its warning goes through the presenter port like the other notices about an
    armed restore, and a presenter that fails is logged rather than raised.
    """
    bm, db, _, _ = mock_env
    from Ankimon import save_import

    backup_dir = bm.backups_path / "backup_2026-06-04_12-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    _seed_db(backup_dir / "ankimon.db", "Blue", 999)
    shown = []

    def broken(message):
        shown.append(message)
        raise RuntimeError("wrapped C/C++ object has been deleted")

    try:
        with patch.object(services, "ui", types.SimpleNamespace(warn=broken)), \
             patch.object(_bm_mod, "close_anki", side_effect=RuntimeError("no main window")):
            bm.restore_backup(str(backup_dir))
        assert [message.split(":")[0] for message in shown] == ["Anki could not close"]
        assert save_import.pending_import_info(db.db_path) is not None
    finally:
        save_import.cancel_pending_import(db.db_path)


def test_a_restore_that_fails_before_staging_is_reported_not_raised(mock_env):
    """Its handlers name exceptions that were imported inside the same try.

    Anything raised before that import -- here, a backup folder that cannot be
    read -- made the first handler raise UnboundLocalError instead, and the
    restore escaped with nothing said.
    """
    bm, db, _, _ = mock_env
    from Ankimon.save_import import pending_import_info

    backup_dir = bm.backups_path / "backup_2026-06-05_12-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    is_file = Path.is_file

    def unreadable(self, *args, **kwargs):
        if self.parent == backup_dir:
            raise PermissionError("simulated unreadable backup folder")
        return is_file(self, *args, **kwargs)

    with patch.object(Path, "is_file", unreadable), \
         patch.object(_bm_mod, "showWarning") as warning:
        bm.restore_backup(str(backup_dir))
    assert warning.call_count == 1
    assert warning.call_args.args[0].startswith("Failed to prepare backup restore")
    assert pending_import_info(db.db_path) is None


@pytest.mark.parametrize("current, answer", [("healthy", None), ("damaged", False), ("damaged", True)])
def test_restore_over_a_damaged_save_asks_before_anything_is_staged(mock_env, current, answer):
    """The first question promises a verified copy of the save being replaced. A
    damaged save cannot give one, so replacing it takes an answer of its own."""
    bm, db, _, _ = mock_env
    from Ankimon.save_import import cancel_pending_import, pending_import_info
    from test_save_import import damage_a_table

    if current == "damaged":
        damage_a_table(db.db_path)
    backup_dir = bm.backups_path / "backup_2026-06-06_12-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    _seed_db(backup_dir / "ankimon.db", "Blue", 999)
    prompts = []

    def ask(prompt, **kwargs):
        prompts.append(prompt)
        return answer if "integrity check" in prompt else True

    try:
        with patch.object(_bm_mod, "askUser", side_effect=ask), \
             patch.object(_bm_mod, "showInfo") as notice:
            bm.restore_backup(str(backup_dir))
        pending = pending_import_info(db.db_path)
        if current == "healthy":
            assert len(prompts) == 1
            assert "retain_unverified" not in pending
            return
        assert len(prompts) == 2 and "the selected backup" in prompts[1]
        if not answer:
            assert pending is None
            assert not notice.called
            return
        assert pending["retain_unverified"] is True
        assert str(pending["unverified_path"]) in notice.call_args.args[0]
    finally:
        cancel_pending_import(db.db_path)


def test_restore_over_a_damaged_save_installs_after_restart_and_keeps_original(mock_env):
    """Exercise Backup Manager's accepted recovery path through a fresh process.

    Removing ``retain_unverified`` from the restore call, replacing the live
    database before restart, or dropping the raw recovery copy breaks this test.
    """
    bm, db, _, _ = mock_env
    from test_save_import import (
        commit_in_new_process,
        damage_a_table,
        quick_check_passes,
    )

    from Ankimon.save_import import pending_import_info

    target = Path(db.db_path)
    damage_a_table(target)
    original_bytes = target.read_bytes()
    backup_dir = bm.backups_path / "backup_2026-06-07_12-00-00"
    backup_dir.mkdir(parents=True, exist_ok=True)
    _seed_db(backup_dir / "ankimon.db", "Blue", 999)

    with patch.object(_bm_mod, "askUser", return_value=True), \
         patch.object(_bm_mod, "showInfo"):
        bm.restore_backup(str(backup_dir))

    staged = pending_import_info(target)
    assert staged is not None and staged["retain_unverified"] is True
    assert target.read_bytes() == original_bytes
    assert quick_check_passes(target) is False
    with closing(sqlite3.connect(target)) as still_active:
        assert still_active.execute(
            "SELECT value FROM config WHERE key='trainer.name'"
        ).fetchone() == ("Red",)
    assert not staged["recovery_path"].exists()
    assert not staged["unverified_path"].exists()

    # The startup installer must run without the current process's live handle.
    db.close()
    result = commit_in_new_process(target)
    assert json.loads(result.stdout) == {"installed": True}

    with closing(sqlite3.connect(target)) as restored:
        assert restored.execute(
            "SELECT value FROM config WHERE key='trainer.name'"
        ).fetchone() == ("Blue",)

    kept = staged["unverified_path"]
    assert kept.is_file()
    assert quick_check_passes(kept) is False
    with closing(sqlite3.connect(kept)) as original:
        assert original.execute(
            "SELECT value FROM config WHERE key='trainer.name'"
        ).fetchone() == ("Red",)
    assert pending_import_info(target) is None


def test_a_backup_publishes_through_locks_that_clear(mock_env, monkeypatch):
    """On Windows a scanner reading the snapshot just written blocks, for a moment,
    both its rename into the backup folder and the folder's rename into place."""
    bm, _, _, _ = mock_env
    importer = importlib.import_module("Ankimon.save_import")
    monkeypatch.setattr(importer, "_is_file_lock_error", lambda error: isinstance(error, PermissionError))
    monkeypatch.setattr(importer, "_FILE_LOCK_RETRY_DELAYS", (0, 0, 0, 0, 0))
    replace, rename = os.replace, Path.rename
    refused = []

    def snapshot_held_once(source, destination):
        if Path(destination).name == "ankimon.db" and "snapshot" not in refused:
            refused.append("snapshot")
            raise PermissionError(13, "simulated scanner holding the snapshot")
        return replace(source, destination)

    def folder_held_once(self, destination):
        if Path(destination).name.startswith("backup_") and "folder" not in refused:
            refused.append("folder")
            raise PermissionError(13, "simulated scanner inside the folder")
        return rename(self, destination)

    monkeypatch.setattr(os, "replace", snapshot_held_once)
    monkeypatch.setattr(Path, "rename", folder_held_once)
    assert bm.create_backup(required_file="ankimon.db", deadline=time.monotonic() + 30) is True
    assert refused == ["snapshot", "folder"]
    assert len(list(bm.backups_path.glob("backup_*"))) == 1


def test_backups_path_is_none_when_no_profile_resolves(mock_env, monkeypatch):
    """A profileFolder() failure must not leave a stale path anchored.

    If it did, a later create_backup could publish into the previous profile's
    folder, where the current profile's get_backups cannot see it.
    """
    bm, _, _, _ = mock_env
    # The fixture already anchored a real path; prove refresh clears it.
    assert bm.backups_path is not None
    monkeypatch.setattr("aqt.mw.pm.profileFolder",
                        MagicMock(side_effect=RuntimeError("profile volume unavailable")))
    bm.refresh_profile_path()
    assert bm.backups_path is None


def test_no_profile_means_no_backup_operations(mock_env, monkeypatch):
    """Every entry point that touches backups_path must refuse when it is None."""
    bm, _, user_files_dir, _ = mock_env
    monkeypatch.setattr("aqt.mw.pm.profileFolder",
                        MagicMock(side_effect=RuntimeError("profile volume unavailable")))
    bm.refresh_profile_path()
    assert bm.backups_path is None

    # get_backups: nothing to list, and no crash on None.iterdir().
    assert bm.get_backups() == []

    before = {p.name for p in user_files_dir.iterdir()}
    assert bm.create_backup(required_file="ankimon.db") is False
    after = {p.name for p in user_files_dir.iterdir()}
    assert before == after
    with patch.object(_bm_mod, "showWarning") as warning:
        assert bm.create_backup(manual=True, required_file="ankimon.db") is False
    assert warning.call_count == 1
    assert "no active Anki profile folder" in warning.call_args.args[0]
    assert {p.name for p in user_files_dir.iterdir()} == before

    # cleanup_backups and its helpers: no-op rather than AttributeError.
    bm.cleanup_backups()
    assert bm._discarded() == []
    bm._sweep_leftovers()


def test_migration_collision_keeps_both_backups_listable(mock_env):
    """A same-named backup directory in both locations must stay listable.

    Renaming files inside the destination backup (the previous approach) hides
    the legacy snapshot from get_backups(), which looks for ``ankimon.db`` by
    name. Renaming the colliding backup directory as a whole keeps both
    ``backup_*`` folders visible, and each keeps an ``ankimon.db``.
    """
    bm, _, _, addon_dir = mock_env
    legacy_root = addon_dir.parent / "ankimon_backups"
    legacy_root.mkdir()
    legacy_name = "backup_2026-09-25_10-00-00"
    legacy_backup = legacy_root / legacy_name
    legacy_backup.mkdir()
    _seed_db(legacy_backup / "ankimon.db", "Legacy", 111)
    (legacy_backup / "summary.json").write_text(json.dumps({
        "date": "2026-09-25 10-00-00",
        "normal_stats": {"trainer_name": "Legacy", "trainer_cash": 111},
    }), encoding="utf-8")

    # The destination already holds a same-named backup with a different save.
    existing = bm.backups_path / legacy_name
    existing.mkdir()
    _seed_db(existing / "ankimon.db", "Existing", 999)

    bm._migrate_legacy_backups(bm.backups_path.parent)

    # The legacy snapshot still exists as a listable ``backup_*`` directory,
    # under a distinct name, with its inner ``ankimon.db`` intact.
    legacy_copies = [
        p for p in bm.backups_path.iterdir()
        if p.name.startswith(legacy_name) and (p / "ankimon.db").is_file()
    ]
    assert {p.name for p in legacy_copies} == {
        legacy_name, f"{legacy_name}__legacy_{legacy_copies[1].name.rsplit('_', 1)[-1]}",
    } or len(legacy_copies) == 2, "both backups must survive the collision"
    assert legacy_root.exists() is False or not list(legacy_root.iterdir())

    # get_backups() must see both the existing backup and the migrated legacy
    # snapshot, since each still contains an ``ankimon.db`` for the active mode.
    names = {Path(bk["path"]).name for bk in bm.get_backups()}
    assert existing.name in names
    migrated = next(p for p in legacy_copies if p.name != existing.name)
    assert migrated.name in names
    # The migrated snapshot kept its original ``ankimon.db`` filename, which is
    # what the listing and restore paths expect.
    assert (migrated / "ankimon.db").is_file()
    assert not list(bm.backups_path.glob("ankimon__legacy_*.db"))


def test_migration_collision_seeds_legacy_name_inside_backup_namespace(mock_env):
    """The renamed legacy directory must still match the ``backup_`` listing filter."""
    bm, _, _, addon_dir = mock_env
    legacy_root = addon_dir.parent / "ankimon_backups"
    legacy_root.mkdir()
    name = "backup_2026-09-25_11-00-00"
    (legacy_root / name).mkdir()
    _seed_db(legacy_root / name / "ankimon.db", "Legacy", 1)
    (bm.backups_path / name).mkdir()
    _seed_db(bm.backups_path / name / "ankimon.db", "Existing", 2)

    bm._migrate_legacy_backups(bm.backups_path.parent)

    assert len(list(bm.backups_path.glob("backup_*"))) == 2
    assert bm._legacy_collision_name(bm.backups_path / name).name.startswith("backup_")


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_migration_does_not_follow_a_linked_legacy_root(mock_env, tmp_path):
    """A linked legacy_path is not a migration source: walking it moves external files."""
    bm, _, _, addon_dir = mock_env
    external = tmp_path / "external-backups"
    external.mkdir()
    (external / "backup_2026-09-25_12-00-00").mkdir()
    (external / "backup_2026-09-25_12-00-00" / "ankimon.db").write_bytes(b"external")
    legacy_root = addon_dir.parent / "ankimon_backups"
    legacy_root.symlink_to(external, target_is_directory=True)

    bm._migrate_legacy_backups(bm.backups_path.parent)

    # The external folder is untouched, and nothing was copied into the profile.
    assert (external / "backup_2026-09-25_12-00-00" / "ankimon.db").read_bytes() == b"external"
    assert list(bm.backups_path.iterdir()) == []
    # The link itself remains, so the user can decide what to do with it.
    assert legacy_root.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_migration_moves_a_linked_entry_without_following_it(mock_env, tmp_path):
    """A link inside the legacy root moves as a link; its target is not walked."""
    bm, _, _, addon_dir = mock_env
    external = tmp_path / "external-backups"
    external.mkdir()
    (external / "ankimon.db").write_bytes(b"external")
    legacy_root = addon_dir.parent / "ankimon_backups"
    legacy_root.mkdir()
    (legacy_root / "linked-entry").symlink_to(external, target_is_directory=True)
    (legacy_root / "backup_2026-09-25_13-00-00").mkdir()
    (legacy_root / "backup_2026-09-25_13-00-00" / "ankimon.db").write_bytes(b"legacy")

    bm._migrate_legacy_backups(bm.backups_path.parent)

    # The external folder is untouched: the link was moved, not walked.
    assert (external / "ankimon.db").read_bytes() == b"external"
    assert (bm.backups_path / "linked-entry").is_symlink()
    assert (bm.backups_path / "backup_2026-09-25_13-00-00" / "ankimon.db").read_bytes() == b"legacy"
    assert not legacy_root.exists()


def _legacy_migration_save(mock_env):
    bm, _, _, addon_dir = mock_env
    root = addon_dir.parent / "ankimon_backups"
    backup = root / "backup_2026-09-25_12-00-00"
    backup.mkdir(parents=True)
    _seed_db(backup / "ankimon.db", "Legacy", 111)
    return bm, root, backup


@pytest.mark.parametrize("partial_delete", [False, True])
def test_migration_retry_preserves_five_distinct_snapshots(mock_env, monkeypatch, partial_delete):
    import shutil

    bm, _, _, addon = mock_env
    root = addon.parent / "ankimon_backups"
    root.mkdir()
    originals = set()
    for i in range(5):
        folder = root / f"backup_{i}"
        folder.mkdir()
        _seed_db(folder / "ankimon.db", f"Save{i}", i)
        originals.add((folder / "ankimon.db").read_bytes())

    def interrupted_cleanup(path, *args, **kwargs):
        if partial_delete:
            (Path(path) / "ankimon.db").unlink(missing_ok=True)
        raise PermissionError("antivirus lock")

    with monkeypatch.context() as patcher:
        patcher.setattr(shutil, "rmtree", interrupted_cleanup)
        bm.run_profile_backup_tasks()
    assert len(bm.get_backups()) == 5
    bm.MAX_BACKUPS = 1
    bm.cleanup_backups()
    assert len(bm.get_backups()) == 5  # Pending publications are protected.
    # A fresh manager must resume the persistent record, not just in-memory state.
    retry = BackupManager(bm.logger, bm.settings_obj)
    retry.run_profile_backup_tasks()
    retry.cleanup_backups()
    assert not root.exists()
    assert len(retry.get_backups()) == 5
    assert {Path(b["path"]).joinpath("ankimon.db").read_bytes()
            for b in retry.get_backups()} == originals


@pytest.mark.parametrize("fail_at", [1, 2])
def test_failed_migration_keeps_all_originals_discoverable(mock_env, monkeypatch, fail_at):
    import shutil

    bm, _, _, addon = mock_env
    root = addon.parent / "ankimon_backups"
    root.mkdir()
    for i in range(3):
        folder = root / f"backup_{i}"
        folder.mkdir()
        _seed_db(folder / "ankimon.db", f"Save{i}", i)
    real_copy = shutil.copyfile
    calls = 0

    def disk_full(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError(28, "No space left on device")
        return real_copy(*args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(shutil, "copyfile", disk_full)
        bm.run_profile_backup_tasks()
    assert len(bm.get_backups()) == 3
    assert all(Path(b["path"]).joinpath("ankimon.db").is_file() for b in bm.get_backups())
    bm.run_profile_backup_tasks()
    assert len(bm.get_backups()) == 3
    assert not root.exists()


@pytest.mark.parametrize("legacy_taskman", [False, True])
def test_profile_refresh_schedules_without_copying_on_gui_thread(mock_env, monkeypatch, legacy_taskman):
    from concurrent.futures import ThreadPoolExecutor

    bm, root, _ = _legacy_migration_save(mock_env)
    tasks = []
    if legacy_taskman:
        # Anki 2.1.66 has no uses_collection parameter and uses a shared pool.
        def run_in_background(task, on_done=None, args=None):
            tasks.append((task, {}))
    else:
        def run_in_background(task, on_done=None, args=None, uses_collection=True):
            tasks.append((task, {"uses_collection": uses_collection}))
    monkeypatch.setattr("aqt.mw.taskman.run_in_background", run_in_background)
    bm.refresh_profile_path()
    bm.schedule_profile_backup_tasks()
    assert root.exists()
    assert not list(bm.backups_path.glob("backup_*"))
    seen_threads = []
    original = bm._migrate_legacy_backups

    def migrate(profile):
        seen_threads.append(threading.current_thread())
        original(profile)

    monkeypatch.setattr(bm, "_migrate_legacy_backups", migrate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert len(tasks) == 1
        assert tasks[0][1] == ({} if legacy_taskman else {"uses_collection": False})
        pool.submit(tasks[0][0]).result()
    assert not root.exists()
    assert all(thread is not threading.main_thread() for thread in seen_threads)


@pytest.mark.parametrize("message", ["task scheduling failed", "invalid uses_collection value"])
def test_profile_backup_scheduling_does_not_retry_unrelated_type_errors(mock_env, monkeypatch, message):
    bm, _, _, _ = mock_env
    error = TypeError(message)
    schedule = MagicMock(side_effect=error)
    monkeypatch.setattr("aqt.mw.taskman.run_in_background", schedule)

    with pytest.raises(TypeError) as raised:
        bm.schedule_profile_backup_tasks()

    assert raised.value is error
    schedule.assert_called_once()


def test_developer_startup_backup_waits_for_profile_and_runs_once(mock_env, monkeypatch):
    bm, _, _, _ = mock_env
    profile = bm.backups_path.parent
    bm.backups_path = None
    bm.settings_obj.get.side_effect = lambda key, default=None: key == "misc.developer_mode"
    backup = MagicMock(return_value=True)
    monkeypatch.setattr(bm, "create_backup", backup)
    bm.run_profile_backup_tasks()
    backup.assert_not_called()
    monkeypatch.setattr("aqt.mw.pm.profileFolder", lambda: str(profile))
    bm.refresh_profile_path()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(bm.run_profile_backup_tasks)
        second = pool.submit(bm.run_profile_backup_tasks)
        first.result()
        second.result()
    backup.assert_called_once_with(manual=False)


def test_hot_reload_never_arms_deferred_startup_backup(mock_env, monkeypatch):
    bm, _, _, _ = mock_env
    bm.settings_obj.get.side_effect = lambda key, default=None: key == "misc.developer_mode"
    monkeypatch.setattr(services, "_is_reloading", True, raising=False)
    reloaded = BackupManager(bm.logger, bm.settings_obj)
    # Startup clears this flag before a later profile-open event.
    monkeypatch.setattr(services, "_is_reloading", False)
    backup = MagicMock(return_value=True)
    monkeypatch.setattr(reloaded, "create_backup", backup)
    reloaded.run_profile_backup_tasks()
    reloaded.run_profile_backup_tasks()
    backup.assert_not_called()


@pytest.mark.parametrize("interrupted", [False, True])
def test_migration_publication_failure_preserves_save(mock_env, monkeypatch, interrupted):
    bm, root, backup = _legacy_migration_save(mock_env)
    original = (backup / "ankimon.db").read_bytes()
    old = time.time() - 7200
    os.utime(backup, (old, old))

    def refuse_publication(source, destination):
        if interrupted:
            raise KeyboardInterrupt("interrupted before publication")
        raise PermissionError("publication temporarily locked")

    with monkeypatch.context() as patcher:
        patcher.setattr(os, "replace", refuse_publication)
        if interrupted:
            with pytest.raises(KeyboardInterrupt):
                bm.refresh_profile_path()
                bm.run_profile_backup_tasks()
        else:
            bm.refresh_profile_path()
            bm.run_profile_backup_tasks()
        assert (backup / "ankimon.db").read_bytes() == original
        # Simulate cleanup before a retry; abandoned staging is never the only copy.
        bm.backups_path = root.parent / "anki-profile" / "Ankimon_Backups"
        bm._sweep_leftovers()
        assert (backup / "ankimon.db").read_bytes() == original

    bm.refresh_profile_path()
    bm.run_profile_backup_tasks()
    assert not root.exists()
    assert len(bm.get_backups()) == 1
    assert (bm.backups_path / backup.name / "ankimon.db").read_bytes() == original


def test_migration_partial_copy_stays_hidden(mock_env, monkeypatch):
    import shutil

    bm, _, backup = _legacy_migration_save(mock_env)
    original = (backup / "ankimon.db").read_bytes()

    def disk_full(source, destination, **kwargs):
        Path(destination).write_bytes(b"partial database")
        raise OSError("disk full during copy")

    monkeypatch.setattr(shutil, "copyfile", disk_full)
    bm.refresh_profile_path()
    bm.run_profile_backup_tasks()
    assert (backup / "ankimon.db").read_bytes() == original
    assert len(bm.get_backups()) == 1  # Original remains available for restore.
    assert not list(bm.backups_path.glob("backup_*"))


def test_migration_failed_source_cleanup_keeps_published_save(mock_env, monkeypatch):
    import shutil

    bm, _, backup = _legacy_migration_save(mock_env)
    original = (backup / "ankimon.db").read_bytes()
    real_rmtree = shutil.rmtree

    def failed_cleanup(path, *args, **kwargs):
        if Path(path) == backup:
            (backup / "ankimon.db").unlink()
            raise PermissionError("source cleanup interrupted")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", failed_cleanup)
    bm.refresh_profile_path()
    bm.run_profile_backup_tasks()
    assert len(bm.get_backups()) == 1
    assert (bm.backups_path / backup.name / "ankimon.db").read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_migration_preserves_nested_relative_db_link(mock_env):
    bm, root, backup = _legacy_migration_save(mock_env)
    archive = root.parent / "archive"
    archive.mkdir()
    referent = archive / "original.db"
    (backup / "ankimon.db").rename(referent)
    original = referent.read_bytes()
    (backup / "ankimon.db").symlink_to("../../archive/original.db")

    bm.refresh_profile_path()
    bm.run_profile_backup_tasks()

    migrated = bm.backups_path / backup.name / "ankimon.db"
    assert migrated.is_symlink()
    assert migrated.resolve() == referent
    assert migrated.read_bytes() == original
    assert len(bm.get_backups()) == 1
    assert not root.exists()


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
@pytest.mark.parametrize("link_first", [False, True])
def test_migration_maps_links_to_collision_renamed_sibling(mock_env, monkeypatch, link_first):
    bm, root, backup = _legacy_migration_save(mock_env)
    original = (backup / "ankimon.db").read_bytes()
    alias = root / "backup_2026-09-25_13-00-00"
    alias.symlink_to(backup.name, target_is_directory=True)
    linked_backup = root / "backup_2026-09-25_14-00-00"
    linked_backup.mkdir()
    (linked_backup / "ankimon.db").symlink_to(f"../{backup.name}/ankimon.db")
    existing = bm.backups_path / backup.name
    existing.mkdir()
    _seed_db(existing / "ankimon.db", "Existing", 999)
    existing_bytes = (existing / "ankimon.db").read_bytes()
    real_iterdir = Path.iterdir

    def ordered_entries(path):
        if path == root:
            entries = [alias, linked_backup, backup] if link_first else [backup, linked_backup, alias]
            return iter(entries)
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", ordered_entries)
    bm.refresh_profile_path()
    bm.run_profile_backup_tasks()

    assert not root.exists()
    assert (existing / "ankimon.db").read_bytes() == existing_bytes
    migrated = next(bm.backups_path.glob(f"{backup.name}__legacy_*"))
    assert (bm.backups_path / alias.name).resolve() == migrated
    nested = bm.backups_path / linked_backup.name / "ankimon.db"
    assert nested.resolve() == migrated / "ankimon.db"
    assert nested.read_bytes() == original
    assert len(bm.get_backups()) == 4


def test_migration_retry_never_adopts_an_unrelated_destination(mock_env, monkeypatch):
    bm, root, original = _legacy_migration_save(mock_env)
    original_bytes = (original / "ankimon.db").read_bytes()
    with monkeypatch.context() as failing:
        failing.setattr("shutil.copyfile", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        bm.run_profile_backup_tasks()
    other = bm.backups_path / original.name
    other.mkdir()
    _seed_db(other / "ankimon.db", "Unrelated", 222)
    other_bytes = (other / "ankimon.db").read_bytes()

    bm.run_profile_backup_tasks()

    assert (original / "ankimon.db").read_bytes() == original_bytes
    assert (other / "ankimon.db").read_bytes() == other_bytes
    assert {Path(row["path"]) for row in bm.get_backups()} == {original, other}
    assert (bm.backups_path / bm.MIGRATION_RECORD).exists()


def test_startup_backup_cannot_take_a_reserved_migration_name(mock_env, monkeypatch):
    bm, _, _, addon = mock_env
    fixed = datetime.datetime.now()

    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(_bm_mod.datetime, "datetime", FrozenDatetime)
    root = addon.parent / "ankimon_backups"
    original = root / ("backup_" + fixed.strftime("%Y-%m-%d_%H-%M-%S"))
    original.mkdir(parents=True)
    _seed_db(original / "ankimon.db", "Legacy", 111)
    original_bytes = (original / "ankimon.db").read_bytes()
    bm.settings_obj.get.side_effect = lambda key, default=None: key == "misc.developer_mode"
    with monkeypatch.context() as failing:
        failing.setattr("shutil.copyfile", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        bm.run_profile_backup_tasks()
    assert not (bm.backups_path / original.name).exists()
    assert bm._startup_backup_pending is False  # A distinct startup snapshot succeeded.

    bm.run_profile_backup_tasks()

    assert not root.exists()
    assert (bm.backups_path / original.name / "ankimon.db").read_bytes() == original_bytes
    assert len(bm.get_backups()) == 2
    assert {row.get("trainer_name") for row in bm.get_backups()} == {None, "Red"}


@pytest.mark.parametrize("same_contents", [False, True])
def test_old_migration_journal_requires_proof_before_deleting_source(mock_env, same_contents):
    import shutil

    bm, root, original = _legacy_migration_save(mock_env)
    original_bytes = (original / "ankimon.db").read_bytes()
    target = bm.backups_path / original.name
    if same_contents:
        shutil.copytree(original, target)
    else:
        target.mkdir()
        _seed_db(target / "ankimon.db", "Unrelated", 222)
    (bm.backups_path / bm.MIGRATION_RECORD).write_text(json.dumps({
        "source": str(root.resolve()), "entries": {original.name: original.name},
    }))

    bm.run_profile_backup_tasks()

    if same_contents:
        assert not root.exists()
        assert (target / "ankimon.db").read_bytes() == original_bytes
    else:
        assert (original / "ankimon.db").read_bytes() == original_bytes
        assert len(bm.get_backups()) == 2


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_dangling_legacy_entry_migrates_without_blocking_backup_management(mock_env):
    bm, _, _, addon = mock_env
    root = addon.parent / "ankimon_backups"
    root.mkdir()
    (root / "obsolete-link").symlink_to("no-longer-exists")

    bm.run_profile_backup_tasks()

    assert not root.exists()
    assert (bm.backups_path / "obsolete-link").is_symlink()
    assert not (bm.backups_path / bm.MIGRATION_RECORD).exists()
    for i in range(7):
        folder = bm.backups_path / f"backup_new_{i}"
        folder.mkdir()
        _seed_db(folder / "ankimon.db", f"New{i}", i)
    bm.cleanup_backups()
    assert len(bm.get_backups()) == bm.MAX_BACKUPS
    victim = Path(bm.get_backups()[0]["path"])
    bm.delete_backup(str(victim))
    assert not victim.exists()


def test_blocked_migration_only_protects_its_own_recovery_copies(mock_env, monkeypatch):
    import shutil

    bm, root, original = _legacy_migration_save(mock_env)
    real_rmtree = shutil.rmtree

    def refuse_source_cleanup(path, *args, **kwargs):
        if Path(path) == original:
            raise PermissionError("legacy file remains locked")
        return real_rmtree(path, *args, **kwargs)

    with monkeypatch.context() as locked:
        locked.setattr(shutil, "rmtree", refuse_source_cleanup)
        bm.run_profile_backup_tasks()
    published = bm.backups_path / original.name
    for i in range(7):
        folder = bm.backups_path / f"backup_new_{i}"
        folder.mkdir()
        _seed_db(folder / "ankimon.db", f"New{i}", i)
    stale = bm.backups_path / ".backup_abandoned"
    stale.mkdir()
    os.utime(stale, (0, 0))

    bm.cleanup_backups()

    assert len(bm.get_backups()) == bm.MAX_BACKUPS + 1
    assert original.exists() and published.exists()
    assert not stale.exists()
    bm.delete_backup(str(published))
    assert published.exists()
    victim = next(Path(row["path"]) for row in bm.get_backups() if Path(row["path"]) != published)
    bm.delete_backup(str(victim))
    assert not victim.exists()
    bm.run_profile_backup_tasks()
    assert not root.exists()
    assert not (bm.backups_path / bm.MIGRATION_RECORD).exists()


@pytest.mark.parametrize("finish_at", ["before_restore", "first_confirmation", "second_confirmation", "after_busy_notice"])
def test_restore_selection_survives_background_migration(mock_env, monkeypatch, finish_at):
    from concurrent.futures import ThreadPoolExecutor
    from Ankimon.save_import import cancel_pending_import, pending_import_info
    import Ankimon.save_import as save_import

    bm, root, original = _legacy_migration_save(mock_env)
    db = mock_env[1]
    entered, release = threading.Event(), threading.Event()
    copy_entry = BackupManager._copy_migration_entry

    def paused_copy(*args, **kwargs):
        if Path(args[0]) == original:
            entered.set()
            assert release.wait(5)
        return copy_entry(*args, **kwargs)

    monkeypatch.setattr(BackupManager, "_copy_migration_entry", staticmethod(paused_copy))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bm.run_profile_backup_tasks)
        try:
            assert entered.wait(5)
            selected = bm.get_backups()[0]["path"]
            assert selected == str(original)

            def finish_migration(*args, **kwargs):
                release.set()
                future.result(5)
                return True

            if finish_at == "first_confirmation":
                monkeypatch.setattr(_bm_mod, "askUser", finish_migration)
            elif finish_at == "second_confirmation":
                monkeypatch.setattr(save_import, "should_confirm_unverified_copy", lambda target: True)
                confirmations = []

                def confirm(*args, **kwargs):
                    confirmations.append(args)
                    return finish_migration() if len(confirmations) == 2 else True

                monkeypatch.setattr(_bm_mod, "askUser", confirm)
            elif finish_at == "before_restore":
                finish_migration()
            bm.restore_backup(selected)
            if finish_at == "after_busy_notice":
                assert pending_import_info(db.db_path) is None
                assert "Backups are being moved" in _bm_mod.showWarning.call_args.args[0]
                _bm_mod.showWarning.reset_mock()
                finish_migration()
                bm.restore_backup(selected)
        finally:
            release.set()
            future.result(5)
    try:
        pending = pending_import_info(db.db_path)
        assert pending is not None
        with closing(sqlite3.connect(pending["pending_path"])) as staged:
            assert staged.execute("SELECT value FROM config WHERE key='trainer.name'").fetchone() == ("Legacy",)
        assert not root.exists()
        _bm_mod.showWarning.assert_not_called()
    finally:
        cancel_pending_import(db.db_path)
    # The same stale dialog selection also supports Delete after migration.
    bm.delete_backup(selected)
    assert not (bm.backups_path / original.name).exists()


def test_migration_flushes_writable_copy_before_restoring_readonly_mode(mock_env, monkeypatch):
    import stat
    import Ankimon.save_import as save_import

    bm, root, original = _legacy_migration_save(mock_env)
    source = original / "ankimon.db"
    source.chmod(stat.S_IRUSR)
    original_bytes = source.read_bytes()
    sync_file = save_import._fsync_file
    synced = []

    def writable_sync(path):
        # Model Windows' writable-handle requirement, even when tests run as
        # a Unix superuser who could open a read-only file for writing.
        assert path.stat().st_mode & stat.S_IWUSR
        synced.append(path)
        sync_file(path)

    monkeypatch.setattr(save_import, "_fsync_file", writable_sync)
    try:
        bm.run_profile_backup_tasks()
        target = bm.backups_path / original.name / "ankimon.db"
        assert synced
        assert target.read_bytes() == original_bytes
        assert not target.stat().st_mode & stat.S_IWUSR
        if os.name != "nt":
            assert not root.exists()
        # Windows may refuse removal of a read-only original. The complete
        # publication must still survive and the pending journal permits retry.
    finally:
        for base in (root, bm.backups_path):
            for path in base.rglob("ankimon.db"):
                path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.parametrize("hidden_name", [".backup_abandoned", ".discard_abandoned"])
@pytest.mark.parametrize("failed_backup", [False, True])
def test_leftover_sweep_preserves_journaled_migration_copies(
    mock_env, monkeypatch, hidden_name, failed_backup,
):
    import shutil

    bm, _, _, addon = mock_env
    root = addon.parent / "ankimon_backups"
    hidden = root / hidden_name
    hidden.mkdir(parents=True)
    (hidden / "ankimon.db").write_bytes(b"abandoned copy")
    os.utime(hidden, (0, 0))
    original = root / "backup_locked"
    original.mkdir()
    _seed_db(original / "ankimon.db", "Legacy", 111)
    real_iterdir = Path.iterdir

    def ordered_entries(path):
        if path == root:
            return iter(p for p in (hidden, original) if p.exists())
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", ordered_entries)
    real_rmtree = shutil.rmtree

    def refuse_last_source(path, *args, **kwargs):
        if Path(path) == original:
            raise PermissionError("source cleanup interrupted")
        return real_rmtree(path, *args, **kwargs)

    with monkeypatch.context() as locked:
        locked.setattr(shutil, "rmtree", refuse_last_source)
        bm.run_profile_backup_tasks()
    published = bm.backups_path / hidden_name
    assert not hidden.exists()
    assert published.is_dir()
    unrelated = bm.backups_path / (hidden_name + "_unrelated")
    unrelated.mkdir()
    os.utime(unrelated, (0, 0))

    if failed_backup:
        with patch.object(bm, "_snapshot_database", side_effect=OSError("disk full")):
            assert not bm.create_backup()
    else:
        bm.cleanup_backups()

    assert published.is_dir(), "the only remaining journaled copy was swept"
    assert not unrelated.exists(), "unrelated leftovers should still be swept"
    bm.run_profile_backup_tasks()
    assert not root.exists()
    assert not (bm.backups_path / bm.MIGRATION_RECORD).exists()
    bm.cleanup_backups()
    assert not published.exists(), "completed migrations must release hidden copies"


def test_damaged_migration_record_also_protects_hidden_copies(mock_env):
    bm, _, _, _ = mock_env
    copies = [bm.backups_path / name for name in (".backup_old", ".discard_old")]
    for copy in copies:
        copy.mkdir()
        os.utime(copy, (0, 0))
    journal = bm.backups_path / bm.MIGRATION_RECORD
    journal.write_text("invalid JSON", encoding="utf-8")

    bm._sweep_leftovers()
    assert all(copy.exists() for copy in copies)
    journal.unlink()
    bm._sweep_leftovers()
    assert not any(copy.exists() for copy in copies)


def test_direct_leftover_sweep_does_not_race_migration(mock_env):
    from concurrent.futures import ThreadPoolExecutor

    bm, _, _, _ = mock_env
    stale = bm.backups_path / ".backup_old"
    stale.mkdir()
    os.utime(stale, (0, 0))
    entered, release = threading.Event(), threading.Event()

    def migrating():
        with bm._profile_work_lock:
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(migrating)
        try:
            assert entered.wait(5)
            bm._sweep_leftovers()
            assert stale.exists()
        finally:
            release.set()
            future.result(5)
    bm._sweep_leftovers()
    assert not stale.exists()


@pytest.mark.parametrize("committed_wal", [False, True])
@pytest.mark.parametrize("linked", [False, pytest.param(
    True, marks=pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges"),
)])
def test_restore_preserves_migration_digest_and_committed_wal(
    mock_env, monkeypatch, committed_wal, linked,
):
    import shutil
    from Ankimon.save_import import cancel_pending_import, pending_import_info

    bm, root, original = _legacy_migration_save(mock_env)
    source = original / "ankimon.db"
    if linked:
        external = root.parent / "external.db"
        source.rename(external)
        source.symlink_to(external)
        source = external
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    expected_name = "Legacy"
    if committed_wal:
        writer.execute("UPDATE config SET value='In WAL' WHERE key='trainer.name'")
        writer.commit()
        expected_name = "In WAL"
        assert Path(str(source) + "-wal").stat().st_size > 0
    else:
        writer.close()
    real_rmtree = shutil.rmtree

    def refuse_source_cleanup(path, *args, **kwargs):
        if Path(path) == original:
            raise PermissionError("legacy source remains locked")
        return real_rmtree(path, *args, **kwargs)

    try:
        with monkeypatch.context() as locked:
            locked.setattr(shutil, "rmtree", refuse_source_cleanup)
            bm.run_profile_backup_tasks()
        published = bm.backups_path / original.name
        before = bm._migration_digest(published)
        bm.restore_backup(str(published))
        pending = pending_import_info(mock_env[1].db_path)
        assert pending is not None
        with closing(sqlite3.connect(pending["pending_path"])) as staged:
            assert staged.execute(
                "SELECT value FROM config WHERE key='trainer.name'"
            ).fetchone() == (expected_name,)
        assert bm._migration_digest(published) == before
        _bm_mod.showWarning.assert_not_called()
    finally:
        writer.close()
        cancel_pending_import(mock_env[1].db_path)

    bm.run_profile_backup_tasks()
    assert not root.exists()
    assert not (bm.backups_path / bm.MIGRATION_RECORD).exists()


@pytest.mark.parametrize("change_at", ["main", "wal"])
@pytest.mark.parametrize("linked", [False, pytest.param(
    True, marks=pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges"),
)])
def test_restore_retries_checkpointed_file_family(mock_env, monkeypatch, change_at, linked):
    import shutil
    from Ankimon.save_import import cancel_pending_import, pending_import_info

    bm, active, _, addon = mock_env
    backup = bm.backups_path / "backup_checkpoint"
    backup.mkdir()
    source = addon.parent / "external.db" if linked else backup / "ankimon.db"
    _seed_db(source, "Before", 111)
    if linked:
        (backup / "ankimon.db").symlink_to(source)
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("UPDATE config SET value='Committed before restore' WHERE key='trainer.name'")
    writer.commit()
    copyfile = shutil.copyfile
    changed = False
    copies = 0

    def checkpoint_during_copy(src, dst, *args, **kwargs):
        nonlocal changed, copies
        result = copyfile(src, dst, *args, **kwargs)
        if Path(src) == source:
            copies += 1
        trigger = source if change_at == "main" else Path(str(source) + "-wal")
        if Path(src) == trigger and not changed:
            changed = True
            if change_at == "main":
                assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
            else:
                # Closing checkpoints and removes the WAL AFTER it was copied.
                # A retry must discard that now-stale private WAL.
                writer.close()
                assert not Path(str(source) + "-wal").exists()
        return result

    monkeypatch.setattr(shutil, "copyfile", checkpoint_during_copy)
    try:
        bm.restore_backup(str(backup))
        assert changed and copies == 2
        pending = pending_import_info(active.db_path)
        assert pending is not None, _bm_mod.showWarning.call_args_list
        with closing(sqlite3.connect(pending["pending_path"])) as staged:
            assert staged.execute(
                "SELECT value FROM config WHERE key='trainer.name'"
            ).fetchone() == ("Committed before restore",)
        _bm_mod.showWarning.assert_not_called()
    finally:
        writer.close()
        cancel_pending_import(active.db_path)


def test_restore_refuses_continuously_changing_backup(mock_env, monkeypatch):
    import shutil
    from Ankimon.save_import import pending_import_info

    bm, active, _, _ = mock_env
    backup = bm.backups_path / "backup_busy"
    backup.mkdir()
    source = backup / "ankimon.db"
    _seed_db(source, "Before", 111)
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    copyfile = shutil.copyfile
    copies = 0

    def update_during_every_copy(src, dst, *args, **kwargs):
        nonlocal copies
        result = copyfile(src, dst, *args, **kwargs)
        if Path(src) == source:
            copies += 1
            writer.execute("UPDATE config SET value=? WHERE key='trainer.cash'", (str(copies),))
            writer.commit()
            writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return result

    monkeypatch.setattr(shutil, "copyfile", update_during_every_copy)
    try:
        bm.restore_backup(str(backup))
        assert copies == 3
        assert pending_import_info(active.db_path) is None
        assert "changed while being copied" in _bm_mod.showWarning.call_args.args[0]
        _bm_mod.close_anki.assert_not_called()
    finally:
        writer.close()


def test_restore_checks_bytes_when_timestamps_do_not_change(mock_env, monkeypatch):
    import shutil
    from Ankimon.save_import import cancel_pending_import, pending_import_info

    bm, active, _, _ = mock_env
    backup = bm.backups_path / "backup_cached_timestamps"
    backup.mkdir()
    source = backup / "ankimon.db"
    _seed_db(source, "Before", 111)
    original_stat = source.stat()
    real_stat, copyfile = Path.stat, shutil.copyfile
    copies = 0

    def cached_timestamp(path, *args, **kwargs):
        # Windows may retain cached timestamps while a writer's handle is open.
        return original_stat if path == source else real_stat(path, *args, **kwargs)

    def update_once(src, dst, *args, **kwargs):
        nonlocal copies
        result = copyfile(src, dst, *args, **kwargs)
        if Path(src) == source:
            copies += 1
            if copies == 1:
                with closing(sqlite3.connect(source)) as writer:
                    writer.execute("UPDATE config SET value='NewOne' WHERE key='trainer.name'")
                    writer.commit()
                assert real_stat(source).st_size == original_stat.st_size
        return result

    monkeypatch.setattr(Path, "stat", cached_timestamp)
    monkeypatch.setattr(shutil, "copyfile", update_once)
    try:
        bm.restore_backup(str(backup))
        assert copies == 2
        pending = pending_import_info(active.db_path)
        assert pending is not None
        with closing(sqlite3.connect(pending["pending_path"])) as staged:
            assert staged.execute(
                "SELECT value FROM config WHERE key='trainer.name'"
            ).fetchone() == ("NewOne",)
    finally:
        cancel_pending_import(active.db_path)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges")
@pytest.mark.parametrize("directory_link", [False, True])
def test_partial_link_publication_keeps_original_listable_and_restorable(
    mock_env, monkeypatch, directory_link,
):
    import shutil
    from Ankimon.save_import import cancel_pending_import, pending_import_info

    bm, active, _, addon = mock_env
    root = addon.parent / "ankimon_backups"
    alias, original = root / "backup_a", root / "backup_z"
    original.mkdir(parents=True)
    _seed_db(original / "ankimon.db", "Legacy", 123)
    if directory_link:
        alias.symlink_to(original.name, target_is_directory=True)
    else:
        alias.mkdir()
        (alias / "ankimon.db").symlink_to("../backup_z/ankimon.db")
    copyfile, iterdir = shutil.copyfile, Path.iterdir

    def ordered_entries(path):
        if path == root:
            return iter([alias, original])
        return iterdir(path)

    def fail_dependency(source, target, **kwargs):
        if Path(source) == original / "ankimon.db":
            raise OSError("disk full")
        return copyfile(source, target, **kwargs)

    with monkeypatch.context() as failed:
        failed.setattr(Path, "iterdir", ordered_entries)
        failed.setattr(shutil, "copyfile", fail_dependency)
        bm.run_profile_backup_tasks()
    assert (alias / "ankimon.db").is_file()
    assert not (bm.backups_path / alias.name / "ankimon.db").exists()
    assert str(alias) in {row["path"] for row in bm.get_backups()}
    try:
        bm.restore_backup(str(alias))
        pending = pending_import_info(active.db_path)
        assert pending is not None, _bm_mod.showWarning.call_args_list
        with closing(sqlite3.connect(pending["pending_path"])) as staged:
            assert staged.execute(
                "SELECT value FROM config WHERE key='trainer.name'"
            ).fetchone() == ("Legacy",)
    finally:
        cancel_pending_import(active.db_path)
    bm.run_profile_backup_tasks()
    assert not root.exists()
    assert bm._resolve_backup_path(alias, "ankimon.db") == bm.backups_path / alias.name
    assert {row["path"] for row in bm.get_backups()} == {
        str(bm.backups_path / name) for name in (alias.name, original.name)
    }
