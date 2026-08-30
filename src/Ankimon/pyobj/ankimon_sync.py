"""Mobile-review sync hook, plus the save-file primitives that outlived the
removed AnkiWeb file-sync.

Two unrelated things used to live in this module under the shared word "sync":

* **Mobile/web review replay** — turns reviews done on AnkiDroid/AnkiMobile into
  battles. It rides Anki's own AnkiWeb COLLECTION sync, reads ``revlog`` from the
  already-synced collection, and is registered by ``setup_ankimon_sync_hooks``
  below. **This still works exactly as before and is untouched.**

* **AnkiWeb save-file sync** — copied ``user_files/ankimon.db`` into
  ``collection.media`` so Anki's MEDIA sync would carry it between devices, and
  decided which side was newer by comparing filesystem mtimes. **This has been
  removed**, because the comparison could never work:

  - Anki's media protocol transmits ``{fname, usn, sha1}`` and no timestamp. On
    download, ``add_file_from_ankiweb`` stamps the file with the LOCAL clock
    (``let mtime = mtime_as_i64(path)?;``), so the mtime being compared was
    "when this arrived here", never "when this was authored". Two machines'
    clocks are unrelated anyway.
  - When a media file differs on both sides, Anki resolves it silently in the
    server's favour (``determine_required_change``: ``// differs from server,
    favour server`` → ``RequiredChange::Download``) with no hook an add-on can
    use to veto or even observe it.
  - The import half was registered on ``gui_hooks.sync_did_finish``, which aqt
    fires from ``on_collection_sync_finished`` while the Rust media sync is
    still running — so it read the media file from BEFORE the download, every
    time.

  Successive attempts to tune that comparison (#529, #627, #747, #717, #794)
  could not have converged, because the quantity being compared does not carry
  the information the comparison needs. Users get an explicit
  ``Export save…`` / ``Import save…`` pair instead — see
  ``pyobj/save_transfer.py``, which reuses the primitives kept below.
"""

import base64
import contextlib
import errno
import gc
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Any

from aqt import mw, gui_hooks
from aqt.utils import showWarning, tooltip
from ..pyobj.error_handler import show_warning_with_traceback

from ..resources import user_path


# --------------------------------------------------------------------------
# File-lock tolerance (issue #636)
# --------------------------------------------------------------------------
# On Windows, a file-sync client (OneDrive / Google Drive / Dropbox) or an
# antivirus scanner briefly opens files in the Anki folder to read/upload them.
# While such a handle is open WITHOUT FILE_SHARE_DELETE, os.replace() cannot
# rename over (or delete) the file and raises PermissionError [WinError 5], or
# the sharing/lock-violation variants (32/33). These holds are transient — on a
# small just-created file they typically clear within a second — so the fix is a
# bounded retry (the retry, not the message, is the primary fix), falling back
# to a single friendly, actionable message only if the lock persists.

# WinError codes that mean "another handle is blocking this rename/delete":
# 5 = ERROR_ACCESS_DENIED (what OneDrive typically yields, incl. delete-pending
# and cloud/AV filter-driver cases), 32 = ERROR_SHARING_VIOLATION,
# 33 = ERROR_LOCK_VIOLATION.
_SYNC_LOCK_WINERRORS = frozenset({5, 32, 33})

# Backoff schedule for retrying a locked file op (~2.5 s worst case, and only on
# the failure path — the common case succeeds on the first try with no delay).
_SYNC_LOCK_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.0)

# One friendly, actionable message reused by every manual (modal) entry point.
SYNC_LOCK_MESSAGE = (
    "Access Denied: another program is holding your Ankimon data file open, so "
    "the sync could not finish.\n\n"
    "This is usually OneDrive, Google Drive, Dropbox, or an antivirus that scans "
    "the Anki folder. Please pause it (or exclude your Anki folder from syncing) "
    "and try again.\n\n"
    "Your existing Ankimon data has NOT been changed."
)


