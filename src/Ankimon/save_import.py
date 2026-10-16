"""Durable save imports applied before the next process builds its game state.

Staging never replaces a running session's database. A cancelled Anki close or
an add-on reload therefore leaves that session usable. The composition root
may commit pending work only before it opens any Ankimon database or creates
game objects. This module deliberately has no Anki, Qt, or services imports.
"""

from __future__ import annotations

import errno
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid


_TIMEOUT = 30.0
# get_db installs pending work during add-on import, which Anki runs inside
# AnkiQt.__init__ -- before setupProfile, before any window is shown and before
# a progress dialog exists. A locked save there is Anki looking hung with
# nothing on screen, so the WHOLE installation gets one budget, the way the
# shutdown backup does, rather than a full SQLite timeout per file per step.
STARTUP_IMPORT_BUDGET = 30.0
_PROCESS_ATTRIBUTE = "_ankimon_save_import_process"
_STAGING_ATTRIBUTE = "_ankimon_save_import_staging"
_OPERATION_ATTRIBUTE = "_ankimon_save_operation"


def _process_staging_lock():
    # Developer reload purges this module while its workers may still run.
    # Coordinate the old and new modules through sys, which survives that purge.
    # A fork must not reuse a lock possibly held by a vanished parent thread.
    state = getattr(sys, _STAGING_ATTRIBUTE, None)
    if state is None or state[0] != os.getpid():
        candidate = (os.getpid(), threading.RLock())
        state = vars(sys).setdefault(_STAGING_ATTRIBUTE, candidate)
        if state[0] != candidate[0]:
            state = candidate
            setattr(sys, _STAGING_ATTRIBUTE, state)
    return state[1]


_STAGING_LOCK = _process_staging_lock()


class SaveOperationBusyError(RuntimeError):
    """A save request cannot start without waiting for another operation."""


def _process_operation_state():
    state = getattr(sys, _OPERATION_ATTRIBUTE, None)
    if state is None or state["pid"] != os.getpid():
        candidate = {"pid": os.getpid(), "lock": threading.Lock(), "owner": None}
        state = vars(sys).setdefault(_OPERATION_ATTRIBUTE, candidate)
        if state["pid"] != candidate["pid"]:
            state = candidate
            setattr(sys, _OPERATION_ATTRIBUTE, state)
    return state


class _SaveOperationLease:
    def __init__(self, state):
        self._state = state
        self._owner = object()
        self._release_lock = threading.Lock()
        self._released = False

    def validate(self):
        with self._release_lock:
            if (
                self._released
                or self._state["pid"] != os.getpid()
                or self._state["owner"] is not self._owner
            ):
                raise RuntimeError("The save operation finished. Please confirm again.")

    def release(self):
        # Completion may arrive from both a worker and its GUI callback. An old
        # callback must never release a newer request, including after reload.
        with self._release_lock:
            if self._released:
                return
            self._released = True
            if (
                self._state["pid"] == os.getpid()
                and self._state["owner"] is self._owner
            ):
                self._state["owner"] = None
                self._state["lock"].release()


def reserve_save_operation():
    """Reserve a request before dialogs or dispatch, without waiting for disk I/O.

    The owner survives module purges and may finish on a different thread.
    Callers lending it to another entry point retain responsibility for release.
    """
    state = _process_operation_state()
    if not state["lock"].acquire(blocking=False):
        raise SaveOperationBusyError(
            "A save operation is already in progress. Please try again when it finishes."
        )
    lease = _SaveOperationLease(state)
    state["owner"] = lease._owner
    return lease


def save_operation_busy():
    return _process_operation_state()["lock"].locked()


class ImportInstalledError(RuntimeError):
    """The imported save is active, but its final sync or cleanup failed."""


class ImportAlreadyPendingError(RuntimeError):
    """Another import is staged for this save and has not been cancelled."""


class ImportStagedError(RuntimeError):
    """The import is published and WILL install, but staging did not finish.

    Publishing ``pending.json`` is the commit point: after it, the next full
    start installs the save whether or not the durability and read-back steps
    that follow succeed. Callers must not report that as an abort — the user
    would keep playing believing a replacement they were told had failed
    cannot happen. Cancelling is the only way to stop it.
    """

    def __init__(self, message: str, token: str):
        super().__init__(message)
        self.token = token


class ImportUnsafeToOpenError(RuntimeError):
    """A pending import's save is missing, and journals are still beside it.

    Opening a database at that path creates a fresh save, and SQLite discards
    journals it finds beside a database with no pages. They may hold the only
    remaining copy of the missing save's progress, so nothing may open that
    path until the install has set them aside or the user has moved them.
    """


class SaveDamagedError(ValueError):
    """A save failed SQLite's integrity check."""


class CurrentSaveDamagedError(ValueError):
    """The save an install would replace failed SQLite's integrity check.

    The install keeps a verified copy of that save before replacing it, and a
    damaged save cannot give one, so restarting does not change the answer. Only
    an import staged with ``retain_unverified``, which the user chose after being
    told, installs over it, keeping the save as it is instead.
    """


def _process_identity() -> str:
    # sys survives add-on module purges. Include the PID so a subprocess/fork
    # cannot inherit the parent's identity while a reload keeps its identity.
    identity = getattr(sys, _PROCESS_ATTRIBUTE, None)
    if identity is None or identity[0] != os.getpid():
        identity = (os.getpid(), uuid.uuid4().hex)
        setattr(sys, _PROCESS_ATTRIBUTE, identity)
    return f"{identity[0]}:{identity[1]}"


def _paths(target: Path) -> tuple[Path, Path]:
    target = Path(target).resolve()
    return target, target.parent / f".ankimon-import-{target.name}"


def _serialize_staging(operation):
    @wraps(operation)
    def serialized(*args, **kwargs):
        with _process_staging_lock():
            return operation(*args, **kwargs)

    return serialized


def _fsync_file(path: Path) -> None:
    # Windows FlushFileBuffers requires write access to the file handle.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


# What a filesystem that cannot sync a directory answers: a VirtualBox shared
# folder, some network mounts. SQLite ignores these in its own directory sync.
# Raising on them left a staged import that could never install on that volume.
_NO_DIRECTORY_SYNC = frozenset(
    code
    for code in (
        errno.EINVAL,
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if code is not None
)


def _fsync_directory(path: Path) -> None:
    # Windows does not allow opening directories this way. File fsync and the
    # same-volume replace still apply there.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    except OSError as error:
        # A real I/O failure (EIO, ENOSPC) still stops the import.
        if error.errno not in _NO_DIRECTORY_SYNC:
            raise
    finally:
        os.close(descriptor)


def _budget(deadline: float | None) -> float:
    """What one step may spend: its own timeout, or what is left of a shared one.

    Raises rather than starting a step that has no time to finish in, so a
    caller with an expired budget fails now instead of after another full wait.
    """
    if deadline is None:
        return _TIMEOUT
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("the save import budget expired")
    return min(_TIMEOUT, remaining)


# What Windows answers for a rename or delete that another handle blocks (#636):
# 5 = ERROR_ACCESS_DENIED, what OneDrive and antivirus filters usually give,
# 32 = ERROR_SHARING_VIOLATION, 33 = ERROR_LOCK_VIOLATION. Kept here, with the
# retry below, because this module must stay importable without Anki; the sync
# module takes its classification from here.
_FILE_LOCK_WINERRORS = frozenset({5, 32, 33})

# Waits between tries of one locked file operation: about 2.5 seconds in all, and
# only after a first try has failed.
_FILE_LOCK_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.0)


