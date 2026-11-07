"""Exercise staged imports across real process and SQLite boundaries."""

from contextlib import closing
import errno
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
import urllib.parse
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
    # A sqlite3 connection's context manager ends the transaction but does not
    # close the connection. That leaked handle blocks replacement on Windows.
    with closing(sqlite3.connect(path)) as conn, conn:
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
    with closing(sqlite3.connect(path)) as conn:
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


def test_an_import_installs_over_a_save_a_crash_left_with_a_hot_journal(tmp_path):
    """Only a read-write connection can roll a hot rollback journal back.

    Every step before the journal-mode switch reads the save read-only, so that
    start failed, and the user played a session on the old save.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    # A transaction too large for the page cache writes into the save itself, and
    # only its journal can undo that. The writer dies before committing.
    child(
        "conn = sqlite3.connect(target, isolation_level=None)\n"
        "conn.execute('PRAGMA journal_mode=DELETE')\n"
        "conn.execute('PRAGMA cache_size=1')\n"
        "conn.execute('BEGIN IMMEDIATE')\n"
        "for i in range(2000):\n"
        "    conn.execute('INSERT INTO captured_pokemon VALUES (?, ?)', (f'torn-{i}', 'x' * 400))\n"
        "os._exit(1)\n", target, expected=1,
    )
    journal = Path(str(target) + "-journal")
    assert journal.is_file() and journal.stat().st_size > 0

    result = commit_in_new_process(target)
    assert json.loads(result.stdout)["installed"] is True
    assert names(target) == ["incoming"]
    assert not journal.exists()
    # The copy kept of the old save is its last committed state, not the torn one.
    assert names(staged["recovery_path"]) == ["local"]
    assert importer.pending_import_info(target) is None


def test_a_locked_save_stops_the_install_with_the_lock_not_the_budget(tmp_path):
    """The rollback step's read-write open waits on a lock like any other.

    Passing that lock over left the read-only steps to wait on it again with
    nothing left of the budget, so the startup notice said the budget expired
    instead of naming the lock.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    locker = sqlite3.connect(target, isolation_level=None)
    try:
        locker.execute("BEGIN EXCLUSIVE")
        result = child(
            "import time\n"
            "try:\n"
            "    module.commit_pending_import(target, deadline=time.monotonic() + 1.5)\n"
            "except Exception as error:\n"
            "    print(json.dumps({'error': type(error).__name__, 'message': str(error)}))\n"
            "else:\n"
            "    raise AssertionError('the install went ahead over a locked save')\n",
            target,
        )
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    failure = json.loads(result.stdout)
    assert failure["error"] == "OperationalError", failure
    assert "locked" in failure["message"], failure
    assert names(target) == ["local"]
    assert not staged["recovery_path"].exists()
    assert importer.pending_import_info(target)["token"] == staged["token"]


def test_a_save_the_rollback_step_cannot_open_for_writing_still_installs(tmp_path):
    """The steps that predate the rollback step only ever opened the save read-only.

    Only a lock stops the install there; a save SQLite will not open read-write
    is logged and left to the read-only steps, which read it as they always did.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    result = child(
        "messages = []\n"
        "class Logger:\n"
        "    def log(self, level, message):\n"
        "        messages.append(message)\n"
        "real_connect = sqlite3.connect\n"
        "trips = []\n"
        "def cannot_write(database, *args, **kwargs):\n"
        "    # The rollback step's is the install's first read-write open; the\n"
        "    # journal-mode switch after it gets a real connection.\n"
        "    if 'mode=rw' in str(database) and not trips:\n"
        "        trips.append(database)\n"
        "        raise sqlite3.OperationalError('unable to open database file')\n"
        "    return real_connect(database, *args, **kwargs)\n"
        "module.sqlite3.connect = cannot_write\n"
        "installed = module.commit_pending_import(target, Logger())\n"
        "print(json.dumps({'installed': installed, 'messages': messages,\n"
        "                  'trips': len(trips)}))\n",
        target,
    )
    outcome = json.loads(result.stdout)
    assert outcome["installed"] is True, outcome
    assert outcome["trips"] == 1, outcome
    assert any("roll back an unfinished write" in message
               and "unable to open database file" in message
               for message in outcome["messages"]), outcome
    assert names(target) == ["incoming"]
    assert names(staged["recovery_path"]) == ["local"]
    assert importer.pending_import_info(target) is None


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


@pytest.mark.parametrize("damage", ["corrupt", "moved"])
def test_a_record_staging_cannot_read_is_refused_as_a_pending_import(tmp_path, damage):
    """Cancel is the only way to clear such a record, so refuse in the type that names it.

    A plain ValueError reached Import's and Backup Restore's generic handlers,
    which reported an abort and never mentioned Cancel Pending Save Import.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    other = make_save(tmp_path / "other.db", "second")
    staged = importer.stage_import(source, target)
    manifest = staged["pending_path"].parent / "pending.json"
    if damage == "corrupt":
        manifest.write_text("{ definitely-not-json", encoding="utf-8")
    else:
        # What moving the Anki base folder leaves: the record names the old path.
        record = json.loads(manifest.read_text(encoding="utf-8"))
        record["target"] = str(tmp_path / "old-base-folder" / target.name)
        manifest.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(importer.ImportAlreadyPendingError) as refused:
        importer.stage_import(other, target)
    assert isinstance(refused.value.__cause__, ValueError)
    # The callers word this answer as unknown and point at Cancel.
    assert importer.pending_import_is_installed(target) is None

    assert importer.cancel_pending_import(target) is True
    assert not staged["pending_path"].exists()
    importer.stage_import(other, target)
    assert json.loads(commit_in_new_process(target).stdout)["installed"] is True
    assert names(target) == ["second"]


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


