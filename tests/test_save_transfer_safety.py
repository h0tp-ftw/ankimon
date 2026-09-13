"""Regression coverage for PR #797's final transfer review.

Use real SQLite files, backups and atomic replacement. Only the Anki host/UI
and external writers are controlled at the boundary.
"""

import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from concurrent.futures import Future
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from test_save_transfer import _Logger, _make_save, st
from test_save_import import commit_in_new_process
from Ankimon.functions import mobile_sync
from Ankimon.pyobj import ankimon_sync, backup_manager
from Ankimon.pyobj import settings as settings_module
from Ankimon.pyobj.database_manager import AnkimonDB
from Ankimon.services import services


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    # Other suites swap these modules. Pin the real primitives and this
    # fixture's registry for the production functions' lazy imports as well.
    registry = ModuleType("Ankimon.services")
    registry.services = services
    monkeypatch.setitem(sys.modules, "Ankimon.services", registry)
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.ankimon_sync", ankimon_sync)
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.backup_manager", backup_manager)
    active = _make_save(tmp_path / "ankimon.db", pokemon=3, name="Local")
    incoming = _make_save(tmp_path / "incoming.db", pokemon=42, name="Offered")
    collection = sqlite3.connect(":memory:")
    collection.execute("CREATE TABLE revlog(id INTEGER, cid INTEGER, ease INTEGER, time INTEGER, type INTEGER)")
    col = SimpleNamespace(db=SimpleNamespace(
        scalar=lambda sql: collection.execute(sql).fetchone()[0],
        all=lambda sql, *args: collection.execute(sql, args).fetchall(),
    ))
    monkeypatch.setattr(services, "db", None)
    monkeypatch.setattr(services, "col", col)
    monkeypatch.setattr(services, "logger", _Logger())
    monkeypatch.setattr(services, "settings", SimpleNamespace(get=lambda key, default=None: default))
    monkeypatch.setattr(st, "_active_db_path", lambda: active)
    monkeypatch.setattr(st, "showInfo", MagicMock())
    monkeypatch.setattr(st, "showWarning", MagicMock())
    def restart(**kwargs):
        if services.db is not None:
            services.db.close()
        commit_in_new_process(active)
    monkeypatch.setattr(st, "close_anki", restart)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: True)
    monkeypatch.setattr(st.QFileDialog, "getOpenFileName", lambda *a, **k: (str(incoming), ""))
    monkeypatch.setattr(backup_manager, "user_path", tmp_path)
    monkeypatch.setattr(backup_manager, "addon_dir", tmp_path / "addon")
    sync = ankimon_sync.AnkimonDataSync()
    monkeypatch.setattr(ankimon_sync, "get_ankimon_sync", lambda: sync)
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    monkeypatch.setattr(st.tempfile, "tempdir", str(snapshots))
    try:
        yield SimpleNamespace(active=active, incoming=incoming, sync=sync,
                              collection=collection, col=col, snapshots=snapshots,
                              backups=tmp_path / "ankimon_recovery")
    finally:
        collection.close()


def test_import_installs_the_save_shown_even_if_source_changes_in_dialog(transfer, tmp_path, monkeypatch):
    replacement = _make_save(tmp_path / "download.db", pokemon=1, name="Different")

    def confirm(prompt, **kwargs):
        assert "Pokemon: 42" in prompt and "Trainer: Offered" in prompt
        os.replace(replacement, transfer.incoming)
        return True

    monkeypatch.setattr(st, "askUser", confirm)
    assert st.import_save()
    assert st.get_db_stats(transfer.active)["pokemon"] == 42
    assert st.get_db_stats(transfer.incoming)["pokemon"] == 1
    backups = list(transfer.backups.glob("*/ankimon.db"))
    assert len(backups) == 1 and st.get_db_stats(backups[0])["pokemon"] == 3
    assert list(transfer.snapshots.iterdir()) == []