def _is_file_lock_error(error: BaseException) -> bool:
    """Whether ``error`` is Windows refusing a file operation another handle blocks.

    Windows only: POSIX renames and deletes over open files, so a PermissionError
    there is a real permission problem, and retrying it only delays the report.
    """
    if os.name != "nt":
        return False
    if isinstance(error, PermissionError):
        return True
    return (
        isinstance(error, OSError)
        and getattr(error, "winerror", None) in _FILE_LOCK_WINERRORS
    )


def _retry_on_file_lock(operation, deadline: float | None = None):
    """Run one file operation, trying it again while another handle blocks it.

    A sync client or virus scanner that opens a file just written usually lets
    go within a second. Only this operation is retried, never the step around it,
    and any other error propagates at once. With ``deadline``, no wait runs past
    it: a lock still held then raises its own error rather than a spent budget,
    so the notice names what blocked the file.
    """
    for delay in (*_FILE_LOCK_RETRY_DELAYS, None):
        try:
            return operation()
        except OSError as error:
            if (
                delay is None
                or not _is_file_lock_error(error)
                or (deadline is not None and time.monotonic() + delay >= deadline)
            ):
                raise
        time.sleep(delay)


def _sqlite_uri(path, mode: str) -> str:
    """A SQLite URI for ``path`` that a Windows network path can use too.

    ``as_uri`` percent-encodes spaces and non-ASCII names, which a bare f-string
    URI gets wrong. For a UNC path, though, it puts the server in the URI
    authority (``file://server/share/...``), and SQLite refuses every authority
    but an empty one or ``localhost``: "invalid uri authority". That is any
    profile on a redirected AppData folder, or on a mapped drive ``resolve``
    rewrites to UNC. SQLite's URI syntax has no network-path form, so that case
    percent-encodes the whole native path into ``file:`` with no authority.
    SQLite decodes it back to exactly the filename a plain ``connect`` would
    open, and ``mode`` still applies.
    """
    resolved = Path(path).resolve()
    uri = resolved.as_uri()
    if not uri.startswith("file:///"):
        uri = "file:" + urllib.parse.quote(str(resolved), safe="")
    return f"{uri}?mode={mode}"


def _connect_readonly(path: Path, deadline: float = None):
    timeout = _budget(deadline)
    conn = sqlite3.connect(_sqlite_uri(path, "ro"), uri=True, timeout=timeout)
    limit = time.monotonic() + timeout
    conn.set_progress_handler(lambda: int(time.monotonic() > limit), 2000)
    return conn


def _verify_save(path: Path, deadline: float = None) -> None:
    if not path.is_file() or path.stat().st_size < 512:
        raise ValueError("The pending save is missing or truncated")
    conn = _connect_readonly(path, deadline)
    try:
        if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise SaveDamagedError("The save failed its SQLite integrity check")
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='captured_pokemon'"
            ).fetchone()
            is None
        ):
            raise ValueError("The file is not an Ankimon save")
    finally:
        conn.close()


def _remove_owned_copy(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def _snapshot(source: Path, dest: Path, deadline: float = None) -> None:
    """Build a verified single-file copy, including committed WAL contents."""
    source_conn = _connect_readonly(source, deadline)
    try:
        timeout = _budget(deadline)
        dest_conn = sqlite3.connect(dest, timeout=timeout)
        try:
            limit = time.monotonic() + timeout

            def progress(status, remaining, total):
                if time.monotonic() > limit:
                    raise TimeoutError("Timed out taking the import safety snapshot")

            source_conn.backup(dest_conn, pages=256, progress=progress, sleep=0.05)
            dest_conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    _verify_save(dest, deadline)
    # An fsync in flight cannot be abandoned, so the budget is checked before
    # one starts rather than trusted to cover it.
    _budget(deadline)
    _fsync_file(dest)


def _digest(path: Path, deadline: float = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            _budget(deadline)
            digest.update(block)
    return digest.hexdigest()


def _copy_within(source: Path, dest: Path, deadline: float = None) -> None:
    """``shutil.copyfile`` that checks a shared budget between blocks."""
    with source.open("rb") as reader, dest.open("wb") as writer:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            _budget(deadline)
            writer.write(block)


def _prepare_incoming(
    path: Path, token: str, *, sanitize_credentials: bool = True
) -> None:
    """Prepare a staged save for installation.

    Portable imports must never carry another installation's leaderboard
    credentials. A Backup Manager restore is different: it restores the user's
    own private local snapshot, so callers can explicitly retain credentials.
    """
    conn = sqlite3.connect(path, timeout=_TIMEOUT)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if sanitize_credentials:
            conn.execute("PRAGMA secure_delete=ON")
        with conn:
            if sanitize_credentials:
                # Explicit empty auth rows also prevent Settings from falling
                # back to this installation's legacy config.obf when an old save
                # has no config. A portable imported save never inherits local
                # credentials.
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)"
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO config VALUES (?, '')",
                    [
                        ("leaderboard.username",),
                        ("leaderboard.api_key",),
                    ],
                )
                if "user_data" in tables:
                    conn.execute(
                        "DELETE FROM user_data WHERE key IN ('username', 'api_key')"
                    )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.executemany(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                [
                    ("import_token", token),
                    ("import_rebase_pending", "1"),
                    # Do not merge destination legacy JSON into a whole-save import.
                    # Ordinary schema upgrades still run in AnkimonDB construction.
                    ("migrated", "true"),
                    ("migrated_phase2", "true"),
                ],
            )
        if sanitize_credentials:
            # Eliminate credentials from free pages as well as live rows. New
            # game state will require this device's user to sign in again.
            conn.execute("VACUUM")
    finally:
        conn.close()
    _verify_save(path)
    _fsync_file(path)