def _is_lock_error(exc: BaseException) -> bool:
    """True if ``exc`` is a transient Windows file-lock error (another process
    such as OneDrive/antivirus holding the file open).

    Gated on Windows (``os.name == "nt"``): on POSIX, ``os.replace`` succeeds
    over open handles, so a ``PermissionError`` there is a GENUINE permission
    problem (read-only dir, bad ACL) that must NOT be retried for ~2.5 s or
    blamed on a sync client — it falls through to the normal traceback handler
    instead. On Windows, ``PermissionError`` covers the common WinError 5 and the
    winerror set also catches the sharing/lock-violation variants (32/33). A
    non-lock ``OSError`` (e.g. cross-device link, file-not-found) always returns
    False."""
    if os.name != "nt":
        return False
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in _SYNC_LOCK_WINERRORS


def _retry_on_lock(op: Callable[[], Any], delays=None) -> Any:
    """Run ``op()``, retrying while it fails with a transient file-lock error,
    with a bounded backoff between tries. A non-lock error propagates
    immediately; if every attempt is exhausted the last lock error is re-raised
    for the caller to translate into a friendly message.

    A blocking lock is held by ANOTHER process (OneDrive/antivirus), so there is
    no handle of ours to reclaim here — callers that need to drop their own live
    DB handle (``_atomic_replace``) do the one ``gc.collect()`` that matters
    before calling in, rather than paying a full-heap scan on every retry."""
    if delays is None:
        delays = _SYNC_LOCK_RETRY_DELAYS
    attempts = len(delays) + 1
    for i in range(attempts):
        try:
            return op()
        except OSError as e:
            if not _is_lock_error(e) or i == attempts - 1:
                raise
            time.sleep(delays[i])


def _synctmp_path(target_file: Path) -> Path:
    """Choose a path for the temp copy that ``os.replace`` will atomically rename
    over ``target_file``. Prefer the system temp dir when it is on the SAME
    volume — ``os.replace`` is only atomic within one filesystem — because it
    lives OUTSIDE the cloud-synced subtree, so OneDrive/Dropbox never opens the
    freshly-created temp file and cannot lock the rename SOURCE (the dominant
    cause of issue #636). Fall back to a sibling ``.synctmp`` in the target's own
    directory (the previous behaviour, guaranteed same-filesystem) when the temp
    dir is on a different volume or anything goes wrong."""
    fallback = target_file.with_name(target_file.name + ".synctmp")
    try:
        sys_tmp = Path(tempfile.gettempdir())
        if os.stat(sys_tmp).st_dev == os.stat(target_file.parent).st_dev:
            fd, name = tempfile.mkstemp(prefix="ankimon-", suffix=".synctmp", dir=str(sys_tmp))
            os.close(fd)
            return Path(name)
    except Exception:
        pass
    return fallback


def _is_cross_device_error(exc: BaseException) -> bool:
    """True if ``exc`` is an OS 'not on the same filesystem' error (POSIX EXDEV /
    Windows ERROR_NOT_SAME_DEVICE 17): ``os.replace`` can't rename across
    volumes. This is NOT a lock — it means ``_synctmp_path`` put the temp on a
    different volume than the destination (an ``st_dev`` value it couldn't foresee
    as lying: cloned disks, junctions, some overlay/network mounts)."""
    return isinstance(exc, OSError) and (
        getattr(exc, "errno", None) == errno.EXDEV
        or getattr(exc, "winerror", None) == 17
    )


