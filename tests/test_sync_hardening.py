"""Tests for the mobile-sync + file-sync hardening.

Covers the robustness additions layered on the mobile-sync decoupling fix:
* ``AnkimonDB.set_mobile_watermark`` is monotonic (never regresses) unless forced;
* ``AnkimonDataSync`` import safety: integrity-check rejects a corrupt/foreign
  media DB, the atomic replace swaps the file and clears stale WAL sidecars, the
  overwrite is refused if a safety backup can't be made, and the manual import
  reports failure (not a false success) when nothing was actually imported.

Tier-1 house pattern: stub ``aqt``/``anki``/``PyQt6`` in ``sys.modules`` so the
real add-on modules import Qt-free, then drive the real code.
"""

import os
import sys
import atexit
import types
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"

for _name in (
    "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations",
    "aqt.theme", "aqt.sound", "aqt.webview", "aqt.main",
    "anki", "anki.hooks", "anki.collection", "anki.utils",
    "PyQt6", "PyQt6.QtGui", "PyQt6.QtWidgets", "PyQt6.QtCore",
    "PyQt6.QtWebChannel", "PyQt6.QtWebEngineWidgets",
):
    sys.modules.setdefault(_name, MagicMock())

for _pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj", "Ankimon.ankimon_items_web"):
    _existing = sys.modules.get(_pkg)
    if _existing is None or not hasattr(_existing, "__path__"):
        _mod = types.ModuleType(_pkg)
        _mod.__path__ = [str(_SRC / _pkg.replace(".", "/"))]
        _mod.__package__ = _pkg
        sys.modules[_pkg] = _mod

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_USER_DIR = Path(tempfile.mkdtemp(prefix="ankimon_hardening_ut_"))
os.environ.setdefault("ANKIMON_USER_PATH", str(_USER_DIR))
atexit.register(shutil.rmtree, _USER_DIR, ignore_errors=True)

from Ankimon.services import services  # noqa: E402
from Ankimon.pyobj.database_manager import AnkimonDB  # noqa: E402
from Ankimon.pyobj.ankimon_sync import AnkimonDataSync  # noqa: E402
import Ankimon.pyobj.ankimon_sync as aksync  # noqa: E402
from Ankimon.functions import mobile_sync as ms  # noqa: E402

# backup_manager imports askUser at module top; another test module late in a
# full-suite run may have swapped the aqt.utils stub for one lacking it, so make
# sure the current stub carries the names it needs, then import BackupManager
# once here (a stable reference immune to later sys.modules churn).
import aqt.utils as _aqt_utils  # noqa: E402
for _n in ("showInfo", "showWarning", "tooltip", "askUser"):
    if not hasattr(_aqt_utils, _n):
        setattr(_aqt_utils, _n, MagicMock())
from Ankimon.pyobj.backup_manager import BackupManager  # noqa: E402


class _Logger:
    def log(self, *a, **k): pass
    def game_log(self, *a, **k): pass
    def log_and_showinfo(self, *a, **k): pass


@pytest.fixture
def db(tmp_path):
    prev = services.db
    d = AnkimonDB(_Logger(), db_path=str(tmp_path / "ankimon.db"))
    services.db = d
    ms.clear_desktop_session()
    try:
        yield d
    finally:
        services.db = prev
        ms.clear_desktop_session()
        try:
            d.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# never-regress watermark
# --------------------------------------------------------------------------
def test_watermark_is_monotonic(db):
    db.set_mobile_watermark(100)
    assert db.get_mobile_watermark() == 100

    db.set_mobile_watermark(50)          # backwards — must be clamped
    assert db.get_mobile_watermark() == 100

    db.set_mobile_watermark(250)         # forwards — must advance
    assert db.get_mobile_watermark() == 250


def test_watermark_force_allows_reset(db):
    db.set_mobile_watermark(500)
    db.set_mobile_watermark(10, force=True)   # explicit reset escape hatch
    assert db.get_mobile_watermark() == 10


