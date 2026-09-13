"""Exercise staged imports across real process and SQLite boundaries."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


MODULE = Path(__file__).resolve().parents[1] / "src/Ankimon/save_import.py"


def load_module():
    assert MODULE.is_file(), "The staged import helper has not been implemented"
    spec = importlib.util.spec_from_file_location("save_import_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_save(path, name):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE captured_pokemon (individual_id TEXT PRIMARY KEY, data TEXT);"
            "CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE user_data (key TEXT PRIMARY KEY, value TEXT);"
        )
        conn.execute("INSERT INTO captured_pokemon VALUES (?, '{}')", (name,))
        conn.executemany("INSERT INTO config VALUES (?, ?)", [
            ("leaderboard.username", "source-account"),
            ("leaderboard.api_key", "private-source-api-key"),
            ("trainer.name", name),
        ])
        conn.executemany("INSERT INTO user_data VALUES (?, ?)", [
            ("username", "legacy-account"), ("api_key", "private-legacy-key"),
        ])
    return path


def names(path):
    with sqlite3.connect(path) as conn:
        return [row[0] for row in conn.execute(
            "SELECT individual_id FROM captured_pokemon ORDER BY individual_id"
        )]


def child(body, *args, expected=0):
    setup = (
        "import importlib.util, json, os, sqlite3, sys\n"
        "from pathlib import Path\n"
        "spec = importlib.util.spec_from_file_location('save_import_child', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "target = Path(sys.argv[2])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", setup + body, str(MODULE), *(str(a) for a in args)],
        text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    return result


def commit_in_new_process(target, *, expected=0):
    """Run the real pending-import gate without constructing the Anki host.

    Integration tests can inspect returncode and the JSON ``installed`` value
    on stdout. Exceptions retain their normal nonzero process exit status.
    """
    return child(
        "print(json.dumps({'installed': module.commit_pending_import(target)}))\n",
        target, expected=expected,
    )


def test_staging_keeps_runtime_and_source_unchanged_and_requires_fresh_process(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    original = source.read_bytes()
    staged = importer.stage_import(source, target)

    assert names(target) == ["local"]
    assert source.read_bytes() == original
    assert staged["pending_path"].is_file()
    assert not staged["recovery_path"].exists()
    assert importer.commit_pending_import(target) is False
    # A module purge/reload is not a new process, even if get_db is reset.
    assert load_module().commit_pending_import(target) is False
    assert names(target) == ["local"]
    assert importer.pending_import_info(target)["token"] == staged["token"]


def test_staged_copy_requires_reauthentication_without_exporting_source_secrets(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)

    with sqlite3.connect(staged["pending_path"]) as conn:
        assert dict(conn.execute("SELECT key, value FROM config WHERE key LIKE 'leaderboard.%'")) == {"leaderboard.username": "", "leaderboard.api_key": ""}
        assert conn.execute("SELECT key FROM user_data").fetchall() == []
        assert conn.execute("SELECT value FROM config WHERE key='trainer.name'").fetchone()[0] == "incoming"
        assert conn.execute("SELECT value FROM metadata WHERE key='import_rebase_pending'").fetchone()[0] == "1"
    assert b"private-source-api-key" not in staged["pending_path"].read_bytes()
    assert b"private-legacy-key" not in staged["pending_path"].read_bytes()


def test_file_sync_uses_descriptor_that_can_flush_on_windows(tmp_path, monkeypatch):
    importer = load_module()
    path = tmp_path / "snapshot.db"
    path.write_bytes(b"snapshot contents")
    real_fsync = os.fsync

    def require_write_access(descriptor):
        # Windows FlushFileBuffers requires a handle with write access. A
        # zero-byte write exercises that requirement on the host platform too.
        os.write(descriptor, b"")
        real_fsync(descriptor)

    monkeypatch.setattr(importer.os, "fsync", require_write_access)
    importer._fsync_file(path)
    assert path.read_bytes() == b"snapshot contents"


def test_next_process_installs_and_recovers_commits_made_after_staging(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    # A killed writer leaves committed data in the WAL. No Anki runtime remains.
    child(
        "conn = sqlite3.connect(target)\n"
        "conn.execute('PRAGMA journal_mode=WAL')\n"
        "conn.execute('PRAGMA wal_autocheckpoint=0')\n"
        "conn.execute(\"INSERT INTO captured_pokemon VALUES ('late-commit', '{}')\")\n"
        "conn.commit()\n"
        "os._exit(0)\n", target,
    )
    assert Path(str(target) + "-wal").exists()
    result = commit_in_new_process(target)
    assert json.loads(result.stdout)["installed"] is True

    assert names(target) == ["incoming"]
    assert names(staged["recovery_path"]) == ["late-commit", "local"]
    assert importer.pending_import_info(target) is None
    assert not staged["pending_path"].exists()
    child("assert module.commit_pending_import(target) is False\n", target)

    # Recovery uses the same supported import path and restores every committed
    # row, including the one that was present only in the previous WAL.
    rollback = importer.stage_import(staged["recovery_path"], target)
    commit_in_new_process(target)
    assert names(target) == ["late-commit", "local"]
    assert names(rollback["recovery_path"]) == ["incoming"]


def test_cancellation_discards_only_pending_import(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    assert importer.cancel_pending_import(target) is True
    assert importer.cancel_pending_import(target) is False
    assert not staged["pending_path"].exists()
    child("assert module.commit_pending_import(target) is False\n", target)
    assert names(target) == ["local"]
    assert names(source) == ["incoming"]


def test_cancellation_recovers_from_a_damaged_pending_manifest(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    manifest = staged["pending_path"].parent / "pending.json"
    manifest.write_text("{ definitely-not-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        importer.pending_import_info(target)
    assert importer.cancel_pending_import(target) is True
    assert not manifest.exists()
    assert not staged["pending_path"].exists()
    assert names(target) == ["local"]


def test_cancellation_is_committed_even_if_staged_copy_cleanup_is_locked(
    tmp_path, monkeypatch
):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    manifest = staged["pending_path"].parent / "pending.json"

    def locked(_path):
        raise PermissionError("simulated antivirus lock")

    monkeypatch.setattr(importer, "_remove_owned_copy", locked)
    assert importer.cancel_pending_import(target) is True
    assert not manifest.exists()
    assert importer.pending_import_info(target) is None
    assert names(target) == ["local"]


def test_pending_import_applies_only_to_the_explicit_target(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimonDEV.db", "local")
    normal = make_save(tmp_path / "ankimon.db", "normal")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)
    child("assert module.commit_pending_import(target) is False\n", normal)
    assert names(normal) == ["normal"]
    assert names(target) == ["local"]
    child("assert module.commit_pending_import(target) is True\n", target)
    assert names(target) == ["incoming"]


def test_invalid_source_never_publishes_pending_work(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = tmp_path / "source.db"
    source.write_bytes(b"not a database")
    with pytest.raises(Exception):
        importer.stage_import(source, target)
    assert importer.pending_import_info(target) is None
    assert names(target) == ["local"]


def test_damaged_pending_snapshot_refuses_install_and_remains_recoverable(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    staged["pending_path"].write_bytes(b"truncated")
    child("module.commit_pending_import(target)\n", target, expected=1)
    assert names(target) == ["local"]
    assert importer.pending_import_info(target) is not None
    assert not staged["recovery_path"].exists()


def test_backup_failure_refuses_replacement_and_preserves_pending(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    # A real filesystem obstruction, not a mocked successful backup call.
    staged["recovery_path"].parent.parent.mkdir(parents=True)
    staged["recovery_path"].parent.write_text("obstruction")
    child("module.commit_pending_import(target)\n", target, expected=1)
    assert names(target) == ["local"]
    assert importer.pending_import_info(target) is not None


def test_failed_replace_keeps_backup_and_retry_backs_up_latest_progress(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    child(
        "real_replace = os.replace\n"
        "def fail_install(source, dest):\n"
        "    if Path(dest) == target:\n"
        "        raise PermissionError('simulated antivirus lock')\n"
        "    return real_replace(source, dest)\n"
        "module.os.replace = fail_install\n"
        "module.commit_pending_import(target)\n", target, expected=1,
    )
    assert names(target) == ["local"]
    assert names(staged["recovery_path"]) == ["local"]
    with sqlite3.connect(target) as conn:
        conn.execute("INSERT INTO captured_pokemon VALUES ('after-failure', '{}')")
    child("assert module.commit_pending_import(target) is True\n", target)
    assert names(target) == ["incoming"]
    backups = list(staged["recovery_path"].parent.glob("*.db"))
    assert sorted(names(path) for path in backups) == [["after-failure", "local"], ["local"]]
    # The advertised path is the only recovery filename the user is ever shown,
    # so it must hold the LATEST pre-install save, not the first attempt's.
    assert names(staged["recovery_path"]) == ["after-failure", "local"]
    superseded = [path for path in backups if path != staged["recovery_path"]]
    assert [names(path) for path in superseded] == [["local"]]


def test_failed_startup_install_cannot_retry_after_runtime_and_module_reload(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    staged["recovery_path"].parent.parent.mkdir(parents=True)
    staged["recovery_path"].parent.write_text("temporary obstruction")
    child(
        "try:\n"
        "    module.commit_pending_import(target)\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('initial startup installation should fail')\n"
        "# get_db would now construct the original runtime.\n"
        "with sqlite3.connect(target) as runtime:\n"
        "    runtime.execute(\"INSERT INTO captured_pokemon VALUES ('runtime-progress', '{}')\")\n"
        "module.pending_import_info(target)['recovery_path'].parent.unlink()\n"
        "# Reloading the helper must not reopen this process's install gate.\n"
        "spec = importlib.util.spec_from_file_location('reloaded_save_import', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "assert module.commit_pending_import(target) is False\n", target,
    )
    assert names(target) == ["local", "runtime-progress"]
    assert importer.pending_import_info(target) is not None
    # The next actual process can still install, backing up all interim play.
    commit_in_new_process(target)
    assert names(target) == ["incoming"]
    assert names(staged["recovery_path"]) == ["local", "runtime-progress"]


def test_crash_after_atomic_install_never_reapplies_import_over_new_progress(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    child(
        "real_replace = os.replace\n"
        "def crash_after_install(source, dest):\n"
        "    real_replace(source, dest)\n"
        "    if Path(dest) == target:\n"
        "        os._exit(37)\n"
        "module.os.replace = crash_after_install\n"
        "module.commit_pending_import(target)\n", target, expected=37,
    )
    assert names(target) == ["incoming"]
    with sqlite3.connect(target) as conn:
        conn.execute("INSERT INTO captured_pokemon VALUES ('new-progress', '{}')")
    child("module.commit_pending_import(target)\n", target)
    assert names(target) == ["incoming", "new-progress"]
    assert names(staged["recovery_path"]) == ["local"]
    assert importer.pending_import_info(target) is None


@pytest.mark.parametrize("already_installed,failure", [
    (False, "directory_sync"), (False, "temporary_cleanup"),
    (False, "manifest_cleanup"), (True, "directory_sync"), (True, "manifest_cleanup"),
])
def test_post_install_failure_reports_installed_and_retries_without_losing_progress(
    tmp_path, failure, already_installed
):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    if already_installed:
        child(
            "replace = os.replace\n"
            "def crash_after_install(source, dest):\n"
            "    replace(source, dest)\n"
            "    if Path(dest) == target:\n"
            "        os._exit(37)\n"
            "module.os.replace = crash_after_install\n"
            "module.commit_pending_import(target)\n", target, expected=37,
        )

    child(
        "token = module.pending_import_info(target)['token']\n"
        "failure = sys.argv[3]\n"
        "sync, cleanup, unlink = module._fsync_directory, module._remove_owned_copy, Path.unlink\n"
        "open_path = Path.open\n"
        "def fail_sync(path):\n"
        "    if path == target.parent and module._installed_token(target) == token:\n"
        "        raise OSError('injected directory sync failure')\n"
        "    return sync(path)\n"
        "def fail_cleanup(path):\n"
        "    if path.name.startswith('.ankimon-install-'):\n"
        "        raise OSError('injected temporary cleanup failure')\n"
        "    return cleanup(path)\n"
        "def fail_unlink(path, *args, **kwargs):\n"
        "    if path.name == 'pending.json':\n"
        "        raise OSError('injected manifest cleanup failure')\n"
        "    return unlink(path, *args, **kwargs)\n"
        # Retiring the record is committed by rewriting it, so a lock has to
        # hold the writer out, not only the unlink, to leave it unretired.
        "def fail_retire(path, mode='r', *args, **kwargs):\n"
        "    if path.name == 'pending.json' and mode != 'r':\n"
        "        raise OSError('injected manifest cleanup failure')\n"
        "    return open_path(path, mode, *args, **kwargs)\n"
        "if failure == 'directory_sync': module._fsync_directory = fail_sync\n"
        "elif failure == 'temporary_cleanup': module._remove_owned_copy = fail_cleanup\n"
        "else: Path.unlink, Path.open = fail_unlink, fail_retire\n"
        "try:\n"
        "    module.commit_pending_import(target)\n"
        "except Exception as error:\n"
        "    assert type(error).__name__ == 'ImportInstalledError', repr(error)\n"
        "    assert 'injected' in str(error)\n"
        "else:\n"
        "    raise AssertionError('Expected an installed-with-warning result')\n"
        "assert module._installed_token(target) == token\n"
        "assert module.commit_pending_import(target) is False\n", target, failure,
    )
    assert names(target) == ["incoming"]
    assert importer.pending_import_info(target)["token"] == staged["token"]
    with sqlite3.connect(target) as conn:
        conn.execute("INSERT INTO captured_pokemon VALUES ('new-progress', '{}')")
    commit_in_new_process(target)
    assert names(target) == ["incoming", "new-progress"]
    assert names(staged["recovery_path"]) == ["local"]
    assert importer.pending_import_info(target) is None


def test_staging_twice_requires_explicit_cancellation(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    first = importer.stage_import(source, target)
    with pytest.raises(RuntimeError, match="pending"):
        importer.stage_import(source, target)
    assert importer.pending_import_info(target)["token"] == first["token"]


def test_new_recovery_directories_are_private_even_with_permissive_umask(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    child("os.umask(0)\nmodule.commit_pending_import(target)\n", target)
    if os.name != "nt":
        assert stat.S_IMODE(staged["recovery_path"].parent.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(staged["recovery_path"].parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(staged["recovery_path"].stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_existing_recovery_directories_are_private_before_snapshot_access(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    recovery_directory = staged["recovery_path"].parent
    recovery_directory.mkdir(parents=True)
    recovery_directory.chmod(0o777)
    recovery_directory.parent.chmod(0o777)
    child(
        "import stat\n"
        "snapshot = module._snapshot\n"
        "def inspect_access(source, dest, deadline=None):\n"
        "    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700\n"
        "    assert stat.S_IMODE(dest.parent.parent.stat().st_mode) == 0o700\n"
        "    snapshot(source, dest)\n"
        "module._snapshot = inspect_access\n"
        "assert module.commit_pending_import(target) is True\n", target,
    )
    assert names(staged["recovery_path"]) == ["local"]


def rebase_state(path, *, marker="1"):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO metadata VALUES ('mobile_revlog_watermark', '9999')")
    if marker is not None:
        conn.execute("INSERT INTO metadata VALUES ('import_rebase_pending', ?)", (marker,))
    conn.commit()
    return conn, SimpleNamespace(_get_connection=lambda: conn)


@pytest.mark.parametrize("maximum,expected", [(200, "200"), (None, "0")])
def test_rebase_reads_post_shutdown_collection_and_can_lower_imported_watermark(tmp_path, maximum, expected):
    importer = load_module()
    assert hasattr(importer, "rebase_after_import"), "The import rebase has not been implemented"
    conn, manager = rebase_state(tmp_path / "save.db")
    collection = SimpleNamespace(db=SimpleNamespace(scalar=lambda sql: maximum))
    try:
        assert importer.rebase_after_import(manager, collection) is True
        assert conn.execute("SELECT value FROM metadata WHERE key='mobile_revlog_watermark'").fetchone()[0] == expected
        assert conn.execute("SELECT value FROM metadata WHERE key='import_rebase_pending'").fetchone() is None
        # Repeated sync hooks must preserve normal future watermark updates.
        conn.execute("UPDATE metadata SET value='20000' WHERE key='mobile_revlog_watermark'")
        conn.commit()
        assert importer.rebase_after_import(manager, None) is False
        assert conn.execute("SELECT value FROM metadata WHERE key='mobile_revlog_watermark'").fetchone()[0] == "20000"
    finally:
        conn.close()


@pytest.mark.parametrize("maximum", [-1, True, "123", 12.5])
def test_rebase_invalid_collection_watermark_leaves_marker_for_retry(tmp_path, maximum):
    importer = load_module()
    assert hasattr(importer, "rebase_after_import"), "The import rebase has not been implemented"
    conn, manager = rebase_state(tmp_path / "save.db")
    collection = SimpleNamespace(db=SimpleNamespace(scalar=lambda sql: maximum))
    try:
        with pytest.raises(ValueError):
            importer.rebase_after_import(manager, collection)
        assert dict(conn.execute("SELECT key,value FROM metadata")) == {
            "mobile_revlog_watermark": "9999", "import_rebase_pending": "1",
        }
    finally:
        conn.close()


def test_rebase_requires_collection_only_while_import_marker_exists(tmp_path):
    importer = load_module()
    assert hasattr(importer, "rebase_after_import"), "The import rebase has not been implemented"
    conn, manager = rebase_state(tmp_path / "save.db")
    try:
        with pytest.raises(RuntimeError):
            importer.rebase_after_import(manager, None)
        conn.execute("DELETE FROM metadata WHERE key='import_rebase_pending'")
        conn.commit()
        assert importer.rebase_after_import(manager, None) is False
    finally:
        conn.close()


def test_rebase_rolls_back_watermark_if_marker_cannot_be_retired(tmp_path):
    importer = load_module()
    assert hasattr(importer, "rebase_after_import"), "The import rebase has not been implemented"
    conn, manager = rebase_state(tmp_path / "save.db")
    conn.execute(
        "CREATE TRIGGER reject_marker_delete BEFORE DELETE ON metadata "
        "WHEN OLD.key='import_rebase_pending' BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END"
    )
    conn.commit()
    collection = SimpleNamespace(db=SimpleNamespace(scalar=lambda sql: 200))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            importer.rebase_after_import(manager, collection)
        assert dict(conn.execute("SELECT key,value FROM metadata")) == {
            "mobile_revlog_watermark": "9999", "import_rebase_pending": "1",
        }
    finally:
        conn.close()


@pytest.mark.parametrize(
    "failure", ["manifest_cleanup", "staging_directory", "target_directory", "read_back"],
)
def test_publication_failure_reports_a_pending_import_rather_than_an_abort(
    tmp_path, monkeypatch, failure,
):
    """Replacing pending.json is the commit point; later steps cannot undo it.

    Cleanup, durability and read-back all run after publication and
    deliberately keep the staged copy when they fail. A caller told only
    "aborted" would leave the user playing on into a replacement they believe
    cannot happen, so staging reports this state with its own exception.

    Each parameter breaks exactly one of those steps, in the order stage_import
    performs them, so no two cases exercise the same line.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    _, directory = importer._paths(target)
    injecting = [True]
    fsync_directory, read_back = importer._fsync_directory, importer.pending_import_info
    unlink = Path.unlink

    def failing_unlink(self, *args, **kwargs):
        if injecting[0] and failure == "manifest_cleanup" and self.suffix == ".json":
            raise PermissionError("injected manifest cleanup failure")
        return unlink(self, *args, **kwargs)

    def failing_sync(path):
        broken = {"staging_directory": directory, "target_directory": target.parent}
        if injecting[0] and Path(path) == broken.get(failure):
            raise OSError(f"injected {failure} sync failure")
        return fsync_directory(path)

    published = []

    def failing_read_back(path):
        # The guard read at the start of staging must still work: only the
        # read-back of what this call published is broken.
        if injecting[0] and failure == "read_back" and published:
            raise OSError("injected read-back failure")
        published.append(path)
        return read_back(path)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    monkeypatch.setattr(importer, "_fsync_directory", failing_sync)
    monkeypatch.setattr(importer, "pending_import_info", failing_read_back)

    with pytest.raises(importer.ImportStagedError):
        importer.stage_import(source, target)

    injecting[0] = False
    staged = importer.pending_import_info(target)
    assert staged is not None and staged["pending_path"].is_file()
    # Nothing was replaced yet, and the next full start still installs it.
    assert names(target) == ["local"]
    assert json.loads(commit_in_new_process(target).stdout)["installed"] is True
    assert names(target) == ["incoming"]