def _atomic_write_over(src: Path, dest: Path, before_replace: Callable[[], Any] = None) -> None:
    """Copy ``src`` over ``dest`` atomically: write a temp on the same volume as
    ``dest`` (``_synctmp_path`` prefers the system temp dir, else a sibling), then
    ``os.replace`` it into place, retrying a transient OneDrive/antivirus lock so
    a lock or interruption can't leave a half-written destination (issue #636).
    ``before_replace``, if given, runs once after the copy and just before the
    replace (e.g. to close a live DB handle on ``dest`` first).

    If the chosen temp turns out to be cross-device — an ``st_dev`` match that
    ``_synctmp_path`` trusted but ``os.replace`` rejects with EXDEV / WinError 17
    — retry once via a guaranteed same-directory sibling temp, so the atomic
    replace still completes instead of surfacing a spurious traceback (the old
    plain ``shutil.copy2`` never hit this because it wrote straight to ``dest``)."""
    # Reap a same-named sibling orphaned by an earlier crash between copy and
    # replace, so a stray '<dest>.synctmp' can't accumulate in — and, for an
    # export into collection.media, be uploaded to AnkiWeb from — the dest dir.
    sibling = dest.with_name(dest.name + ".synctmp")
    try:
        sibling.unlink(missing_ok=True)
    except Exception:
        pass

    tmp = _synctmp_path(dest)
    try:
        shutil.copy2(src, tmp)
        if before_replace is not None:
            before_replace()
        try:
            _retry_on_lock(lambda: os.replace(tmp, dest))
        except OSError as e:
            if not _is_cross_device_error(e) or tmp == sibling:
                raise
            try:
                shutil.copy2(src, sibling)
                _retry_on_lock(lambda: os.replace(sibling, dest))
            finally:
                try:
                    sibling.unlink(missing_ok=True)
                except Exception:
                    pass
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _verify_sqlite_integrity(db_file: Path, timeout: float = 30.0) -> bool:
    """True only if ``db_file`` is a readable, non-empty Ankimon SQLite DB that
    passes a quick integrity check and carries the core ``captured_pokemon``
    table. Guards the live save against being overwritten by a truncated /
    corrupt / foreign file — whether that file came from the media folder or
    from a file the user picked in the Import dialog.

    ``timeout`` bounds the wait on a locked file. It used to be unset, which is
    sqlite3's 5 s default — paid synchronously on the profile-open stack by the
    media migration, where a single locked save visibly froze Anki's startup.
    User-initiated Import/Export still wait the full 30 s; the migration passes
    ``save_transfer.MIGRATION_PROBE_TIMEOUT`` and rescans later instead.
    """
    try:
        db_file = Path(db_file)
        if not db_file.is_file() or db_file.stat().st_size < 512:
            return False
        import sqlite3
        # Build the read-only URI via as_uri() so a profile path with spaces
        # or unicode (e.g. C:\Users\John Doe\...) is percent-encoded correctly
        # — a raw f-string URI would fail to open a perfectly valid DB and
        # wrongly refuse the import.
        uri = db_file.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        try:
            row = conn.execute("PRAGMA quick_check;").fetchone()
            if not row or str(row[0]).lower() != "ok":
                return False
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            return "captured_pokemon" in tables
        finally:
            conn.close()
    except Exception:
        return False


def _handle_manual_sync_error(exc: BaseException, message: str) -> bool:
    """Present a MANUAL (modal) sync failure and return False. A transient file
    lock (OneDrive/antivirus) that survived the retry gets the single friendly,
    actionable ``SYNC_LOCK_MESSAGE``; a genuine error gets the raw traceback. The
    caller early-returns on False, so neither is stacked with a second dialog."""
    if _is_lock_error(exc):
        showWarning(SYNC_LOCK_MESSAGE)
    else:
        show_warning_with_traceback(parent=mw, exception=exc, message=message)
    return False