@pytest.mark.parametrize("unreadable_media", [False, True])
def test_sync_removal_notice_stays_dismissed_after_saving_settings(
    transfer, tmp_path, monkeypatch, unreadable_media,
):
    media = tmp_path / "collection.media"
    media.mkdir()
    if unreadable_media:
        (media / "_old_ankimon.db").write_bytes(b"unreadable save")
    monkeypatch.setattr(st, "_media_dir", lambda: media)
    monkeypatch.setattr(st.mw.pm, "profile", {}, raising=False)
    monkeypatch.setattr(st.mw.pm, "save", lambda: None, raising=False)
    monkeypatch.setattr(settings_module, "services", services)
    monkeypatch.setattr(settings_module, "user_path", tmp_path)
    notices = []
    monkeypatch.setattr(st, "showInfo", notices.append)
    db = AnkimonDB(_Logger(), db_path=tmp_path / "settings.db")
    monkeypatch.setattr(services, "db", db)
    try:
        db.set_config_value("misc.ankiweb_sync", True)
        db.set_config_value("trainer.name", "Local")
        settings = settings_module.Settings()
        monkeypatch.setattr(services, "settings", settings)
        cached_config = settings.config
        assert cached_config["misc.ankiweb_sync"] in (True, "true")

        st.run_media_migration(settings, services.logger)
        assert len(notices) == 1
        # An empty folder settles on its examined-empty fingerprint; a folder
        # holding a file that will not open stays armed to retry it.
        assert st._migration_done() is not unreadable_media
        assert db.get_config_value("misc.ankiweb_sync", None) is None

        # The web settings screen saves this same live dictionary in full.
        settings.save_config(settings.config, explicit_overrides=set())
        monkeypatch.setattr(services, "settings", settings_module.Settings())
        st.run_media_migration(services.settings, services.logger)
        # A settled profile runs no further pass, so also ask the notice
        # directly: the row must be gone from the database, not merely unread.
        st._notify_affected_user(services.logger)

        assert len(notices) == 1
        assert settings.config is cached_config
        assert "misc.ankiweb_sync" not in cached_config
        assert db.get_config_value("misc.ankiweb_sync", None) is None
        assert settings.config["trainer.name"] == "Local"
    finally:
        db.close()


@pytest.mark.parametrize("with_config", [False, True])
def test_export_removes_legacy_credentials_without_changing_live_save(
    transfer, tmp_path, monkeypatch, with_config,
):
    connection = sqlite3.connect(transfer.active)
    try:
        with connection:
            connection.execute("CREATE TABLE user_data (key TEXT PRIMARY KEY, value TEXT)")
            connection.executemany("INSERT INTO user_data VALUES (?, ?)", [
                ("api_key", '"legacy-secret-for-export-test"'),
                ("username", '"Ash"'),
            ])
            if with_config:
                connection.execute("INSERT INTO config VALUES ('leaderboard.api_key', 'config-secret-for-export-test')")
            else:
                connection.execute("DROP TABLE config")
    finally:
        connection.close()
    before = transfer.active.read_bytes()
    exported = tmp_path / "portable.db"
    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", lambda *a, **k: (str(exported), ""))

    assert st.export_save()

    connection = sqlite3.connect(exported)
    try:
        assert dict(connection.execute("SELECT * FROM user_data")) == {"username": '"Ash"'}
        if with_config:
            assert connection.execute("SELECT value FROM config WHERE key='leaderboard.api_key'").fetchone() is None
    finally:
        connection.close()
    assert b"legacy-secret-for-export-test" not in exported.read_bytes()
    assert b"config-secret-for-export-test" not in exported.read_bytes()
    assert transfer.active.read_bytes() == before
    assert st.get_db_stats(exported)["pokemon"] == 3
    assert list(transfer.snapshots.iterdir()) == []


def test_import_does_not_copy_a_source_changed_after_verification(transfer, monkeypatch):
    from Ankimon import save_import
    stage = save_import.stage_import

    def stage_during_download(snapshot, target):
        result = stage(snapshot, target)
        transfer.incoming.write_bytes(b"corrupted after staging" * 100)
        return result

    monkeypatch.setattr(save_import, "stage_import", stage_during_download)
    assert st.import_save()
    assert ankimon_sync._verify_sqlite_integrity(transfer.active)
    assert st.get_db_stats(transfer.active)["pokemon"] == 42
    assert list(transfer.snapshots.iterdir()) == []