def pending_import_info(target: Path) -> dict | None:
    """Read pending work for exactly this target; paths come from local code.

    Invalid metadata raises instead of silently treating a failed import as
    absent. No pathname from the manifest can redirect a write or deletion.
    """
    target, directory = _paths(target)
    try:
        with (directory / "pending.json").open(encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        return None
    if _is_cancelled_record(record):
        # What a crash can bring back after Cancel: it installs nothing, and a
        # new import may publish over it.
        return None
    if not isinstance(record, dict):
        raise ValueError("The pending import record is damaged")
    token = record.get("token", "")
    if (
        record.get("version") != 1
        or record.get("target") != str(target)
        or not isinstance(token, str)
        or re.fullmatch(r"[0-9a-f]{32}", token) is None
        or not isinstance(record.get("process"), str)
        or not isinstance(record.get("digest"), str)
        or not isinstance(record.get("retain_unverified", False), bool)
        or ("warning" in record and not isinstance(record["warning"], str))
    ):
        raise ValueError("The pending import record does not match this save")
    recovery = target.parent / "ankimon_recovery" / f"pre-import-{token}"
    return {
        **record,
        "pending_path": directory / f"{token}.db",
        "recovery_path": recovery / target.name,
        "unverified_path": recovery / _UNVERIFIED / target.name,
    }


@_serialize_staging
def pending_import_status(target: Path) -> dict:
    """Describe whether this save has pending, installed, or cancelled work."""
    target, directory = _paths(target)
    info = pending_import_info(target)
    if info is None:
        state = (
            "cancelled"
            if _manifest_is_cancelled(directory / "pending.json")
            else "none"
        )
        return {"state": state}

    installed = pending_import_is_installed(target)
    if installed is True:
        state = "installed"
    elif installed is False:
        state = "pending"
    else:
        state = "unknown"
    return {"state": state, "info": info}


def stage_import(
    snapshot: Path,
    target: Path,
    *,
    sanitize_credentials: bool = True,
    retain_unverified: bool = False,
    before_publish=None,
    publish_guard=None,
    operation=None,
    wait_for_staging: bool = True,
) -> dict:
    """Stage one reserved request; native GUI callers refuse a busy disk lock."""
    owned = operation is None
    operation = reserve_save_operation() if owned else operation
    try:
        operation.validate()
        lock = _process_staging_lock()
        if not lock.acquire(blocking=wait_for_staging):
            raise SaveOperationBusyError(
                "Save information is still loading. Please try again shortly."
            )
        try:
            operation.validate()
            return _stage_import(
                snapshot,
                target,
                sanitize_credentials=sanitize_credentials,
                retain_unverified=retain_unverified,
                before_publish=before_publish,
                publish_guard=publish_guard,
            )
        finally:
            lock.release()
    finally:
        if owned:
            operation.release()


def _stage_import(
    snapshot: Path,
    target: Path,
    *,
    sanitize_credentials: bool = True,
    retain_unverified: bool = False,
    before_publish=None,
    publish_guard=None,
) -> dict:
    """Durably retain the chosen save without touching the runtime database.

    Returns pending_path, recovery_path and token. Recovery is reserved now and
    written from the final local save immediately before installation. Existing
    pending work must be cancelled explicitly before choosing another import.

    ``sanitize_credentials`` is True for portable imports/rescues. Backup
    Manager restores may set it False because their source is already private
    local recovery material owned by this installation.

    ``retain_unverified`` records that the user agreed, after
    ``damaged_save_question``, to replace a save that fails its integrity check.
    The install then keeps that save as it is, under ``unverified_path``, when it
    cannot keep a verified copy. It never relaxes the checks on the new save.

    ``before_publish`` runs after the private snapshot and manifest are ready,
    immediately before each attempt at the manifest commit point. A failed guard
    leaves nothing staged. ``publish_guard`` may hold a lifecycle barrier around
    that single rename, but never around a retry delay or snapshot copy.
    The process-wide staging lock serializes this check-and-publish path
    with other imports, rescues, and cancellations.

    Raises ``ImportStagedError`` when publication succeeded but a later step
    did not: the import is armed for the next start and can only be stopped by
    cancelling it. Every other failure leaves nothing staged -- including the
    one after publication that is not ``ImportStagedError``: a manifest gone
    by read-back raises plain ``OSError``, because then nothing will install,
    and the staged copy is removed with it.
    """
    target, directory = _paths(target)
    try:
        existing = pending_import_info(target)
    except ValueError as error:
        # A damaged record, or one written for this save at another path, blocks
        # staging just the same, and only Cancel clears it. Callers name Cancel
        # for this refusal; as a plain error they reported an abort instead.
        raise ImportAlreadyPendingError(
            "An import is already pending; cancel it before choosing another save"
        ) from error
    if existing is not None:
        raise ImportAlreadyPendingError(
            "An import is already pending; cancel it before choosing another save"
        )
    token = uuid.uuid4().hex
    # A Backup Restore's staged copy keeps credentials, so this folder gets the
    # recovery folders' checks: never a link, and private to this user.
    _private_directory(directory)
    incoming = directory / f"{token}.db"
    temp_manifest = directory / f"{token}.json"
    published = False
    try:
        # mkstemp permissions are private even if the user's umask is broad.
        fd = os.open(incoming, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        _snapshot(Path(snapshot), incoming)
        _prepare_incoming(incoming, token, sanitize_credentials=sanitize_credentials)
        record = {
            "version": 1,
            "target": str(target),
            "token": token,
            "process": _process_identity(),
            "digest": _digest(incoming),
        }
        if retain_unverified:
            record["retain_unverified"] = True
        with temp_manifest.open("x", encoding="utf-8") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())

        def publish():
            if before_publish is not None:
                before_publish()
            operation = lambda: os.replace(temp_manifest, directory / "pending.json")
            return (
                publish_guard(operation) if publish_guard is not None else operation()
            )

        _retry_on_file_lock(publish)
        published = True
    finally:
        if not published:
            temp_manifest.unlink(missing_ok=True)
            _remove_owned_copy(incoming)

    # Past the commit point. Everything below is cleanup, durability and
    # read-back, and none of it can un-arm the install, so a failure here is
    # reported as a staged import the user can cancel — never as "nothing was
    # replaced". The leftover manifest is removed here rather than in the
    # finally above for that reason: missing_ok hides only a missing file, and
    # a locked directory would otherwise abort over an already-published save.
    try:
        temp_manifest.unlink(missing_ok=True)
        _fsync_directory(directory)
        _fsync_directory(target.parent)
        info = pending_import_info(target)
    except Exception as error:
        raise ImportStagedError(str(error), token) from error
    if info is None:
        # The manifest vanished between publishing and reading it back, so
        # nothing will install after all; do not strand the staged copy.
        _remove_owned_copy(incoming)
        raise OSError(
            "The published pending import disappeared before it could be read back"
        )
    return info


@_serialize_staging
def set_pending_import_warning(
    target: Path,
    warning: str,
    expected_token: str | None = None,
) -> bool:
    """Persist a warning on an already-published pending import."""
    if not isinstance(warning, str):
        raise TypeError("A pending import warning must be text")
    target, directory = _paths(target)
    info = pending_import_info(target)
    if info is None or (expected_token is not None and info["token"] != expected_token):
        return False
    manifest = directory / "pending.json"
    temporary = directory / f"{info['token']}.{uuid.uuid4().hex}.warning.json"
    try:
        with manifest.open(encoding="utf-8") as handle:
            record = json.load(handle)
        if (
            not isinstance(record, dict)
            or record.get("token") != info["token"]
            or (expected_token is not None and record.get("token") != expected_token)
        ):
            raise ValueError("The pending save changed while its warning was recorded")
        record["warning"] = warning
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        _retry_on_file_lock(lambda: os.replace(temporary, manifest))
        _fsync_directory(directory)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _write_cancelled_record(manifest: Path, target: Path) -> None:
    """Overwrite the manifest in place with a record that installs nothing.

    In place rather than by rename: a new directory entry would need the very
    directory sync whose failure this has to survive, while the existing entry
    only needs its file synced. If the write or its sync fails, the original
    bytes are put back before raising, so this session still sees the import
    its caller is about to report could not be cancelled.

    The record is padded with whitespace, which JSON ignores, to the original
    length, so no truncate follows the write. A crash between a shorter write
    and its truncate left the new record followed by the old one's tail, which
    parses as neither -- and after an install, that made every later start
    report a replacement that had already happened as one that had failed.
    """
    record = json.dumps({"version": 1, "target": str(target), "cancelled": True})
    with manifest.open("r+b") as handle:
        original = handle.read()
        try:
            handle.seek(0)
            handle.write(record.encode("utf-8").ljust(len(original), b" "))
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            try:
                handle.seek(0)
                handle.write(original)
                handle.truncate()
                handle.flush()
            except Exception:
                pass
            raise


def _is_cancelled_record(record) -> bool:
    return isinstance(record, dict) and record.get("cancelled") is True


def _manifest_is_cancelled(manifest: Path) -> bool:
    try:
        with manifest.open(encoding="utf-8") as handle:
            return _is_cancelled_record(json.load(handle))
    except Exception:
        return False


@_serialize_staging
def cancel_pending_import(target: Path) -> bool:
    """Cancel staged work even if its manifest is damaged.

    The commit point is ``pending.json`` rewritten in place as a cancelled
    record and synced as a FILE. Its directory entry already exists, so that
    is durable even where the directory itself cannot be synced, and a crash
    that undoes the unlink below brings back a record that installs nothing --
    not the import the user was told had been cancelled. If the rewrite or its
    sync fails this raises with the pending import left as it was, so the
    caller can say cancellation failed and a retry still finds it to cancel.

    Past that commit, neither the unlink, the directory sync nor a failure to
    delete an orphaned private staged copy may make the UI claim cancellation
    failed. A cancelled record that an earlier call committed but could not
    remove is synced again, removed, and reported as nothing pending. Invalid
    manifests are deliberately not trusted for paths; only locally-generated
    32-hex-token database names are cleaned up.

    Journals beside a save that is missing are moved into the import's recovery
    folder first, as its install would have moved them. If they cannot be moved,
    or a damaged record gives them nowhere to go, this raises ``OSError`` before
    the commit point, with nothing cancelled.
    """
    target, directory = _paths(target)
    manifest = directory / "pending.json"
    if not manifest.exists():
        return False

    try:
        info = pending_import_info(target)
    except Exception:
        info = None
    already_cancelled = info is None and _manifest_is_cancelled(manifest)
    if not already_cancelled:
        # The record is what keeps a fresh save from being opened over journals
        # left beside a missing save (refuse_to_open_over_journals). They go
        # where the install would have put them before it is retired; if they
        # cannot, nothing is cancelled and that protection stays in force.
        _set_aside_before_cancelling(target, info)

    # Commit cancellation before touching any staged data.
    try:
        if already_cancelled:
            _fsync_file(manifest)
        else:
            _write_cancelled_record(manifest, target)
    except FileNotFoundError:
        return False
    try:
        manifest.unlink()
        _fsync_directory(directory)
    except Exception:
        # The synced cancelled record already stops every install, including
        # one after a crash that brings this directory entry back.
        pass

    if info is not None:
        candidates = [info["pending_path"]]
    else:
        candidates = [
            path
            for path in directory.glob("*.db")
            if re.fullmatch(r"[0-9a-f]{32}\.db", path.name) is not None
        ]
    for path in candidates:
        try:
            _remove_owned_copy(path)
        except OSError:
            # The cancellation is already on disk, so this is only an orphaned
            # private file. A later cleanup/stage can retry without any chance
            # of installing it.
            pass
    try:
        _fsync_directory(directory)
    except OSError:
        # Only the durability of those orphan removals is in question here.
        pass
    return not already_cancelled


def _installed_token(target: Path, deadline: float = None) -> str | None:
    conn = _connect_readonly(target, deadline)
    try:
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
            ).fetchone()
            is None
        ):
            return None
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='import_token'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# Choosing a message is not worth freezing the menu for: a save something else
# holds locked answers "unknown" within this long, not after SQLite's full timeout.
_WORDING_BUDGET = 2.0


