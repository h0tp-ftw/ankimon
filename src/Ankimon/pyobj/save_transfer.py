"""Manual save export/import and migration from the removed AnkiWeb file sync.

Exports use SQLite snapshots with local credentials removed. Imports and rescues
require a verified snapshot, explicit confirmation, a safety backup and atomic
replacement. The migration preserves bare media saves in a private profile-local
recovery directory outside ``collection.media``. Progress counters select a
candidate for comparison; they do not prove one save contains another.

The automatic file sync was removed because Anki media downloads have local
arrival timestamps, so mtimes cannot identify the save with newer progress.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from aqt import mw
from aqt.utils import askUser, showInfo, showWarning
from PyQt6.QtWidgets import QFileDialog

from ..resources import user_path
from ..utils import close_anki

# Keep normal/developer partitions separate and name each distinct save by
# content. Older revisions wrote underscore-prefixed copies into
# ``collection.media`` to protect them from Delete Unused Files; new recovery
# material lives outside that directory so Anki media sync cannot upload it.
_SAVE_PREFIX = {"ankimon.db": "_ankimon_save_", "ankimonDEV.db": "_ankimon_save_dev_"}


def _recovery_store(media_dir: Path, *, create: bool = False) -> Path:
    """Profile-local media-save recovery storage that Anki does not sync."""
    recovery = Path(media_dir).parent / "ankimon-media-recovery"
    if create:
        recovery.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                recovery.chmod(0o700)
            except OSError:
                pass
    return recovery


# Use 128 bits of SHA-256 for compact protected filenames.
_DIGEST_CHARS = 32

# Settle when every candidate is readable and the comparison is resolved, or
# when the partition holds no candidate at all. Unreadable saves and failed
# rescues must remain eligible for a later scan. The profile flag stores the
# media files' stat fingerprint, so a download after startup re-arms migration —
# which is what makes settling on an empty folder safe: a save that lands later
# changes the fingerprint, and the next boot or media-sync stop rescans. A
# media-sync stop triggers a scan; it does not establish that the download
# succeeded.
_MIGRATION_FLAG = "ankimonMediaSyncRemovedV1"

# What the flag stores for a partition that was examined and held no candidate.
# Distinct from "" (nothing was computed), which never settles, and from every
# real fingerprint, which always contains a ":".
_EMPTY_MEDIA_FINGERPRINT = "empty"

# Remember the answered snapshot/folder independently: an unreadable neighbor
# requires another scan without repeating an already answered rescue prompt.
_MIGRATION_ANSWERED_FLAG = "ankimonMediaSyncRemovedAnsweredV1"

# Bound automatic probes so locks and oversized saves do not delay rescanning.
# Manual export/import retain their longer timeout for user-requested transfers.
MIGRATION_PROBE_TIMEOUT = 0.5

# Reload safety (F31): the (hook, handler) pair this module last registered,
# anchored on the services registry so it survives a re-execution of this module
# and can be removed before re-appending.
_MIGRATION_HOOK_RECORD = "_ankimon_media_migration_handlers"


# ---------------------------------------------------------------------------
# Reading a save without opening it read-write
# ---------------------------------------------------------------------------

def get_db_stats(db_path: Path, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    """Summarise an Ankimon save for side-by-side display.

    Returns ``None`` when the file cannot be read, so the caller can say
    "couldn't read this side" instead of rendering a healthy save as an empty
    one. That distinction matters: the previous dialog swallowed read errors and
    displayed ``Level: 1, Cash: 0``, which invites a user to overwrite the save
    that actually holds their progress.

    Opened read-only through a percent-encoded URI so a profile path containing
    spaces or non-ASCII characters still resolves, with an explicit busy timeout
    — the default 5 s is easy to exceed while the live connection is mid-write,
    and a timeout here would otherwise look like a corrupt file.

    ``timeout`` is generous (30 s) for the user-initiated Export/Import. The
    automatic migration passes ``MIGRATION_PROBE_TIMEOUT`` instead: a file it
    cannot read this second is rescanned on a later pass.
    """
    try:
        if not Path(db_path).is_file():
            return None
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        # Same wall-clock bound as _verify_sqlite_integrity: connect(timeout=)
        # covers lock waiting only, and the COUNT(*)s below scan whole tables.
        # Aborting surfaces as an exception and is read as "could not read this
        # side", which is the honest answer and keeps the migration armed.
        _deadline = time.monotonic() + timeout
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() > _deadline else 0, 2000
        )
    except Exception:
        return None

    stats: Dict[str, Any] = {
        "trainer_name": "-",
        "trainer_level": 0,
        "trainer_cash": 0,
        "pokemon": 0,
        "items": 0,
        "badges": 0,
        "history": 0,
    }
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)};")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        def _scalar(sql: str, params=(), default=0):
            """One value, tolerating a save written by an older schema.

            A missing column or renamed table reports the fields the save does
            have instead of reading as unreadable. A statement the progress
            handler ABORTED is not that: it means the probe budget ran out
            mid-count, and swallowing it would return 0 for a table that may
            hold thousands of rows — "unknown" indistinguishable from "empty",
            the one thing this function exists to keep apart, and a false
            (0, 0, 0) feeds straight into a comparison that can authorise a
            rescue over the save that actually holds the progress. So a
            deadline overrun, or an interruption raised by other means
            (``interrupt()``, a handler a test installed), is re-raised.
            """
            try:
                row = conn.execute(sql, params).fetchone()
                return row[0] if row and row[0] is not None else default
            except Exception as e:
                if time.monotonic() > _deadline:
                    raise
                if isinstance(e, sqlite3.OperationalError) and "interrupt" in str(e).lower():
                    raise
                return default

        def _as_int(value, default=0):
            # Tolerates only the CONVERSION failing — a level stored as '' or
            # 'None' by an older build — so _scalar's re-raise still stands.
            try:
                return int(value)
            except Exception:
                return default

        if "captured_pokemon" in tables:
            stats["pokemon"] = _scalar("SELECT COUNT(*) FROM captured_pokemon")
        if "items" in tables:
            stats["items"] = _scalar("SELECT SUM(quantity) FROM items")
        if "pokemon_history" in tables:
            stats["history"] = _scalar("SELECT COUNT(*) FROM pokemon_history")
        if "badges" in tables:
            # The achieved flag has been written as 1, 'true' and 'True' by
            # different generations of the badge code; match badges_functions
            # rather than only the integer form, or a healthy save reads as
            # zero badges.
            stats["badges"] = _scalar(
                "SELECT COUNT(*) FROM badges "
                "WHERE achieved IN (1, 'true', 'True')"
            )
        if "config" in tables:
            cfg = "SELECT value FROM config WHERE key = ?"
            stats["trainer_name"] = _scalar(cfg, ("trainer.name",), "-")
            stats["trainer_level"] = _as_int(_scalar(cfg, ("trainer.level",)))
            stats["trainer_cash"] = _as_int(_scalar(cfg, ("trainer.cash",)))
        return stats
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _format_stats(stats: Optional[Dict[str, Any]]) -> str:
    if stats is None:
        return "  (could not read this file)"
    return (
        f"  Trainer: {stats['trainer_name']}\n"
        f"  Level: {stats['trainer_level']}\n"
        f"  Cash: {stats['trainer_cash']}\n"
        f"  Pokemon: {stats['pokemon']}\n"
        f"  Items: {stats['items']}\n"
        f"  Badges: {stats['badges']}\n"
        f"  History entries: {stats['history']}"
    )


def _progress_key(stats: Optional[Dict[str, Any]]) -> tuple:
    """Rank candidates by captures, badges and history entries.

    These aggregates can decrease and do not establish containment. Tuple
    ordering only selects a candidate to display; rescue eligibility uses
    ``_dominates`` so a higher capture count cannot hide fewer badges.
    Cash and trainer level are excluded because spending and XP-curve changes
    can lower them without losing progress.
    """
    if stats is None:
        return (-1, -1, -1)
    return (stats["pokemon"], stats["badges"], stats["history"])


def _dominates(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    """Whether ``a`` meets every progress count in ``b`` and exceeds at least one.

    This decides whether to offer rescue, never authorizes replacement or
    proves containment. An unreadable save neither dominates nor is dominated.
    """
    if a is None or b is None:
        return False
    ka, kb = _progress_key(a), _progress_key(b)
    return ka != kb and all(x >= y for x, y in zip(ka, kb))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _active_db_path() -> Optional[Path]:
    try:
        from ..services import services

        if services.db is not None and getattr(services.db, "db_path", None):
            return Path(services.db.db_path)
    except Exception:
        pass
    fallback = user_path / "ankimon.db"
    return fallback if fallback.is_file() else None


def _sqlite_backup(source: Path, dest: Path, timeout: float = 30.0) -> None:
    """Take a consistent snapshot, including committed WAL pages.

    SQLite's online backup works in every journal mode. Opening the source
    read-only prevents creating a missing file or recovering its journal in
    place. Both lock retries and copying are bounded by ``timeout``.
    """
    # Imports and migration candidates are external files. A read-only source
    # must neither create a missing file nor recover a hot journal in place.
    uri = Path(source).resolve().as_uri() + "?mode=ro"
    src_conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    try:
        dest_conn = sqlite3.connect(str(dest), timeout=timeout)
        try:
            deadline = time.monotonic() + timeout

            def check_deadline(status, remaining, total):
                # backup() retries SQLITE_BUSY itself, beyond connect's busy
                # timeout. Bound both those retries and a large unlocked copy.
                if time.monotonic() > deadline:
                    raise TimeoutError("Timed out taking a save snapshot")

            src_conn.backup(dest_conn, pages=256, progress=check_deadline, sleep=0.05)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def _snapshot_save(source: Path, timeout: float = 30.0) -> Path:
    """Return a private verified snapshot; its owner must discard it afterwards.

    The same file supplies the displayed stats and the eventual replacement.
    A cloud download can replace the original pathname while a dialog or the
    safety backup runs, without changing the save the user approved.
    """
    from .ankimon_sync import _verify_sqlite_integrity

    fd, name = tempfile.mkstemp(prefix="ankimon-snapshot-", suffix=".db")
    os.close(fd)
    snapshot = Path(name)
    try:
        _sqlite_backup(source, snapshot, timeout=timeout)
        if not _verify_sqlite_integrity(snapshot, timeout=timeout):
            raise ValueError("The selected file is not a valid Ankimon save")
        return snapshot
    except BaseException:
        _discard_snapshot(snapshot)
        raise


def _discard_snapshot(snapshot: Optional[Path]) -> None:
    """Release only a caller-owned temporary snapshot and its SQLite sidecars."""
    if snapshot is not None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                Path(str(snapshot) + suffix).unlink(missing_ok=True)
            except OSError:
                pass


def _is_same_file(a: Path, b: Path) -> bool:
    """True if ``a`` and ``b`` name the same file on disk.

    ``resolve()`` catches the ordinary cases and works when the destination does
    not exist yet; ``os.path.samefile`` additionally catches hard links and
    aliases, but raises when either side is missing, so it only runs when both
    do.
    """
    try:
        if Path(a).resolve() == Path(b).resolve():
            return True
    except Exception:
        pass
    try:
        if Path(a).exists() and Path(b).exists():
            return os.path.samefile(str(a), str(b))
    except Exception:
        pass
    return False


def _strip_local_secrets(db_path: Path) -> None:
    """Strip current and legacy credentials from the private export copy.

    Developer saves may still use ``user_data.api_key`` without a config table.
    VACUUM removes freed pages so deleted secrets cannot be recovered from the
    exported bytes. Any failure aborts export without touching the live save.
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.execute("PRAGMA secure_delete = ON;")
        if "config" in tables:
            conn.execute("DELETE FROM config WHERE key = ?", ("leaderboard.api_key",))
        if "user_data" in tables:
            conn.execute("DELETE FROM user_data WHERE key = ?", ("api_key",))
        conn.commit()
        conn.execute("VACUUM;")
        conn.commit()
    finally:
        conn.close()


