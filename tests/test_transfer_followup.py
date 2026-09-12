"""Real-file regressions for the follow-up safety review of #797."""
import os
import sqlite3
import zipfile
from pathlib import Path
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from test_save_transfer import _make_save, _Logger, st, _protected
from test_save_transfer_safety import transfer


@pytest.mark.parametrize("failure", [None, "redaction", "publication"])
def test_export_never_places_secrets_in_destination(transfer, tmp_path, monkeypatch, failure):
    secret = b"synthetic-secret-destination-observer"
    with sqlite3.connect(transfer.active) as conn:
        conn.execute("INSERT INTO config VALUES ('leaderboard.api_key', ?)", (secret.decode(),))
    destination = tmp_path / "shared"
    destination.mkdir()
    exported = destination / "portable.db"
    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", lambda *a, **k: (str(exported), ""))
    observed = []

    def observe():
        for path in destination.iterdir():
            if path.is_file():
                observed.append(path.read_bytes())

    backup, redact, replace = st._sqlite_backup, st._strip_local_secrets, os.replace

    def snapshot(*args, **kwargs):
        backup(*args, **kwargs)
        observe()

    def sanitise(path):
        observe()
        if failure == "redaction":
            raise OSError("injected redaction failure")
        redact(path)
        observe()

    def publish(src, dst):
        observe()
        if failure == "publication" and Path(dst) == exported:
            raise OSError("injected publication failure")
        replace(src, dst)
        observe()

    monkeypatch.setattr(st, "_sqlite_backup", snapshot)
    monkeypatch.setattr(st, "_strip_local_secrets", sanitise)
    monkeypatch.setattr(st.os, "replace", publish)
    assert st.export_save() is (failure is None)
    assert all(secret not in content for content in observed)
    assert secret in transfer.active.read_bytes()
    assert list(destination.iterdir()) == ([exported] if failure is None else [])


def test_export_statistics_describe_snapshot_after_picker(transfer, tmp_path, monkeypatch):
    dest = tmp_path / "export.db"

    def picker(*args):
        with sqlite3.connect(transfer.active) as conn:
            conn.execute("INSERT INTO captured_pokemon VALUES ('during-picker', 0, '{}')")
        return str(dest), ""

    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", picker)
    assert st.export_save()
    assert st.get_db_stats(dest)["pokemon"] == 4
    assert "Pokemon: 4" in st.showInfo.call_args.args[0]


@pytest.mark.parametrize("change", ["replace", "delete"])
def test_both_bare_saves_are_guarded_until_worker_captures_them(transfer, tmp_path, monkeypatch, change):
    media = tmp_path / "collection.media"
    media.mkdir()
    for name in ("ankimon.db", "ankimonDEV.db"):
        _make_save(media / name, pokemon=10, name=name)
    monkeypatch.setattr(st, "_media_dir", lambda: media)
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    monkeypatch.setattr(st, "_MIGRATION_SCAN_STATE", {"running": False, "rerun": False})
    pm = SimpleNamespace(profileFolder=lambda: str(tmp_path), media_syncing_enabled=lambda: True)
    monkeypatch.setattr(st.mw, "pm", pm)
    work = []
    monkeypatch.setattr(st.mw.taskman, "run_in_background", lambda scan, done, **kw: work.append((scan, done)))
    st.start_media_migration(None, _Logger())
    assert pm.media_syncing_enabled() is False
    assert _protected(media) == []
    assert len(work) == 1
    scan, done = work[0]
    future = Future()
    future.set_result(scan())
    assert pm.media_syncing_enabled() is False
    done(future)
    assert pm.media_syncing_enabled() is True
    for name in ("ankimon.db", "ankimonDEV.db"):
        (media / name).unlink()
        if change == "replace":
            _make_save(media / name, pokemon=1)
        copies = _protected(media, name)
        assert any(st.get_db_stats(path)["pokemon"] == 10 for path in copies)
    assert len(work) == 1


def test_verified_recovery_copies_stay_outside_anki_media_sync(transfer, tmp_path):
    media = tmp_path / "collection.media"
    media.mkdir()
    source = _make_save(media / "ankimon.db", pokemon=7)
    with sqlite3.connect(source) as conn:
        conn.execute(
            "INSERT INTO config VALUES ('leaderboard.api_key', 'local-private-key')"
        )

    result = st._protect_bare_saves(media)

    protected = result["protected"][source]
    assert protected.parent == st._recovery_store(media)
    assert protected.parent != media
    assert not list(media.glob("_ankimon_save_*.db"))
    with sqlite3.connect(protected) as conn:
        assert conn.execute(
            "SELECT value FROM config WHERE key='leaderboard.api_key'"
        ).fetchone()[0] == "local-private-key"


