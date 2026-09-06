"""Regression coverage for PR #797's final transfer review.

Use real SQLite files, backups and atomic replacement. Only the Anki host/UI
and external writers are controlled at the boundary.
"""

import os
import sqlite3
import sys
import time
from concurrent.futures import Future
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from test_save_transfer import _Logger, _make_save, st
from Ankimon.functions import mobile_sync
from Ankimon.pyobj import ankimon_sync, backup_manager
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
    monkeypatch.setattr(st, "close_anki", MagicMock())
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
                              backups=tmp_path / "ankimon_backups")
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


def test_import_does_not_copy_a_source_changed_after_verification(transfer, monkeypatch):
    backup = transfer.sync._backup_before_overwrite

    def backup_during_download(required):
        result = backup(required)
        transfer.incoming.write_bytes(b"corrupted during backup" * 100)
        return result

    monkeypatch.setattr(transfer.sync, "_backup_before_overwrite", backup_during_download)
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
        with sqlite3.connect(str(incoming)) as conn:
            assert conn.execute("SELECT value FROM metadata WHERE key='mobile_revlog_watermark'").fetchone()[0] == str(foreign_watermark)
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
        for callback in callbacks:
            callback()
    else:
        assert st.import_save() is False
    assert transfer.active.read_bytes() == before
    assert list(transfer.snapshots.iterdir()) == []


@pytest.mark.parametrize("failure", ["decline", "backup", "replace"])
def test_unsuccessful_import_releases_snapshot_and_preserves_live_save(transfer, monkeypatch, failure):
    before = transfer.active.read_bytes()
    if failure == "decline":
        monkeypatch.setattr(st, "askUser", lambda *a, **k: False)
    elif failure == "backup":
        monkeypatch.setattr(transfer.sync, "_backup_before_overwrite", lambda *a: False)
    else:
        def disk_failure(*args):
            raise OSError("cannot replace destination")
        monkeypatch.setattr(transfer.sync, "_atomic_replace", disk_failure)
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