def test_publication_failure_can_still_be_cancelled(tmp_path, monkeypatch):
    """The reported remedy has to work: cancelling stops the pending install."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    fsync_directory = importer._fsync_directory
    injecting = [True]

    def failing_sync(path):
        if injecting[0] and Path(path) == target.parent:
            raise OSError("injected sync failure")
        return fsync_directory(path)

    monkeypatch.setattr(importer, "_fsync_directory", failing_sync)
    with pytest.raises(importer.ImportStagedError):
        importer.stage_import(source, target)

    injecting[0] = False
    assert importer.cancel_pending_import(target) is True
    assert importer.pending_import_info(target) is None
    assert json.loads(commit_in_new_process(target).stdout)["installed"] is False
    assert names(target) == ["local"]


def test_a_manifest_that_vanishes_before_read_back_leaves_nothing_staged(tmp_path, monkeypatch):
    """Nothing will install, so this really is an abort — and must not litter."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    _, directory = importer._paths(target)
    read_back = importer.pending_import_info
    published = []

    def vanishing(path):
        if published:
            (directory / "pending.json").unlink(missing_ok=True)
        published.append(path)
        return read_back(path)

    monkeypatch.setattr(importer, "pending_import_info", vanishing)
    with pytest.raises(OSError) as raised:
        importer.stage_import(source, target)
    assert not isinstance(raised.value, importer.ImportStagedError)

    monkeypatch.undo()
    assert importer.pending_import_info(target) is None
    assert list(directory.glob("*.db")) == []
    assert names(target) == ["local"]