def export_save(parent=None) -> bool:
    """Write the ACTIVE Ankimon save to a file the user chooses."""
    from .ankimon_sync import (
        SYNC_LOCK_MESSAGE, _is_lock_error, _retry_on_lock, _verify_sqlite_integrity,
    )

    parent = parent or mw
    source = _active_db_path()
    if source is None or not source.is_file():
        showWarning("No Ankimon save was found to export.")
        return False

    stats = get_db_stats(source)
    trainer = (stats or {}).get("trainer_name") or "trainer"
    safe_trainer = "".join(c for c in str(trainer) if c.isalnum() or c in "-_") or "trainer"
    suggested = str(Path.home() / f"ankimon-save-{safe_trainer}.db")

    dest_str, _ = QFileDialog.getSaveFileName(
        parent, "Export Ankimon save", suggested, "Ankimon save (*.db)"
    )
    if not dest_str:
        return False
    dest = Path(dest_str)
    if dest.suffix.lower() != ".db":
        dest = dest.with_suffix(".db")

    # Refuse to export ONTO the live save. import_save has always guarded the
    # mirror image of this; without it here, os.replace() swaps the pathname
    # underneath the add-on's open SQLite connection — the handle keeps the old,
    # now-unlinked inode while ankimon.db names a new file, so subsequent writes
    # land where nothing will ever read them again (and on Windows it throws
    # WinError 5 instead). Checked after the .db normalisation, since that is
    # what can turn a different-looking choice into the same path.
    if _is_same_file(dest, source):
        showWarning(
            "That is the save Ankimon is currently using.\n\nChoose a different "
            "file or folder for the export — exporting onto the live save would "
            "replace the file Ankimon has open."
        )
        return False

    # The picker confirmed dest_str, which can differ from the normalized
    # filename. Never silently overwrite an existing archive.db when the user
    # selected a nonexistent archive.txt (or archive with no extension).
    if dest != Path(dest_str) and dest.exists() and not askUser(
        f"The export will be saved as:\n{dest}\n\n"
        "That file already exists. Replace it?",
        parent=parent,
        defaultno=True,
    ):
        return False

    # The destination may be shared/cloud-synced. Only sanitised bytes may ever
    # enter that directory, including temporary files and interrupted exports.
    tmp = None
    private = None
    try:
        private = _snapshot_save(source)
        _strip_local_secrets(private)

        if not _verify_sqlite_integrity(private):
            showWarning(
                "Export failed: the exported file did not pass an integrity "
                "check, so it was discarded. Your save is unchanged."
            )
            return False

        stats = get_db_stats(private)
        fd, tmp_name = tempfile.mkstemp(prefix="ankimon-export-", suffix=".db", dir=str(dest.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        shutil.copyfile(private, tmp)

        # Same lock ladder the import path uses. Exporting over an existing
        # file inside a OneDrive/Dropbox folder is the obvious thing a
        # two-desktop user does, and a bare os.replace there throws WinError 5
        # with no actionable text (issue #636).
        def publish():
            """Refuse an archive whose journal could replay over the export."""
            # Check immediately before every replace attempt, including retries:
            # a writer may have opened the archive while the snapshot was built.
            # Sidecars can also survive a crash with no open handles. They
            # belong to the destination, so never delete or recover them here.
            if any(Path(str(dest) + suffix).exists()
                   for suffix in ("-wal", "-shm", "-journal")):
                raise RuntimeError(
                    "The destination is in use or has an unfinished database "
                    "transaction. Choose a different file name for the export."
                )
            os.replace(tmp, dest)

        _retry_on_lock(publish)
        tmp = None
    except Exception as e:
        if _is_lock_error(e):
            showWarning(SYNC_LOCK_MESSAGE)
        else:
            showWarning(f"Export failed: {e}\n\nYour save is unchanged.")
        return False
    finally:
        _discard_snapshot(private)
        _discard_snapshot(tmp)

    showInfo(
        f"Save exported to:\n{dest}\n\n{_format_stats(stats)}\n\n"
        "Copy this file to your other computer and use "
        "Ankimon → Import Save File… there."
    )
    return True


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_save(parent=None) -> bool:
    """Replace the ACTIVE Ankimon save with a file the user chooses."""
    parent = parent or mw
    target = _active_db_path()
    collection = _active_collection()
    if target is None:
        showWarning("The Ankimon database is not ready yet; try again once Anki has finished loading.")
        return False

    src_str, _ = QFileDialog.getOpenFileName(
        parent, "Import Ankimon save", str(Path.home()), "Ankimon save (*.db)"
    )
    if not src_str:
        return False
    source = Path(src_str)

    if _is_same_file(source, target):
        showWarning("That file is the save Ankimon is already using. Nothing was changed.")
        return False

    snapshot = None
    try:
        snapshot = _snapshot_save(source)
        incoming = get_db_stats(snapshot)
        current = get_db_stats(target)
        if not askUser(
            "Replace your current Ankimon save with this file?\n\n"
            f"FILE YOU CHOSE\n{_format_stats(incoming)}\n\n"
            f"YOUR CURRENT SAVE\n{_format_stats(current)}\n\n"
            "The import takes effect on the next full Anki restart. Your final current "
            "save will be backed up before replacement. Leaderboard sign-in will be required.",
            parent=parent,
            defaultno=True,
        ):
            return False

        return _replace_active_save(snapshot, target, "Import", collection=collection)
    except Exception as e:
        showWarning(f"Import aborted: {e}. Nothing was replaced.")
        return False
    finally:
        _discard_snapshot(snapshot)


def _active_collection():
    """The collection whose review history belongs to the active profile."""
    from ..services import services

    return services.col if services.col is not None else mw.col


def _rebase_import_watermark(snapshot: Path, col) -> None:
    """Exclude this collection's existing reviews in the private imported copy.

    Restart does not reset a stored nonzero watermark, and shutdown itself can
    sync before restarting. Stamp MAX(revlog.id) before publishing the save,
    even when the source's watermark is ahead of this collection's. Future
    reviews remain detectable. A missing/unreadable collection aborts import.
    """
    if col is None:
        raise RuntimeError("The Anki collection is not available")
    watermark = col.db.scalar("SELECT MAX(id) FROM revlog")
    if watermark is None:
        watermark = 0
    if type(watermark) is not int or watermark < 0:
        raise ValueError("Could not read the collection's review watermark")
    conn = sqlite3.connect(str(snapshot), timeout=30)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('mobile_revlog_watermark', ?)",
                (str(watermark),),
            )
    finally:
        conn.close()


def _log_transfer_failure(context: str, error: Exception) -> None:
    """Record a non-fatal transfer problem without needing a logger argument."""
    try:
        from ..services import services

        services.logger.log("error", f"Ankimon: {context}: {error}")
    except Exception:
        pass


def _warn_about_pending_import(message: str, context: str) -> None:
    """Show a staged-import warning that cannot unwind into an abort handler.

    Every caller of this is reporting an import that IS armed. Letting Qt's
    failure travel up would hand that report to a generic ``except Exception``
    which says nothing was replaced -- the one thing that must never be said
    about a published import.
    """
    try:
        showWarning(message)
    except Exception as error:
        _log_transfer_failure(context, error)


def _replace_active_save(source: Path, target: Path, what: str, *, collection,
                         local_revision=None, local_digest=None) -> bool:
    """Prepare the approved save for a fresh process; never replace live state.

    Cancellation of Anki's asynchronous shutdown leaves the original runtime
    and database together. Installation and the final recovery snapshot happen
    before any database manager or game objects exist on the next full start.

    True means an import is pending — including the case where staging
    published one but could not finish cleanly, which is reported as pending
    rather than aborted and must not lead a caller to offer another save.
    """
    from ..save_import import (
        ImportAlreadyPendingError, ImportStagedError,
        pending_import_is_installed, stage_import,
    )
    from .ankimon_sync import get_ankimon_sync

    try:
        if _active_db_path() != Path(target) or _active_collection() is not collection:
            raise RuntimeError("The active profile or save changed; please try the import again")
        _rebase_import_watermark(source, collection)
        if local_revision is not None and _local_save_revision(target) != local_revision:
            return False
        if local_digest is not None:
            with get_ankimon_sync()._quiesce_live_db_connection(target) as closed:
                if not closed or _save_snapshot_digest(target) != local_digest:
                    return False
                pending = stage_import(source, target)
        else:
            pending = stage_import(source, target)
    except ImportAlreadyPendingError:
        # An earlier choice is still staged. "Nothing was replaced" is true of
        # this attempt and false of the session, which is the confusing half:
        # the rescue offer can return after a staging failure, and answering it
        # again must not read as though no import were coming.
        #
        # Both notices below are guarded for the reason the success notice is,
        # and more sharply: showWarning reaches into Qt through
        # mw.app.activeWindow(), this runs in a shutdown-adjacent state, and an
        # exception escaping here does not merely lose a message -- it unwinds
        # into import_save's "Import aborted: ... Nothing was replaced" handler
        # over a published, armed import. That is the mis-report this branch
        # exists to prevent.
        if pending_import_is_installed(Path(target)):
            # The record outlived the import it describes. Telling the user it
            # "will install at the next restart" describes a replacement that
            # has already happened, to somebody deciding what to do about the
            # save they are looking at.
            _warn_about_pending_import(
                f"{what} not started: the previous import has ALREADY installed "
                "and is the save you are playing now. Only its leftover record "
                "could not be cleared.\n\n"
                "Use Ankimon → Cancel Pending Save Import to clear that record, "
                "then try again. Nothing will be installed a second time.",
                f"{what} was refused over an already-installed import, "
                "but the notice could not be shown",
            )
            return True
        _warn_about_pending_import(
            f"{what} not started: a save import is already pending and will "
            "install at the next full Anki restart.\n\n"
            "Use Ankimon → Cancel Pending Save Import first if you want to "
            "choose a different save instead.",
            f"{what} was refused because an import is already pending, "
            "but the notice could not be shown",
        )
        return True
    except ImportStagedError as error:
        # Publication already happened, so this is not an abort: the save will
        # install at the next full start. Saying otherwise would leave the user
        # playing on towards a replacement they were told could not happen.
        _warn_about_pending_import(
            f"{what} could not be finished cleanly: {error}.\n\n"
            "Your current save is still active, but this import is now PENDING "
            "and will install at the next full Anki restart. Its final progress "
            "will still be retained in a recovery copy first. Use Ankimon → "
            "Cancel Pending Save Import if you do not want it.",
            f"{what} is staged but unfinished, and the notice could not be shown",
        )
        return True
    except Exception as error:
        showWarning(f"{what} aborted: {error}. Your current save is unchanged.")
        return False

    # Announcing the import is the last thing that can go wrong, and it can:
    # showInfo reaches into Qt, and this runs in a shutdown-adjacent state.
    # A torn-down parent widget must not travel back up to the caller's
    # "aborted, nothing was replaced" handler over a save that is staged.
    try:
        showInfo(
            f"{what} prepared for the next full Anki restart.\n\n"
            "Your current save stays active until Anki exits. At the next start, "
            "its final progress will be saved in a separately retained recovery copy "
            "before the imported save is installed:\n"
            f"{pending['recovery_path']}\n\n"
            "Please reopen Anki after it closes. If you keep editing, this import "
            "stays pending; use Ankimon → Cancel Pending Save Import to discard it.\n\n"
            "Leaderboard credentials are not imported. Sign in again after restart."
        )
    except Exception as error:
        _log_transfer_failure(f"{what} was prepared but its notice could not be shown", error)
    from ..events import events

    events.emit("save_import_prepared", target=str(target), recovery_path=str(pending["recovery_path"]))
    try:
        close_anki(raise_on_error=True)
    except Exception as error:
        showWarning(f"Anki could not close: {error}. Your current save is still active. "
                    "The prepared import will run on the next full restart, or you can cancel it.")
    return True


def cancel_pending_save_import() -> None:
    """Cancel a staged import and report filesystem failures through the menu."""
    from ..save_import import cancel_pending_import, pending_import_is_installed

    target = _active_db_path()
    try:
        # Asked BEFORE cancelling: removing the manifest is what makes the two
        # states indistinguishable afterwards.
        installed = target is not None and pending_import_is_installed(target)
        cancelled = target is not None and cancel_pending_import(target)
    except OSError as error:
        showWarning(f"The pending save import could not be cancelled: {error}. "
                    "Close anything using the Ankimon folder and try again.")
        return
    if cancelled and installed:
        from ..events import events

        events.emit("save_import_cancelled", target=str(target), installed=True)
        showInfo("That import had already installed: the save you are playing IS the "
                 "imported one. Only its leftover record was cleared, so nothing will "
                 "be installed again.\n\nYour previous save was retained before the "
                 "replacement — see Ankimon → Browse Recovered Saves.")
    elif cancelled:
        from ..events import events

        events.emit("save_import_cancelled", target=str(target))
        showInfo("The pending save import was cancelled. Your current save is unchanged.")
    else:
        showInfo("There is no pending save import for the active mode.")


def browse_recovered_saves() -> None:
    """Open the separately retained pre-import recovery folder."""
    target = _active_db_path()
    recovery = Path(target).parent / "ankimon_recovery" if target else user_path / "ankimon_recovery"
    from aqt.utils import openFolder

    try:
        recovery.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            recovery.chmod(0o700)
        openFolder(str(recovery))
    except OSError as error:
        showWarning(f"The recovery folder could not be opened: {error}. "
                    "Check access to the Ankimon folder and try again.")


# ---------------------------------------------------------------------------
# One-shot migration off the removed feature
# ---------------------------------------------------------------------------

def _media_dir() -> Optional[Path]:
    try:
        folder = mw.pm.profileFolder()
        if not folder:
            return None
        return Path(folder) / "collection.media"
    except Exception:
        return None


def _profile_flag(key: str):
    """A per-profile marker, or ``None`` when no profile is loaded."""
    try:
        return mw.pm.profile.get(key)
    except Exception:
        return None


def _set_profile_flag(key: str, value: str) -> None:
    try:
        mw.pm.profile[key] = value
        mw.pm.save()
    except Exception:
        pass


def _migration_done() -> bool:
    """Is this profile finished — for the media folder AS IT IS RIGHT NOW?

    The stored value is a fingerprint of the partition's media files, not a bare
    True, so "finished" expires the moment one of those files changes (see the
    migration flag policy above). Absent — or the bare ``True`` an earlier build
    wrote, which settled permanently — reads as not finished: one more pass, and
    it re-settles with a fingerprint unless there is real work.
    """
    stored = _profile_flag(_MIGRATION_FLAG)
    if not stored:
        return False
    try:
        return stored == _join_fingerprint(_current_fingerprint_entries())
    except Exception:
        return False


def _mark_migration_done(fingerprint: str) -> None:
    """Record WHAT was resolved, not merely THAT something was.

    ``fingerprint`` comes from the scan that reached this resolution — it
    describes the folder AS EXAMINED, not as it stands now. Those differ: the
    scan runs on a worker while Anki's media sync is running, so a download can
    land between the read and this call, and storing the folder's current state
    would settle on a file nothing ever looked at. A fingerprint that could not
    be computed is the empty string, which ``_migration_done`` reads as
    not-settled: the cost is one more scan, where the cost of wrongly settling
    is a save nobody offers back. A folder that was examined and held nothing
    is ``_EMPTY_MEDIA_FINGERPRINT`` instead, and does settle.
    """
    _set_profile_flag(_MIGRATION_FLAG, fingerprint)


def _current_fingerprint_entries() -> Dict[str, str]:
    media_dir = _media_dir()
    if media_dir is None:
        return {}
    target = _active_db_path()
    return _media_fingerprint_entries(media_dir, Path(target).name if target else "ankimon.db")


def _media_fingerprint_entries(media_dir: Path, target_db: str) -> Dict[str, str]:
    """``{filename: stat signature}`` for the partition's media saves.

    Deliberately cheap — no SQLite, no reads — because it runs on the
    profile-open stack before anything is dispatched. Size and mtime together
    are enough: Anki stamps a downloaded media file's mtime from the local clock
    at the moment it writes it, so a save that arrives from a peer always looks
    different from the one it replaced, and it only downloads at all when the
    sha1 differs. Missing paths are omitted; other stat failures retain an
    empty-string entry so the whole fingerprint stays unknown, including when
    other candidates are readable. An unreadable newcomer must not match a
    previously settled empty folder or subset of saves.

    Only sync-visible media paths belong here. Private recovery copies are
    scanned separately and cannot re-arm or settle a media-sync fingerprint.
    """
    entries: Dict[str, str] = {}
    for path in _media_candidate_paths(media_dir, target_db):
        entry = _fingerprint_entry(path)
        if entry is not None:
            entries[path.name] = entry
    return entries


def _fingerprint_entry(path: Path) -> Optional[str]:
    """A stat signature, ``None`` for absence, or ``""`` for unknown metadata."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except Exception:
        return ""
    return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def _join_fingerprint(entries: Dict[str, str]) -> str:
    """One string for a partition's entries, shared by the scan and the settle.

    An examined partition with no candidate is a real state, not an unknown
    one, so it gets a non-empty value of its own: the profile can settle on it,
    and ``_migration_done`` — which recomputes through this same function —
    expires that settle the moment a save lands. An unknown entry makes the
    entire fingerprint unknown (the empty string), which never settles.
    """
    if "" in entries.values():
        return ""
    return "|".join(sorted(entries.values())) or _EMPTY_MEDIA_FINGERPRINT


def _target_db_for(candidate: Path) -> str:
    """Which local database a media candidate belongs to.

    Developer mode keeps a separate ``ankimonDEV.db``, and the old sync wrote
    both under their own media names. Ranking them in one list would let a
    developer save with more test captures be crowned "best" and be offered as a
    rescue over the real save (or the reverse, in developer mode). Compare like
    with like instead.

    Content-addressed copies carry no "ankimonDEV", and the normal prefix is a
    prefix OF the developer one, so the developer test comes first. A digest can
    never spell "dev_" (v is not a hex digit), so the two prefixes cannot be
    confused in the other direction.
    """
    name = candidate.name
    if name.startswith(_SAVE_PREFIX["ankimonDEV.db"]):
        return "ankimonDEV.db"
    if name.startswith(_SAVE_PREFIX["ankimon.db"]):
        return "ankimon.db"
    return "ankimonDEV.db" if "ankimonDEV" in name else "ankimon.db"


def _protected_copy_name(target_db: str, digest: str) -> str:
    """Where a save with content ``digest`` is preserved in this partition."""
    return f"{_SAVE_PREFIX[target_db]}{digest}.db"


def _content_digest(path: Path) -> Optional[str]:
    """Return a digest of the file bytes, or ``None`` if unreadable.

    Preservation compares content rather than progress counters so distinct
    saves remain separate even when their summaries match.
    """
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:_DIGEST_CHARS]
    except Exception:
        return None


def _local_save_revision(path: Path) -> Optional[tuple]:
    """Cheap change token for the local DB and its writable SQLite sidecars.

    Include WAL commits and changes that leave the displayed counters equal.
    Exclude SHM: readers update it without changing the save. These stat calls
    let the UI reject stale worker results without opening SQLite on startup.
    """
    entries = []
    for suffix in ("", "-wal", "-journal"):
        try:
            stat = Path(str(path) + suffix).stat()
            entries.append((stat.st_dev, stat.st_ino, stat.st_size,
                            stat.st_mtime_ns, stat.st_ctime_ns))
        except FileNotFoundError:
            if not suffix:
                return None
            entries.append(None)
        except OSError:
            return None
    return tuple(entries)


def _save_snapshot_digest(path: Path) -> Optional[str]:
    """Identify committed content consistently across SQLite checkpoints."""
    snapshot = _snapshot_save(path, timeout=MIGRATION_PROBE_TIMEOUT)
    try:
        return _content_digest(snapshot)
    finally:
        _discard_snapshot(snapshot)


def _rescan_local_save(logger) -> None:
    """Discard an outdated comparison and request current statistics."""
    from ..services import services

    start_media_migration(services.settings, logger)


def _media_candidate_paths(media_dir: Path, target_db: str) -> list:
    """Every sync-visible path that could hold a save for ``target_db``.

    ONE definition is shared by the media fingerprint and the media side of the
    scanner. Private recovery copies are intentionally excluded: they must not
    affect the fingerprint that answers "did collection.media change?"

    Three kinds of sync-visible name may already exist: the bare one the removed
    feature wrote; underscore/content-addressed copies written by older
    migration revisions; and the pre-2024 legacy names, which are GLOBBED rather
    than reconstructed. The old code built them from
    ``Path(__file__).parents[2].name``, which is ``addons21`` in a normal install
    and ``src`` in a git checkout, and real profiles have been seen carrying the
    numeric package id instead — so the exact prefix cannot be computed after
    the fact, only matched.
    """
    paths = [media_dir / target_db]
    try:
        for pattern in (f"_*_{target_db}", _SAVE_PREFIX[target_db] + "*.db"):
            for path in sorted(media_dir.glob(pattern)):
                # The normal partition's glob deliberately over-matches — it
                # catches the developer partition's content-addressed names too
                # — so the partition is re-checked explicitly rather than
                # trusted to the pattern.
                if _target_db_for(path) == target_db and path not in paths:
                    paths.append(path)
    except Exception:
        pass
    return paths


def _recovery_candidate_paths(media_dir: Path, target_db: str) -> list:
    """Verified local recovery copies for ``target_db``, outside media sync."""
    recovery = _recovery_store(media_dir)
    if not recovery.is_dir():
        return []
    try:
        return [
            path
            for path in sorted(recovery.glob(_SAVE_PREFIX[target_db] + "*.db"))
            if _target_db_for(path) == target_db
        ]
    except Exception:
        return []


def _find_media_saves(media_dir: Path, target_db: str) -> tuple:
    """Every recoverable media save belonging to ``target_db``.

    This includes sync-visible legacy files in ``collection.media`` and verified
    local copies retained beside it. Returns ``(candidates, unreadable)``. A file
    that exists but will not open lands in ``unreadable`` rather than being
    silently dropped: it may be a real save merely locked by another process
    this second, and the caller must stay armed and rescan rather than settle as
    though the folder held nothing.
    """
    from .ankimon_sync import _verify_sqlite_integrity

    candidates = []
    unreadable = []
    seen = set()

    for path in (
        _media_candidate_paths(media_dir, target_db)
        + _recovery_candidate_paths(media_dir, target_db)
    ):
        try:
            resolved = path.resolve()
        except Exception:
            continue
        if resolved in seen or not path.is_file():
            continue
        # Only ever touch a file that positively identifies as an Ankimon save.
        # Bare names like ankimon.db are specific enough, but the check also
        # guards against a same-named file another tool put there.
        if not _verify_sqlite_integrity(path, timeout=MIGRATION_PROBE_TIMEOUT):
            unreadable.append(path)
            continue
        seen.add(resolved)
        candidates.append(path)
    return candidates, unreadable


def _offer_rescue_later(snapshot: Path, target: Path, collection,
                        local_revision: tuple, local_digest: str, logger) -> None:
    """Defer shutdown until profile-open returns; revalidate the approved save.

    Startup sync and modal dialogs can advance local progress before this
    callback runs. A changed save needs a fresh comparison and confirmation.
    """
    media_dir = _media_dir()

    def _go():
        try:
            if (_media_dir() != media_dir or _active_db_path() != target
                    or _active_collection() is not collection):
                return
            if _local_save_revision(target) != local_revision:
                _rescan_local_save(logger)
                return
            replaced = _replace_active_save(
                snapshot, target, "Rescue", collection=collection,
                local_revision=local_revision, local_digest=local_digest,
            )
            if not replaced and _local_save_revision(target) != local_revision:
                _rescan_local_save(logger)
        except Exception:
            pass
        finally:
            _discard_snapshot(snapshot)

    try:
        mw.progress.single_shot(0, _go, False)
    except Exception:
        _go()


def _notify_affected_user(logger) -> None:
    """Tell the users who actually had the feature ON that it is gone.

    Without this the removal is silent for exactly the population it affects: a
    two-device user learns nothing unless the rescue prompt happens to fire, and
    that only fires when the media copy is ahead of the local save on every
    counter — two devices that happen to be level produce no message at all.

    The stored ``misc.ankiweb_sync`` row is the only reliable way to identify
    them. The key is gone from DEFAULT_CONFIG, but the row persists in the
    config table (writes were INSERT OR REPLACE only, nothing ever deleted), so
    it can still be read here. It is deleted afterwards, so this is safe to run
    on every pass and cannot repeat. Clear the live settings cache as well so
    a later settings save cannot reinsert the retired row.
    """
    try:
        from ..services import services

        db = services.db
        if db is None:
            return
        raw = db.get_config_value("misc.ankiweb_sync", None)
        if raw in (None, "", False, 0, "0", "false", "False"):
            return

        showInfo(
            "Ankimon's automatic AnkiWeb save-sync has been removed.\n\n"
            "It decided which device's save was newer by comparing file "
            "timestamps, but AnkiWeb never sends a save's authoring time — so it "
            "could pick the wrong one and overwrite newer progress. It could not "
            "be made reliable, so it has been taken out rather than left to lose "
            "data.\n\n"
            "Your save on this computer is untouched. Media-save protection "
            "is checked separately; any files that could not be verified are "
            "reported and retried.\n\n"
            "To move a save between computers now, use Ankimon → Export Save "
            "File… on one and Import Save File… on the other."
        )
        try:
            # Through the committing helper, not a bare ``db.execute``: that
            # left the DELETE in an open transaction — invisible to the next
            # boot, so the notice repeated, and holding this connection's write
            # lock until some unrelated write happened to commit it.
            db.delete_config_value("misc.ankiweb_sync")
            config = getattr(getattr(services, "settings", None), "config", None)
            if config is not None:
                config.pop("misc.ankiweb_sync", None)
        except Exception:
            # Non-fatal: the worst case is the notice appearing once more.
            logger.log("info", "Could not clear the legacy misc.ankiweb_sync row.")
    except Exception as e:
        try:
            logger.log("error", f"Could not show the sync-removal notice: {e}")
        except Exception:
            pass


def _migration_scan(media_dir: Path, target: Optional[Path]) -> Dict[str, Any]:
    """Discover and protect media saves, then prepare a rescue comparison.

    Runs on a worker and returns plain data. Preserve the bare media save
    regardless of ranking, verifying any existing protected copy before reuse.
    Rank candidates for display and freeze the chosen save in a private
    snapshot. The local revision lets the UI reject an outdated comparison.

    The source signature is read before AND after the capture, and ``stable``
    says whether they agreed. Only a capture bound to one observed revision can
    release the media-sync guard.
    """
    notes: list = []
    unreadable: list = []
    written: list = []
    target_db = Path(target).name if target else "ankimon.db"

    # Taken BEFORE anything is read or written. This fingerprint deliberately
    # describes only collection.media: private recovery copies are outside Anki
    # media sync and must not make a settled media state look changed.
    entries = _media_fingerprint_entries(media_dir, target_db)

    # The revision the capture is BOUND to, read before anything is copied.
    # _preserve verifies the bytes it wrote, not that the source held still
    # while it read them, so without this a writer landing mid-capture would
    # leave a recovery copy of the old version described by the new version's
    # signature — and the callback, comparing new against new, would release
    # the sync guard over bytes that were never preserved.
    _, before_protection = _pending_media_protection(media_dir, target)

    protection = _protect_bare_saves(media_dir)
    notes.extend(protection["log"])
    # The state this result actually describes. The caller's dispatch-time
    # signature is not it: a transient stat failure there reads as "unknown",
    # and comparing that against a readable file later would discard a sound
    # scan. Taken after protection, so a change during ranking or snapshotting
    # still invalidates the result.
    _, captured_signature = _pending_media_protection(media_dir, target)
    # Protection only ever reads collection.media (read-only SQLite opens; its
    # copies and archives are written outside the folder), so an unequal pair
    # means somebody else wrote, not that we did.
    stable_capture = before_protection == captured_signature

    unreadable.extend(protection["unprotected"])
    written.extend(path for path in protection["protected"].values()
                   if _target_db_for(path) == target_db)

    saves, integrity_failures = _find_media_saves(media_dir, target_db)
    unreadable.extend(integrity_failures)

    def _result(outcome: str, **extra) -> Dict[str, Any]:
        base = {
            "outcome": outcome,
            "notify": False,
            "log": notes,
            "media_dir": media_dir,
            "target": target,
            "unreadable": unreadable,
            "media_path": None,
            "media_stats": None,
            "local_stats": None,
            "fingerprint": _join_fingerprint(entries),
            "protection": protection,
            "signature": captured_signature,
            "stable": stable_capture,
        }
        base.update(extra)
        return base

    if not saves:
        if unreadable:
            # Something is there that could not be judged this pass — a lock,
            # or damage that may yet be repaired — so stay armed to retry it.
            # The removal notice is independent of that and safe to run on
            # every pass.
            return _result("armed", notify=True)
        # Nothing at all for this partition. The folder may simply not have
        # received the peer's save yet — but that is what the fingerprint
        # settle is for: the save landing later changes it, and the next boot
        # or media-sync stop rescans. Staying armed instead dispatched a scan —
        # two glob passes over collection.media — on every profile open and
        # every media-sync stop, forever, for the majority of profiles that
        # never had the removed feature on. Settle on the examined-empty
        # fingerprint; the removal notice still runs from this result.
        return _result("empty", notify=True)

    # Read each candidate exactly ONCE: every one is a SQLite open on a file
    # that may be locked, so re-reading a path to re-compare it multiplies the
    # worst case by the number of comparisons.
    stats: Dict[Path, Dict[str, Any]] = {}
    for path in saves:
        summary = get_db_stats(path, timeout=MIGRATION_PROBE_TIMEOUT)
        if summary is None:
            unreadable.append(path)
        else:
            stats[path] = summary

    if not stats:
        notes.append((
            "info",
            "Ankimon: no save in the media folder could be read this pass; "
            "rescanning later.",
        ))
        return _result("armed")

    at_risk = media_dir / target_db
    preserved = protection["protected"].get(at_risk)

    local_revision = _local_save_revision(target) if target else None
    local_stats = (
        get_db_stats(target, timeout=MIGRATION_PROBE_TIMEOUT) if target else None
    )

    # JUDGE. Prefer eligible rescues before ranking by raw counters. Otherwise
    # a divergent save with more captures can hide an eligible copy and settle
    # the folder without ever offering it. With no eligible copy, keep the
    # highest-ranked candidate for the existing divergence/equality handling.
    best = max(stats, key=lambda p: (
        _dominates(stats[p], local_stats), _progress_key(stats[p]),
    ))
    media_path, media_stats = best, stats[best]
    if best == at_risk and preserved is not None and (
        preserved in written or preserved in stats
    ):
        # Prefer the protected path only if this scan read or verified it.
        media_path = preserved

    local_digest = None
    if target is not None and local_stats is not None:
        try:
            local_digest = _save_snapshot_digest(target)
        except Exception:
            pass

    # A save that will not open is UNKNOWN, never empty — the distinction the
    # whole comparison rests on, and one _progress_key cannot make on its own,
    # since it floors an unreadable save to (-1, -1, -1). A local save merely
    # locked this second would otherwise lose to any readable media copy, be
    # offered against a side the dialog renders as "could not read this file",
    # and then, if the user sensibly declined, settle the profile on a
    # comparison that never happened.
    if target is not None and (
        local_stats is None or local_revision is None or local_digest is None
    ):
        notes.append((
            "info",
            f"Ankimon: {Path(target).name} could not be read this pass; "
            "not comparing saves, rescanning later.",
        ))
        return _result("armed", media_path=media_path, media_stats=media_stats)

    # Freeze the chosen file on this worker, then read the displayed statistics
    # from that snapshot. Ranking/preservation above can span a media download;
    # their earlier stats must never describe different bytes from the rescue.
    snapshot = None
    try:
        snapshot = _snapshot_save(media_path, timeout=MIGRATION_PROBE_TIMEOUT)
        media_stats = get_db_stats(snapshot, timeout=MIGRATION_PROBE_TIMEOUT)
        if media_stats is None:
            raise ValueError("Could not read the rescue snapshot")
        # Remember the content actually shown, not just the folder's stat
        # signature: releasing a lock can expose a different candidate without
        # changing any file's size or mtime. Hash the private verified snapshot
        # here on the worker, never the mutable media path on the UI thread.
        candidate_digest = _content_digest(snapshot)
        if candidate_digest is None:
            raise ValueError("Could not identify the rescue snapshot")
    except Exception as e:
        _discard_snapshot(snapshot)
        notes.append(("info", f"Could not snapshot {media_path.name}: {e}; rescanning later."))
        return _result("armed")

    return _result(
        "compare",
        snapshot_path=snapshot,
        candidate_digest=candidate_digest,
        media_path=media_path,
        media_stats=media_stats,
        local_stats=local_stats,
        local_revision=local_revision,
        local_digest=local_digest,
    )


def _preserve(at_risk: Path, media_dir: Path, target_db: str,
              notes: list, unreadable: list, written: list) -> Optional[Path]:
    """Keep a verified protected copy without changing any existing media file.

    Reuse a digest-named copy only when its bytes still match. If that name is
    occupied by damaged or different content, try numbered alternatives. Read
    via SQLite's backup API so committed WAL pages are included. Read-only
    source access refuses to recover an external rollback journal in place.
    """
    from .ankimon_sync import _atomic_write_over, _verify_sqlite_integrity

    tmp = None
    dest = None
    try:
        recovery_dir = _recovery_store(media_dir, create=True)

        def destination(digest):
            """Find an identical copy or an unused name, skipping damaged copies."""
            base = recovery_dir / _protected_copy_name(target_db, digest)
            candidate, suffix = base, 0
            while candidate.exists() or candidate.is_symlink():
                if _content_digest(candidate) == digest:
                    return candidate
                suffix += 1
                candidate = base.with_name(f"{base.stem}-{suffix}.db")
            return candidate

        fd, name = tempfile.mkstemp(prefix="ankimon-protect-", suffix=".db")
        os.close(fd)
        tmp = Path(name)
        _sqlite_backup(at_risk, tmp, timeout=MIGRATION_PROBE_TIMEOUT)
        if not _verify_sqlite_integrity(tmp, timeout=MIGRATION_PROBE_TIMEOUT):
            raise OSError(f"the copy of {at_risk.name} did not verify")
        digest = _content_digest(tmp)
        if digest is None:
            raise OSError(f"could not read back the copy of {at_risk.name}")
        dest = destination(digest)
        if dest.is_file():
            return dest
        _atomic_write_over(tmp, dest)
        written.append(dest)
        notes.append((
            "info",
            f"Ankimon: preserved {at_risk.name} as {dest} "
            "outside collection.media so Anki media sync cannot upload it. "
            "Nothing was deleted or overwritten.",
        ))
        return dest
    except Exception as e:
        notes.append(("error", f"Could not preserve {at_risk.name} in media: {e}"))
        # Stay armed and retry next pass: the at-risk file is still sitting
        # there under a name a media check can delete.
        unreadable.append(dest if dest is not None else at_risk)
        return None
    finally:
        _discard_snapshot(tmp)


def _protect_bare_saves(media_dir: Path) -> Dict[str, Any]:
    """Capture both modes on the worker while media sync is paused.

    A locked/corrupt SQLite source
    gets a labelled raw archive including sidecars, never a claimed valid save.
    Validation, ranking and recovery decisions remain eligible for retry.
    """
    result = {"protected": {}, "unprotected": [], "archives": [], "archived_sources": [], "log": []}
    for name in _SAVE_PREFIX:
        source = media_dir / name
        try:
            source.stat()
        except FileNotFoundError:
            continue
        except OSError:
            pass  # Unknown/unreadable is not the same as an absent file.
        protected = _preserve(source, media_dir, name, result["log"], [], [])
        if protected is not None:
            result["protected"][source] = protected
            continue
        result["unprotected"].append(source)
        archive = None
        try:
            fd, temporary = tempfile.mkstemp(prefix="ankimon-unverified-", suffix=".zip")
            os.close(fd)
            archive = Path(temporary)
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.write(source, source.name)
                for suffix in ("-wal", "-shm", "-journal"):
                    sidecar = Path(str(source) + suffix)
                    if sidecar.exists():
                        bundle.write(sidecar, sidecar.name)
            digest = _content_digest(archive)
            if digest is None:
                raise OSError("Could not verify the raw archive bytes")
            recovery_dir = _recovery_store(media_dir, create=True)
            destination = recovery_dir / f"_ankimon_unverified_{name}_{digest}.zip"
            base, suffix = destination, 0
            while destination.exists() or destination.is_symlink():
                if _content_digest(destination) == digest:
                    break
                suffix += 1
                destination = base.with_name(f"{base.stem}-{suffix}.zip")
            from .ankimon_sync import _atomic_write_over
            if not destination.exists():
                _atomic_write_over(archive, destination)
            result["archives"].append(destination)
            result["archived_sources"].append(source)
        except Exception as error:
            result["log"].append(("error", f"Could not archive {source}: {error}"))
        finally:
            _discard_snapshot(archive)
    return result


def _resume_deferred_media_sync() -> bool:
    """Re-request one media sync the guard turned away; say whether it went.

    The answer matters because every "no" here is a stand-down, not a refusal:
    the request is still owed, and the caller puts it back rather than dropping
    it. Anki has no deferred request of its own to fall back on, and in the one
    state this most often declines for -- a backup restore in progress -- it has
    switched its own unattended sync off too, so a dropped request is simply
    gone for the session.

    Anki reads ``media_syncing_enabled()`` once, when a sync starts, and passes
    it into the backend call; a collection sync that saw False finishes without
    media and nothing re-requests it. Clearing the guard only makes the NEXT
    attempt permissible, and Anki's own next attempt is a profile close or a
    periodic tick whose clock the skipped sync has just reset. Ask for one now
    instead. ``MediaSyncer.start`` re-checks the user's preference and sign-in,
    so this cannot sync for a user who turned media sync off.

    Ask for it the way Anki's own unattended timer does. Nobody clicked for
    this request, and MediaSyncer only keeps a failed one out of a dialog when
    it is marked periodic. That timer's other condition, a backup restore in
    progress, is the one thing ``start`` does not check for itself.
    """
    try:
        syncer = getattr(mw, "media_syncer", None)
        if syncer is None or getattr(mw, "col", None) is None:
            return False
        if getattr(mw, "restoring_backup", False):
            return False
        syncer.start(True)
        return True
    except Exception:
        # Never let a restart attempt keep the guard from being released.
        return False


def _reading_to_display_the_preference() -> bool:
    """Is this gate being read by Anki's Preferences dialog rather than a sync?

    ``Preferences.setup_network`` reads ``media_syncing_enabled()`` only to tick
    its "Synchronize audio and images too" box, and ``update_network`` writes
    whatever that box shows straight back into ``pm.profile`` on OK. Answering
    False there would turn the user's real AnkiWeb preference off for good, and
    they would never see it happen. The guard exists to delay a sync, never to
    edit a setting, so it stands aside for that reader.

    Failing open is the safe direction: Preferences starts no sync.
    """
    try:
        frame = sys._getframe(1)
        while frame is not None:
            if frame.f_globals.get("__name__") == "aqt.preferences":
                return True
            frame = frame.f_back
    except Exception:
        pass
    return False


def _guard_uncaptured_media(media_dir: Path, protection: Dict[str, Any]) -> None:
    """Pause media sync for this profile only while original bytes are at risk.

    Anki checks this method for collection-triggered and periodic media sync.
    Keep its preference untouched; the in-memory guard clears on a successful
    capture and automatically allows other profiles. Anchor it on the profile
    manager so an addon module reload cannot stack wrappers or lose the guard.

    A sync turned away while the guard was up is remembered and re-requested
    when the guard clears, because Anki keeps no deferred request of its own.
    Anki reads the gate on a background thread, so the record-and-release pair
    is taken under a lock: without it a request could be remembered just after
    the release that would have replayed it, and be dropped after all.
    """
    uncaptured = set(protection["unprotected"]) - set(protection["archived_sources"])
    pm = mw.pm
    state = getattr(pm, "_ankimon_media_protection_guard", None)
    if not isinstance(state, dict):
        if not uncaptured:
            return
        original = pm.media_syncing_enabled
        state = {"blocked": set(), "deferred": set(), "lock": threading.Lock()}

        def enabled():
            # The preference first: Anki's own media_syncing_enabled is a dict
            # lookup that cannot fail, and a user who has media sync off needs
            # no answer from the disk at all.
            allowed = original()
            if not allowed or _reading_to_display_the_preference():
                return allowed
            try:
                folder = Path(pm.profileFolder()) / "collection.media"
            except Exception:
                # profileFolder(create=True) makes directories, so a base on a
                # volume that went away raises here. Three callers read this --
                # the Preferences dialog, the periodic timer's Qt slot, and the
                # sync worker -- and none of them expect it to. Fail open: the
                # guard only ever delays a sync, so answering "allowed" costs at
                # most one unprotected pass, which the next scan re-arms.
                return True
            with state["lock"]:
                if folder not in state["blocked"]:
                    return True
                # Only a sync the user's own preference would have permitted
                # counts as deferred; nothing else may be restarted later.
                state["deferred"].add(folder)
            return False

        pm.media_syncing_enabled = enabled
        pm._ankimon_media_protection_guard = state
    state.setdefault("deferred", set())
    lock = state.setdefault("lock", threading.Lock())
    with lock:
        if uncaptured:
            state["blocked"].add(media_dir)
            deferred = False
        else:
            state["blocked"].discard(media_dir)
            deferred = media_dir in state["deferred"]
            state["deferred"].discard(media_dir)
    # Outside the lock: restarting a sync re-enters Anki, which reads the gate.
    if deferred and not _resume_deferred_media_sync():
        # Standing down is not the same as being done. Put the request back so a
        # later release -- or the next profile open, which is where Anki clears
        # restoring_backup -- can still replay it. Dropping it here left the
        # user who restored a backup and then synced by hand with no media sync
        # for the rest of the session.
        with lock:
            state["deferred"].add(media_dir)


_LAST_PROTECTION_NOTICE = None


def _report_protection(result: Dict[str, Any], logger) -> None:
    """Keep the removal announcement separate from verified preservation."""
    global _LAST_PROTECTION_NOTICE
    for level, message in result["log"]:
        logger.log(level, message)
    identity = (tuple(map(str, result["protected"].values())),
                tuple(map(str, result["unprotected"])), tuple(map(str, result["archives"])))
    if identity == _LAST_PROTECTION_NOTICE or not any(identity):
        return
    _LAST_PROTECTION_NOTICE = identity
    lines = []
    if result["protected"]:
        lines.append("Verified recovery copies:\n" + "\n".join(map(str, result["protected"].values())))
    if result["unprotected"]:
        lines.append("Could not create a verified recovery save for:\n" +
                     "\n".join(map(str, result["unprotected"])))
        if result["archives"]:
            lines.append("Raw files were archived with their SQLite sidecars. These archives "
                         "are unverified and may require recovery:\n" +
                         "\n".join(map(str, result["archives"])))
        lines.append("Ankimon will retry. Keep these files until you have verified your progress.")
        if set(result["unprotected"]) - set(result["archived_sources"]):
            lines.append("Media sync is paused for this profile because some original files "
                         "could not be copied at all. Close anything locking those files and "
                         "restart Anki to retry. Your Anki sync preferences have not changed.")
        showWarning("\n\n".join(lines))
    else:
        # Paths remain available in the Ankimon log without a popup every boot.
        logger.log("info", "\n\n".join(lines))


def _apply_migration_result(result: Dict[str, Any], logger) -> None:
    """Apply a scan and release its snapshot unless a deferred rescue owns it."""
    try:
        _apply_migration_decision(result, logger)
    finally:
        _discard_snapshot(result.pop("snapshot_path", None))


def _apply_migration_decision(result: Dict[str, Any], logger) -> None:
    """Log, prompt and update profile flags on the main thread.

    Recheck the local revision before displaying worker statistics or saving
    an answer. Accepted rescues recheck again in their deferred callback.
    """
    for level, message in result.get("log", ()):
        try:
            logger.log(level, message)
        except Exception:
            pass

    if result.get("protection"):
        _report_protection(result["protection"], logger)
    if result.get("notify"):
        _notify_affected_user(logger)

    outcome = result.get("outcome")
    if outcome == "empty":
        # Nothing in this partition: settle on the examined-empty fingerprint,
        # unless the active save changed partitions while the worker ran and
        # the scan described the wrong one. ``_migration_done`` would catch
        # that on its own recompute; the guard only saves a pointless write.
        if _active_db_path() == result.get("target"):
            _mark_migration_done(result.get("fingerprint", ""))
        return
    if outcome != "compare":
        return

    target = result.get("target")
    collection = _active_collection()
    local_revision = result.get("local_revision")
    local_digest = result.get("local_digest")
    if target is not None:
        if _active_db_path() != Path(target):
            return
        if (local_revision is None or local_digest is None
                or _local_save_revision(target) != local_revision):
            _rescan_local_save(logger)
            return
    media_stats = result.get("media_stats")
    local_stats = result.get("local_stats")
    fingerprint = result.get("fingerprint", "")
    # A pass that must stay armed for an unreadable file never settles, so the
    # answer is remembered on its own or the question repeats every boot. Scope
    # it to BOTH this folder and the chosen snapshot: a previously locked save
    # can become the best candidate without changing the folder fingerprint.
    # Legacy folder-only answers safely reoffer once.
    candidate_digest = result.get("candidate_digest")
    answer_identity = (
        f"v2:{candidate_digest}:{fingerprint}"
        if fingerprint and candidate_digest else ""
    )
    answered = bool(answer_identity) and _profile_flag(_MIGRATION_ANSWERED_FLAG) == answer_identity

    if target is not None and _dominates(media_stats, local_stats) and not answered:
        if askUser(
            "Ankimon's automatic AnkiWeb save-sync has been removed — it "
            "could not tell reliably which device's save was newer, and "
            "sometimes overwrote the wrong one.\n\n"
            "A save recovered from your Anki media folder (synced from AnkiWeb, "
            "if media sync is on) is further along than the save on this "
            "computer on every count Ankimon can compare:\n\n"
            f"RECOVERED MEDIA SAVE\n{_format_stats(media_stats)}\n\n"
            f"ON THIS COMPUTER\n{_format_stats(local_stats)}\n\n"
            "Load the recovered copy? Compare the two above first — Ankimon "
            "counts what each save holds, it cannot tell whether one contains "
            "the other. Your current save will be backed up before anything is "
            "replaced, and Anki will close so the copy can be loaded cleanly."
            "\n\n"
            "If you say no, nothing changes — the recovery copy stays preserved "
            "locally, and you will not be asked about it again unless a "
            "different media save arrives.",
            parent=mw,
            defaultno=True,
        ):
            # Deliberately NOT settled here. On success the replace closes Anki;
            # the next boot re-runs this, finds the media copy no longer ahead of
            # the (now equal) local save, skips the prompt and settles then. On
            # FAILURE — a refused backup, a persisting file lock — the flag is
            # still unset, so the user is offered the rescue again next launch
            # instead of silently losing their only route back to that data.
            _offer_rescue_later(
                result.pop("snapshot_path"), Path(target), collection,
                local_revision, local_digest, logger,
            )
            return
        if _active_collection() is not collection or _active_db_path() != Path(target):
            return
        if _local_save_revision(target) != local_revision:
            _rescan_local_save(logger)
            return
        # Declined: remember this candidate in this folder, whether or not this
        # pass goes on to settle.
        _set_profile_flag(_MIGRATION_ANSWERED_FLAG, answer_identity)
    elif (
        not answered
        and target is not None
        and media_stats is not None
        and local_stats is not None
        and not _dominates(local_stats, media_stats)
        and _progress_key(media_stats) != _progress_key(local_stats)
    ):
        # DIVERGED from the local save. Neither side is a superset, so there is
        # no version of "load this one" that does not throw away progress, and
        # offering the replace would be the same lie the removed sync told. Say
        # what is true and leave both files alone.
        showInfo(
            "Ankimon's automatic AnkiWeb save-sync has been removed — it "
            "could not tell reliably which device's save was newer.\n\n"
            "There is a save recovered from your Anki media folder that has "
            "DIVERGED from the one on this computer: each contains progress the "
            "other does not, so neither can simply replace the other.\n\n"
            f"RECOVERED MEDIA SAVE ({result['media_path']})\n"
            f"{_format_stats(media_stats)}\n\n"
            f"ON THIS COMPUTER\n{_format_stats(local_stats)}\n\n"
            "Nothing has been changed and nothing has been deleted. If you want "
            "the recovered copy, load the recovery file shown above with "
            "Ankimon → Import Save File… — your current save is backed up before "
            "it is replaced."
        )
        _set_profile_flag(_MIGRATION_ANSWERED_FLAG, answer_identity)

    if result.get("unreadable"):
        # Something in the folder exists but could not be judged this pass — a
        # lock, or damage that may yet be repaired. Notify, but stay armed so a
        # later scan can still protect and offer it.
        _notify_affected_user(logger)
        try:
            logger.log(
                "info",
                "Ankimon: "
                + ", ".join(sorted(p.name for p in result["unreadable"]))
                + " could not be read this pass; rescanning later.",
            )
        except Exception:
            pass
        return

    # A genuine terminal state: every candidate readable and the rescue
    # answered. The notice runs here too — the users most likely to have nothing
    # left in media are the most likely to have had the feature on — and it
    # cannot repeat, because it deletes the config row it keys off.
    _notify_affected_user(logger)
    _mark_migration_done(fingerprint)


def run_media_migration(settings_obj, logger) -> None:
    """Protect, and offer to rescue, whatever the removed sync left in media.

    The SYNCHRONOUS form: scan and act on the calling thread. Used directly by
    tests. Anki's
    own callers go through ``start_media_migration``, which keeps the file work
    off the profile-open stack.

    Runs until it RESOLVES for a profile, and re-arms whenever the media folder
    changes underneath a resolution. Failures are logged and swallowed so the
    migration cannot stop Ankimon from loading.
    """
    try:
        if _migration_done():
            return
        media_dir = _media_dir()
        if media_dir is None or not media_dir.is_dir():
            return          # no profile / no media folder — retry later
        _apply_migration_result(_migration_scan(media_dir, _active_db_path()), logger)
    except Exception as e:
        try:
            logger.log("error", f"AnkiWeb sync-removal migration failed: {e}")
        except Exception:
            pass


# In-flight state for the backgrounded form. ``rerun`` is what makes a request
# that arrives mid-scan safe to accept: dropping it would lose the post-sync
# pass whenever the boot scan is still running when the download lands, which is
# exactly the ordering the post-sync pass exists to cover.
_MIGRATION_SCAN_STATE = {"running": False, "rerun": False}
_MIGRATION_RETRY_DELAY = 30.0


def _schedule_migration_retry(settings_obj, logger) -> None:
    """Ask for another pass later, without going through a media-sync hook.

    The add-on's only recurring rescan trigger is
    ``gui_hooks.media_sync_did_start_or_stop``, and ``MediaSyncer.start``
    returns before ``start_monitoring`` when the gate says no. So exactly while
    the guard is up, the 5-minute periodic media sync that would have fired that
    hook fires nothing at all -- the guard suppresses its own retry. The
    ``retry_at`` delay recorded after a failed capture had no clock behind it,
    and a file that stopped being locked mid-session went unnoticed until the
    user synced by hand or closed the profile, with media sync off throughout.

    One timer at a time, cleared when it fires. A capture that succeeds releases
    the guard and stops the cycle; nothing reschedules from here.
    """
    if _MIGRATION_SCAN_STATE.get("retry_scheduled"):
        return

    def _retry() -> None:
        _MIGRATION_SCAN_STATE["retry_scheduled"] = False
        try:
            start_media_migration(settings_obj, logger)
        except Exception:
            pass

    try:
        # A little past the throttle, so the pass it asks for is not refused by
        # the very retry_at that scheduled it.
        mw.progress.single_shot(int(_MIGRATION_RETRY_DELAY * 1000) + 250, _retry, True)
        _MIGRATION_SCAN_STATE["retry_scheduled"] = True
    except Exception:
        # No timer is a missed retry, not a failure: a manual sync and the next
        # profile open still rescan.
        pass


def _pending_media_protection(media_dir: Path, target: Optional[Path]):
    """Stat both bare saves and sidecars without opening or copying their bytes.

    SHM is left out for the reason ``_local_save_revision`` leaves it out: it is
    a rebuildable index rather than save content, and merely reading updates it.
    This scan is one of those readers. Every read-only open it performs on a
    WAL-mode media save -- ``get_db_stats``, ``_sqlite_backup`` under
    ``_protect_bare_saves``, ``_snapshot_save`` -- creates that save's ``-shm``
    or restamps the existing one. Counting that as a source change would make
    the before/after pair in ``_migration_scan`` disagree on every single pass:
    ``stable`` would never be true, so the media-sync guard would never release
    and ``_done`` would re-dispatch the scan forever.
    """
    protection = {"protected": {}, "unprotected": [], "archives": [],
                  "archived_sources": [], "log": []}
    signature = []
    for name in _SAVE_PREFIX:
        source = media_dir / name
        for suffix in ("", "-wal", "-journal"):
            path = Path(str(source) + suffix)
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            except OSError:
                signature.append((path.name, "unknown"))
            else:
                signature.append((path.name, stat.st_size, stat.st_mtime_ns,
                                  stat.st_ctime_ns, stat.st_ino, stat.st_mode))
            if not suffix:
                protection["unprotected"].append(source)
    # A newly downloaded candidate must also bypass an unreadable-file delay.
    entries = _media_fingerprint_entries(media_dir, target.name if target else "ankimon.db")
    return protection, (tuple(signature), tuple(sorted(entries.items())))


def guard_media_saves_now(logger) -> None:
    """Pause media sync for uncaptured originals, synchronously and cheaply.

    Split out of ``start_media_migration`` so profile-open can arm the guard as
    its very first act while leaving the SCAN where it has to be: last, after
    everything in that handler which opens a modal dialog.

    A modal spins a nested event loop, which delivers taskman's queued main-
    thread callbacks. Dispatching the scan early therefore let its completion
    run re-entrantly inside those dialogs -- nesting its own warnings and rescue
    prompt inside them, and, if the user accepted, reaching ``close_anki`` while
    Anki's ``loadProfile`` was still on the stack. ``_offer_rescue_later`` posts
    its work with a zero-delay timer precisely to get off that stack, which only
    works if nothing after the dispatch pumps the loop.

    Only stat calls: no SQLite, no thread, nothing that can block a profile open.
    """
    try:
        media_dir = _media_dir()
        if media_dir is None or not media_dir.is_dir():
            return
        protection, _ = _pending_media_protection(media_dir, _active_db_path())
        _guard_uncaptured_media(media_dir, protection)
    except Exception as e:
        try:
            logger.log("error", f"Could not pause media sync for uncaptured saves: {e}")
        except Exception:
            pass


def start_media_migration(settings_obj, logger) -> None:
    """Scan on a background worker, then apply decisions on the main thread.

    Cheap guards avoid dispatching scans for an unchanged, settled profile.
    ``uses_collection=False`` keeps inspection off Anki's collection executor,
    where it would delay startup sync. Requests arriving during a scan are
    coalesced into a later pass, and profile changes invalidate its result.
    """
    try:
        media_dir = _media_dir()
        if media_dir is None or not media_dir.is_dir():
            return
        target = _active_db_path()
        collection = _active_collection()
        protection, signature = _pending_media_protection(media_dir, target)
        if not protection["unprotected"]:
            # A user may move an uncaptured save out of media. Release its old
            # guard even when the folder now matches a previously settled scan.
            _guard_uncaptured_media(media_dir, protection)
        completed = _MIGRATION_SCAN_STATE.setdefault("completed", {})
        key = (media_dir, target)
        previous = completed.get(key)
        if previous is not None and previous["signature"] == signature:
            if previous["protection"]["unprotected"]:
                if time.monotonic() < previous["retry_at"]:
                    return
            elif _migration_done():
                return
        elif not protection["unprotected"] and _migration_done():
            return

        # Anki checks this gate before startup/periodic media sync. Pause it
        # before dispatch so neither SQLite backups nor raw ZIP writes block
        # profile-open. Only a completed capture can release the guard.
        _guard_uncaptured_media(media_dir, protection)

        if _MIGRATION_SCAN_STATE["running"]:
            _MIGRATION_SCAN_STATE["rerun"] = True
            return

        def _scan():
            return _migration_scan(media_dir, target)

        def _done(future) -> None:
            result = None
            try:
                result = future.result()
                # A profile switch during the scan would leave us applying one
                # profile's media folder to another's flag and another's save.
                # mw.pm has already moved on by the time this runs, so compare.
                if (result is not None and _media_dir() == media_dir
                        and _active_collection() is collection):
                    _, current_signature = _pending_media_protection(media_dir, target)
                    baseline = result.get("signature", signature)
                    if current_signature != baseline or not result.get("stable", True):
                        # A download or external writer changed the source
                        # during or after the capture. Keep sync paused until
                        # the next pass and drop this result whole: its
                        # comparison figures and its rescue snapshot describe a
                        # save that is already gone, and the protected copy is
                        # not of the version sitting there now, so releasing
                        # the guard would expose unpreserved progress.
                        _MIGRATION_SCAN_STATE["rerun"] = True
                    else:
                        if result.get("protection"):
                            completed[key] = {"signature": baseline,
                                              "protection": result["protection"],
                                              "retry_at": time.monotonic() + _MIGRATION_RETRY_DELAY}
                            _guard_uncaptured_media(media_dir, result["protection"])
                            if (set(result["protection"]["unprotected"])
                                    - set(result["protection"]["archived_sources"])):
                                # The guard stays up, so Anki's media-sync hook
                                # -- this scan's only other trigger -- is the
                                # one thing that cannot bring the retry around.
                                _schedule_migration_retry(settings_obj, logger)
                        _apply_migration_result(result, logger)
            except Exception as e:
                try:
                    logger.log("error", f"AnkiWeb sync-removal migration failed: {e}")
                    if _media_dir() == media_dir and _active_collection() is collection:
                        _report_protection(protection, logger)
                        showWarning("Ankimon's media recovery comparison failed and will be retried "
                                    "after the next sync or restart. See the Ankimon log for details.")
                except Exception:
                    pass
            finally:
                if result is not None:
                    _discard_snapshot(result.pop("snapshot_path", None))
                _MIGRATION_SCAN_STATE["running"] = False
                if _MIGRATION_SCAN_STATE["rerun"]:
                    _MIGRATION_SCAN_STATE["rerun"] = False
                    start_media_migration(settings_obj, logger)

        _MIGRATION_SCAN_STATE["running"] = True
        try:
            mw.taskman.run_in_background(_scan, _done, uses_collection=False)
        except Exception as error:
            _MIGRATION_SCAN_STATE["running"] = False
            _report_protection(protection, logger)
            logger.log("error", f"Could not schedule media recovery scan: {error}")
            showWarning("Ankimon could not start its media recovery scan. "
                        "It will be retried after the next sync or restart. "
                        "Media sync stays paused while original save files remain uncaptured.")
    except Exception as e:
        try:
            logger.log("error", f"AnkiWeb sync-removal migration failed: {e}")
        except Exception:
            pass


def register_media_migration_hooks(settings_obj, logger) -> None:
    """Register a rescan on media-sync stop and start the initial scan.

    A stop signal means only that the worker stopped, including failure or
    cancellation. Fingerprints determine whether the migration is settled.
    Remember handlers in services so reloading removes the previous callbacks;
    catch exceptions because Anki unregisters callbacks that raise.
    """
    from aqt import gui_hooks
    from ..services import services

    for hook, handler in getattr(services, _MIGRATION_HOOK_RECORD, ()):
        try:
            hook.remove(handler)
        except Exception:
            pass

    def on_media_sync_state(running: bool) -> None:
        try:
            if not running:
                start_media_migration(settings_obj, logger)
        except Exception:
            pass

    handlers = ((gui_hooks.media_sync_did_start_or_stop, on_media_sync_state),)
    for hook, handler in handlers:
        hook.append(handler)
    setattr(services, _MIGRATION_HOOK_RECORD, handlers)

    start_media_migration(settings_obj, logger)
