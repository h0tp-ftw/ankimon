"""Durable save imports applied before the next process builds its game state.

Staging never replaces a running session's database. A cancelled Anki close or
an add-on reload therefore leaves that session usable. The composition root
may commit pending work only before it opens any Ankimon database or creates
game objects. This module deliberately has no Anki, Qt, or services imports.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import uuid


_TIMEOUT = 30.0
# get_db installs pending work during add-on import, which Anki runs inside
# AnkiQt.__init__ -- before setupProfile, before any window is shown and before
# a progress dialog exists. A locked save there is Anki looking hung with
# nothing on screen, so the WHOLE installation gets one budget, the way the
# shutdown backup does, rather than a full SQLite timeout per file per step.
STARTUP_IMPORT_BUDGET = 30.0
_PROCESS_ATTRIBUTE = "_ankimon_save_import_process"


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


def _fsync_file(path: Path) -> None:
    # Windows FlushFileBuffers requires write access to the file handle.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    # Windows does not allow opening directories this way. File fsync and the
    # same-volume replace still apply there.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
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


def _connect_readonly(path: Path, deadline: float = None):
    timeout = _budget(deadline)
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)
    limit = time.monotonic() + timeout
    conn.set_progress_handler(lambda: int(time.monotonic() > limit), 2000)
    return conn


def _verify_save(path: Path, deadline: float = None) -> None:
    if not path.is_file() or path.stat().st_size < 512:
        raise ValueError("The pending save is missing or truncated")
    conn = _connect_readonly(path, deadline)
    try:
        if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("The save failed its SQLite integrity check")
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='captured_pokemon'"
        ).fetchone() is None:
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
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if sanitize_credentials:
            conn.execute("PRAGMA secure_delete=ON")
        with conn:
            if sanitize_credentials:
                # Explicit empty auth rows also prevent Settings from falling
                # back to this installation's legacy config.obf when an old save
                # has no config. A portable imported save never inherits local
                # credentials.
                conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
                conn.executemany("INSERT OR REPLACE INTO config VALUES (?, '')", [
                    ("leaderboard.username",), ("leaderboard.api_key",),
                ])
                if "user_data" in tables:
                    conn.execute("DELETE FROM user_data WHERE key IN ('username', 'api_key')")
            conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", [
                ("import_token", token), ("import_rebase_pending", "1"),
                # Do not merge destination legacy JSON into a whole-save import.
                # Ordinary schema upgrades still run in AnkimonDB construction.
                ("migrated", "true"), ("migrated_phase2", "true"),
            ])
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
    if (record.get("version") != 1 or record.get("target") != str(target)
            or not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None
            or not isinstance(record.get("process"), str)
            or not isinstance(record.get("digest"), str)):
        raise ValueError("The pending import record does not match this save")
    return {
        **record,
        "pending_path": directory / f"{token}.db",
        "recovery_path": target.parent / "ankimon_recovery" / f"pre-import-{token}" / target.name,
    }


def stage_import(
    snapshot: Path, target: Path, *, sanitize_credentials: bool = True
) -> dict:
    """Durably retain the chosen save without touching the runtime database.

    Returns pending_path, recovery_path and token. Recovery is reserved now and
    written from the final local save immediately before installation. Existing
    pending work must be cancelled explicitly before choosing another import.

    ``sanitize_credentials`` is True for portable imports/rescues. Backup
    Manager restores may set it False because their source is already private
    local recovery material owned by this installation.

    Raises ``ImportStagedError`` when publication succeeded but a later step
    did not: the import is armed for the next start and can only be stopped by
    cancelling it. Every other failure leaves nothing staged -- including the
    one after publication that is not ``ImportStagedError``: a manifest gone
    by read-back raises plain ``OSError``, because then nothing will install,
    and the staged copy is removed with it.
    """
    target, directory = _paths(target)
    if pending_import_info(target) is not None:
        raise ImportAlreadyPendingError(
            "An import is already pending; cancel it before choosing another save")
    token = uuid.uuid4().hex
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
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
            "version": 1, "target": str(target), "token": token,
            "process": _process_identity(), "digest": _digest(incoming),
        }
        with temp_manifest.open("x", encoding="utf-8") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_manifest, directory / "pending.json")
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
        raise ImportStagedError(str(error)) from error
    if info is None:
        # The manifest vanished between publishing and reading it back, so
        # nothing will install after all; do not strand the staged copy.
        _remove_owned_copy(incoming)
        raise OSError("The published pending import disappeared before it could be read back")
    return info


def _write_cancelled_record(manifest: Path, target: Path) -> None:
    """Overwrite the manifest in place with a record that installs nothing.

    In place rather than by rename: a new directory entry would need the very
    directory sync whose failure this has to survive, while the existing entry
    only needs its file synced. If the write or its sync fails, the original
    bytes are put back before raising, so this session still sees the import
    its caller is about to report could not be cancelled.
    """
    record = json.dumps({"version": 1, "target": str(target), "cancelled": True})
    with manifest.open("r+b") as handle:
        original = handle.read()
        try:
            handle.seek(0)
            handle.write(record.encode("utf-8"))
            handle.truncate()
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
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
        ).fetchone() is None:
            return None
        row = conn.execute("SELECT value FROM metadata WHERE key='import_token'").fetchone()
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
        deadline = time.monotonic() + _WORDING_BUDGET
        return _installed_token(target, deadline) == info["token"]
    except Exception:
        return None


def _log(logger, level: str, message: str) -> None:
    if logger is not None:
        try:
            logger.log(level, message)
        except Exception:
            pass


def _finish_installed_import(target, recovery, logger, install_temp=None) -> None:
    _log(logger, "info", f"Ankimon import installed. Previous save: {recovery}")
    try:
        # Retry this sync after a prior crash too, before retiring the manifest.
        _fsync_directory(target.parent)
        if install_temp is not None:
            _remove_owned_copy(install_temp)
        cancel_pending_import(target)
    except Exception as error:
        # The installed token prevents a later launch from reinstalling over
        # new progress. Surface this separately from a refused replacement.
        message = f"Imported save is active; final sync or cleanup did not finish: {error}"
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

    if not target.is_file():
        # Every step below reads the save, so without this the user gets a bare
        # SQLite "unable to open database file" on every start. The developer
        # save is never recreated, so that is forever.
        raise FileNotFoundError(
            f"{target.name} no longer exists, so the save import prepared for it "
            "cannot be installed. Use Ankimon \u2192 Cancel Pending Save Import to "
            "discard it."
        )

    if _installed_token(target, deadline) == info["token"]:
        _finish_installed_import(target, info["recovery_path"], logger)
        return True

    incoming = info["pending_path"]
    _verify_save(incoming, deadline)
    if _digest(incoming, deadline) != info["digest"]:
        raise ValueError("The pending save changed after it was confirmed")
    recovery = info["recovery_path"]
    # mkdir(mode=...) does not tighten pre-existing directories. Restrict both
    # levels before inspecting or writing private recovery material.
    recovery.parent.parent.mkdir(mode=0o700, exist_ok=True)
    recovery.parent.parent.chmod(0o700)
    recovery.parent.mkdir(mode=0o700, exist_ok=True)
    recovery.parent.chmod(0o700)
    if recovery.is_file():
        # A failed previous replacement may be followed by more local play, so
        # the snapshot taken now is the one holding everything. Move the older
        # attempt aside rather than redirecting this one: the canonical name is
        # the only recovery filename the user is ever shown -- the staging
        # notice quotes it once, before Anki closes, and nothing names the file
        # again afterwards. Redirecting left that advertised path holding the
        # stale first attempt while the genuinely final save sat beside it under
        # a name nobody had been given.
        os.replace(recovery, recovery.with_name(f"retry-{uuid.uuid4().hex}-{target.name}"))
        _fsync_directory(recovery.parent)
        _prune_superseded_recovery(recovery.parent, target.name)
    elif recovery.exists():
        # Something that is not a snapshot holds the name. Write beside it, as
        # this has always done: replacing it would fail the install outright.
        recovery = recovery.with_name(f"retry-{uuid.uuid4().hex}-{target.name}")

    fd, name = tempfile.mkstemp(prefix=".backup-", suffix=".db", dir=recovery.parent)
    os.close(fd)
    backup_temp = Path(name)
    try:
        _snapshot(target, backup_temp, deadline)
        os.replace(backup_temp, recovery)
        _fsync_directory(recovery.parent)
        _fsync_directory(recovery.parent.parent)
        _fsync_directory(target.parent)
    finally:
        _remove_owned_copy(backup_temp)

    # Let SQLite merge and remove the OLD database's journals itself. Never
    # delete a WAL before replacement: a crash in that gap would lose committed
    # progress. A busy external connection refuses the mode change and import.
    conn = sqlite3.connect(target.as_uri() + "?mode=rw", uri=True, timeout=_budget(deadline))
    try:
        mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(mode).lower() != "delete":
            raise RuntimeError("Could not safely close the old save's journal")
    finally:
        conn.close()
    if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise RuntimeError("The old save still has SQLite journals; import remains pending")

    fd, name = tempfile.mkstemp(prefix=".ankimon-install-", suffix=".db", dir=target.parent)
    os.close(fd)
    install_temp = Path(name)
    try:
        _copy_within(incoming, install_temp, deadline)
        _budget(deadline)
        _fsync_file(install_temp)
        os.replace(install_temp, target)
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
            raise RuntimeError("The Anki collection is not available to finish the import")
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