class AnkimonDataSync:
    """Save-file primitives shared by the manual save transfer and the
    sync-removal migration.

    This class NO LONGER SYNCS ANYTHING. It used to drive an automatic
    bidirectional copy of ``user_files/ankimon.db`` through Anki's media folder;
    that path is gone (see this module's docstring). What survives here is the
    part that was hard-won and is still correct: the legacy ``config.obf``
    obfuscation helpers that ``settings.py`` still needs to read a pre-SQLite
    config, the integrity gate, the backup-before-overwrite gate, and the atomic
    replace with its Windows file-lock tolerance (issue #636).
    ``pyobj/save_transfer.py`` builds the user-facing Export/Import on top of
    these, and resolves the media folder itself when it needs it.
    """

    _OBFUSCATION_KEY = "H0tP-!s-N0t-4-C@tG!rL_v2"

    def _obfuscate_data(self, data: dict) -> str:
        """Obfuscates dictionary data into a string."""
        json_str = json.dumps(data)
        obfuscated_bytes = bytearray()
        key_bytes = self._OBFUSCATION_KEY.encode('utf-8')
        for i, byte in enumerate(json_str.encode('utf-8')):
            obfuscated_bytes.append(byte ^ key_bytes[i % len(key_bytes)])
        return base64.b64encode(obfuscated_bytes).decode('utf-8')

    def _deobfuscate_data(self, obfuscated_str: str) -> dict:
        """De-obfuscates string back into a dictionary."""
        new_separator = "---DATA_START---"
        old_separator = "\n---"
        
        if new_separator in obfuscated_str:
            parts = obfuscated_str.split(new_separator)
            obfuscated_data = parts[1]
        elif old_separator in obfuscated_str:
            parts = obfuscated_str.split(old_separator)
            obfuscated_data = parts[1]
        else:
            obfuscated_data = obfuscated_str # Fallback for old format

        obfuscated_bytes = base64.b64decode(obfuscated_data)
        deobfuscated_bytes = bytearray()
        key_bytes = self._OBFUSCATION_KEY.encode('utf-8')
        for i, byte in enumerate(obfuscated_bytes):
            deobfuscated_bytes.append(byte ^ key_bytes[i % len(key_bytes)])
        return json.loads(deobfuscated_bytes.decode('utf-8'))


    def _close_live_db_connection(
        self, target_file: Path, *, required: bool = False
    ) -> bool:
        """Close the live DB when it targets ``target_file``."""
        try:
            from ..services import services

            db = services.db
            if db is None:
                return True
            db_path = getattr(db, "db_path", None)
            if db_path is None or Path(db_path).resolve() != Path(target_file).resolve():
                return True
            closed = bool(db.close(2.0))
            if required and not closed:
                raise RuntimeError(
                    "Database replacement aborted because active operations did not finish"
                )
            return closed
        except Exception:
            if required:
                raise
            return False

    @contextlib.contextmanager
    def _quiesce_live_db_connection(self, target_file: Path):
        """Keep connection creation blocked across a live DB file replacement."""
        from ..services import services

        db = services.db
        db_path = getattr(db, "db_path", None) if db is not None else None
        if db is None or db_path is None or Path(db_path).resolve() != Path(target_file).resolve():
            yield True
            return

        quiesce = getattr(db, "quiesce", None)
        if quiesce is None:
            closed = bool(db.close(2.0))
            yield closed
            return

        with quiesce(2.0) as closed:
            yield closed

    @staticmethod
    def _verify_sqlite_integrity(db_file: Path, timeout: float = 30.0) -> bool:
        """Delegates to the module-level ``_verify_sqlite_integrity``.

        Kept as a method so callers and tests that reach for
        ``AnkimonDataSync._verify_sqlite_integrity`` keep working, while
        ``save_transfer`` can import the plain function without constructing an
        ``AnkimonDataSync`` (which needs a loaded Anki profile).
        """
        return _verify_sqlite_integrity(db_file, timeout=timeout)

    def _backup_before_overwrite(self, required_file: str = "ankimon.db") -> bool:
        """Timestamped backup of the local Ankimon DB(s) before an import
        overwrites them, so a bad cross-device import is recoverable via the
        Backup Manager. Reuses BackupManager (WAL checkpoint + summary +
        retention) rather than a bare copy. Returns True only if a backup of
        ``required_file`` (the file about to be overwritten) was actually
        written — callers MUST refuse to overwrite when this is False, or a
        failed backup would leave the live save with no recovery path."""
        try:
            from ..services import services
            from .backup_manager import BackupManager
            return bool(
                BackupManager(services.logger, services.settings).create_backup(
                    manual=False, required_file=required_file
                )
            )
        except Exception as e:
            try:
                from ..services import services
                services.logger.log("error", f"Pre-import backup failed: {e}")
            except Exception:
                pass
            return False

    def _atomic_replace(self, media_file: Path, source_file: Path) -> None:
        """Overwrite ``source_file`` with ``media_file`` atomically via
        ``_atomic_write_over`` (temp on the same volume + ``os.replace``, retrying
        a transient OneDrive/antivirus lock), after closing the live connection to
        ``source_file`` so the OS releases its handle before the rename. A
        persisting lock re-raises for the caller to surface as a friendly message;
        a non-lock error propagates unchanged. (``_atomic_write_over`` prefers the
        system temp dir, falling back to a same-directory sibling.)

        The media file is a single-file export (no WAL sidecar), so any stale
        ``-wal`` / ``-shm`` belonging to the OLD ``source_file`` must be removed
        after the swap — a fresh connection that found them would try to replay
        an unrelated WAL over the new file and hit 'database disk image is
        malformed'.

        The connection registry requests closure from GUI and background wrappers.
        If an in-flight operation does not release its lease within the bounded
        wait, replacement aborts and the original database remains untouched."""
        source_file.parent.mkdir(parents=True, exist_ok=True)
        quiescence = self._quiesce_live_db_connection(source_file)
        entered = False

        def _release_handles():
            nonlocal entered
            closed = quiescence.__enter__()
            entered = True
            if not closed:
                raise RuntimeError(
                    "Database replacement aborted because active operations did not finish"
                )
            gc.collect()

        try:
            _atomic_write_over(media_file, source_file, before_replace=_release_handles)

            for sidecar in ("-wal", "-shm"):
                stale = source_file.with_name(source_file.name + sidecar)
                try:
                    stale.unlink(missing_ok=True)
                except Exception:
                    pass
        finally:
            if entered:
                quiescence.__exit__(None, None, None)