def test_a_pending_save_swapped_for_another_valid_save_is_refused(tmp_path):
    """Size and integrity checks pass for any real save; only the digest knows.

    A truncated file never reaches the digest comparison: the size check refuses
    it first. This one is a complete Ankimon save, just not the confirmed one.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    shutil.copyfile(make_save(tmp_path / "other.db", "other"), staged["pending_path"])

    result = child("module.commit_pending_import(target)\n", target, expected=1)
    assert "changed after it was confirmed" in result.stderr
    assert names(target) == ["local"]
    assert importer.pending_import_info(target) is not None


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_an_installed_import_keeps_the_saves_permissions(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    # Not the umask default, so an install that hard-codes one cannot pass.
    target.chmod(0o640)
    staged = importer.stage_import(source, target)
    commit_in_new_process(target)
    assert names(target) == ["incoming"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    # The copy that may still carry credentials stays private.
    assert stat.S_IMODE(staged["recovery_path"].stat().st_mode) == 0o600


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
    original_length = manifest.stat().st_size
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

    # The crash left both files in place, and play went on afterwards. The
    # record was rewritten at its own length: no truncate, so no torn tail.
    assert manifest.is_file() and staged["pending_path"].is_file()
    assert manifest.stat().st_size == original_length
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
    # The superseded copy kept is the newest one, not the first attempt's.
    [retry] = [path for path in copies if path != staged["recovery_path"]]
    assert names(retry) == ["local", "run-0", "run-1"]
    # The advertised path still holds the most recent attempt's progress.
    assert names(staged["recovery_path"]) == [
        "local", "run-0", "run-1", "run-2",
    ]


def test_an_import_for_a_save_that_no_longer_exists_installs_it(tmp_path):
    """There is no current save to lose, so the import goes in.

    Refusing it let get_db create a fresh save in the same start, and the start
    after that installed the import over the fresh save without a notice.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    target.unlink()

    child("assert module.commit_pending_import(target) is True\n", target)
    assert names(target) == ["incoming"]
    assert importer.pending_import_info(target) is None
    # Nothing was retained, so no empty recovery folder suggests otherwise.
    assert not staged["recovery_path"].parent.exists()


@pytest.mark.parametrize("suffixes", [("-journal",), ("-wal", "-shm")])
def test_journals_left_beside_a_missing_save_are_set_aside_not_replayed(tmp_path, suffixes):
    """SQLite pairs a journal with a database by its filename alone.

    Left in place, a stale one would be applied to the imported save. It may be
    all that remains of the missing save, so it is kept rather than deleted.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    target.unlink()
    for suffix in suffixes:
        Path(str(target) + suffix).write_bytes(f"stale {suffix}".encode())

    child("assert module.commit_pending_import(target) is True\n", target)
    assert not [suffix for suffix in suffixes if Path(str(target) + suffix).exists()]
    assert names(target) == ["incoming"]
    kept = sorted(path.read_bytes() for path in staged["recovery_path"].parent.iterdir())
    assert kept == sorted(f"stale {suffix}".encode() for suffix in suffixes)
    if os.name != "nt":
        assert stat.S_IMODE(staged["recovery_path"].parent.stat().st_mode) == 0o700


def test_an_install_into_a_missing_save_that_keeps_its_record_names_no_recovery_copy(tmp_path):
    """Nothing was replaced, so nothing was kept, whatever path the record reserved.

    Its final cleanup can fail like any other install's, leaving the record for
    the next start and for Cancel. Neither may send the user after a copy.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    target.unlink()
    child(
        "def refuse(target):\n"
        "    raise OSError('injected cleanup failure')\n"
        "module.cancel_pending_import = refuse\n"
        "try:\n"
        "    module.commit_pending_import(target)\n"
        "except module.ImportInstalledError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('the cleanup failure should have been reported')\n",
        target,
    )
    assert names(target) == ["incoming"]
    assert importer.pending_import_is_installed(target) is True
    assert importer.pending_import_recovery_copy(target) is None
    assert not staged["recovery_path"].exists()

    result = child(
        "messages = []\n"
        "class Logger:\n"
        "    def log(self, level, message):\n"
        "        messages.append(message)\n"
        "assert module.commit_pending_import(target, Logger()) is True\n"
        "print(json.dumps(messages))\n",
        target,
    )
    messages = json.loads(result.stdout)
    assert messages and not any("ankimon_recovery" in message for message in messages)
    assert importer.pending_import_info(target) is None