def test_rescue_display_matches_a_download_that_lands_during_scan(transfer, tmp_path, monkeypatch):
    media = tmp_path / "collection.media"
    media.mkdir()
    bare = _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    replacement = _make_save(tmp_path / "download.db", pokemon=1)
    read_stats = st.get_db_stats

    def read_then_download(path, **kwargs):
        result = read_stats(path, **kwargs)
        if Path(path) == bare and replacement.exists():
            os.replace(replacement, bare)
        return result

    monkeypatch.setattr(st, "get_db_stats", read_then_download)
    result = st._migration_scan(media, transfer.active)
    prompts = []
    monkeypatch.setattr(st, "askUser", lambda prompt, **kwargs: prompts.append(prompt) or True)
    monkeypatch.setattr(st.mw.progress, "single_shot", lambda ms, fn, *args: fn())
    st._apply_migration_result(result, _Logger())
    # If the scan offers the original 42-Pokemon save, it must install that
    # snapshot. If it sees the new 1-Pokemon save, no rescue is warranted.
    assert st.get_db_stats(transfer.active)["pokemon"] == (42 if prompts else 3)
    assert list(transfer.snapshots.iterdir()) == []


@pytest.fixture
def rescue(transfer, tmp_path, monkeypatch):
    media = tmp_path / "collection.media"
    media.mkdir()
    _make_save(media / "ankimon.db", pokemon=4, name="Incoming")
    callbacks, workers, prompts, profile = [], [], [], {}
    monkeypatch.setattr(st, "_media_dir", lambda: media)
    monkeypatch.setattr(st, "_MIGRATION_SCAN_STATE", {"running": False, "rerun": False})
    monkeypatch.setattr(st, "_notify_affected_user", lambda logger: None)
    monkeypatch.setattr(st.mw.pm, "profile", profile, raising=False)
    monkeypatch.setattr(st.mw.pm, "save", lambda: None, raising=False)
    monkeypatch.setattr(st.mw.progress, "single_shot", lambda ms, fn, *a: callbacks.append(fn))
    monkeypatch.setattr(st.mw.taskman, "run_in_background",
                        lambda work, done, **kw: workers.append((work, done)))
    monkeypatch.setattr(st, "askUser", lambda prompt, **kw: prompts.append(prompt) or True)

    def finish_workers():
        while workers:
            work, done = workers.pop(0)
            future = Future()
            future.set_result(work())
            done(future)

    return SimpleNamespace(media=media, callbacks=callbacks, workers=workers,
                           prompts=prompts, profile=profile, finish_workers=finish_workers)


def test_rescue_rechecks_local_progress_before_showing_worker_result(transfer, rescue):
    result = st._migration_scan(rescue.media, transfer.active)
    connection = sqlite3.connect(transfer.active)
    try:
        with connection:
            connection.executemany("INSERT INTO captured_pokemon VALUES (?, 0, '{}')",
                                   [(f"mobile-{i}",) for i in range(5)])
    finally:
        connection.close()

    st._apply_migration_result(result, _Logger())
    rescue.finish_workers()

    assert rescue.prompts == []
    assert rescue.callbacks == []
    assert st.get_db_stats(transfer.active)["pokemon"] == 8
    assert list(transfer.snapshots.iterdir()) == []


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
@pytest.mark.parametrize("change", ["ahead", "same_counts"])
def test_deferred_rescue_invalidates_approval_when_local_save_changes(
    transfer, rescue, monkeypatch, journal_mode, change,
):
    writer = sqlite3.connect(transfer.active)
    try:
        writer.execute(f"PRAGMA journal_mode={journal_mode}")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        st._apply_migration_result(st._migration_scan(rescue.media, transfer.active), _Logger())
        rescue.finish_workers()
        assert len(rescue.callbacks) == 1
        with writer:
            if change == "ahead":
                writer.executemany("INSERT INTO captured_pokemon VALUES (?, 0, '{}')",
                                   [(f"mobile-{i}",) for i in range(5)])
            else:
                writer.execute("UPDATE captured_pokemon SET data='new battle progress'")
        # A fresh comparison may still offer rescue, but the earlier yes must
        # never authorize replacing this changed save.
        monkeypatch.setattr(st, "askUser", lambda prompt, **kw: rescue.prompts.append(prompt) or False)
        rescue.callbacks.pop(0)()
        rescue.finish_workers()
        assert st.get_db_stats(transfer.active)["pokemon"] == (8 if change == "ahead" else 3)
        if change == "same_counts":
            assert len(rescue.prompts) == 2
            assert writer.execute("SELECT data FROM captured_pokemon LIMIT 1").fetchone()[0] == "new battle progress"
        else:
            assert len(rescue.prompts) == 1
        assert not transfer.backups.exists()
        assert list(transfer.snapshots.iterdir()) == []
    finally:
        writer.close()