# Global instance for easy access - but will be lazy initialized
_ankimon_sync_instance = None

# One-shot guard so a persistent mobile-detection failure surfaces a tooltip
# once per session instead of spamming it on every sync.
_mobile_detection_warned = False

# Reload safety (F31): the (hook, handler) pairs this module last registered,
# stored on the services registry so they survive a re-execution of this module
# (unlike a module-level flag) and can be removed before re-appending. The KEY
# STRING must not change: an add-on reload from a pre-removal version stored the
# old two-hook tuple under this exact name, and reading it back is the only way
# that version's ``sync_will_start`` handler gets unregistered.
_SYNC_HOOK_RECORD = "_ankimon_sync_hook_handlers"

def get_ankimon_sync() -> AnkimonDataSync:
    """Get the global AnkimonDataSync instance, creating it if needed."""
    global _ankimon_sync_instance
    if _ankimon_sync_instance is None:
        _ankimon_sync_instance = AnkimonDataSync()
    return _ankimon_sync_instance

def setup_ankimon_sync_hooks(settings_obj, logger):
    """Register the post-sync hook that turns mobile/web reviews into battles.

    Registered UNCONDITIONALLY (not gated on any setting) so that mobile-review
    detection actually runs for every user. Mobile reviews arrive via Anki's own
    AnkiWeb COLLECTION sync, so gating detection behind the old
    ``misc.ankiweb_sync`` file-sync toggle (default False, and never
    auto-enabled) meant ``on_sync_did_finish`` was never attached and a
    mid-session sync never turned phone reviews into battles — the regression
    #586 fixed. The block inside keeps its own narrower guard and self-gates on
    ``mobile.enabled``.

    This function used to register a SECOND hook, ``sync_will_start``, which
    staged ``ankimon.db`` into ``collection.media`` for the AnkiWeb file-sync,
    and a matching import tail on this hook. Both are gone; only mobile-review
    detection remains. Detection is unaffected by that removal because it reads
    ``revlog`` from the already-synced collection, never the media folder.
    """

    def on_sync_did_finish():
        """Called after sync finishes."""
        # === Mobile-review sync engine (F29): dual-DB detection + queueing ===
        # Runs regardless of the AnkiWeb-config auto-sync toggle below — mobile
        # review detection is independent of Ankimon-config file syncing.
        try:
            from ..services import services
            db = services.db
            col = services.col if services.col is not None else mw.col
            if db is not None and col is not None and settings_obj.get("mobile.enabled", True):
                from ..functions.mobile_sync import (
                    detect_mobile_reviews,
                    get_desktop_session_revlog_ids,
                    clear_desktop_session,
                    _mobile_sync_lock,
                    MOBILE_QUEUE_CAP,
                )
                from ..menu_buttons import update_mobile_badge

                dev_db_path = user_path / "ankimonDEV.db"
                original_db_name = db.db_path.name
                desktop_ids = get_desktop_session_revlog_ids(col)

                newly_queued_normal = 0
                newly_queued_dev = 0

                def _cap_mobile(reviews):
                    # Bound each queueing pass to the same MOBILE_QUEUE_CAP the
                    # startup pass (process_mobile_reviews_after_sync) applies, so
                    # a huge backlog is handled consistently regardless of which
                    # trigger fires first. Keep the newest N (list is ASC) and
                    # warn — the dropped oldest are permanently discarded.
                    if len(reviews) > MOBILE_QUEUE_CAP:
                        discarded = len(reviews) - MOBILE_QUEUE_CAP
                        msg = (
                            f"Mobile sync: {len(reviews)} new reviews exceed the "
                            f"{MOBILE_QUEUE_CAP} system cap — the {discarded} oldest "
                            f"were discarded and will not become mobile battles."
                        )
                        try:
                            logger.log_and_showinfo("warning", msg)
                        except Exception:
                            logger.log("warning", msg)
                        return reviews[-MOBILE_QUEUE_CAP:]
                    return reviews

                # Serialize the dual-DB switch/queue/restore dance against any
                # in-flight background mobile-resolve (run_mobile_battles /
                # commit_replay_outcome's do_db_work both hold _mobile_sync_lock).
                # Without this, switch_database() mutates db.db_path mid-op and a
                # background thread's next _get_connection() silently reopens
                # against the wrong Ankimon DB file. The lock is released before
                # the auto-resolve branch below (resolveAll reacquires it).
                with _mobile_sync_lock:
                    try:
                        # 1. Queue to ankimon.db
                        if db.db_path.name != "ankimon.db":
                            db.switch_database("ankimon.db")
                        watermark_normal = db.get_mobile_watermark()
                        all_mobile_normal = detect_mobile_reviews(col, watermark_normal, desktop_ids)
                        if all_mobile_normal:
                            all_mobile_normal = _cap_mobile(all_mobile_normal)
                            newly_queued_normal = db.queue_mobile_battles(all_mobile_normal)
                            db.set_mobile_watermark(max(r["id"] for r in all_mobile_normal))

                        max_revlog_id = col.db.scalar("SELECT MAX(id) FROM revlog")
                        if isinstance(max_revlog_id, (int, float)):
                            current_watermark = db.get_mobile_watermark()
                            if max_revlog_id > current_watermark:
                                db.set_mobile_watermark(max_revlog_id)

                        # 2. Queue to ankimonDEV.db if present
                        if dev_db_path.is_file():
                            if db.db_path.name != "ankimonDEV.db":
                                db.switch_database("ankimonDEV.db")
                            watermark_dev = db.get_mobile_watermark()
                            all_mobile_dev = detect_mobile_reviews(col, watermark_dev, desktop_ids)
                            if all_mobile_dev:
                                all_mobile_dev = _cap_mobile(all_mobile_dev)
                                newly_queued_dev = db.queue_mobile_battles(all_mobile_dev)
                                db.set_mobile_watermark(max(r["id"] for r in all_mobile_dev))

                            if isinstance(max_revlog_id, (int, float)):
                                current_watermark = db.get_mobile_watermark()
                                if max_revlog_id > current_watermark:
                                    db.set_mobile_watermark(max_revlog_id)
                    finally:
                        if db.db_path.name != original_db_name:
                            db.switch_database(original_db_name)

                # Clear desktop session to prevent indefinite exclusion-set accumulation
                clear_desktop_session()

                # Always update the badge with the TOTAL pending count of the active DB
                total_pending = db.get_pending_mobile_count()
                update_mobile_badge(total_pending)

                msg_parts = []
                if newly_queued_normal > 0:
                    msg_parts.append(f"{newly_queued_normal} in Normal")
                if newly_queued_dev > 0:
                    msg_parts.append(f"{newly_queued_dev} in Dev")

                if msg_parts:
                    resolution_mode = settings_obj.get("mobile.resolution_mode", "manual")

                    if resolution_mode == "auto":
                        try:
                            from ..ankimon_items_web.shop_obj import MobileBridge
                            bridge = MobileBridge(mw)
                            result = bridge.resolveAll()
                            if result.get("success"):
                                xp_gained = result.get("xp_gained", 0)
                                cash_gained = result.get("cash_gained", 0)
                                caught_list = result.get("caught_list", [])
                                resolved = result.get("resolved", 0)
                                caught_names = [p["name"] for p in caught_list]
                                caught_str = f" Caught: {', '.join(caught_names)}." if caught_names else ""
                                tooltip(f"⚔ Auto-resolved {resolved} mobile/web reviews! +{xp_gained} XP, +{cash_gained}¥.{caught_str}")
                                logger.log("info", f"Auto-resolved {resolved} mobile/web reviews on active DB. +{xp_gained} XP, +{cash_gained}¥.{caught_str}")
                            else:
                                error_msg = result.get("error", "Unknown error")
                                tooltip(f"⚠ Auto-resolve failed: {error_msg}. Please resolve manually.")
                                logger.log("error", f"Auto-resolve failed: {error_msg}")
                        except Exception as ex:
                            tooltip(f"⚠ Auto-resolve error: {ex}. Please resolve manually.")
                            logger.log("error", f"Auto-resolve error: {ex}")
                    else:
                        tooltip(f"⚔ Mobile/web reviews synced: {', '.join(msg_parts)}! Open Ankimon → Mobile & Web Reviews to resolve.")
                        logger.log("info", f"Mobile/web reviews synced: {', '.join(msg_parts)}. Total active pending: {total_pending}.")
                logger.log("info", f"Mobile dual-queue complete. Active DB restored to: {db.db_path.name}")
                try:
                    from ..events import events
                    events.emit("stats_changed")
                    from ..singletons import notify_stats_changed
                    notify_stats_changed()
                except Exception:
                    pass
        except Exception as e:
            logger.log("error", f"Mobile review detection failed: {e}")
            # Surface a hard failure to the user ONCE per session — a silent log
            # is exactly what hid the original "sync not working" bug. Guarded so
            # a persistent failure can't spam a tooltip on every sync.
            global _mobile_detection_warned
            if not _mobile_detection_warned:
                _mobile_detection_warned = True
                try:
                    tooltip(
                        "Ankimon: couldn't process mobile reviews after sync — see "
                        "the Ankimon log. Your card reviews themselves are unaffected."
                    )
                except Exception:
                    pass
        # === END mobile-review sync engine ===

    # Reload safety (F31 registry-anchored guard): a second boot in the same
    # Anki session (the F26 branch self-updater reloading add-on code, or any
    # re-run of register_profile_hooks) must not stack a second
    # on_sync_did_finish — that would double the dual-DB queueing pass, double
    # the tooltip, and in auto mode fire MobileBridge.resolveAll() twice per
    # sync. Remove the previously recorded handlers first; gui_hooks' remove()
    # tolerates already-absent callbacks, and the closure above is a NEW object
    # each call, so only the stored originals can be found and removed.
    #
    # The record KEY is deliberately unchanged across this refactor. An add-on
    # reload from a pre-removal version stored a 2-tuple here including the old
    # ``sync_will_start`` handler; reading the same key is what lets that stale
    # handler be unregistered instead of being left attached to a module whose
    # file-sync functions no longer exist.
    from ..services import services
    for hook, handler in getattr(services, _SYNC_HOOK_RECORD, ()):
        hook.remove(handler)

    _handlers = (
        (gui_hooks.sync_did_finish, on_sync_did_finish),
    )
    for hook, handler in _handlers:
        hook.append(handler)
    setattr(services, _SYNC_HOOK_RECORD, _handlers)

    logger.log("info", "Ankimon mobile-review sync hook registered")