def test_media_preservation_captures_wal(transfer, tmp_path):
    media = tmp_path / "collection.media"
    media.mkdir()
    source = _make_save(media / "ankimon.db", pokemon=3)
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        with writer:
            writer.execute("INSERT INTO captured_pokemon VALUES ('wal-only', 0, '{}')")
        protected = st._preserve(source, media, source.name, [], [], [])
        assert protected is not None
        assert st.get_db_stats(protected)["pokemon"] == 4
    finally:
        writer.close()


def test_existing_digest_copy_must_verify_before_being_reported_protected(transfer, tmp_path):
    media = tmp_path / "collection.media"
    media.mkdir()
    source = _make_save(media / "ankimon.db", pokemon=17)
    snapshot = tmp_path / "copied.db"
    st._sqlite_backup(source, snapshot)
    recovery = st._recovery_store(media, create=True)
    existing = recovery / st._protected_copy_name(source.name, st._content_digest(snapshot))
    existing.write_bytes(b"damaged existing save")

    protection = st._protect_bare_saves(media)

    numbered = existing.with_name(f"{existing.stem}-1.db")
    assert protection["protected"] == {source: numbered}
    assert protection["unprotected"] == []
    assert st.get_db_stats(numbered)["pokemon"] == 17
    assert existing.read_bytes() == b"damaged existing save"


def test_locked_media_archive_is_never_claimed_verified(transfer, tmp_path, monkeypatch):
    media = tmp_path / "collection.media"
    media.mkdir()
    source = _make_save(media / "ankimon.db", pokemon=17)
    original = source.read_bytes()
    writer = sqlite3.connect(source)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        monkeypatch.setattr(st, "MIGRATION_PROBE_TIMEOUT", 0.01)
        result = st._protect_bare_saves(media)
        assert result["protected"] == {}
        assert result["unprotected"] == [source]
        assert len(result["archives"]) == 1
        assert result["archives"][0].parent == st._recovery_store(media)
        assert not list(media.glob("_ankimon_unverified_*.zip"))
        with zipfile.ZipFile(result["archives"][0]) as archive:
            assert archive.read("ankimon.db") == original
        monkeypatch.setattr(st, "_LAST_PROTECTION_NOTICE", None)
        st._report_protection(result, _Logger())
        assert "unverified" in st.showWarning.call_args.args[0]
    finally:
        writer.rollback()
        writer.close()


def test_damaged_existing_raw_archive_is_not_reported_as_a_retained_copy(transfer, tmp_path, monkeypatch):
    media = tmp_path / "collection.media"
    media.mkdir()
    source = _make_save(media / "ankimon.db", pokemon=17)
    original = source.read_bytes()
    writer = sqlite3.connect(source)
    writer.execute("BEGIN EXCLUSIVE")
    monkeypatch.setattr(st, "MIGRATION_PROBE_TIMEOUT", 0.01)
    try:
        first = st._protect_bare_saves(media)
        damaged = first["archives"][0]
        damaged.write_bytes(b"damaged archive")

        retried = st._protect_bare_saves(media)

        assert len(retried["archives"]) == 1
        assert retried["archives"][0] != damaged
        assert damaged.read_bytes() == b"damaged archive"
        with zipfile.ZipFile(retried["archives"][0]) as archive:
            assert archive.read("ankimon.db") == original
    finally:
        writer.rollback()
        writer.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unreadable-file reproduction")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root bypasses the mode bits this test relies on")
def test_uncaptured_original_pauses_only_its_profile_and_successful_retry_restores_sync(
    transfer, tmp_path, monkeypatch,
):
    profiles = [tmp_path / "profile-a", tmp_path / "profile-b"]
    for path in profiles:
        (path / "collection.media").mkdir(parents=True)
    active_profile = [profiles[0]]
    preferences = {"syncMedia": True, "autoSync": True}
    pm = SimpleNamespace(
        profile=preferences,
        profileFolder=lambda: str(active_profile[0]),
        media_syncing_enabled=lambda: preferences["syncMedia"],
    )
    monkeypatch.setattr(st.mw, "pm", pm)
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    monkeypatch.setattr(st, "_LAST_PROTECTION_NOTICE", None)
    monkeypatch.setattr(st, "_MIGRATION_SCAN_STATE", {"running": False, "rerun": False})
    callbacks = []
    monkeypatch.setattr(st.mw.taskman, "run_in_background", lambda scan, done, **kw: callbacks.append((scan, done)))
    source = _make_save(profiles[0] / "collection.media" / "ankimon.db", pokemon=17)
    original_mode = source.stat().st_mode
    source.chmod(0)
    try:
        st.start_media_migration(None, _Logger())
        assert pm.media_syncing_enabled() is False
        guarded_method = pm.media_syncing_enabled
        assert preferences == {"syncMedia": True, "autoSync": True}
        active_profile[0] = profiles[1]
        assert pm.media_syncing_enabled() is True
        active_profile[0] = profiles[0]
        assert pm.media_syncing_enabled() is False

        scan, done = callbacks.pop(0)
        failed = Future()
        failed.set_result(scan())
        done(failed)
        assert pm.media_syncing_enabled() is False
        assert any("Media sync is paused" in call.args[0] for call in st.showWarning.call_args_list)
    finally:
        source.chmod(original_mode)

    st.start_media_migration(None, _Logger())
    assert pm.media_syncing_enabled is guarded_method
    assert pm.media_syncing_enabled() is False
    scan, done = callbacks.pop(0)
    recovered = Future()
    recovered.set_result(scan())
    done(recovered)
    assert pm.media_syncing_enabled() is True
    assert preferences == {"syncMedia": True, "autoSync": True}
    preferences["syncMedia"] = False
    assert pm.media_syncing_enabled() is False


