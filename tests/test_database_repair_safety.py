"""Repair must preserve committed saves across writers and filesystem failures."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import types

import pytest

from conftest import isolated_modules


ROOT = Path(__file__).resolve().parents[1]


def load_database(root):
    """Load the actual manager without constructing the Anki host."""
    for name in ("Ankimon", "Ankimon.pyobj", "Ankimon.functions"):
        package = types.ModuleType(name)
        package.__path__ = [str(ROOT / "src" / name.replace(".", "/"))]
        sys.modules[name] = package
    resources = types.ModuleType("Ankimon.resources")
    resources.user_path = root
    for attr in (
        "csv_file_items_cost",
        "mypokemon_path",
        "mainpokemon_path",
        "items_path",
        "badges_path",
        "team_pokemon_path",
    ):
        setattr(resources, attr, root / attr)
    sys.modules[resources.__name__] = resources
    # No Pokemon normalization is needed by these persistence fixtures. Its
    # real populated-save path is exercised by the native diagnostics proof.
    pokedex = types.ModuleType("Ankimon.functions.pokedex_functions")
    pokedex.is_valid_base_stats = lambda stats: True
    pokedex.search_pokedex = lambda *args: None
    sys.modules[pokedex.__name__] = pokedex
    spec = importlib.util.spec_from_file_location(
        "Ankimon.pyobj.database_manager", ROOT / "src/Ankimon/pyobj/database_manager.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repair_env(tmp_path):
    with isolated_modules("Ankimon"):
        module = load_database(tmp_path)
        db = module.AnkimonDB(db_path=tmp_path / "ankimon.db")
        db.set_config_value("trainer.cash", 100)
        db.save_item(1, "potion", 7)
        yield module, db
        db.close()


def persisted_cash(path):
    assert path.is_file(), "Reading the result must not create a new database"
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        row = conn.execute(
            "SELECT value FROM config WHERE key='trainer.cash'"
        ).fetchone()
        return json.loads(row[0])


def test_repair_blocks_writer_from_snapshot_through_publication(
    repair_env, monkeypatch
):
    module, db = repair_env
    dumped = threading.Event()
    resume = threading.Event()
    write_started = threading.Event()
    write_finished = threading.Event()
    original_connect = sqlite3.connect

    class PausedDump(sqlite3.Connection):
        def iterdump(self):
            yield from super().iterdump()
            dumped.set()
            assert resume.wait(5), "Repair was never resumed"

    def connect(path, *args, **kwargs):
        if Path(path) == db.db_path:
            kwargs["factory"] = PausedDump
        return original_connect(path, *args, **kwargs)

    def write():
        write_started.set()
        db.set_config_value("trainer.cash", 9876)
        write_finished.set()

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    with ThreadPoolExecutor(2) as pool:
        repairing = pool.submit(db.repair_database)
        try:
            assert dumped.wait(5)
            writing = pool.submit(write)
            assert write_started.wait(5)
            assert not write_finished.wait(0.1), "A write escaped the repair barrier"
        finally:
            resume.set()
        repairing.result(timeout=5)
        writing.result(timeout=5)
    assert persisted_cash(db.db_path) == 9876
    assert persisted_cash(db.db_path.with_suffix(".db.corrupt_backup")) == 100
    assert db.get_item("potion")["quantity"] == 7


def test_repair_waits_for_committed_leased_writer_before_dump(repair_env, monkeypatch):
    module, db = repair_env
    writing = threading.Event()
    draining = threading.Event()
    finish_write = threading.Event()
    original_close = db._close_locked
    monkeypatch.setattr(module, "_is_main_thread", lambda: False)

    def close(wait_seconds):
        draining.set()
        return original_close(wait_seconds)

    def write():
        with db.lease_connection() as conn:
            conn.execute(
                "UPDATE config SET value='9876' WHERE key='trainer.cash'"
            ).close()
            writing.set()
            assert finish_write.wait(5)
            conn.commit()

    monkeypatch.setattr(db, "_close_locked", close)
    with ThreadPoolExecutor(2) as pool:
        writer = pool.submit(write)
        try:
            assert writing.wait(5)
            repair = pool.submit(db.repair_database)
            assert draining.wait(5)
        finally:
            finish_write.set()
        writer.result(timeout=5)
        repair.result(timeout=5)
    assert persisted_cash(db.db_path) == 9876
    assert persisted_cash(db.db_path.with_suffix(".db.corrupt_backup")) == 9876


def test_repair_closes_stale_handles_and_preserves_wal_commits(repair_env):
    _, db = repair_env
    db.close()
    db._wal = True
    stale = db._get_connection()
    db.set_config_value("trainer.cash", 4321)
    assert Path(str(db.db_path) + "-wal").is_file()
    db.repair_database()
    assert stale._closed
    assert persisted_cash(db.db_path) == 4321
    assert persisted_cash(db.db_path.with_suffix(".db.corrupt_backup")) == 4321
    with stale.execute("SELECT value FROM config WHERE key='trainer.cash'") as cursor:
        assert json.loads(cursor.fetchone()[0]) == 4321


def test_repair_with_an_outstanding_cursor_aborts_without_taking_a_snapshot(repair_env):
    _, db = repair_env
    original = db.db_path.read_bytes()
    with db._get_connection().cursor() as cursor:
        cursor.execute("SELECT 1")
        with pytest.raises(RuntimeError, match="active operations did not finish"):
            db.repair_database()
        assert cursor.fetchone()[0] == 1
    assert db.db_path.read_bytes() == original
    assert list(db.db_path.parent.glob("ankimon.db.*")) == []
    assert not db._is_repairing
    db.repair_database()
    assert persisted_cash(db.db_path) == 100


def test_repair_refuses_an_external_wal_snapshot(repair_env):
    _, db = repair_env
    db.close()
    db._wal = True
    db.set_config_value("trainer.cash", 111)
    with closing(sqlite3.connect(db.db_path)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM config").fetchall()
        db.set_config_value("trainer.cash", 9876)
        with pytest.raises(RuntimeError, match="checkpoint|journals remain"):
            db.repair_database()
        assert persisted_cash(db.db_path) == 9876
    db.repair_database()
    assert persisted_cash(db.db_path) == 9876


@pytest.mark.skipif(os.name != "nt", reason="requires a real Windows sharing violation")
@pytest.mark.parametrize("release_handle", [True, False])
def test_windows_scanner_handle_retries_or_preserves_repair(
    repair_env, monkeypatch, release_handle
):
    _, db = repair_env
    import Ankimon.save_import as filesystem

    # Python's Windows file handle does not grant FILE_SHARE_DELETE. The OS,
    # not an injected exception, must refuse replacement until it is closed.
    handle = db.db_path.open("rb")
    real_replace = os.replace
    failures = []

    def replace(source, target):
        try:
            return real_replace(source, target)
        except OSError as error:
            if Path(target) == db.db_path:
                failures.append(error)
                if release_handle:
                    handle.close()
            raise

    monkeypatch.setattr(os, "replace", replace)
    try:
        if release_handle:
            db.repair_database()
        else:
            with pytest.raises(OSError):
                db.repair_database()
            assert list(db.db_path.parent.glob("*.repair-*.tmp"))
        assert failures and filesystem._is_file_lock_error(failures[0])
        assert persisted_cash(db.db_path) == 100
        assert persisted_cash(db.db_path.with_suffix(".db.corrupt_backup")) == 100
    finally:
        handle.close()


@pytest.mark.parametrize(
    "failure",
    [
        "copy",
        "copy_mismatch",
        "repair_fsync",
        "backup_fsync",
        "backup_replace",
        "backup_directory_sync",
        "publish",
        "publish_directory_sync",
    ],
)
def test_repair_failure_preserves_active_and_previous_backup(
    repair_env, monkeypatch, failure
):
    module, db = repair_env
    import Ankimon.save_import as filesystem

    old_backup = db.db_path.with_suffix(".db.corrupt_backup")
    old_backup.write_bytes(b"previous recovery artifact")
    original_copy = module.shutil.copyfile
    original_sync = filesystem._fsync_file
    original_replace = Path.replace
    original_directory_sync = filesystem._fsync_directory
    directory_syncs = 0

    def copy(source, target):
        if failure in ("copy", "copy_mismatch"):
            Path(target).write_bytes(b"partial")
            if failure == "copy":
                raise OSError("injected backup copy failure")
            return target
        return original_copy(source, target)

    def sync(path):
        is_backup = ".repair-backup-" in path.name
        if failure == ("backup_fsync" if is_backup else "repair_fsync"):
            raise OSError("injected fsync failure")
        return original_sync(path)

    def replace(path, target):
        if (failure == "publish" and target == db.db_path) or (
            failure == "backup_replace" and ".corrupt_backup-" in target.name
        ):
            raise OSError("injected replacement failure")
        return original_replace(path, target)

    def sync_directory(path):
        nonlocal directory_syncs
        directory_syncs += 1
        if (failure == "backup_directory_sync" and directory_syncs == 1) or (
            failure == "publish_directory_sync" and directory_syncs == 2
        ):
            raise OSError("injected directory sync failure")
        return original_directory_sync(path)

    monkeypatch.setattr(module.shutil, "copyfile", copy)
    monkeypatch.setattr(filesystem, "_fsync_file", sync)
    monkeypatch.setattr(filesystem, "_fsync_directory", sync_directory)
    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises((OSError, RuntimeError), match="injected|verification failed"):
        db.repair_database()
    assert old_backup.read_bytes() == b"previous recovery artifact"
    assert persisted_cash(db.db_path) == 100
    assert not db._is_repairing
    # Retry remains possible, and does not erase any retained artifact.
    monkeypatch.undo()
    retained = {path: path.read_bytes() for path in db.db_path.parent.glob("*.tmp")}
    db.repair_database()
    assert persisted_cash(db.db_path) == 100
    assert old_backup.read_bytes() == b"previous recovery artifact"
    assert all(path.read_bytes() == content for path, content in retained.items())


@pytest.mark.parametrize(
    "artifact", [".tmp", ".corrupt_backup", ".corrupt_backup-old", ".repair-old.tmp"]
)
def test_missing_active_with_legacy_repair_artifacts_never_creates_blank_save(
    repair_env, artifact
):
    module, db = repair_env
    db.close()
    recovery = Path(str(db.db_path) + artifact)
    db.db_path.replace(recovery)
    original = recovery.read_bytes()
    with pytest.raises(RuntimeError, match="interrupted repair") as error:
        module.AnkimonDB(db_path=db.db_path)
    assert str(recovery) in str(error.value)
    assert not db.db_path.exists()
    assert recovery.read_bytes() == original


@pytest.mark.parametrize(
    "boundary", ["before_backup", "after_backup", "before_publish", "after_publish"]
)
def test_process_exit_at_repair_publication_keeps_a_populated_save(tmp_path, boundary):
    script = r"""
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("repair_tests", sys.argv[1])
tests = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(Path(sys.argv[1]).parent))
spec.loader.exec_module(tests)
root = Path(sys.argv[2])
module = tests.load_database(root)
db = module.AnkimonDB(db_path=root / "ankimon.db")
db.set_config_value("trainer.cash", 4321)
db.save_item(1, "potion", 7)
real_replace = Path.replace
boundary = sys.argv[3]
def crash_replace(path, target):
    stage = "publish" if target == db.db_path else "backup"
    if boundary == "before_" + stage:
        os._exit(77)
    result = real_replace(path, target)
    if boundary == "after_" + stage:
        os._exit(77)
    return result
Path.replace = crash_replace
db.repair_database()
raise AssertionError("Crash boundary was not reached")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, __file__, str(tmp_path), boundary],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 77, result.stdout + result.stderr
    active = tmp_path / "ankimon.db"
    assert persisted_cash(active) == 4321
    # A distinct process must reopen the same populated save, not just find a
    # valid SQLite file through the parent test's existing handles.
    reopen = (
        script[: script.index("db.set_config_value")]
        + """
assert tests.persisted_cash(db.db_path) == 4321
assert db.get_item("potion")["quantity"] == 7
db.close()
"""
    )
    restarted = subprocess.run(
        [sys.executable, "-c", reopen, __file__, str(tmp_path), boundary],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