def test_rescue_recovery_includes_progress_after_staging(transfer, rescue, monkeypatch):
    st._apply_migration_result(st._migration_scan(rescue.media, transfer.active), _Logger())
    assert len(rescue.callbacks) == 1

    def finish_old_session(**kwargs):
        with sqlite3.connect(transfer.active) as conn:
            conn.executemany("INSERT INTO captured_pokemon VALUES (?, 0, '{}')",
                             [(f"mobile-{i}",) for i in range(5)])
        commit_in_new_process(transfer.active)

    monkeypatch.setattr(st, "close_anki", finish_old_session)
    rescue.callbacks.pop(0)()
    assert st.get_db_stats(transfer.active)["pokemon"] == 4
    backups = list(transfer.backups.glob("*/ankimon.db"))
    assert len(backups) == 1 and st.get_db_stats(backups[0])["pokemon"] == 8
    assert list(transfer.snapshots.iterdir()) == []


def test_rescue_accepts_unchanged_progress_after_backup_checkpoints_wal(
    transfer, rescue, monkeypatch,
):
    transfer.active.unlink()
    db = AnkimonDB(_Logger(), db_path=transfer.active, wal=True)
    monkeypatch.setattr(services, "db", db)
    try:
        db.set_config_value("trainer.cash", 250)
        assert Path(str(transfer.active) + "-wal").stat().st_size > 0
        st._apply_migration_result(st._migration_scan(rescue.media, transfer.active), _Logger())
        rescue.finish_workers()
        assert len(rescue.callbacks) == 1
        rescue.callbacks.pop(0)()

        assert st.get_db_stats(transfer.active)["pokemon"] == 4
        assert len(rescue.prompts) == 1
        assert not rescue.workers
        assert list(transfer.snapshots.iterdir()) == []
    finally:
        db.close()


def test_rescue_rechecks_progress_after_waiting_for_active_writers(
    transfer, rescue, monkeypatch,
):
    st._apply_migration_result(st._migration_scan(rescue.media, transfer.active), _Logger())
    assert len(rescue.callbacks) == 1
    quiesce = transfer.sync._quiesce_live_db_connection

    @contextmanager
    def finish_background_writer(target):
        connection = sqlite3.connect(target)
        try:
            with connection:
                connection.executemany("INSERT INTO captured_pokemon VALUES (?, 0, '{}')",
                                       [(f"mobile-{i}",) for i in range(5)])
        finally:
            connection.close()
        with quiesce(target) as closed:
            yield closed

    monkeypatch.setattr(transfer.sync, "_quiesce_live_db_connection", finish_background_writer)
    rescue.callbacks.pop(0)()
    rescue.finish_workers()

    assert st.get_db_stats(transfer.active)["pokemon"] == 8
    assert len(rescue.prompts) == 1
    assert list(transfer.snapshots.iterdir()) == []