# --------------------------------------------------------------------------
# AnkimonDataSync import safety
# --------------------------------------------------------------------------
def _make_ankimon_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE captured_pokemon (id INTEGER, data TEXT)")
    conn.execute("INSERT INTO captured_pokemon VALUES (1, 'x')")
    conn.commit()
    conn.close()


def test_integrity_accepts_valid_ankimon_db(tmp_path):
    p = tmp_path / "good.db"
    _make_ankimon_db(p)
    assert AnkimonDataSync._verify_sqlite_integrity(p) is True


def test_integrity_accepts_valid_db_under_path_with_spaces(tmp_path):
    """A profile path with spaces/unicode (common on Windows) must not make the
    read-only URI fail to open a perfectly valid DB."""
    d = tmp_path / "John Doe" / "collección média"
    d.mkdir(parents=True)
    p = d / "ankimon.db"
    _make_ankimon_db(p)
    assert AnkimonDataSync._verify_sqlite_integrity(p) is True


def test_integrity_rejects_zero_byte(tmp_path):
    p = tmp_path / "empty.db"
    p.write_bytes(b"")
    assert AnkimonDataSync._verify_sqlite_integrity(p) is False


def test_integrity_rejects_non_database(tmp_path):
    p = tmp_path / "junk.db"
    p.write_bytes(b"this is not a sqlite database" + b"\x00" * 600)
    assert AnkimonDataSync._verify_sqlite_integrity(p) is False


def test_integrity_rejects_db_without_core_table(tmp_path):
    p = tmp_path / "foreign.db"
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE notes (id INTEGER)")
    conn.execute("INSERT INTO notes VALUES (1)")
    conn.commit()
    conn.close()
    assert AnkimonDataSync._verify_sqlite_integrity(p) is False


def test_atomic_replace_swaps_file_and_clears_stale_sidecars(tmp_path, monkeypatch):
    prev = services.db
    services.db = None  # so _close_live_db_connection is a no-op
    # Capture the ACTUAL temp path used: _synctmp_path prefers a randomized
    # system-temp file on the same volume, so a hard-coded sibling ".synctmp"
    # assertion would be vacuously true — this checks the path actually used.
    created = _spy_synctmp(monkeypatch)
    try:
        src = tmp_path / "ankimon.db"
        src.write_bytes(b"OLD" + b"\x00" * 600)
        media = tmp_path / "media.db"
        media.write_bytes(b"NEW" + b"\x00" * 600)
        # Stale WAL sidecars from the OLD db must be removed, or a reopen would
        # try to replay them over the new file.
        (tmp_path / "ankimon.db-wal").write_bytes(b"waldata")
        (tmp_path / "ankimon.db-shm").write_bytes(b"shmdata")

        AnkimonDataSync()._atomic_replace(media, src)

        assert src.read_bytes().startswith(b"NEW")
        assert not (tmp_path / "ankimon.db-wal").exists()
        assert not (tmp_path / "ankimon.db-shm").exists()
        assert created and all(not t.exists() for t in created)   # temp cleaned
    finally:
        services.db = prev


def test_atomic_replace_holds_quiescence_through_os_replace(tmp_path, monkeypatch):
    import contextlib
    import importlib

    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"
    media.write_bytes(b"REMOTE" + b"\x00" * 600)

    class QuiescingDB:
        db_path = src
        inside = False
        entered = 0
        exited = 0

        @contextlib.contextmanager
        def quiesce(self, wait_seconds=0.0):
            self.entered += 1
            self.inside = True
            try:
                yield True
            finally:
                self.inside = False
                self.exited += 1

    runtime_services = importlib.import_module("Ankimon.services").services
    prev = runtime_services.db
    fake_db = QuiescingDB()
    runtime_services.db = fake_db
    original_replace = aksync.os.replace

    def checked_replace(source, destination):
        assert fake_db.inside, "database lifecycle barrier released before os.replace"
        return original_replace(source, destination)

    monkeypatch.setattr(aksync.os, "replace", checked_replace)
    try:
        AnkimonDataSync()._atomic_replace(media, src)
    finally:
        runtime_services.db = prev

    assert src.read_bytes().startswith(b"REMOTE")
    assert fake_db.entered == 1
    assert fake_db.exited == 1
    assert fake_db.inside is False


