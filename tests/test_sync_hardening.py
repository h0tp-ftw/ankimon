"""Tests for the save-file primitives kept after the AnkiWeb file-sync removal.

The automatic file-sync these once also covered is gone (see
``pyobj/ankimon_sync.py``'s docstring), and the tests that drove
``save_configs`` / ``read_configs`` / ``force_sync_*`` went with it. What remains
is the hardening those bugs taught us, which the manual Export/Import and the
one-shot media migration now depend on:

* ``AnkimonDB.set_mobile_watermark`` is monotonic (never regresses) unless forced;
* the integrity check rejects a corrupt, truncated or foreign DB;
* the atomic replace swaps the file, holds quiescence across ``os.replace``,
  clears stale WAL sidecars, aborts when the live DB will not drain, and
  survives a transient Windows file lock (issue #636);
* ``BackupManager.create_backup`` isolates per-file failures and reports failure
  for the file a caller actually depends on, so a failed backup can refuse a
  destructive overwrite.

See ``test_save_transfer.py`` for the Export/Import and migration paths built on
top of these.

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




# --------------------------------------------------------------------------
# Hook registration: mobile-review replay survives, file-sync is gone
# --------------------------------------------------------------------------
class _Hook:
    """Minimal stand-in for a gui_hooks hook list."""

    def __init__(self):
        self.handlers = []

    def append(self, fn):
        self.handlers.append(fn)

    def remove(self, fn):
        if fn in self.handlers:
            self.handlers.remove(fn)


def _isolated_registration(monkeypatch):
    """Fake gui_hooks + a private services registry.

    ``setup_ankimon_sync_hooks`` re-imports ``..services`` on every call, and
    other test modules swap that module in ``sys.modules``, so reading the
    registry through this file's own import binding is order-dependent. Giving
    the function a private module to import makes these tests hermetic.
    """
    hooks = types.SimpleNamespace(sync_did_finish=_Hook(), sync_will_start=_Hook())
    monkeypatch.setattr(aksync, "gui_hooks", hooks)

    registry = types.ModuleType("Ankimon.services")
    registry.services = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "Ankimon.services", registry)
    return hooks, registry.services


def test_only_the_mobile_hook_is_registered(monkeypatch):
    """The whole point of the removal: mobile-review replay keeps its
    ``sync_did_finish`` handler, and NOTHING is attached to ``sync_will_start``
    any more — that hook existed only to stage ankimon.db into collection.media
    for the AnkiWeb file-sync."""
    hooks, _ = _isolated_registration(monkeypatch)

    aksync.setup_ankimon_sync_hooks(MagicMock(), _Logger())

    assert len(hooks.sync_did_finish.handlers) == 1
    assert hooks.sync_will_start.handlers == []


def test_re_registration_does_not_stack_a_second_mobile_handler(monkeypatch):
    """Reload safety (F31): a second boot in one Anki session — the branch
    self-updater reloading add-on code, or any re-run of register_profile_hooks
    — must not stack a second on_sync_did_finish, which would double the dual-DB
    queueing pass and, in auto mode, fire resolveAll() twice per sync."""
    hooks, _ = _isolated_registration(monkeypatch)

    aksync.setup_ankimon_sync_hooks(MagicMock(), _Logger())
    first = hooks.sync_did_finish.handlers[0]
    aksync.setup_ankimon_sync_hooks(MagicMock(), _Logger())

    assert len(hooks.sync_did_finish.handlers) == 1
    assert hooks.sync_did_finish.handlers[0] is not first   # swapped, not stacked


def test_a_stale_pre_removal_two_hook_record_is_unregistered(monkeypatch):
    """An add-on reload from a PRE-removal version left a 2-tuple under the same
    services key, including a ``sync_will_start`` handler bound to a module whose
    file-sync functions no longer exist. Re-registering must find and remove it —
    which only works because the record key string was kept identical."""
    hooks, registry = _isolated_registration(monkeypatch)

    stale_start, stale_finish = (lambda: None), (lambda: None)
    hooks.sync_will_start.append(stale_start)
    hooks.sync_did_finish.append(stale_finish)
    setattr(registry, aksync._SYNC_HOOK_RECORD, (
        (hooks.sync_will_start, stale_start),
        (hooks.sync_did_finish, stale_finish),
    ))

    aksync.setup_ankimon_sync_hooks(MagicMock(), _Logger())

    assert hooks.sync_will_start.handlers == []          # stale export half gone
    assert len(hooks.sync_did_finish.handlers) == 1
    assert hooks.sync_did_finish.handlers[0] is not stale_finish
