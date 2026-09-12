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
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid


_TIMEOUT = 30.0
_PROCESS_ATTRIBUTE = "_ankimon_save_import_process"


class ImportInstalledError(RuntimeError):
    """The imported save is active, but its final sync or cleanup failed."""


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


def _connect_readonly(path: Path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=_TIMEOUT)
    deadline = time.monotonic() + _TIMEOUT
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
    return conn


def _verify_save(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 512:
        raise ValueError("The pending save is missing or truncated")
    conn = _connect_readonly(path)
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


def _snapshot(source: Path, dest: Path) -> None:
    """Build a verified single-file copy, including committed WAL contents."""
    source_conn = _connect_readonly(source)
    try:
        dest_conn = sqlite3.connect(dest, timeout=_TIMEOUT)
        try:
            deadline = time.monotonic() + _TIMEOUT

            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise TimeoutError("Timed out taking the import safety snapshot")

            source_conn.backup(dest_conn, pages=256, progress=progress, sleep=0.05)
            dest_conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    _verify_save(dest)
    _fsync_file(dest)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
    cancelling it. Every other failure leaves nothing staged.
    """
    target, directory = _paths(target)
    if pending_import_info(target) is not None:
        raise RuntimeError("An import is already pending; cancel it before choosing another save")
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


def cancel_pending_import(target: Path) -> bool:
    """Cancel staged work even if its manifest is damaged.

    Removing and fsyncing the manifest is the cancellation commit point. Once
    that succeeds, failure to delete an orphaned private staged copy must not
    make the UI claim cancellation failed: without ``pending.json`` no startup
    can install it. Invalid manifests are deliberately not trusted for paths;
    only locally-generated 32-hex-token database names are cleaned up.
    """
    target, directory = _paths(target)
    manifest = directory / "pending.json"
    if not manifest.exists():
        return False

    try:
        info = pending_import_info(target)
    except Exception:
        info = None

    # Commit cancellation before touching any staged data.
    manifest.unlink()
    _fsync_directory(directory)

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
            # The manifest is already durably gone, so this is only an orphaned
            # private file. A later cleanup/stage can retry without any chance
            # of installing it.
            pass
    try:
        _fsync_directory(directory)
    except OSError:
        # The cancellation itself was already fsynced above.
        pass
    return True


def _installed_token(target: Path) -> str | None:
    conn = _connect_readonly(target)
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
        ).fetchone() is None:
            return None
        row = conn.execute("SELECT value FROM metadata WHERE key='import_token'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


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


def commit_pending_import(target: Path, logger=None) -> bool:
    """Install before any runtime exists, refusing work staged in this process.

    Returns True if installed (or a prior crash installed it); False if there is
    nothing to do yet. Failures before replacement leave pending work available
    for retry or explicit cancellation. ImportInstalledError means replacement
    succeeded but final sync or cleanup failed. The installed token prevents a
    retry from reapplying the old import over subsequent game progress.
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

    if _installed_token(target) == info["token"]:
        _finish_installed_import(target, info["recovery_path"], logger)
        return True

    incoming = info["pending_path"]
    _verify_save(incoming)
    if _digest(incoming) != info["digest"]:
        raise ValueError("The pending save changed after it was confirmed")
    recovery = info["recovery_path"]
    # mkdir(mode=...) does not tighten pre-existing directories. Restrict both
    # levels before inspecting or writing private recovery material.
    recovery.parent.parent.mkdir(mode=0o700, exist_ok=True)
    recovery.parent.parent.chmod(0o700)
    recovery.parent.mkdir(mode=0o700, exist_ok=True)
    recovery.parent.chmod(0o700)
    if recovery.exists():
        # A failed previous replacement may be followed by more local play.
        # Retain that attempt's backup and capture the current save again.
        recovery = recovery.with_name(f"retry-{uuid.uuid4().hex}-{target.name}")

    fd, name = tempfile.mkstemp(prefix=".backup-", suffix=".db", dir=recovery.parent)
    os.close(fd)
    backup_temp = Path(name)
    try:
        _snapshot(target, backup_temp)
        os.replace(backup_temp, recovery)
        _fsync_directory(recovery.parent)
        _fsync_directory(recovery.parent.parent)
        _fsync_directory(target.parent)
    finally:
        _remove_owned_copy(backup_temp)

    # Let SQLite merge and remove the OLD database's journals itself. Never
    # delete a WAL before replacement: a crash in that gap would lose committed
    # progress. A busy external connection refuses the mode change and import.
    conn = sqlite3.connect(target.as_uri() + "?mode=rw", uri=True, timeout=_TIMEOUT)
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
        shutil.copyfile(incoming, install_temp)
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