def test_atomic_replace_aborts_when_live_db_does_not_drain(tmp_path):
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"
    media.write_bytes(b"REMOTE" + b"\x00" * 600)

    class BusyDB:
        db_path = src

        def close(self, wait_seconds=0.0):
            return False

    import importlib

    runtime_services = importlib.import_module("Ankimon.services").services
    prev = runtime_services.db
    runtime_services.db = BusyDB()
    try:
        with pytest.raises(RuntimeError, match="active operations did not finish"):
            AnkimonDataSync()._atomic_replace(media, src)
    finally:
        runtime_services.db = prev

    assert src.read_bytes().startswith(b"LOCAL")


def _wire_read_configs(ds, monkeypatch, src, media):
    monkeypatch.setattr(ds, "_migrate_legacy_files", lambda: [])
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: media)


def test_import_aborts_when_backup_fails(tmp_path, monkeypatch):
    """The live save must NOT be overwritten if a safety backup can't be made
    (disk full / unwritable backup dir), symmetric with the integrity check."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"
    media.write_bytes(b"REMOTE" + b"\x00" * 600)

    ds = AnkimonDataSync()
    _wire_read_configs(ds, monkeypatch, src, media)
    monkeypatch.setattr(ds, "_verify_sqlite_integrity", lambda p: True)
    monkeypatch.setattr(ds, "_backup_before_overwrite", lambda *a: False)  # backup FAILS

    updated = ds.read_configs(media_sync_status=False)

    assert updated == []                            # nothing imported
    assert src.read_bytes().startswith(b"LOCAL")    # local save untouched


def test_import_proceeds_when_backup_succeeds(tmp_path, monkeypatch):
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"
    media.write_bytes(b"REMOTE" + b"\x00" * 600)

    ds = AnkimonDataSync()
    _wire_read_configs(ds, monkeypatch, src, media)
    monkeypatch.setattr(ds, "_verify_sqlite_integrity", lambda p: True)
    monkeypatch.setattr(ds, "_backup_before_overwrite", lambda *a: True)  # backup OK

    prev = services.db
    services.db = None  # _atomic_replace's _close_live_db_connection is a no-op
    try:
        updated = ds.read_configs(media_sync_status=False)
    finally:
        services.db = prev

    assert updated == ["ankimon.db"]
    assert src.read_bytes().startswith(b"REMOTE")   # imported atomically


def test_import_integrity_failure_aborts_before_backup(tmp_path, monkeypatch):
    """A corrupt media file is rejected BEFORE a backup is even attempted, and
    the local save is untouched."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"
    media.write_bytes(b"corrupt")   # < 512 bytes -> integrity check fails

    ds = AnkimonDataSync()
    _wire_read_configs(ds, monkeypatch, src, media)
    backup_attempts = []
    monkeypatch.setattr(
        ds, "_backup_before_overwrite", lambda *a: backup_attempts.append(1) or True
    )

    updated = ds.read_configs(media_sync_status=False)

    assert updated == []
    assert src.read_bytes().startswith(b"LOCAL")
    assert backup_attempts == []    # integrity gate short-circuits before backup


