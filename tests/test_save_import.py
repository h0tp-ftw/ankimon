"""Exercise staged imports across real process and SQLite boundaries."""

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
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