def test_the_startup_budget_is_checked_before_a_recovery_snapshot_is_published(tmp_path):
    """A snapshot that used up the budget must not go on to publish and sync it.

    Every recovery step runs inside add-on import, before Anki has a window.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    child(
        "import time\n"
        "end = time.monotonic() + 60\n"
        "snapshot = module._snapshot\n"
        "def snapshot_then_stall(source, dest, deadline=None):\n"
        "    snapshot(source, dest, deadline)\n"
        "    module.time.monotonic = lambda: end + 1\n"
        "module._snapshot = snapshot_then_stall\n"
        "try:\n"
        "    module.commit_pending_import(target, deadline=end)\n"
        "except TimeoutError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('the budget was not checked after the snapshot')\n",
        target,
    )
    assert names(target) == ["local"]
    assert not staged["recovery_path"].exists()
    assert not list(staged["recovery_path"].parent.glob(".backup-*"))
    assert importer.pending_import_info(target) is not None


def test_a_spent_startup_budget_still_sets_journals_aside(tmp_path):
    """Moving them is what keeps them, so no budget check may come first.

    An install that stops leaves get_db to open a fresh save at that path, and
    SQLite does not keep journals it finds beside a database with no pages.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    target.unlink()
    journal = Path(str(target) + "-journal")
    journal.write_bytes(b"stale journal")
    child(
        "import time\n"
        "try:\n"
        "    module.commit_pending_import(target, deadline=time.monotonic() - 1)\n"
        "except TimeoutError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('an expired budget should refuse the install')\n",
        target,
    )
    assert not journal.exists()
    assert [path.read_bytes() for path in staged["recovery_path"].parent.iterdir()] == [
        b"stale journal"]
    assert not target.exists()
    assert importer.pending_import_info(target) is not None
    # Set aside, so a fresh save may be opened there after all.
    importer.refuse_to_open_over_journals(target)