def test_a_cancellation_survives_a_crash_that_undoes_its_removals(tmp_path, monkeypatch):
    """Cancel is committed on disk before anything is removed.

    Without a directory sync, a crash can bring back both ``pending.json`` and
    the staged copy, and the next start would install over progress made after
    the user was told the import was cancelled. The record is rewritten in place
    and synced as a file first, so what a crash brings back installs nothing.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    manifest = staged["pending_path"].parent / "pending.json"
    unlink = Path.unlink

    def undone_by_the_crash(path, *args, **kwargs):
        if path == manifest:
            return None
        return unlink(path, *args, **kwargs)

    def failing_sync(path):
        raise OSError("injected cancellation flush failure")

    monkeypatch.setattr(importer, "_fsync_directory", failing_sync)
    monkeypatch.setattr(importer, "_remove_owned_copy", lambda path: None)
    monkeypatch.setattr(Path, "unlink", undone_by_the_crash)
    assert importer.cancel_pending_import(target) is True
    monkeypatch.undo()

    # The crash left both files in place, and play went on afterwards.
    assert manifest.is_file() and staged["pending_path"].is_file()
    with sqlite3.connect(target) as conn:
        conn.execute("INSERT INTO captured_pokemon VALUES ('after-cancel', '{}')")
    assert importer.pending_import_info(target) is None
    assert json.loads(commit_in_new_process(target).stdout)["installed"] is False
    assert names(target) == ["after-cancel", "local"]

    # Finishing off the leftover is not reported as a second cancellation.
    assert importer.cancel_pending_import(target) is False
    assert not manifest.exists()
    assert not staged["pending_path"].exists()


def test_a_cancellation_that_cannot_reach_the_disk_is_reported_and_kept(tmp_path, monkeypatch):
    """If the commit itself cannot be synced, the import has not been cancelled.

    A crash could bring it back as it was, so "cancelled" would be a promise
    nobody kept. The record is put back for this session too: the retry that
    the warning asks for must find something to cancel.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)

    def failing_fsync(descriptor):
        raise OSError("injected file sync failure")

    monkeypatch.setattr(importer.os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="injected file sync failure"):
        importer.cancel_pending_import(target)
    monkeypatch.undo()

    assert importer.pending_import_info(target)["token"] == staged["token"]
    assert staged["pending_path"].is_file()
    assert importer.cancel_pending_import(target) is True
    assert json.loads(commit_in_new_process(target).stdout)["installed"] is False
    assert names(target) == ["local"]