def pending_import_is_installed(target: Path) -> bool | None:
    """Whether the pending record describes an import that ALREADY installed.

    ``_finish_installed_import`` retires the manifest after replacement, so True
    is only reachable when that last step failed: the save on disk IS the
    imported one and a stale ``pending.json`` is still sitting beside it, which
    the next full start clears by itself.

    ``commit_pending_import`` discriminates this state (it checks the installed
    token before doing anything), but the menu did not, and answered for the
    ordinary pending case: Cancel said the current save was unchanged when the
    import had already replaced it, and a second import attempt was told the
    first one "will install at the next full Anki restart". Both are the wrong
    way round for a user deciding what to do about their save.

    None means the answer could not be read -- a damaged record, or a save that
    is locked or unreadable -- and callers word it as unknown rather than as
    either case. Callers run on the GUI thread, so the save gets
    ``_WORDING_BUDGET`` rather than SQLite's 30-second busy timeout.
    """
    try:
        info = pending_import_info(target)
        if info is None:
            return False
        if not Path(target).is_file():
            # No save on disk can be the imported one. An install that replaces a
            # save writes the recovery copy first, so with one an install may have
            # replaced the save before it went missing, and only "unknown" points
            # the user at that copy. Without one, "unknown" would send the user
            # after a copy that was never made. The one install that writes no
            # copy, into a save that was already missing, leaves nothing to point
            # at either; if its save goes missing too, the next start installs the
            # import again.
            return None if _recovery_copy(info) is not None else False
        deadline = time.monotonic() + _WORDING_BUDGET
        return _installed_token(target, deadline) == info["token"]
    except Exception:
        return None


def _recovery_copy(info: dict) -> Path | None:
    """This record's pre-import copy, if one is on disk.

    An install into a save that no longer existed replaced nothing and kept
    nothing, so the record's reserved path is not evidence of a copy. A verified
    copy comes first; an install that could not make one, over a save that failed
    its integrity check, kept that save unverified instead.
    """
    for copy in (info["recovery_path"], info["unverified_path"]):
        if copy.is_file():
            return copy
    return None


def pending_import_recovery_copy(target: Path) -> Path | None:
    """Where the pending record for ``target`` kept the save it replaced, if anywhere.

    None when there is no readable record or no copy on disk, so a notice never
    sends the user after a copy that was never made.
    """
    try:
        info = pending_import_info(target)
    except Exception:
        return None
    return None if info is None else _recovery_copy(info)


# What Import and Backup Restore may spend on the GUI thread checking the save they
# would replace. SQLite's own busy timeout and the check's progress limit would
# each allow _TIMEOUT for a locked save. The integrity check reads the whole file,
# though, so a save too large to check in this long gets no question, and if it
# is damaged the install refuses it at every start.
_DAMAGE_CHECK_BUDGET = 10.0