def test_a_junction_is_recognised_on_pythons_without_isjunction(tmp_path, monkeypatch):
    """os.path.isjunction only exists from Python 3.12; older Anki builds bundle older ones.

    A fallback that always answered False let a junction pass as an ordinary
    folder there. Simulated through the field os.lstat reports on Windows.
    """
    importer = load_module()
    folder = tmp_path / "ankimon_recovery"
    folder.mkdir()
    real_lstat = os.lstat

    def windows_lstat(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        if Path(path) != folder:
            return result
        return SimpleNamespace(st_mode=result.st_mode, st_reparse_tag=0xA0000003)

    monkeypatch.delattr(os.path, "isjunction", raising=False)
    monkeypatch.setattr(os, "lstat", windows_lstat)
    assert importer._is_link(folder)
    assert not importer._is_link(tmp_path)
    with pytest.raises(OSError, match="is a link"):
        importer._private_directory(folder)


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


@pytest.mark.parametrize("step", ["digest", "recovery_sync"])
def test_the_startup_budget_reaches_the_digest_and_the_recovery_sync(tmp_path, step):
    """The install passes its budget into each whole-file step, not just the copy.

    Checked by where the TimeoutError is raised: a call site that dropped the
    deadline would let the step finish and fail later, somewhere else.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    with sqlite3.connect(source) as conn:
        # More than one digest block, so a check between blocks can fire.
        conn.execute("CREATE TABLE ballast (data BLOB)")
        conn.execute("INSERT INTO ballast VALUES (?)", (os.urandom(3 * 1024 * 1024),))
    staged = importer.stage_import(source, target)

    child(
        "import time, traceback\n"
        "step = sys.argv[3]\n"
        "deadline = time.monotonic() + 60\n"
        "def expire():\n"
        "    module.time.monotonic = lambda: deadline + 1\n"
        "if step == 'digest':\n"
        "    real_sha = module.hashlib.sha256\n"
        "    class SlowDisk:\n"
        "        def __init__(self): self.inner = real_sha()\n"
        "        def update(self, block): expire(); self.inner.update(block)\n"
        "        def hexdigest(self): return self.inner.hexdigest()\n"
        "    module.hashlib.sha256 = SlowDisk\n"
        "    expected = '_digest'\n"
        "else:\n"
        "    verify = module._verify_save\n"
        "    def verify_then_stall(path, deadline=None):\n"
        "        verify(path, deadline)\n"
        "        if Path(path).name.startswith('.backup-'): expire()\n"
        "    module._verify_save = verify_then_stall\n"
        "    expected = '_snapshot'\n"
        "try:\n"
        "    module.commit_pending_import(target, deadline=deadline)\n"
        "except TimeoutError as error:\n"
        "    frames = [frame.name for frame in traceback.extract_tb(error.__traceback__)]\n"
        "    assert frames[-2:] == [expected, '_budget'], frames\n"
        "else:\n"
        "    raise AssertionError('the budget was not checked at ' + step)\n",
        target, step,
    )
    assert names(target) == ["local"]
    assert importer.pending_import_info(target)["token"] == staged["token"]


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


def test_a_record_for_a_save_that_no_longer_exists_was_not_installed(tmp_path):
    """There is no save on disk, so it is not the imported one.

    Answering "unknown" made Cancel send the user after a recovery copy of a
    save that was never there to copy. Once a recovery copy exists, an install
    may have replaced the save before it went missing, and "unknown" is the
    answer that points at that copy.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimonDEV.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    target.unlink()
    assert importer.pending_import_is_installed(target) is False

    staged["recovery_path"].parent.mkdir(parents=True)
    shutil.copyfile(source, staged["recovery_path"])
    assert importer.pending_import_is_installed(target) is None


@pytest.mark.skipif(os.name == "nt", reason="Windows never syncs a directory")
@pytest.mark.parametrize("code, installs", [
    ("EINVAL", True), ("EOPNOTSUPP", True), ("EIO", False),
])
def test_a_volume_that_cannot_sync_directories_still_installs(tmp_path, code, installs):
    """A VirtualBox shared folder answers a directory fsync with EINVAL.

    SQLite ignores that in its own directory sync. Treating it as fatal left an
    import that could never install on that volume. A real I/O error still stops
    the install before anything is replaced.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)
    child(
        "import errno, stat\n"
        "real_fsync = os.fsync\n"
        "def directory_refuses(fd):\n"
        "    if stat.S_ISDIR(os.fstat(fd).st_mode):\n"
        f"        raise OSError(errno.{code}, 'directory fsync refused')\n"
        "    return real_fsync(fd)\n"
        "module.os.fsync = directory_refuses\n"
        "module.commit_pending_import(target)\n",
        target, expected=0 if installs else 1,
    )
    assert names(target) == (["incoming"] if installs else ["local"])
    assert (importer.pending_import_info(target) is None) is installs


REFUSE_CHMOD = (
    "import errno\n"
    "def fixed_permissions(self, mode, *args, **kwargs):\n"
    "    raise PermissionError(errno.EPERM, 'Operation not permitted', str(self))\n"
    "module.Path.chmod = fixed_permissions\n"
)


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership")
def test_a_recovery_folder_that_refuses_chmod_does_not_block_the_install(tmp_path):
    """FAT and exFAT volumes fix permissions when mounted, and refuse chmod.

    Nothing can ever tighten such a folder, so refusing over it refused the
    install at every start.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    child(REFUSE_CHMOD + "module.commit_pending_import(target)\n", target)
    assert names(target) == ["incoming"]
    assert names(staged["recovery_path"]) == ["local"]


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership")
def test_a_recovery_folder_owned_by_another_account_is_still_refused(tmp_path):
    """EPERM is also chmod's answer about somebody else's folder.

    A copy of the save, credentials included, does not go into a folder another
    account controls.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    result = child(
        REFUSE_CHMOD
        + "real_getuid = os.getuid\n"
        "module.os.getuid = lambda: real_getuid() + 1\n"
        "module.commit_pending_import(target)\n",
        target, expected=1,
    )
    assert "Operation not permitted" in result.stderr
    assert names(target) == ["local"]
    assert not staged["recovery_path"].exists()


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_recovery_folder_that_is_a_link_is_refused(tmp_path):
    """mkdir(exist_ok=True) accepts a link to a folder.

    A snapshot written through it, credentials included, lands wherever the link
    points, and the permissions checked on the way were never that folder's.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (target.parent / "ankimon_recovery").symlink_to(elsewhere, target_is_directory=True)

    result = child("module.commit_pending_import(target)\n", target, expected=1)
    assert "is a link" in result.stderr
    assert names(target) == ["local"]
    assert list(elsewhere.iterdir()) == []
    assert importer.pending_import_info(target) is not None


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_staging_folder_that_is_a_link_is_refused(tmp_path):
    """A prepared restore keeps this installation's credentials in its staged copy.

    Written through a link, that copy lands wherever the link points, like a
    recovery snapshot would.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / f".ankimon-import-{target.name}").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(OSError, match="is a link"):
        importer.stage_import(source, target, sanitize_credentials=False)
    assert list(elsewhere.iterdir()) == []


def unc_as_uri(self):
    """What Path.as_uri gives a UNC path on Windows: the server as the authority."""
    return "file://server" + urllib.parse.quote(str(self))


def test_a_network_path_gets_a_uri_sqlite_accepts(tmp_path, monkeypatch):
    """SQLite refuses any URI authority but an empty one or localhost.

    Linux has no UNC paths, so as_uri is made to answer the way it does on
    Windows for one. The file must still open, read-only, even with URI
    delimiters in its name, and a missing one must not be created.
    """
    importer = load_module()
    # "?" is not a legal Windows filename character; "#" and "%" still need escaping.
    folder = tmp_path / ("share #1 %" if os.name == "nt" else "share #1 ?%")
    folder.mkdir()
    save = make_save(folder / "ankimon.db", "local")
    monkeypatch.setattr(importer.Path, "as_uri", unc_as_uri)

    with pytest.raises(sqlite3.OperationalError, match="authority"):
        sqlite3.connect(save.as_uri() + "?mode=ro", uri=True)
    conn = sqlite3.connect(importer._sqlite_uri(save, "ro"), uri=True)
    try:
        assert conn.execute("SELECT individual_id FROM captured_pokemon").fetchall() == [("local",)]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM captured_pokemon")
    finally:
        conn.close()
    missing = folder / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(importer._sqlite_uri(missing, "ro"), uri=True)
    assert not missing.exists()


def test_an_import_stages_and_installs_on_a_network_path(tmp_path, monkeypatch):
    """Every SQLite open on the import path, staging and startup, takes the helper."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    with monkeypatch.context() as patched:
        patched.setattr(importer.Path, "as_uri", unc_as_uri)
        staged = importer.stage_import(source, target)

    child(
        "import urllib.parse\n"
        "module.Path.as_uri = lambda self: 'file://server' + urllib.parse.quote(str(self))\n"
        "assert module.commit_pending_import(target) is True\n",
        target,
    )
    assert names(target) == ["incoming"]
    assert names(staged["recovery_path"]) == ["local"]


@pytest.mark.parametrize("folder_usable", [False, True])
def test_cancelling_an_import_whose_save_is_missing_keeps_the_journals(tmp_path, folder_usable):
    """The pending record is what keeps a fresh save from being opened over them.

    Cancelling retires it, so the journals go where the install would have put
    them first, and nothing is cancelled when they cannot.
    """
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    target.unlink()
    journal = Path(str(target) + "-wal")
    journal.write_bytes(b"committed progress" * 64)
    if not folder_usable:
        (tmp_path / "ankimon_recovery").write_text("not a folder")
        with pytest.raises(OSError, match="Nothing was cancelled"):
            importer.cancel_pending_import(target)
        assert importer.pending_import_info(target)["token"] == staged["token"]
        assert journal.read_bytes() == b"committed progress" * 64
        with pytest.raises(importer.ImportUnsafeToOpenError):
            importer.refuse_to_open_over_journals(target)
        return
    assert importer.cancel_pending_import(target) is True
    assert importer.pending_import_info(target) is None
    assert not journal.exists()
    assert [path.read_bytes() for path in staged["recovery_path"].parent.iterdir()] == [
        b"committed progress" * 64]
    importer.refuse_to_open_over_journals(target)


def test_a_damaged_record_is_not_cancelled_out_from_under_the_journals_it_protects(tmp_path):
    """With no readable record there is no recovery folder to move them into."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)
    target.unlink()
    journal = Path(str(target) + "-journal")
    journal.write_bytes(b"unfinished transaction" * 64)
    manifest = tmp_path / f".ankimon-import-{target.name}" / "pending.json"
    manifest.write_text("{damaged")

    with pytest.raises(OSError, match="Nothing was cancelled"):
        importer.cancel_pending_import(target)
    assert manifest.read_text() == "{damaged"
    assert journal.exists()

    # Once the user has moved them, the damaged record cancels as it always did.
    journal.unlink()
    assert importer.cancel_pending_import(target) is True
    assert importer.pending_import_info(target) is None


def test_a_cancel_that_fails_partway_through_the_move_names_only_what_is_still_beside_the_save(
    tmp_path, monkeypatch,
):
    """Journals already moved are safe in the recovery folder; only the rest need the user."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    target.unlink()
    wal, journal = Path(str(target) + "-wal"), Path(str(target) + "-journal")
    wal.write_bytes(b"committed progress" * 64)
    journal.write_bytes(b"unfinished transaction" * 64)
    replace = importer.os.replace

    def refuse_the_journal(source, destination):
        if Path(source) == journal:
            raise PermissionError(13, "in use", str(journal))
        return replace(source, destination)

    monkeypatch.setattr(importer.os, "replace", refuse_the_journal)
    with pytest.raises(OSError, match="Nothing was cancelled") as failed:
        importer.cancel_pending_import(target)
    message = str(failed.value)
    assert f"still beside it: {journal.name}." in message
    assert "could not be moved" not in message
    assert not wal.exists() and journal.exists()
    assert importer.pending_import_info(target)["token"] == staged["token"]
    assert [path.read_bytes() for path in staged["recovery_path"].parent.iterdir()] == [
        b"committed progress" * 64]

    monkeypatch.setattr(importer.os, "replace", replace)
    assert importer.cancel_pending_import(target) is True
    assert sorted(path.read_bytes() for path in staged["recovery_path"].parent.iterdir()) == [
        b"committed progress" * 64, b"unfinished transaction" * 64]


def damage_a_table(path, table="junk"):
    """Break one table's root page and leave the rest of the save readable.

    A bad sector or a torn write does this to a save that still loads: SQLite
    opens it and reads the other tables, and only its integrity check notices.
    ``junk`` is created for the purpose; any other table must already exist.
    """
    conn = sqlite3.connect(path)
    try:
        if table == "junk":
            with conn:
                conn.execute("CREATE TABLE junk (id INTEGER PRIMARY KEY, data TEXT)")
                conn.executemany("INSERT INTO junk VALUES (?, ?)",
                                 [(i, "x" * 1000) for i in range(200)])
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        root = conn.execute("SELECT rootpage FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        size = conn.execute("PRAGMA page_size").fetchone()[0]
    finally:
        conn.close()
    with open(path, "r+b") as handle:
        handle.seek((root - 1) * size)
        handle.write(b"\xff" * 64)
    return path


def quick_check_passes(path):
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        # Worse damage makes the check raise rather than list what it found.
        return conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


@pytest.mark.parametrize("journal", ["delete", "wal merged", "wal beside it"])
def test_a_damaged_save_is_kept_as_it_is_when_the_import_was_staged_to_allow_it(tmp_path, journal):
    """The restore a damaged save needs most is the one a verified copy blocked.

    The new save is still checked in full. The one it replaces is copied byte for
    byte, journal included, and nothing is repaired on the way.
    """
    importer = load_module()
    target = damage_a_table(make_save(tmp_path / "ankimon.db", "local"))
    source = make_save(tmp_path / "source.db", "incoming")
    assert importer.current_save_is_damaged(target) is True
    staged = importer.stage_import(source, target, retain_unverified=True)
    wal = Path(str(target) + "-wal")
    commit = "print(json.dumps({'installed': module.commit_pending_import(target)}))\n"
    if journal != "delete":
        # Play after the damage, left in the WAL by a writer that died.
        child(
            "conn = sqlite3.connect(target)\n"
            "conn.execute('PRAGMA journal_mode=WAL')\n"
            "conn.execute('PRAGMA wal_autocheckpoint=0')\n"
            "conn.execute(\"INSERT INTO captured_pokemon VALUES ('wal-only', '{}')\")\n"
            "conn.commit()\n"
            "os._exit(0)\n", target,
        )
        assert wal.stat().st_size > 0
    if journal == "wal beside it":
        # When nothing merges the WAL before the copy, it is copied beside the save.
        commit = "module._recover_hot_journal = lambda *args, **kwargs: None\n" + commit
    before = {suffix: Path(str(target) + suffix).read_bytes()
              for suffix in ("", "-wal") if Path(str(target) + suffix).is_file()}

    result = child(commit, target)
    assert json.loads(result.stdout)["installed"] is True
    assert names(target) == ["incoming"]
    assert importer.pending_import_info(target) is None
    kept = staged["unverified_path"]
    assert not staged["recovery_path"].exists()
    assert sorted(path.name for path in kept.parent.parent.iterdir()) == ["unverified"]
    if journal == "wal merged":
        # SQLite merged it into the save before the copy, as before any install.
        # The install's own read-only reads then leave an empty WAL beside it.
        assert [path.name for path in kept.parent.iterdir()
                if path.name != kept.name and path.stat().st_size] == []
    else:
        assert {suffix: Path(str(kept) + suffix).read_bytes()
                for suffix in ("", "-wal") if Path(str(kept) + suffix).is_file()} == before
    assert not quick_check_passes(kept), "the kept save was repaired"
    expected = ["local"] if journal == "delete" else ["local", "wal-only"]
    assert names(kept) == expected


def test_a_damaged_save_an_import_was_not_staged_to_replace_stays_and_says_why(tmp_path):
    importer = load_module()
    target = damage_a_table(make_save(tmp_path / "ankimon.db", "local"))
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    before = target.read_bytes()

    result = child("module.commit_pending_import(target)\n", target, expected=1)
    assert "CurrentSaveDamagedError" in result.stderr
    assert "restarting will not change that" in result.stderr
    assert "Cancel Pending Save Import" in result.stderr
    assert target.read_bytes() == before
    assert importer.pending_import_info(target)["token"] == staged["token"]
    assert not staged["unverified_path"].parent.exists()
    assert not staged["recovery_path"].exists()


@pytest.mark.parametrize("failure", [
    "sqlite3.OperationalError('database is locked')",
    "TimeoutError('the save import budget expired')",
])
def test_a_lock_or_a_spent_budget_is_not_taken_for_damage(tmp_path, failure):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target, retain_unverified=True)

    result = child(
        "def interrupted(*args, **kwargs):\n"
        f"    raise {failure}\n"
        "module._snapshot = interrupted\n"
        "module.commit_pending_import(target)\n", target, expected=1,
    )
    assert failure.split("(")[0].split(".")[-1] in result.stderr
    assert names(target) == ["local"]
    assert importer.pending_import_info(target) is not None
    assert not staged["unverified_path"].parent.exists()


def test_a_damaged_new_save_is_refused_even_when_the_current_one_may_be_kept(tmp_path):
    importer = load_module()
    target = damage_a_table(make_save(tmp_path / "ankimon.db", "local"))
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target, retain_unverified=True)
    damage_a_table(staged["pending_path"])
    before = target.read_bytes()

    result = child("module.commit_pending_import(target)\n", target, expected=1)
    # The new save's own check refuses it, before anything reads the current one.
    assert "SaveDamagedError" in result.stderr or "malformed" in result.stderr
    assert "CurrentSaveDamagedError" not in result.stderr
    assert target.read_bytes() == before
    assert importer.pending_import_info(target) is not None
    assert not staged["unverified_path"].parent.exists()