# --------------------------------------------------------------------------
# Manual "Import from AnkiWeb" reports its result truthfully
# --------------------------------------------------------------------------
def test_force_import_returns_false_when_nothing_imported(tmp_path, monkeypatch):
    """force_sync_from_media must return False when a corrupt media file is
    safety-rejected — else the caller claims 'imported successfully' and closes
    Anki despite nothing having been imported."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"
    media.write_bytes(b"corrupt")   # fails integrity

    ds = AnkimonDataSync()
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: media)

    assert ds.force_sync_from_media() is False
    assert src.read_bytes().startswith(b"LOCAL")


def test_force_import_returns_true_when_imported(tmp_path, monkeypatch):
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"
    media.write_bytes(b"REMOTE" + b"\x00" * 600)

    ds = AnkimonDataSync()
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: media)
    monkeypatch.setattr(ds, "_verify_sqlite_integrity", lambda p: True)
    monkeypatch.setattr(ds, "_backup_before_overwrite", lambda *a: True)

    prev = services.db
    services.db = None
    try:
        assert ds.force_sync_from_media() is True
    finally:
        services.db = prev
    assert src.read_bytes().startswith(b"REMOTE")


def test_force_import_returns_false_when_no_media_file(tmp_path, monkeypatch):
    """Nothing on AnkiWeb to import yet is a benign no-op: return False (the
    caller shows an informational message, not a scary traceback dialog) and
    leave the local save untouched."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"   # deliberately NOT created

    ds = AnkimonDataSync()
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: media)

    assert ds.force_sync_from_media() is False
    assert src.read_bytes().startswith(b"LOCAL")