def current_save_is_damaged(target: Path) -> bool | None:
    """Whether the save an import would replace fails SQLite's integrity check.

    Asked before staging, on the GUI thread, within ``_DAMAGE_CHECK_BUDGET``. True
    only for a save whose schema SQLite can still read: the install switches that
    save's journal mode before replacing it, which needs the schema, so offering
    to install over any other would promise a replacement that never happens.
    None means the check did not finish: the save is locked, unreadable or slower
    to check than that. A missing save is not damaged, since there is nothing to
    keep.
    """
    target = Path(target)
    if not target.is_file():
        return False
    try:
        conn = _connect_readonly(target, time.monotonic() + _DAMAGE_CHECK_BUDGET)
    except Exception:
        return None
    try:
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception:
            return None
        try:
            return conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]
        except Exception as error:
            return True if _is_damage(error) else None
    finally:
        conn.close()


def should_confirm_unverified_copy(target: Path) -> bool:
    """Whether Import or Backup Restore must ask before staging over ``target``.

    Not while an import is already recorded for it: staging refuses that anyway
    and says why, and a question about a copy that will never be made would only
    come first.
    """
    try:
        if pending_import_info(target) is not None:
            return False
    except Exception:
        return False
    return current_save_is_damaged(target) is True


def damaged_save_question(target: Path, replacement: str) -> str:
    """What Import and Backup Restore ask before staging over a damaged save."""
    target, _ = _paths(target)
    return (
        f"Your current Ankimon save, {target.name}, failed SQLite's integrity check, "
        "so part of it may be damaged, even if Ankimon still opens it.\n\n"
        "Before Ankimon replaces a save, it keeps a verified copy of it, and a damaged "
        f"save cannot give one. If you continue, and {target.name} still fails the "
        "check at the next start, Ankimon copies it exactly as it is, unchecked and "
        f'unrepaired, with its SQLite journal files, into a folder named "{_UNVERIFIED}" '
        f"inside:\n{target.parent / 'ankimon_recovery'}\n"
        f"Then it installs {replacement}, which passed the check.\n\n"
        "Continue?"
    )


def describe_recovery_destination(info: dict) -> str:
    """Where a staging notice says the save being replaced will be kept."""
    destination = str(info["recovery_path"])
    if info.get("retain_unverified"):
        destination += (
            "\n\nIf it still fails its integrity check then, it is copied as it is, "
            f"unverified, to:\n{info['unverified_path']}"
        )
    return destination


def _log(logger, level: str, message: str) -> None:
    if logger is not None:
        try:
            logger.log(level, message)
        except Exception:
            pass