def test_a_save_too_damaged_to_name_its_import_is_replaced_only_when_allowed(tmp_path):
    """The install first asks the save which import it holds, from a table that
    can be the damaged one."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO metadata VALUES ('import_token', 'an-earlier-import')")
    damage_a_table(target, "metadata")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)

    result = child("module.commit_pending_import(target)\n", target, expected=1)
    assert "CurrentSaveDamagedError" in result.stderr and "malformed" in result.stderr
    assert "Cancel Pending Save Import" in result.stderr
    assert names(target) == ["local"]

    importer.cancel_pending_import(target)
    staged = importer.stage_import(source, target, retain_unverified=True)
    assert json.loads(commit_in_new_process(target).stdout)["installed"] is True
    assert names(target) == ["incoming"]
    assert names(staged["unverified_path"]) == ["local"]


def test_each_failed_attempt_keeps_the_newest_unverified_copy_and_one_before_it(tmp_path):
    """Play goes on between failed starts, so each attempt copies the save again."""
    importer = load_module()
    target = damage_a_table(make_save(tmp_path / "ankimon.db", "local"))
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target, retain_unverified=True)
    kept = staged["unverified_path"]
    refuse_install = (
        "real_replace = os.replace\n"
        "def fail_install(source, dest):\n"
        "    if Path(dest) == target:\n"
        "        raise PermissionError('simulated antivirus lock')\n"
        "    return real_replace(source, dest)\n"
        "module.os.replace = fail_install\n"
        "module.commit_pending_import(target)\n"
    )
    for attempt in range(3):
        child(refuse_install, target, expected=1)
        assert importer.pending_import_recovery_copy(target) == kept
        with sqlite3.connect(target) as conn:
            conn.execute("INSERT INTO captured_pokemon VALUES (?, '{}')", (f"run-{attempt}",))

    folder = kept.parent.parent
    superseded = sorted(folder.glob("unverified-superseded-*"))
    assert len(superseded) == 1
    assert names(superseded[0] / target.name) == ["local", "run-0"]
    assert names(kept) == ["local", "run-0", "run-1"]
    assert not list(folder.glob(".unverified-*"))

    assert json.loads(commit_in_new_process(target).stdout)["installed"] is True
    assert names(target) == ["incoming"]
    assert names(kept) == ["local", "run-0", "run-1", "run-2"]


def test_an_unverified_copy_counts_as_a_save_an_install_may_have_replaced(tmp_path):
    """With the save gone, only a copy says an install may have run. Without one
    the menu would say the import never installed."""
    importer = load_module()
    target = damage_a_table(make_save(tmp_path / "ankimon.db", "local"))
    staged = importer.stage_import(make_save(tmp_path / "source.db", "incoming"), target,
                                   retain_unverified=True)
    assert importer.pending_import_is_installed(target) is False
    child(
        "real_replace = os.replace\n"
        "def fail_install(source, dest):\n"
        "    if Path(dest) == target:\n"
        "        raise PermissionError('simulated antivirus lock')\n"
        "    return real_replace(source, dest)\n"
        "module.os.replace = fail_install\n"
        "module.commit_pending_import(target)\n", target, expected=1,
    )
    target.unlink()
    assert importer.pending_import_recovery_copy(target) == staged["unverified_path"]
    assert importer.pending_import_is_installed(target) is None


def test_whether_the_current_save_is_damaged(tmp_path, monkeypatch):
    importer = load_module()
    healthy = make_save(tmp_path / "healthy.db", "local")
    assert importer.current_save_is_damaged(healthy) is False
    assert importer.current_save_is_damaged(tmp_path / "missing.db") is False
    assert importer.current_save_is_damaged(
        damage_a_table(make_save(tmp_path / "damaged.db", "local"))) is True
    # Nothing an install could replace: asking would promise what never happens.
    unreadable = make_save(tmp_path / "unreadable.db", "local")
    with open(unreadable, "r+b") as handle:
        handle.seek(100)
        handle.write(b"\xff" * 64)
    assert importer.current_save_is_damaged(unreadable) is None

    monkeypatch.setattr(importer, "_DAMAGE_CHECK_BUDGET", 0.2)
    writer = sqlite3.connect(healthy, isolation_level=None)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        assert importer.current_save_is_damaged(healthy) is None
    finally:
        writer.close()


def test_only_a_damaged_save_with_nothing_pending_asks_first(tmp_path):
    importer = load_module()
    healthy = make_save(tmp_path / "ankimon.db", "local")
    assert importer.should_confirm_unverified_copy(healthy) is False
    damaged = damage_a_table(make_save(tmp_path / "ankimonDEV.db", "local"))
    assert importer.should_confirm_unverified_copy(damaged) is True
    importer.stage_import(make_save(tmp_path / "source.db", "incoming"), damaged)
    assert importer.should_confirm_unverified_copy(damaged) is False


def test_a_record_whose_unverified_answer_is_not_a_yes_or_no_is_refused(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    staged = importer.stage_import(make_save(tmp_path / "source.db", "incoming"), target)
    manifest = tmp_path / f".ankimon-import-{target.name}" / "pending.json"
    record = json.loads(manifest.read_text())
    assert "retain_unverified" not in record
    manifest.write_text(json.dumps({**record, "retain_unverified": "yes"}))
    with pytest.raises(ValueError, match="does not match"):
        importer.pending_import_info(target)
    assert staged["token"] == record["token"]


LOCKED_ONCE = (
    "import time\n"
    "module._is_file_lock_error = lambda error: isinstance(error, PermissionError)\n"
    "module.time.sleep = lambda seconds: None\n"
    "real_replace = os.replace\n"
    "held = Path(sys.argv[3])\n"
    "refused = []\n"
    "def scanner_lets_go(source, destination):\n"
    "    if Path(destination) == held and not refused:\n"
    "        refused.append(destination)\n"
    "        raise PermissionError(13, 'simulated scanner holding the file')\n"
    "    return real_replace(source, destination)\n"
    "module.os.replace = scanner_lets_go\n"
    "installed = module.commit_pending_import(target, deadline=time.monotonic() + 30)\n"
    "print(json.dumps({'installed': installed, 'refused': len(refused)}))\n"
)


@pytest.mark.parametrize("held", ["save", "recovery copy"])
def test_a_lock_that_clears_during_the_install_does_not_cost_a_restart(tmp_path, held):
    """This process's attempt gate refuses a second install, so a rename that
    failed once used to wait for another full restart."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    staged = importer.stage_import(source, target)
    destination = target if held == "save" else staged["recovery_path"]

    result = child(LOCKED_ONCE, target, destination)
    assert json.loads(result.stdout) == {"installed": True, "refused": 1}
    assert names(target) == ["incoming"]
    assert names(staged["recovery_path"]) == ["local"]
    assert importer.pending_import_info(target) is None