def test_a_new_import_can_be_staged_over_a_cancelled_record(tmp_path, monkeypatch):
    """A cancelled record that could not be removed blocks nothing."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    first = make_save(tmp_path / "first.db", "first")
    second = make_save(tmp_path / "second.db", "second")
    staged = importer.stage_import(first, target)
    manifest = staged["pending_path"].parent / "pending.json"
    unlink = Path.unlink
    monkeypatch.setattr(
        Path, "unlink",
        lambda path, *args, **kwargs: None if path == manifest else unlink(path, *args, **kwargs),
    )
    assert importer.cancel_pending_import(target) is True
    monkeypatch.undo()
    assert manifest.is_file()

    importer.stage_import(second, target)
    assert json.loads(commit_in_new_process(target).stdout)["installed"] is True
    assert names(target) == ["second"]


def test_cancellation_holds_even_when_its_directory_flush_fails(tmp_path, monkeypatch):
    """The synced cancelled record is what stops the install, so say so.

    Reporting "could not be cancelled, try again" once that record is on disk
    earns a "there is no pending save import" on the retry, and the user cannot
    tell from Ankimon which of the two to believe. Some network and FUSE mounts
    refuse a directory sync outright, which would fail every cancellation.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)
    fsync_directory = importer._fsync_directory
    injecting = [True]

    def failing_sync(path):
        if injecting[0]:
            raise OSError("injected cancellation flush failure")
        return fsync_directory(path)

    monkeypatch.setattr(importer, "_fsync_directory", failing_sync)
    assert importer.cancel_pending_import(target) is True

    injecting[0] = False
    assert importer.pending_import_info(target) is None
    assert json.loads(commit_in_new_process(target).stdout)["installed"] is False
    assert names(target) == ["local"]