# A volume whose permissions are fixed by how it is mounted (FAT or exFAT, some
# network shares) refuses chmod outright. Nothing can ever tighten a folder
# there, so refusing the install over it refused it at every start.
_FIXED_PERMISSIONS = frozenset(
    code
    for code in (
        errno.EPERM,
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if code is not None
)


def _restrict_directory(path: Path, logger=None) -> None:
    """Make a recovery folder private to this user where the volume allows it.

    Tolerated only for a folder this user owns. EPERM is also the answer for a
    folder that belongs to another account, and a copy of the save, credentials
    included, does not go into a folder somebody else controls.
    """
    try:
        path.chmod(0o700)
    except OSError as error:
        getuid = getattr(os, "getuid", None)
        owned = getuid is None or path.stat().st_uid == getuid()
        if error.errno not in _FIXED_PERMISSIONS or not owned:
            raise
        _log(logger, "warning", f"Could not restrict access to {path}: {error}")


# What os.lstat reports as st_reparse_tag for a junction on Windows
# (IO_REPARSE_TAG_MOUNT_POINT; the stat module defines it only there).
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003


def _is_junction(path: Path) -> bool:
    """Whether ``path`` is a Windows junction, on every Python Anki bundles.

    ``os.path.isjunction`` reads this same field, but only from Python 3.12, and
    older Anki builds bundle older Pythons; a fallback that answered False let a
    junction pass as an ordinary folder there. Elsewhere ``lstat`` carries no
    reparse tag, and nothing is a junction.
    """
    try:
        return os.lstat(path).st_reparse_tag == _IO_REPARSE_TAG_MOUNT_POINT
    except (OSError, ValueError, AttributeError):
        return False


def _is_link(path: Path) -> bool:
    """Whether ``path`` is a symlink or a Windows junction, without following it."""
    return path.is_symlink() or _is_junction(path)


def _private_directory(path: Path, logger=None) -> None:
    """Create or reuse a folder for private copies of a save, never through a link.

    ``mkdir(exist_ok=True)`` accepts a link to a folder. A copy written through
    one, credentials included, lands wherever it points, and the permissions
    checked here would never have been that folder's.
    """
    path.mkdir(mode=0o700, exist_ok=True)
    if _is_link(path):
        raise OSError(
            f"{path} is a link to another folder, so Ankimon will not write a copy "
            "of the save through it"
        )
    _restrict_directory(path, logger)


def _sync_within(path: Path, deadline: float | None) -> None:
    """Sync a directory, unless the shared startup budget is already spent."""
    _budget(deadline)
    _fsync_directory(path)


def _finish_installed_import(target, recovery, logger, install_temp=None) -> None:
    level = "info"
    if recovery is None:
        previous = "No recovery copy of a previous save exists."
    elif recovery.parent.name == _UNVERIFIED:
        level = "warning"
        previous = (
            "The previous save failed its integrity check and was kept as it "
            f"was, unverified: {recovery}"
        )
    else:
        previous = f"Previous save: {recovery}"
    _log(logger, level, f"Ankimon import installed. {previous}")
    try:
        # Retry this sync after a prior crash too, before retiring the manifest.
        _fsync_directory(target.parent)
        if install_temp is not None:
            _remove_owned_copy(install_temp)
        cancel_pending_import(target)
    except Exception as error:
        # The installed token prevents a later launch from reinstalling over
        # new progress. Surface this separately from a refused replacement.
        message = (
            f"Imported save is active; final sync or cleanup did not finish: {error}"
        )
        _log(logger, "warning", message)
        raise ImportInstalledError(message) from error


def _prune_superseded_recovery(directory: Path, name: str, keep: int = 1) -> None:
    """Keep the canonical snapshot and the newest superseded one, no more.

    An install that cannot finish -- on Windows, any process holding the save
    open is enough -- is retried on every start, and each attempt snapshots the
    live save again before trying. Unbounded, that is a full extra copy of the
    save in user_files per restart, for as long as the lock lasts, while the
    failure notice says only that it will retry. Nothing else prunes this
    directory: Backup Manager's retention works on a different tree entirely.

    One superseded copy is kept because the newest snapshot is taken before the
    install is attempted, so the one before it is the last state captured under
    a different set of conditions. Older ones describe the same save with less
    of the user's progress in it.
    """
    try:
        superseded = sorted(
            (path for path in directory.glob(f"retry-*-{name}") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
    except OSError:
        return
    for stale in superseded[:-keep] if keep else superseded:
        try:
            stale.unlink()
        except OSError:
            # Retaining one copy too many is not worth failing an install over.
            pass


_JOURNAL_SUFFIXES = ("-wal", "-shm", "-journal")
# The folder, inside an import's recovery folder, that holds a save kept without
# verification. The name is what labels it; notices quote it.
_UNVERIFIED = "unverified"


def _journals_beside(target: Path) -> list:
    """The SQLite journals that exist beside ``target``, links included."""
    return [
        path
        for path in (Path(str(target) + suffix) for suffix in _JOURNAL_SUFFIXES)
        if os.path.lexists(path)
    ]


def _retain_current_save(
    target: Path, recovery: Path, logger, deadline, retain_unverified: bool = False
) -> Path:
    """Snapshot the save an install is about to replace; return where it went.

    A save that fails its integrity check cannot give a verified snapshot. That
    stops the install with ``CurrentSaveDamagedError``, unless the import was
    staged with ``retain_unverified``; then the save is kept as it is instead.
    """
    # mkdir(mode=...) neither tightens a folder that already exists nor refuses a
    # link. Check both levels before inspecting or writing private recovery
    # material.
    _budget(deadline)
    _private_directory(recovery.parent.parent, logger)
    _private_directory(recovery.parent, logger)
    if recovery.is_file():
        # A failed previous replacement may be followed by more local play, so
        # the snapshot taken now is the one holding everything. Move the older
        # attempt aside rather than redirecting this one: the canonical name is
        # the only recovery filename the user is ever shown -- the staging
        # notice quotes it once, before Anki closes, and nothing names the file
        # again afterwards. Redirecting left that advertised path holding the
        # stale first attempt while the genuinely final save sat beside it under
        # a name nobody had been given.
        _budget(deadline)
        superseded = recovery.with_name(f"retry-{uuid.uuid4().hex}-{target.name}")
        _retry_on_file_lock(lambda: os.replace(recovery, superseded), deadline)
        _sync_within(recovery.parent, deadline)
        _budget(deadline)
        _prune_superseded_recovery(recovery.parent, target.name)
    elif recovery.exists():
        # Something that is not a snapshot holds the name. Write beside it, as
        # this has always done: replacing it would fail the install outright.
        recovery = recovery.with_name(f"retry-{uuid.uuid4().hex}-{target.name}")

    fd, name = tempfile.mkstemp(prefix=".backup-", suffix=".db", dir=recovery.parent)
    os.close(fd)
    backup_temp = Path(name)
    try:
        try:
            _snapshot(target, backup_temp, deadline)
        except (ValueError, sqlite3.DatabaseError) as error:
            # A lock, a spent budget or a file that is not a save is not damage,
            # and stops the install as it always has.
            if not _is_damage(error):
                raise
            if not retain_unverified:
                raise CurrentSaveDamagedError(
                    _damaged_save_refusal(target, error)
                ) from error
            damage = error
        else:
            damage = None
            # Everything past the snapshot is more recovery I/O inside add-on import.
            _budget(deadline)
            _retry_on_file_lock(lambda: os.replace(backup_temp, recovery), deadline)
            _sync_within(recovery.parent, deadline)
            _sync_within(recovery.parent.parent, deadline)
            _sync_within(target.parent, deadline)
    finally:
        _remove_owned_copy(backup_temp)

    if damage is not None:
        _log(
            logger,
            "warning",
            f"{target.name} failed its integrity check ({damage}), so "
            "it is kept unverified, as the import was staged to allow",
        )
        return _retain_unverified_save(target, recovery.parent, logger, deadline)
    return recovery


def _damaged_save_refusal(target: Path, error: BaseException) -> str:
    """The startup notice for a damaged save an import was not staged to replace."""
    detail = "" if isinstance(error, SaveDamagedError) else f" ({error})"
    return (
        f"{target.name} failed SQLite's integrity check{detail}, so Ankimon could not "
        "keep a verified copy of it before replacing it, and restarting will not change "
        "that. Nothing was replaced. To install anyway, use Ankimon → Game → Cancel "
        "Pending Save Import, then choose the import or backup again: Ankimon checks the "
        "save when you do, and asks whether to keep it as it is, unverified, instead."
    )


def _discard_copies(folder: Path) -> None:
    """Remove a folder of copies this module wrote, leaving whatever will not go."""
    try:
        if _is_link(folder) or not folder.is_dir():
            return
        entries = list(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            # unlink removes a link itself, never what it points at.
            entry.unlink()
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass


def _prune_superseded_unverified(folder: Path, keep: int = 1) -> None:
    """Keep the newest unverified copy an earlier attempt left, and no more.

    The same bound, for the same reason, as ``_prune_superseded_recovery``.
    """
    try:
        superseded = sorted(
            (
                path
                for path in folder.glob(f"{_UNVERIFIED}-superseded-*")
                if path.is_dir() and not _is_link(path)
            ),
            key=lambda path: path.stat().st_mtime,
        )
    except OSError:
        return
    for stale in superseded[:-keep] if keep else superseded:
        _discard_copies(stale)


def _retain_unverified_save(target: Path, folder: Path, logger, deadline) -> Path:
    """Keep a save that failed its integrity check exactly as it is on disk.

    Only for an import staged with ``retain_unverified``. The save, and the WAL
    or rollback journal beside it, are copied byte for byte, unchecked and
    unrepaired, and keep their names, because SQLite pairs a journal with its
    database by name. The shared-memory index is left out; SQLite rebuilds it.
    SQLite's own recovery has already run, as it does before every install:
    ``_recover_hot_journal`` rolled back a hot journal, and closing its
    connection merged a WAL no other connection held open.

    Each attempt fills a hidden folder and publishes it with one rename, so the
    ``unverified`` folder never pairs a save with another attempt's journal. A
    copy an earlier attempt published is moved aside first.
    """
    for stale in folder.glob(f".{_UNVERIFIED}-*"):
        # An attempt that stopped before publishing. The save it copied has not
        # been replaced, so this attempt copies it again.
        _discard_copies(stale)
    _budget(deadline)
    building = folder / f".{_UNVERIFIED}-{uuid.uuid4().hex}"
    _private_directory(building, logger)
    published = folder / _UNVERIFIED
    try:
        journals = [
            path
            for path in (Path(str(target) + "-wal"), Path(str(target) + "-journal"))
            if path.is_file()
        ]
        for source in (target, *journals):
            copy = building / source.name
            _copy_within(source, copy, deadline)
            _budget(deadline)
            _fsync_file(copy)
        _sync_within(building, deadline)
        if os.path.lexists(published):
            superseded = folder / f"{_UNVERIFIED}-superseded-{uuid.uuid4().hex}"
            _retry_on_file_lock(lambda: os.rename(published, superseded), deadline)
        _retry_on_file_lock(lambda: os.rename(building, published), deadline)
    except BaseException:
        _discard_copies(building)
        raise
    # The same syncs a verified copy gets, so the folders that lead to this copy
    # are on disk before the save it holds is replaced.
    _sync_within(folder, deadline)
    _sync_within(folder.parent, deadline)
    _sync_within(target.parent, deadline)
    _prune_superseded_unverified(folder)
    return published / target.name


def _set_aside_orphaned_journals(
    target: Path, directory: Path, logger=None, deadline: float = None
) -> None:
    """Move journals left beside a missing save out of the imported save's way.

    SQLite pairs a journal with a database by filename alone, so one left here
    would be replayed into the imported save. It may also be all that remains of
    the missing save, so it goes into this import's private recovery folder
    rather than being deleted.
    """
    orphans = _journals_beside(target)
    if not orphans:
        return
    # Not budgeted: these renames stay on one volume, and leaving the journals in
    # place is what loses them. Only the syncs below wait on the disk.
    _private_directory(directory.parent, logger)
    _private_directory(directory, logger)
    for orphan in orphans:
        kept = directory / f"orphaned-{uuid.uuid4().hex}-{orphan.name}"
        # A lock is retried outside the budget too, for the reason above.
        _retry_on_file_lock(lambda: os.replace(orphan, kept))
    _sync_within(directory, deadline)
    _sync_within(directory.parent, deadline)
    _sync_within(target.parent, deadline)
    _log(
        logger,
        "warning",
        f"Moved journals left beside the missing {target.name} to {directory}",
    )


def refuse_to_open_over_journals(target: Path, cause: BaseException = None) -> None:
    """Raise ``ImportUnsafeToOpenError`` rather than let a fresh save discard journals.

    Applies while the save is missing, a journal is still beside it, and an
    import is pending for it or its pending record cannot be read: the install
    either could not set the journal aside or stopped before trying. The answer
    comes from the files on disk rather than from that attempt, because an
    add-on reload after a refused start makes no attempt: this process already
    tried. Cancelling the import moves the journals aside before it retires the
    record, so it cannot take this protection away.

    Two cases are left alone. A missing save with no import pending: no install
    offered to keep its journals, and refusing to start would leave no menu to
    resolve it from. And a save deleted while a session has it open: that
    session's database reconnects to its path without asking.
    ``cause`` is why this start's install stopped, when that is known.
    """
    target, directory = _paths(target)
    if os.path.lexists(target):
        return
    journals = _journals_beside(target)
    if not journals:
        return
    unreadable = None
    try:
        info = pending_import_info(target)
    except Exception as error:
        info, unreadable = None, error
    if info is None and unreadable is None:
        return
    listed = "\n".join(f"    {path}" for path in journals)
    exposed = (
        f"Ankimon will not open {target.name}: a new save there would be created over "
        "what may be the last copy of your progress.\n\n"
        f"{target} is missing, and these SQLite journals are still beside it:\n{listed}\n"
        "They can hold progress that was never written into the save, and a new save "
        "at that path would discard them."
    )
    if info is not None:
        folder = info["recovery_path"].parent
        stopped = "" if cause is None else f" It could not: {cause}"
        detail = (
            f"A save import is waiting to install over {target.name}, and it moves these "
            f"journals into {folder} first.{stopped}\n\n"
            "To continue, close anything that may be using those files and make sure "
            f"{folder.parent} is an ordinary folder that belongs to you, not a file or a "
            "link, or move the journal files listed above somewhere safe. Then restart "
            "Anki, and the import installs."
        )
    else:
        detail = (
            f"A save import record for {target.name} is in {directory}, but it could not "
            f"be read ({unreadable}), so the journals were not moved anywhere.\n\n"
            "To continue, move the journal files listed above somewhere safe, and restart "
            "Anki if this stopped Ankimon from loading.\n"
            "Ankimon → Game → Cancel Pending Save Import then clears the damaged record."
        )
    raise ImportUnsafeToOpenError(f"{exposed}\n\n{detail}") from cause


def _set_aside_before_cancelling(target: Path, info) -> None:
    """Move journals beside a missing save aside before its record is retired."""
    if os.path.lexists(target) or not _journals_beside(target):
        return
    if info is None:
        raise OSError(
            f"{target.name} is missing, SQLite journals that may hold its progress are "
            "still beside it, and its import record could not be read, so there is "
            "nowhere to move them. Nothing was cancelled. Move the journal files beside "
            f"{target.name} somewhere safe, then cancel again."
        )
    folder = info["recovery_path"].parent
    try:
        _set_aside_orphaned_journals(target, folder)
    except OSError as error:
        # The move is one rename per journal and then the syncs, so any of them
        # may already be in the folder when this fails. Name only what is left.
        remaining = _journals_beside(target)
        left = (
            "These are still beside it: "
            + ", ".join(path.name for path in remaining)
            + ". "
            if remaining
            else "None is left beside it. "
        )
        raise OSError(
            f"{target.name} is missing, and moving the SQLite journals beside it, which "
            f"may hold its progress, into {folder} failed: {error}. {left}Any already "
            "moved are kept in that folder. Nothing was cancelled. Make sure "
            f"{folder.parent} is an ordinary folder that belongs to you, or move the "
            "journal files still beside the save somewhere safe, then cancel again."
        ) from error


# SQLITE_BUSY and SQLITE_LOCKED: another connection held the save for longer
# than the busy timeout. The sqlite3 module names them only from Python 3.11.
_LOCK_CODES = frozenset(
    code
    for code in (
        getattr(sqlite3, "SQLITE_BUSY", None),
        getattr(sqlite3, "SQLITE_LOCKED", None),
    )
    if code is not None
)


def _is_lock_error(error: sqlite3.Error) -> bool:
    """Whether SQLite gave up waiting for another connection to release the save.

    ``sqlite_errorcode`` only exists from Python 3.11, and older Anki builds
    bundle older Pythons, so without it the message SQLite wrote has to do. The
    code is the extended one (SQLITE_BUSY_RECOVERY, say), which keeps the
    primary code in its low byte.
    """
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None and _LOCK_CODES:
        return (code & 0xFF) in _LOCK_CODES
    message = str(error).lower()
    return "locked" in message or "busy" in message


# SQLITE_CORRUPT: a page is not what the database should hold. SQLITE_NOTADB is
# left out: a save without a readable header gives an install nothing to work on.
_CORRUPT_CODE = 11


def _is_damage(error: BaseException) -> bool:
    """Whether ``error`` says a save is damaged, not locked, slow or unreachable.

    A failed integrity check, or SQLite reporting a malformed database. Every
    other failure, a lock above all, keeps its usual meaning.
    """
    if isinstance(error, SaveDamagedError):
        return True
    if not isinstance(error, sqlite3.DatabaseError) or _is_lock_error(error):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) == _CORRUPT_CODE
    return "malformed" in str(error).lower()


def _recover_hot_journal(target: Path, logger=None, deadline: float = None) -> None:
    """Let SQLite roll back a transaction a crash left unfinished in the save.

    Only a read-write connection can roll back a hot rollback journal, and every
    step before the journal-mode switch opens the save read-only. SQLite refuses
    those with "attempt to write a readonly database", so the install failed for
    that start, and get_db's own connection then rolled the journal back under a
    session on the old save. One read takes the shared lock that rolls it back.
    On a WAL save the close may checkpoint, which the mode switch does anyway.
    """
    # Outside the try: TimeoutError is an OSError, and a spent budget stops the
    # install rather than being logged and passed over.
    timeout = _budget(deadline)
    try:
        conn = sqlite3.connect(_sqlite_uri(target, "rw"), uri=True, timeout=timeout)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as error:
        # A lock stops the install here, with SQLite's own error. Passed over, it
        # left the read-only steps to wait on the same lock with what remained of
        # the budget, so get_db recorded "the save import budget expired" instead
        # of the lock, and without a deadline the lock was waited out twice.
        if isinstance(error, sqlite3.Error) and _is_lock_error(error):
            raise
        # A save this connection cannot write: a read-only volume, a file SQLite
        # will not open read-write, "attempt to write a readonly database". The
        # read-only steps that follow may still read it, and report their own
        # error if they cannot, as they did before this step existed.
        _log(
            logger,
            "warning",
            f"Could not open {target.name} to roll back an unfinished write: {error}",
        )


def _holds_this_import(target: Path, info: dict, logger, deadline) -> bool:
    """Whether ``target`` already is this import, which an earlier start installed.

    A damaged save may be unable to say. An import staged to keep such a save
    unverified goes ahead regardless: whichever save it is, the unverified copy
    keeps it. Any other import is refused as a damaged save, with the notice that
    says what to do, rather than with SQLite's bare error.
    """
    try:
        return _installed_token(target, deadline) == info["token"]
    except sqlite3.DatabaseError as error:
        if not _is_damage(error):
            raise
        if not info.get("retain_unverified"):
            raise CurrentSaveDamagedError(
                _damaged_save_refusal(target, error)
            ) from error
        _log(
            logger,
            "warning",
            f"Could not read which import {target.name} holds: {error}",
        )
        return False


def commit_pending_import(target: Path, logger=None, deadline: float = None) -> bool:
    """Install before any runtime exists, refusing work staged in this process.

    Returns True if installed (or a prior crash installed it); False if there is
    nothing to do yet. Failures before replacement leave pending work available
    for retry or explicit cancellation. ImportInstalledError means replacement
    succeeded but final sync or cleanup failed. The installed token prevents a
    retry from reapplying the old import over subsequent game progress.

    ``deadline`` is an absolute ``time.monotonic()`` instant bounding the WHOLE
    installation rather than each SQLite step. The caller runs during add-on
    import, before Anki has a window to say what it is waiting for, so a locked
    save must not be waited on twice over.
    """
    target, _ = _paths(target)
    # Any attempt may be followed by construction of a runtime. Retrying after
    # reset_db or a module purge must wait for another full process start.
    attribute = "_ankimon_import_startup_attempts"
    identity = _process_identity()
    saved = getattr(sys, attribute, None)
    if saved is None or saved[0] != identity:
        saved = (identity, set())
        setattr(sys, attribute, saved)
    if str(target) in saved[1]:
        return False
    saved[1].add(str(target))
    info = pending_import_info(target)
    if info is None or info["process"] == _process_identity():
        return False

    try:
        target.lstat()
        missing = False
    except FileNotFoundError:
        # Nothing on disk to retain, and no installed token to check. An earlier
        # start may have installed this import before the save went missing;
        # installing it again loses nothing more. Refusing here let get_db create
        # a fresh save in this same start, which the next start then replaced
        # without a notice.
        missing = True
    if not missing and not target.is_file():
        raise OSError(
            f"{target.name} is not a file, so the save import prepared for it "
            "cannot be installed. Use Ankimon \u2192 Game \u2192 Cancel Pending Save "
            "Import to discard it."
        )

    if not missing:
        _recover_hot_journal(target, logger, deadline)
        if _holds_this_import(target, info, logger, deadline):
            _finish_installed_import(target, _recovery_copy(info), logger)
            return True
    if missing:
        # Before anything that can stop the install: if it stops, get_db opens a
        # fresh save at this path, and SQLite does not keep journals it finds
        # beside a database with no pages. If this move is what fails, get_db
        # refuses to open the path at all; see refuse_to_open_over_journals.
        _set_aside_orphaned_journals(
            target, info["recovery_path"].parent, logger, deadline
        )

    incoming = info["pending_path"]
    _verify_save(incoming, deadline)
    if _digest(incoming, deadline) != info["digest"]:
        raise ValueError("The pending save changed after it was confirmed")
    if missing:
        # A copy kept by an earlier attempt still describes a previous save.
        recovery = _recovery_copy(info)
    else:
        recovery = _retain_current_save(
            target,
            info["recovery_path"],
            logger,
            deadline,
            info.get("retain_unverified", False),
        )
        # Let SQLite merge and remove the OLD database's journals itself. Never
        # delete a WAL before replacement: a crash in that gap would lose committed
        # progress. A busy external connection refuses the mode change and import.
        conn = sqlite3.connect(
            _sqlite_uri(target, "rw"), uri=True, timeout=_budget(deadline)
        )
        try:
            mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if str(mode).lower() != "delete":
                raise RuntimeError("Could not safely close the old save's journal")
        finally:
            conn.close()
    if any(Path(str(target) + suffix).exists() for suffix in _JOURNAL_SUFFIXES):
        raise RuntimeError(
            "The old save still has SQLite journals; import remains pending"
        )

    fd, name = tempfile.mkstemp(
        prefix=".ankimon-install-", suffix=".db", dir=target.parent
    )
    os.close(fd)
    install_temp = Path(name)
    try:
        _copy_within(incoming, install_temp, deadline)
        _budget(deadline)
        _fsync_file(install_temp)
        if not missing and os.name != "nt":
            # mkstemp made the copy private, and the rename carried that over the
            # save, so every import or restore left it readable by its owner alone.
            # Set only after the sync, whose open would fail on a read-only mode.
            try:
                install_temp.chmod(target.stat().st_mode & 0o7777)
            except OSError:
                # A volume with fixed permissions refuses chmod.
                pass
        # A lock that clears within the budget must not cost a restart: this
        # process's attempt gate refuses a second install, so only this rename is
        # tried again.
        _retry_on_file_lock(lambda: os.replace(install_temp, target), deadline)
    except Exception:
        _remove_owned_copy(install_temp)
        raise
    _finish_installed_import(target, recovery, logger, install_temp)
    return True


def rebase_after_import(db, col) -> bool:
    """Exclude existing destination reviews before any mobile detection runs.

    The next profile open sees reviews pulled by the previous shutdown sync.
    Retire the marker and set that collection's exact watermark in one database
    transaction. Failures propagate so callers can skip detection and retry;
    the source save's watermark must never be used while the marker remains.
    """
    conn = db._get_connection()
    with conn:
        marker = conn.execute(
            "SELECT value FROM metadata WHERE key='import_rebase_pending'"
        ).fetchone()
        if marker is None or str(marker[0]) != "1":
            return False
        if col is None:
            raise RuntimeError(
                "The Anki collection is not available to finish the import"
            )
        watermark = col.db.scalar("SELECT MAX(id) FROM revlog")
        if watermark is None:
            watermark = 0
        if type(watermark) is not int or watermark < 0:
            raise ValueError("Could not read the collection's review watermark")
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('mobile_revlog_watermark', ?)",
            (str(watermark),),
        )
        conn.execute("DELETE FROM metadata WHERE key='import_rebase_pending'")
    return True