def test_a_lock_that_outlasts_the_budget_stops_the_install_with_the_lock(tmp_path):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)

    result = child(
        "import time\n"
        "module._is_file_lock_error = lambda error: isinstance(error, PermissionError)\n"
        "module._FILE_LOCK_RETRY_DELAYS = (1, 2, 4, 8, 16)\n"
        "real_monotonic, waited = time.monotonic, [0.0]\n"
        "module.time.monotonic = lambda: real_monotonic() + waited[0]\n"
        "module.time.sleep = lambda seconds: waited.__setitem__(0, waited[0] + seconds)\n"
        "attempts = []\n"
        "real_replace = os.replace\n"
        "def never_lets_go(source, destination):\n"
        "    if Path(destination) == target:\n"
        "        attempts.append(destination)\n"
        "        raise PermissionError(13, 'simulated scanner that never lets go')\n"
        "    return real_replace(source, destination)\n"
        "module.os.replace = never_lets_go\n"
        "try:\n"
        "    module.commit_pending_import(target, deadline=real_monotonic() + 10)\n"
        "except PermissionError:\n"
        "    print(json.dumps({'attempts': len(attempts), 'waited': waited[0]}))\n",
        target,
    )
    outcome = json.loads(result.stdout)
    assert 2 <= outcome["attempts"] < 6, outcome
    assert outcome["waited"] < 10
    assert names(target) == ["local"]
    assert importer.pending_import_info(target) is not None
    assert not list(tmp_path.glob(".ankimon-install-*"))