def test_rescue_does_not_remember_a_decline_for_changed_local_progress(
    transfer, rescue, monkeypatch,
):
    def decline_during_progress(prompt, **kwargs):
        rescue.prompts.append(prompt)
        if len(rescue.prompts) == 1:
            connection = sqlite3.connect(transfer.active)
            try:
                with connection:
                    connection.execute("UPDATE config SET value='250' WHERE key='trainer.cash'")
            finally:
                connection.close()
        return False

    monkeypatch.setattr(st, "askUser", decline_during_progress)
    st._apply_migration_result(st._migration_scan(rescue.media, transfer.active), _Logger())
    rescue.finish_workers()

    assert len(rescue.prompts) == 2
    assert "Cash: 250" in rescue.prompts[1].split("ON THIS COMPUTER", 1)[1]
    assert st.get_db_stats(transfer.active)["pokemon"] == 3
    assert st._migration_done()
    assert list(transfer.snapshots.iterdir()) == []


def test_deferred_rescue_keeps_its_snapshot_when_media_changes(transfer, tmp_path, monkeypatch):
    media = tmp_path / "collection.media"
    media.mkdir()
    legacy = _make_save(media / "_addons21_ankimon.db", pokemon=42, badges=8, history=99)
    callbacks = []
    monkeypatch.setattr(st.mw.progress, "single_shot", lambda ms, fn, *args: callbacks.append(fn))
    result = st._migration_scan(media, transfer.active)
    st._apply_migration_result(result, _Logger())
    assert len(callbacks) == 1
    legacy.write_bytes(b"download replaced source" * 100)
    callbacks[0]()
    assert st.get_db_stats(transfer.active)["pokemon"] == 42
    assert list(transfer.snapshots.iterdir()) == []


@pytest.mark.parametrize("accept", [False, True])
def test_export_confirms_the_normalized_existing_destination(transfer, tmp_path, monkeypatch, accept):
    dest = _make_save(tmp_path / "archive.db", pokemon=99)
    before = dest.read_bytes()
    selected = tmp_path / "archive.txt"
    prompts = []
    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", lambda *a, **k: (str(selected), ""))
    monkeypatch.setattr(st, "askUser", lambda prompt, **kwargs: prompts.append(prompt) or accept)
    assert st.export_save() is accept
    assert len(prompts) == 1 and str(dest) in prompts[0]
    if accept:
        assert st.get_db_stats(dest)["pokemon"] == 3
    else:
        assert dest.read_bytes() == before
    assert not selected.exists()


@pytest.mark.parametrize("during_snapshot", [False, True])
def test_export_refuses_an_archive_with_unrecovered_wal(transfer, tmp_path, monkeypatch, during_snapshot):
    dest = _make_save(tmp_path / "archive.db", pokemon=99, name="Archive")
    before = {}

    def leave_unrecovered_wal():
        # Abrupt exit keeps committed WAL pages without any open connection.
        # Replacing just archive.db would replay this old trainer over Local.
        subprocess.run([sys.executable, "-c", """
import os, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA wal_autocheckpoint=0')
conn.execute("UPDATE config SET value='Stale WAL' WHERE key='trainer.name'")
conn.commit()
os._exit(0)
""", str(dest)], check=True, timeout=10)
        assert Path(str(dest) + "-wal").stat().st_size > 0
        before.update((path, path.read_bytes()) for path in tmp_path.glob("archive.db*"))

    if during_snapshot:
        backup = st._sqlite_backup

        def backup_then_external_write(*args, **kwargs):
            backup(*args, **kwargs)
            leave_unrecovered_wal()

        monkeypatch.setattr(st, "_sqlite_backup", backup_then_external_write)
    else:
        leave_unrecovered_wal()
    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), ""))
    active_before = transfer.active.read_bytes()

    assert st.export_save() is False
    assert {path: path.read_bytes() for path in tmp_path.glob("archive.db*")} == before
    assert transfer.active.read_bytes() == active_before
    assert list(tmp_path.glob("ankimon-export-*")) == []
    st.showInfo.assert_not_called()
    st.showWarning.assert_called_once()