def test_a_second_import_names_the_pending_one_it_is_refusing_for(tmp_path):
    """The refusal has its own type: this is not "nothing is going to happen"."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    other = make_save(tmp_path / "other.db", "second")
    importer.stage_import(source, target)

    with pytest.raises(importer.ImportAlreadyPendingError):
        importer.stage_import(other, target)
    # The refusal leaves the first import exactly as it was.
    assert json.loads(commit_in_new_process(target).stdout)["installed"] is True
    assert names(target) == ["incoming"]



def test_a_record_left_over_from_an_installed_import_is_recognised(tmp_path):
    """The manifest outlives the import when the final cleanup fails.

    ``_finish_installed_import`` retires it after replacement, so what survives
    that failure describes a save that has ALREADY been replaced. Nothing in the
    menu could tell the two apart, and both its answers were the wrong way round.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    assert importer.pending_import_is_installed(target) is False

    # Exactly what installation does, without the cleanup that follows it.
    shutil.copyfile(staged["pending_path"], target)
    assert names(target) == ["incoming"]
    assert importer.pending_import_info(target)["token"] == staged["token"]
    assert importer.pending_import_is_installed(target) is True

    # A record for a DIFFERENT import is still ordinary pending work.
    with sqlite3.connect(target) as conn:
        conn.execute("UPDATE metadata SET value = 'another-token' WHERE key = 'import_token'")
    assert importer.pending_import_is_installed(target) is False