@pytest.mark.parametrize("error", [
    "OSError(errno.EIO, 'simulated disk error')",
    pytest.param("PermissionError(13, 'a real permission problem')", marks=pytest.mark.skipif(
        os.name == "nt", reason="on Windows this is how a lock looks")),
])
def test_an_error_that_is_not_a_lock_is_not_retried(tmp_path, error):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    importer.stage_import(source, target)
    classify = ("module._is_file_lock_error = lambda error: isinstance(error, PermissionError)\n"
                if error.startswith("OSError") else "")

    result = child(
        "import errno\n" + classify +
        "attempts = []\n"
        "real_replace = os.replace\n"
        "def broken(source, destination):\n"
        "    if Path(destination) == target:\n"
        "        attempts.append(destination)\n"
        f"        raise {error}\n"
        "    return real_replace(source, destination)\n"
        "module.os.replace = broken\n"
        "try:\n"
        "    module.commit_pending_import(target)\n"
        "except OSError as failure:\n"
        "    print(json.dumps({'attempts': len(attempts), 'errno': failure.errno}))\n",
        target,
    )
    assert json.loads(result.stdout)["attempts"] == 1
    assert names(target) == ["local"]


def test_file_locks_are_recognised_on_windows_only(monkeypatch):
    importer = load_module()
    monkeypatch.setattr(importer.os, "name", "nt")
    assert importer._is_file_lock_error(PermissionError()) is True
    for code in (5, 32, 33):
        error = OSError()
        error.winerror = code
        assert importer._is_file_lock_error(error) is True, code
    missing = OSError()
    missing.winerror = 2
    assert importer._is_file_lock_error(missing) is False
    assert importer._is_file_lock_error(ValueError()) is False
    monkeypatch.setattr(importer.os, "name", "posix")
    assert importer._is_file_lock_error(PermissionError(5, "denied")) is False