def test_dispatch_failure_guards_files_without_running_scan(transfer, tmp_path, monkeypatch):
    media = tmp_path / "collection.media"
    media.mkdir()
    _make_save(media / "ankimon.db", pokemon=17)
    monkeypatch.setattr(st, "_media_dir", lambda: media)
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    monkeypatch.setattr(st, "_MIGRATION_SCAN_STATE", {"running": False, "rerun": False})

    def refused(*args, **kwargs):
        raise RuntimeError("executor stopped")

    def forbidden(*args, **kwargs):
        pytest.fail("comparison ran on GUI thread")

    monkeypatch.setattr(st.mw.taskman, "run_in_background", refused)
    monkeypatch.setattr(st, "_migration_scan", forbidden)
    st.start_media_migration(None, _Logger())
    assert st.get_db_stats(media / "ankimon.db")["pokemon"] == 17
    assert _protected(media) == []
    assert media in st.mw.pm._ankimon_media_protection_guard["blocked"]
    assert "retried" in st.showWarning.call_args.args[0]


@pytest.mark.parametrize("shutdown", ["keep_editing", "exception", "sync"])
def test_import_keeps_old_runtime_on_original_database(transfer, monkeypatch, shutdown):
    def queued_old_write(*args):
        with sqlite3.connect(transfer.active) as conn:
            conn.execute("UPDATE config SET value='Old runtime' WHERE key='trainer.name'")

    monkeypatch.setattr(st, "showInfo", queued_old_write)

    def close(**kwargs):
        if shutdown == "exception":
            raise RuntimeError("shutdown failed")
        if shutdown == "sync":
            with sqlite3.connect(transfer.active) as conn:
                conn.execute("INSERT INTO captured_pokemon VALUES ('shutdown-sync', 0, '{}')")
        # Keep Editing never invokes Anki's completion callback.

    monkeypatch.setattr(st, "close_anki", close)
    st.import_save()
    assert st.get_db_stats(transfer.active)["pokemon"] == (4 if shutdown == "sync" else 3)
    assert st.get_db_stats(transfer.active)["trainer_name"] == "Old runtime"
    from Ankimon.save_import import pending_import_info
    pending = pending_import_info(transfer.active)
    assert pending is not None
    assert st.get_db_stats(pending["pending_path"])["pokemon"] == 42
    assert st.get_db_stats(pending["pending_path"])["trainer_name"] == "Offered"


def test_import_without_config_cannot_resurrect_legacy_local_credentials(transfer, tmp_path, monkeypatch):
    from Ankimon.pyobj import settings as settings_module
    from Ankimon.pyobj.ankimon_sync import AnkimonDataSync
    from Ankimon.pyobj.database_manager import AnkimonDB
    from Ankimon.services import services

    transfer.incoming.unlink()
    incoming_db = AnkimonDB(_Logger(), db_path=transfer.incoming)
    incoming_db.close()
    with sqlite3.connect(transfer.incoming) as conn:
        conn.execute("DROP TABLE config")
    (tmp_path / "config.obf").write_text(AnkimonDataSync()._obfuscate_data({
        "leaderboard.username": "LegacyLocalAccount",
        "leaderboard.api_key": "synthetic-legacy-local-api-key",
    }))
    monkeypatch.setattr(settings_module, "user_path", tmp_path)

    assert st.import_save()
    imported_db = AnkimonDB(_Logger(), db_path=transfer.active)
    monkeypatch.setattr(services, "db", imported_db)
    try:
        loaded = settings_module.Settings()
        assert loaded.get("leaderboard.username") == ""
        assert loaded.get("leaderboard.api_key") == ""
    finally:
        imported_db.close()
