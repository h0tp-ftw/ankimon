"""Regression coverage for PR #850's recovery scheduling and menu failures."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import sys
from types import SimpleNamespace

import pytest

from test_save_transfer import _Logger, _make_save, st
from test_save_transfer_safety import transfer


@pytest.fixture
def media_host(transfer, tmp_path, monkeypatch):
    """Queue workers explicitly while retaining the real profile sync guard."""
    media = tmp_path / "collection.media"
    media.mkdir()
    queued = []
    pm = SimpleNamespace(profile={}, profileFolder=lambda: str(tmp_path),
                         media_syncing_enabled=lambda: True, save=lambda: None)
    monkeypatch.setattr(st.mw, "pm", pm)
    monkeypatch.setattr(st, "_MIGRATION_SCAN_STATE", {"running": False, "rerun": False})
    monkeypatch.setattr(st.mw.taskman, "run_in_background",
                        lambda work, done, **kwargs: queued.append((work, done)))

    def finish():
        work, done = queued.pop(0)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(work)
            future.result()
        done(future)

    return SimpleNamespace(media=media, pm=pm, queued=queued, finish=finish)


@pytest.mark.parametrize("readable", [False, True])
def test_preservation_io_runs_on_worker_with_sync_guard(transfer, media_host, monkeypatch, readable):
    """Neither SQLite copying nor full raw ZIP writes may run on profile-open."""
    host = media_host
    source = host.media / "ankimon.db"
    if readable:
        _make_save(source, pokemon=2)
    else:
        source.write_bytes(b"unreadable database" * 100)
    gui_thread = threading.get_ident()
    preserve = st._protect_bare_saves
    seen = []

    def checked_preserve(media):
        seen.append(threading.get_ident())
        assert threading.get_ident() != gui_thread
        assert host.pm.media_syncing_enabled() is False
        return preserve(media)

    monkeypatch.setattr(st, "_protect_bare_saves", checked_preserve)
    st.start_media_migration(None, _Logger())
    assert not seen
    assert host.pm.media_syncing_enabled() is False
    host.finish()
    assert len(seen) == 1
    assert host.pm.media_syncing_enabled() is True
    copies = list(st._recovery_store(host.media).iterdir())
    assert len(copies) == 1
    assert copies[0].suffix == (".db" if readable else ".zip")


def test_unchanged_unreadable_save_has_retry_delay_but_changes_rearm(transfer, media_host, monkeypatch):
    """Repeated sync stops cannot repeatedly archive the same corrupt file."""
    source = media_host.media / "ankimon.db"
    source.write_bytes(b"corrupt" * 100)
    now = [100.0]
    monkeypatch.setattr(st.time, "monotonic", lambda: now[0])
    st.start_media_migration(None, _Logger())
    media_host.finish()
    for _ in range(5):
        st.start_media_migration(None, _Logger())
    assert media_host.queued == []
    assert media_host.pm.media_syncing_enabled() is True

    now[0] += 61
    st.start_media_migration(None, _Logger())
    assert len(media_host.queued) == 1
    media_host.finish()
    source.write_bytes(b"different corrupt save" * 100)
    st.start_media_migration(None, _Logger())
    assert len(media_host.queued) == 1
    assert media_host.pm.media_syncing_enabled() is False
    media_host.finish()


def test_dispatch_failure_keeps_guard_and_can_retry(transfer, media_host, monkeypatch):
    """A stopped executor leaves original files protected by the sync gate."""
    source = _make_save(media_host.media / "ankimon.db", pokemon=17)
    enqueue = st.mw.taskman.run_in_background

    def refused(*args, **kwargs):
        raise RuntimeError("executor stopped")

    monkeypatch.setattr(st.mw.taskman, "run_in_background", refused)
    st.start_media_migration(None, _Logger())
    assert media_host.pm.media_syncing_enabled() is False
    assert st.get_db_stats(source)["pokemon"] == 17
    assert not st._recovery_store(media_host.media).exists()
    assert "paused" in st.showWarning.call_args.args[0].lower()
    monkeypatch.setattr(st.mw.taskman, "run_in_background", enqueue)
    st.start_media_migration(None, _Logger())
    media_host.finish()
    assert media_host.pm.media_syncing_enabled() is True


def test_removing_uncaptured_save_releases_guard_on_previously_settled_folder(
    transfer, media_host, monkeypatch,
):
    """Moving a failed save elsewhere must not leave media sync paused forever."""
    st.start_media_migration(None, _Logger())
    media_host.finish()
    assert st._migration_done()
    source = _make_save(media_host.media / "ankimon.db", pokemon=17)

    def refused(*args, **kwargs):
        raise RuntimeError("executor stopped")

    monkeypatch.setattr(st.mw.taskman, "run_in_background", refused)
    st.start_media_migration(None, _Logger())
    assert media_host.pm.media_syncing_enabled() is False
    source.rename(source.parent.parent / "moved-save.db")
    st.start_media_migration(None, _Logger())
    assert media_host.pm.media_syncing_enabled() is True


def test_cancel_menu_reports_manifest_lock_without_losing_pending_import(transfer, monkeypatch):
    """A locked manifest keeps the staged save and produces an actionable warning."""
    from Ankimon import save_import

    info = save_import.stage_import(transfer.incoming, transfer.active)
    unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path.name == "pending.json":
            raise PermissionError("manifest locked")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    st.cancel_pending_save_import()
    assert save_import.pending_import_info(transfer.active)["token"] == info["token"]
    assert "could not be cancelled" in st.showWarning.call_args.args[0]
    assert "try again" in st.showWarning.call_args.args[0]


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_browse_tightens_existing_recovery_directory(transfer, monkeypatch):
    """Opening the browser cannot leave local credential backups world-readable."""

    recovery = transfer.active.parent / "ankimon_recovery"
    recovery.mkdir(mode=0o755)
    opened = []
    monkeypatch.setattr(sys.modules["aqt.utils"], "openFolder", lambda path: opened.append(path), raising=False)
    st.browse_recovered_saves()
    assert recovery.stat().st_mode & 0o777 == 0o700
    assert opened == [str(recovery)]