def test_a_file_lock_retry_never_waits_past_its_deadline(monkeypatch):
    importer = load_module()
    now = [100.0]
    monkeypatch.setattr(importer, "_is_file_lock_error", lambda error: isinstance(error, PermissionError))
    monkeypatch.setattr(importer.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(importer.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    attempts = []

    def locked():
        attempts.append(now[0])
        raise PermissionError(13, "held")

    with pytest.raises(PermissionError):
        importer._retry_on_file_lock(locked, deadline=100.5)
    # 0.1 and 0.2 fit before the deadline; waiting 0.4 more would not.
    assert attempts == pytest.approx([100.0, 100.1, 100.3])


def test_staging_publishes_its_record_through_a_lock_that_clears(tmp_path, monkeypatch):
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    monkeypatch.setattr(importer, "_is_file_lock_error", lambda error: isinstance(error, PermissionError))
    monkeypatch.setattr(importer, "_FILE_LOCK_RETRY_DELAYS", (0, 0, 0, 0, 0))
    replace = os.replace
    refused = []

    def scanner_lets_go(source, destination):
        if Path(destination).name == "pending.json" and not refused:
            refused.append(destination)
            raise PermissionError(13, "simulated scanner holding the record")
        return replace(source, destination)

    monkeypatch.setattr(importer.os, "replace", scanner_lets_go)
    staged = importer.stage_import(source, target)
    assert len(refused) == 1
    assert importer.pending_import_info(target)["token"] == staged["token"]


@pytest.mark.skipif(os.name != "nt", reason="needs a real Windows sharing violation")
def test_the_windows_handle_held_by_the_importer_is_released_before_replacement(
    tmp_path,
):
    """The startup checks themselves must not keep the destination locked."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    child("module.stage_import(Path(sys.argv[3]), target)\n", target, source)
    assert importer.commit_pending_import(target, deadline=time.monotonic() + 30) is True
    assert names(target) == ["incoming"]


@pytest.mark.skipif(os.name != "nt", reason="needs a real Windows sharing violation")
def test_a_windows_handle_on_the_save_that_closes_does_not_cost_a_restart(tmp_path, monkeypatch):
    """A handle opened without FILE_SHARE_DELETE, as a scanner or sync client
    opens one, makes Windows refuse the rename over the save. Nothing here is
    simulated but the moment the other program lets go."""
    importer = load_module()
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    child("module.stage_import(Path(sys.argv[3]), target)\n", target, source)
    handle = open(target, "rb")
    replace = os.replace
    refusals = []

    def scanner_lets_go(source, destination):
        try:
            return replace(source, destination)
        except OSError as error:
            if Path(destination) == target and not handle.closed:
                refusals.append(error)
                handle.close()
            raise

    monkeypatch.setattr(importer.os, "replace", scanner_lets_go)
    try:
        assert importer.commit_pending_import(target, deadline=time.monotonic() + 30) is True
    finally:
        handle.close()
    assert refusals and importer._is_file_lock_error(refusals[0])
    assert names(target) == ["incoming"]