def test_repeated_failed_installs_do_not_grow_the_recovery_folder_forever(tmp_path):
    """Every attempt snapshots the live save again before it tries to install.

    A lock that does not clear -- the Windows case -- means one attempt per
    restart, indefinitely, and nothing else prunes this directory: Backup
    Manager's retention works on a different tree.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)

    refuse_install = (
        "real_replace = os.replace\n"
        "def fail_install(source, dest):\n"
        "    if Path(dest) == target:\n"
        "        raise PermissionError('simulated antivirus lock')\n"
        "    return real_replace(source, dest)\n"
        "module.os.replace = fail_install\n"
        "module.commit_pending_import(target)\n"
    )
    for attempt in range(4):
        child(refuse_install, target, expected=1)
        with sqlite3.connect(target) as conn:
            conn.execute("INSERT INTO captured_pokemon VALUES (?, '{}')", (f"run-{attempt}",))

    copies = list(staged["recovery_path"].parent.glob("*.db"))
    assert len(copies) == 2, sorted(path.name for path in copies)
    # The advertised path still holds the most recent attempt's progress.
    assert names(staged["recovery_path"]) == [
        "local", "run-0", "run-1", "run-2",
    ]


def test_an_import_for_a_save_that_no_longer_exists_says_so(tmp_path):
    """Every step below the check reads the target; a bare SQLite error does not.

    get_db tries both modes on every start and never recreates the developer
    save, so an unactionable message here repeats for as long as the record does.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimonDEV.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)
    target.unlink()

    child(
        "try:\n"
        "    module.commit_pending_import(target)\n"
        "except FileNotFoundError as error:\n"
        "    assert 'no longer exists' in str(error), error\n"
        "    assert 'Cancel Pending Save Import' in str(error), error\n"
        "else:\n"
        "    raise AssertionError('a missing target should be reported, not opened')\n",
        target,
    )
    assert importer.pending_import_info(target) is not None