# --------------------------------------------------------------------------
# BackupManager.create_backup: per-file isolation + required_file success
# --------------------------------------------------------------------------
def _make_backup_manager(tmp_path, monkeypatch):
    bm = BackupManager(_Logger(), MagicMock())
    monkeypatch.setattr(bm, "user_files_path", tmp_path)
    monkeypatch.setattr(bm, "backups_path", tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    monkeypatch.setattr(bm, "_generate_summary", lambda d: {})
    monkeypatch.setattr(bm, "cleanup_backups", lambda: None)
    return bm


def _flaky_copy_factory(fail_substr):
    real = shutil.copy2

    def flaky(src, dst, *a, **k):
        if fail_substr in str(src):
            raise OSError(f"simulated failure copying {src}")
        return real(src, dst, *a, **k)

    return flaky


def test_backup_required_file_success_isolated_from_other_file_failure(tmp_path, monkeypatch):
    """A failed ankimonDEV.db copy must NOT blank a successful ankimon.db backup
    — otherwise a perfectly safe import would be needlessly aborted."""
    (tmp_path / "ankimon.db").write_bytes(b"MAIN" + b"\x00" * 600)
    (tmp_path / "ankimonDEV.db").write_bytes(b"DEV" + b"\x00" * 600)
    bm = _make_backup_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(shutil, "copy2", _flaky_copy_factory("ankimonDEV.db"))

    prev = services.db
    services.db = None   # active-mode default is "ankimon.db"
    try:
        ok = bm.create_backup(manual=False, required_file="ankimon.db")
    finally:
        services.db = prev

    assert ok is True   # ankimon.db was backed up despite the DEV copy failing


def test_backup_returns_false_when_required_file_not_backed_up(tmp_path, monkeypatch):
    (tmp_path / "ankimon.db").write_bytes(b"MAIN" + b"\x00" * 600)
    bm = _make_backup_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(shutil, "copy2", _flaky_copy_factory("ankimon.db"))

    prev = services.db
    services.db = None
    try:
        ok = bm.create_backup(manual=False, required_file="ankimon.db")
    finally:
        services.db = prev

    assert ok is False   # the file we needed protected never landed in the backup


# --------------------------------------------------------------------------
# File-lock tolerance (issue #636): OneDrive/antivirus holding ankimon.db open
# --------------------------------------------------------------------------
def _raise_permission_error(*a, **k):
    """A stand-in for a file op that Windows blocks with PermissionError while
    another process (OneDrive/antivirus) holds the file open (WinError 5)."""
    raise PermissionError(5, "Access is denied")


def _no_sleep(monkeypatch):
    """Make the bounded backoff instant, and force the lock classification to the
    Windows answer (PermissionError / sharing-violation => transient lock) so the
    retry + friendly-message paths run even on Linux/CI.

    This patches ``_is_lock_error`` directly rather than flipping ``os.name`` to
    ``"nt"``: mutating the process-global ``os.name`` also flips ``pathlib.Path``
    to ``WindowsPath`` on POSIX, which mangles the system temp path
    (``/tmp`` -> ``\\tmp``) so ``os.stat`` fails and ``_synctmp_path`` silently
    falls back to a sibling — the system-temp branch (the actual issue-#636 fix)
    would then never be exercised, and the ``_spy_synctmp`` re-wrap would write a
    backslash-named temp into the repo CWD. ``test_is_lock_error_classification``
    keeps the real ``os.name``-gated coverage of ``_is_lock_error`` itself."""
    monkeypatch.setattr(aksync.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(
        aksync,
        "_is_lock_error",
        lambda exc: isinstance(exc, PermissionError)
        or (
            isinstance(exc, OSError)
            and getattr(exc, "winerror", None) in aksync._SYNC_LOCK_WINERRORS
        ),
    )


def _spy_synctmp(monkeypatch):
    """Record the ACTUAL temp paths ``_synctmp_path`` hands out, so a test can
    assert they were cleaned up. The real path is a randomized system-temp file
    on the same volume, so a hard-coded sibling ``.synctmp`` assertion would be
    vacuously true — this checks the path actually used."""
    created = []
    real = aksync._synctmp_path

    def spy(target):
        t = Path(real(target))
        created.append(t)
        return t

    monkeypatch.setattr(aksync, "_synctmp_path", spy)
    return created


def test_is_lock_error_classification(monkeypatch):
    # On Windows, a PermissionError / sharing-violation is a transient lock.
    monkeypatch.setattr(aksync.os, "name", "nt")
    assert aksync._is_lock_error(PermissionError()) is True
    for code in (5, 32, 33):
        e = OSError()
        e.winerror = code
        assert aksync._is_lock_error(e) is True, code
    # A non-lock OSError (e.g. file-not-found, winerror 2) must NOT be treated as
    # a lock — it should still reach the normal traceback handler.
    enoent = OSError()
    enoent.winerror = 2
    assert aksync._is_lock_error(enoent) is False
    assert aksync._is_lock_error(ValueError()) is False

    # On POSIX, os.replace does not fail on open handles, so a PermissionError is
    # a GENUINE permission problem (not a lock) and must fall through to the
    # traceback handler rather than the misleading "pause OneDrive" message.
    monkeypatch.setattr(aksync.os, "name", "posix")
    assert aksync._is_lock_error(PermissionError()) is False
    assert aksync._is_lock_error(PermissionError(5, "denied")) is False


def test_retry_on_lock_propagates_non_lock_error_without_retrying(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        raise FileNotFoundError(2, "missing")   # not a lock

    with pytest.raises(FileNotFoundError):
        aksync._retry_on_lock(op)
    assert calls["n"] == 1                       # propagated immediately, no retry


def test_retry_on_lock_recovers_after_transient_failures(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        if calls["n"] < 3:                       # locked twice, then released
            raise PermissionError(5, "Access is denied")
        return "ok"

    assert aksync._retry_on_lock(op) == "ok"
    assert calls["n"] == 3


def test_retry_on_lock_gives_up_after_exhausting_attempts(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        raise PermissionError(5, "Access is denied")

    with pytest.raises(PermissionError):
        aksync._retry_on_lock(op)
    assert calls["n"] == len(aksync._SYNC_LOCK_RETRY_DELAYS) + 1


def test_atomic_replace_recovers_from_transient_lock(tmp_path, monkeypatch):
    """A transient OneDrive/AV lock on os.replace must be retried into a success,
    not surfaced as an error — the whole point of issue #636's fix."""
    prev = services.db
    services.db = None
    _no_sleep(monkeypatch)
    created = _spy_synctmp(monkeypatch)
    try:
        src = tmp_path / "ankimon.db"
        src.write_bytes(b"OLD" + b"\x00" * 600)
        media = tmp_path / "media.db"
        media.write_bytes(b"NEW" + b"\x00" * 600)

        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(a, b, *ar, **k):
            calls["n"] += 1
            if calls["n"] < 3:                   # locked twice, then released
                raise PermissionError(5, "Access is denied")
            return real_replace(a, b, *ar, **k)

        monkeypatch.setattr(aksync.os, "replace", flaky_replace)

        AnkimonDataSync()._atomic_replace(media, src)

        assert src.read_bytes().startswith(b"NEW")   # swap eventually succeeded
        assert calls["n"] == 3
        assert created and all(not t.exists() for t in created)   # temp cleaned
    finally:
        services.db = prev


def test_import_persistent_lock_shows_tooltip_not_traceback(tmp_path, monkeypatch):
    """AUTOMATIC path (read_configs): a persisting lock must surface as a
    NON-blocking tooltip (self-heals next sync), never a raw traceback dialog,
    and must leave the local save untouched."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"
    media.write_bytes(b"REMOTE" + b"\x00" * 600)

    ds = AnkimonDataSync()
    _wire_read_configs(ds, monkeypatch, src, media)
    monkeypatch.setattr(ds, "_verify_sqlite_integrity", lambda p: True)
    monkeypatch.setattr(ds, "_backup_before_overwrite", lambda *a: True)
    _no_sleep(monkeypatch)
    monkeypatch.setattr(aksync.os, "replace", _raise_permission_error)

    tips, tracebacks = [], []
    monkeypatch.setattr(aksync, "tooltip", lambda *a, **k: tips.append(a))
    monkeypatch.setattr(aksync, "show_warning_with_traceback", lambda *a, **k: tracebacks.append(a))

    prev = services.db
    services.db = None
    try:
        updated = ds.read_configs(media_sync_status=False)
    finally:
        services.db = prev

    assert updated == []                             # nothing imported
    assert src.read_bytes().startswith(b"LOCAL")     # local save untouched
    assert len(tips) == 1                            # one friendly tooltip
    assert tracebacks == []                          # NO raw traceback dialog


def test_force_import_persistent_lock_shows_warning_not_traceback(tmp_path, monkeypatch):
    """MANUAL import: a persisting lock returns False + a single friendly modal,
    never a raw traceback, with the local save untouched."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    media = tmp_path / "media.db"
    media.write_bytes(b"REMOTE" + b"\x00" * 600)

    ds = AnkimonDataSync()
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: media)
    monkeypatch.setattr(ds, "_verify_sqlite_integrity", lambda p: True)
    monkeypatch.setattr(ds, "_backup_before_overwrite", lambda *a: True)
    _no_sleep(monkeypatch)
    monkeypatch.setattr(aksync.os, "replace", _raise_permission_error)

    warnings, tracebacks = [], []
    monkeypatch.setattr(aksync, "showWarning", lambda *a, **k: warnings.append(a))
    monkeypatch.setattr(aksync, "show_warning_with_traceback", lambda *a, **k: tracebacks.append(a))

    prev = services.db
    services.db = None
    try:
        assert ds.force_sync_from_media() is False
    finally:
        services.db = prev

    assert src.read_bytes().startswith(b"LOCAL")     # local save untouched
    assert len(warnings) == 1
    assert tracebacks == []


def test_force_export_writes_media_atomically(tmp_path, monkeypatch):
    """Happy-path export lands the file (via the new atomic temp+replace)."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    dest = tmp_path / "media_ankimon.db"            # parent (tmp_path) exists

    ds = AnkimonDataSync()
    monkeypatch.setattr(ds, "_ensure_sync_folder_exists", lambda: True)
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: dest)
    created = _spy_synctmp(monkeypatch)

    prev = services.db
    services.db = None
    try:
        assert ds.force_sync_to_media() is True
    finally:
        services.db = prev

    assert dest.read_bytes().startswith(b"LOCAL")
    assert created and all(not t.exists() for t in created)   # temp cleaned


def test_force_export_returns_false_when_no_local_data(tmp_path, monkeypatch):
    """No local source file => nothing to export: force_sync_to_media must return
    False (not a false 'Exported 0 files' success), so export_to_ankiweb doesn't
    enable auto-sync and close the dialog. Symmetric with the import side."""
    src = tmp_path / "ankimon.db"          # deliberately NOT created
    dest = tmp_path / "media_ankimon.db"

    ds = AnkimonDataSync()
    monkeypatch.setattr(ds, "_ensure_sync_folder_exists", lambda: True)
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: dest)

    prev = services.db
    services.db = None
    try:
        assert ds.force_sync_to_media() is False   # not a false success
    finally:
        services.db = prev

    assert not dest.exists()                        # nothing was exported


def test_force_export_persistent_lock_shows_warning_not_traceback(tmp_path, monkeypatch):
    """MANUAL export: a persisting lock returns False + one friendly modal, never
    a raw traceback (and no leftover temp)."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    dest = tmp_path / "media_ankimon.db"

    ds = AnkimonDataSync()
    monkeypatch.setattr(ds, "_ensure_sync_folder_exists", lambda: True)
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: dest)
    _no_sleep(monkeypatch)
    created = _spy_synctmp(monkeypatch)
    monkeypatch.setattr(aksync.os, "replace", _raise_permission_error)

    warnings, tracebacks = [], []
    monkeypatch.setattr(aksync, "showWarning", lambda *a, **k: warnings.append(a))
    monkeypatch.setattr(aksync, "show_warning_with_traceback", lambda *a, **k: tracebacks.append(a))

    prev = services.db
    services.db = None
    try:
        assert ds.force_sync_to_media() is False
    finally:
        services.db = prev

    assert len(warnings) == 1
    assert tracebacks == []
    assert not dest.exists()                          # nothing half-written landed
    assert created and all(not t.exists() for t in created)   # temp cleaned on failure


def test_save_configs_writes_media_atomically(tmp_path, monkeypatch):
    """Automatic pre-sync export (save_configs) stages the file atomically."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    dest = tmp_path / "media_ankimon.db"             # does not exist yet

    ds = AnkimonDataSync()
    monkeypatch.setattr(ds, "_migrate_legacy_files", lambda: [])
    monkeypatch.setattr(ds, "_ensure_sync_folder_exists", lambda: True)
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: dest)

    prev = services.db
    services.db = None
    try:
        synced = ds.save_configs()
    finally:
        services.db = prev

    assert synced == ["ankimon.db"]
    assert dest.read_bytes().startswith(b"LOCAL")


def test_save_configs_persistent_lock_shows_tooltip_not_traceback(tmp_path, monkeypatch):
    """AUTOMATIC pre-sync export: a persisting lock is a non-blocking tooltip and
    stages nothing — never a raw traceback on every sync."""
    src = tmp_path / "ankimon.db"
    src.write_bytes(b"LOCAL" + b"\x00" * 600)
    dest = tmp_path / "media_ankimon.db"

    ds = AnkimonDataSync()
    monkeypatch.setattr(ds, "_migrate_legacy_files", lambda: [])
    monkeypatch.setattr(ds, "_ensure_sync_folder_exists", lambda: True)
    monkeypatch.setattr(ds, "_get_source_path", lambda fn: src)
    monkeypatch.setattr(ds, "_get_media_path", lambda fn: dest)
    _no_sleep(monkeypatch)
    monkeypatch.setattr(aksync.os, "replace", _raise_permission_error)

    tips, tracebacks = [], []
    monkeypatch.setattr(aksync, "tooltip", lambda *a, **k: tips.append(a))
    monkeypatch.setattr(aksync, "show_warning_with_traceback", lambda *a, **k: tracebacks.append(a))

    prev = services.db
    services.db = None
    try:
        synced = ds.save_configs()
    finally:
        services.db = prev

    assert synced == []
    assert len(tips) == 1
    assert tracebacks == []