@pytest.mark.parametrize("suffix", ["-shm", "-journal"])
def test_export_leaves_other_destination_sidecars_untouched(transfer, tmp_path, monkeypatch, suffix):
    dest = _make_save(tmp_path / "archive.db", pokemon=99)
    before = dest.read_bytes()
    sidecar = Path(str(dest) + suffix)
    sidecar.write_bytes(b"unfinished database state")
    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), ""))

    assert st.export_save() is False
    assert dest.read_bytes() == before
    assert sidecar.read_bytes() == b"unfinished database state"
    assert list(tmp_path.glob("ankimon-export-*")) == []


@pytest.mark.parametrize("foreign_watermark", [1000, 9000])
def test_import_rebases_watermark_and_only_queues_future_reviews(transfer, tmp_path, monkeypatch, foreign_watermark):
    incoming = tmp_path / "real-save.db"
    foreign = AnkimonDB(_Logger(), db_path=str(incoming))
    foreign.set_mobile_watermark(foreign_watermark, force=True)
    foreign.close()
    monkeypatch.setattr(st.QFileDialog, "getOpenFileName", lambda *a, **k: (str(incoming), ""))
    transfer.collection.executemany("INSERT INTO revlog VALUES (?, ?, 3, 1000, 1)",
                                   [(i, i) for i in range(1001, 1041)])
    assert st.import_save()
    db = AnkimonDB(_Logger(), db_path=str(transfer.active))
    monkeypatch.setattr(services, "db", db)
    try:
        mobile_sync.clear_desktop_session()
        assert db.get_mobile_watermark() == 1040
        settings = SimpleNamespace(get=lambda key, default=None: default)
        assert mobile_sync.process_mobile_reviews_after_sync(transfer.col, db, settings, _Logger()) == 0
        transfer.collection.execute("INSERT INTO revlog VALUES (1041, 1041, 3, 1000, 1)")
        assert mobile_sync.process_mobile_reviews_after_sync(transfer.col, db, settings, _Logger()) == 1
        assert db.get_pending_mobile_count() == 1
        conn = sqlite3.connect(str(incoming))
        try:
            assert conn.execute("SELECT value FROM metadata WHERE key='mobile_revlog_watermark'").fetchone()[0] == str(foreign_watermark)
        finally:
            conn.close()
    finally:
        db.close()
    assert list(transfer.snapshots.iterdir()) == []


def test_import_leaves_live_save_untouched_when_collection_cannot_be_read(transfer, monkeypatch):
    before = transfer.active.read_bytes()
    def unavailable(sql):
        raise RuntimeError("collection closed")
    monkeypatch.setattr(transfer.col.db, "scalar", unavailable)
    assert st.import_save() is False
    assert transfer.active.read_bytes() == before
    assert list(transfer.snapshots.iterdir()) == []


@pytest.mark.parametrize("rescue", [False, True])
def test_confirmation_cannot_follow_a_profile_switch(transfer, tmp_path, monkeypatch, rescue):
    before = transfer.active.read_bytes()
    callbacks = []
    monkeypatch.setattr(st.mw.progress, "single_shot", lambda ms, fn, *args: callbacks.append(fn))

    def change_profile(prompt, **kwargs):
        # Ankimon's save path is installation-wide; the collection identifies
        # the destination profile even when the active DB pathname is unchanged.
        services.col = SimpleNamespace(db=SimpleNamespace(scalar=lambda sql: 9999))
        return True

    monkeypatch.setattr(st, "askUser", change_profile)
    if rescue:
        media = tmp_path / "collection.media"
        media.mkdir()
        _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
        st._apply_migration_result(st._migration_scan(media, transfer.active), _Logger())
        assert len(callbacks) == 1
        for callback in callbacks:
            callback()
    else:
        assert st.import_save() is False
    assert transfer.active.read_bytes() == before
    assert list(transfer.snapshots.iterdir()) == []