def test_a_spent_startup_budget_refuses_rather_than_waiting_again(tmp_path):
    """The install runs before Anki has a window to say what it is waiting for."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)

    child(
        "import time\n"
        "started = time.monotonic()\n"
        "try:\n"
        "    module.commit_pending_import(target, deadline=started - 1)\n"
        "except TimeoutError as error:\n"
        "    assert 'budget expired' in str(error), error\n"
        "else:\n"
        "    raise AssertionError('an expired budget should refuse the install')\n"
        "assert time.monotonic() - started < 5, 'it waited anyway'\n",
        target,
    )
    assert names(target) == ["local"]
    assert importer.pending_import_info(target) is not None


def test_whole_file_reads_and_copies_check_the_budget_between_blocks(tmp_path, monkeypatch):
    """One large save must not carry a startup install past its budget."""
    importer = load_module()
    source = tmp_path / "large.db"
    source.write_bytes(b"\0" * (3 * 1024 * 1024))
    clock = [0.0]

    def tick():
        clock[0] += 1
        return clock[0]

    monkeypatch.setattr(importer.time, "monotonic", tick)
    with pytest.raises(TimeoutError):
        importer._digest(source, deadline=2.5)
    clock[0] = 0.0
    with pytest.raises(TimeoutError):
        importer._copy_within(source, tmp_path / "copy.db", deadline=2.5)
    assert (tmp_path / "copy.db").stat().st_size < source.stat().st_size


def test_a_budget_spent_during_the_install_copy_leaves_the_old_save_pending(tmp_path):
    """Copying the save into place is the largest step, and the last one checked."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)

    child(
        "import time\n"
        "copy = module._copy_within\n"
        "def slow_disk(source, dest, deadline=None):\n"
        "    module.time.monotonic = lambda: deadline + 1\n"
        "    return copy(source, dest, deadline)\n"
        "module._copy_within = slow_disk\n"
        "try:\n"
        "    module.commit_pending_import(target, deadline=time.monotonic() + 60)\n"
        "except TimeoutError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('the install copy ignored its budget')\n",
        target,
    )
    assert names(target) == ["local"]
    assert importer.pending_import_info(target) is not None
    assert not list(tmp_path.glob(".ankimon-install-*"))


def test_a_locked_save_answers_unknown_within_the_wording_budget(tmp_path):
    """The menu asks this on the GUI thread, only to choose its wording.

    SQLite's own busy timeout here would be 30 seconds of a frozen Anki, and a
    save that cannot be read is evidence of neither answer.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)
    locker = sqlite3.connect(target, isolation_level=None)
    try:
        locker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        assert importer.pending_import_is_installed(target) is None
        assert time.monotonic() - started < importer._WORDING_BUDGET + 3
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    assert importer.pending_import_is_installed(target) is False