@pytest.mark.parametrize("failure", ["decline", "staging"])
def test_unsuccessful_import_releases_snapshot_and_preserves_live_save(transfer, monkeypatch, failure):
    before = transfer.active.read_bytes()
    if failure == "decline":
        monkeypatch.setattr(st, "askUser", lambda *a, **k: False)
    else:
        def disk_failure(*args):
            raise OSError("cannot prepare destination")
        monkeypatch.setattr("Ankimon.save_import.stage_import", disk_failure)
    assert st.import_save() is False
    assert transfer.active.read_bytes() == before
    assert list(transfer.snapshots.iterdir()) == []


def test_import_snapshot_includes_committed_wal_pages(transfer):
    writer = sqlite3.connect(str(transfer.incoming))
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO captured_pokemon VALUES ('wal-pokemon', 0, '{}')")
        writer.commit()
        assert Path(str(transfer.incoming) + "-wal").is_file()
        assert st.import_save()
        assert st.get_db_stats(transfer.active)["pokemon"] == 43
        assert st.get_db_stats(transfer.incoming)["pokemon"] == 43
    finally:
        writer.close()
    assert list(transfer.snapshots.iterdir()) == []


def test_discarded_scan_releases_its_snapshot_after_collection_change(transfer, tmp_path, monkeypatch):
    media = tmp_path / "collection.media"
    media.mkdir()
    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    monkeypatch.setattr(st, "_media_dir", lambda: media)
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    monkeypatch.setattr(st, "_MIGRATION_SCAN_STATE", {"running": False, "rerun": False})
    pending = []
    monkeypatch.setattr(st.mw.taskman, "run_in_background",
                        lambda work, done, **kwargs: pending.append((work, done)))
    st.start_media_migration(services.settings, services.logger)
    work, done = pending.pop()
    future = Future()
    future.set_result(work())
    assert list(transfer.snapshots.iterdir())
    services.col = SimpleNamespace(db=SimpleNamespace(scalar=lambda sql: 9000))
    done(future)
    assert list(transfer.snapshots.iterdir()) == []
    assert st.get_db_stats(transfer.active)["pokemon"] == 3


def test_busy_snapshot_obeys_migration_budget_and_cleans_up(transfer):
    writer = sqlite3.connect(str(transfer.incoming))
    try:
        writer.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            st._snapshot_save(transfer.incoming, timeout=0.05)
        assert time.monotonic() - started < 1.5
    finally:
        writer.rollback()
        writer.close()
    assert list(transfer.snapshots.iterdir()) == []


def test_import_published_but_unfinished_is_reported_as_pending(transfer, monkeypatch):
    """A staged import that will install must never be announced as aborted."""
    from Ankimon import save_import

    fsync_directory = save_import._fsync_directory
    injecting = [True]

    def failing_sync(path):
        if injecting[0] and Path(path) == transfer.active.parent:
            raise OSError("injected directory sync failure")
        return fsync_directory(path)

    monkeypatch.setattr(save_import, "_fsync_directory", failing_sync)
    closed = []
    monkeypatch.setattr(st, "close_anki", lambda **kwargs: closed.append(kwargs))

    assert st.import_save() is True
    injecting[0] = False
    message = st.showWarning.call_args.args[0]
    assert "PENDING" in message
    assert "Cancel Pending Save Import" in message
    assert "aborted" not in message.lower()
    assert "unchanged" not in message.lower()
    # Anki is not closed on our own initiative after an I/O failure, but the
    # warning's claim is real: the save is armed for the next full start.
    assert closed == []
    assert st.get_db_stats(transfer.active)["pokemon"] == 3
    commit_in_new_process(transfer.active)
    assert st.get_db_stats(transfer.active)["pokemon"] == 42


def test_backup_restore_published_but_unfinished_is_reported_as_pending(transfer, monkeypatch, tmp_path):
    """Backup Restore shares the staging path and must share its honesty."""
    from Ankimon import save_import

    backup_dir = tmp_path / "backup_2026-01-01_00-00-00"
    backup_dir.mkdir()
    _make_save(backup_dir / transfer.active.name, pokemon=11, name="Restored")
    manager = backup_manager.BackupManager(_Logger(), SimpleNamespace(get=lambda *a, **k: None))
    monkeypatch.setattr(services, "db", SimpleNamespace(db_path=transfer.active))
    warn = MagicMock()
    monkeypatch.setattr(backup_manager, "showWarning", warn)
    monkeypatch.setattr(backup_manager, "showInfo", MagicMock())
    monkeypatch.setattr(backup_manager, "askUser", lambda *a, **k: True)
    monkeypatch.setattr(backup_manager, "close_anki", MagicMock())

    fsync_directory = save_import._fsync_directory
    injecting = [True]

    def failing_sync(path):
        if injecting[0] and Path(path) == transfer.active.parent:
            raise OSError("injected directory sync failure")
        return fsync_directory(path)

    monkeypatch.setattr(save_import, "_fsync_directory", failing_sync)
    manager.restore_backup(str(backup_dir))
    injecting[0] = False

    message = warn.call_args.args[0]
    assert "PENDING" in message
    assert "Cancel Pending Save Import" in message
    assert "Failed to prepare" not in message
    # Like Import, the restore does not close Anki on its own after an I/O
    # failure; the user decides when to restart.
    backup_manager.close_anki.assert_not_called()
    commit_in_new_process(transfer.active)
    assert st.get_db_stats(transfer.active)["pokemon"] == 11


def test_backup_restore_that_cannot_announce_itself_is_not_called_a_failure(
    transfer, monkeypatch, tmp_path,
):
    """Staging succeeded outright here, so "failed to prepare" is simply false."""
    backup_dir = tmp_path / "backup_2026-02-02_00-00-00"
    backup_dir.mkdir()
    _make_save(backup_dir / transfer.active.name, pokemon=13, name="Restored")
    manager = backup_manager.BackupManager(_Logger(), SimpleNamespace(get=lambda *a, **k: None))
    monkeypatch.setattr(services, "db", SimpleNamespace(db_path=transfer.active))
    warn = MagicMock()
    monkeypatch.setattr(backup_manager, "showWarning", warn)
    monkeypatch.setattr(backup_manager, "askUser", lambda *a, **k: True)
    monkeypatch.setattr(backup_manager, "close_anki", MagicMock())

    def broken_notice(*args, **kwargs):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    monkeypatch.setattr(backup_manager, "showInfo", broken_notice)
    manager.restore_backup(str(backup_dir))

    assert "Failed to prepare backup restore" not in str(warn.call_args_list)
    # And the restore really is staged, whatever the notice did.
    commit_in_new_process(transfer.active)
    assert st.get_db_stats(transfer.active)["pokemon"] == 13


def test_import_that_cannot_announce_itself_is_not_called_an_abort(transfer, monkeypatch):
    """The notice is the last thing that can fail, and it is not the import."""
    def broken_notice(*args, **kwargs):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    monkeypatch.setattr(st, "showInfo", broken_notice)
    warn = MagicMock()
    monkeypatch.setattr(st, "showWarning", warn)

    assert st.import_save() is True
    assert "aborted" not in str(warn.call_args_list).lower()
    assert "Nothing was replaced" not in str(warn.call_args_list)
    assert st.get_db_stats(transfer.active)["pokemon"] == 42


def test_a_rescue_that_cannot_quiet_the_save_says_why_nothing_happened(transfer):
    """The user said yes. A silent False left no rescue, no message and no reason,
    and the same question came back at the next launch."""
    from Ankimon import save_import

    @contextmanager
    def still_busy(target):
        yield False

    transfer.sync._quiesce_live_db_connection = still_busy
    digest = st._save_snapshot_digest(transfer.active)
    assert st._replace_active_save(transfer.incoming, transfer.active, "Rescue",
                                   collection=transfer.col, local_digest=digest) is False
    message = st.showWarning.call_args.args[0]
    assert message.startswith("Rescue aborted:")
    assert "did not stop in time" in message
    # No menu action starts a rescue; the next scan offers it again.
    assert "offered again after the next sync or restart" in message
    assert save_import.pending_import_info(transfer.active) is None
