"""Manual save-file transfer, and the one-shot cleanup of the removed AnkiWeb
file-sync.

This module replaces the automatic AnkiWeb save-sync that used to live in
``ankimon_sync.py``. That sync shipped ``user_files/ankimon.db`` through Anki's
media folder and decided which copy was newer by comparing filesystem mtimes.
Anki's media protocol carries no authorship timestamp — a downloaded file's
mtime is stamped locally by the downloader (``add_file_from_ankiweb`` in
rslib reads it back off disk after writing) — so that comparison could not
answer the question it was asked, and when a file differed on both sides Anki
silently kept the server's copy (``determine_required_change``:
``// differs from server, favour server``). Nothing an add-on can do makes an
mtime meaningful across two machines, so the automatic path is gone rather than
tuned again.

What replaces it here is deliberately explicit and one-directional at a time:

* **Export save…** writes a consistent snapshot of the ACTIVE database to a file
  the user picks, via SQLite's online-backup API (``Connection.backup``). That is
  transactionally consistent regardless of journal mode, unlike the previous
  checkpoint-then-``copy2``, which could ship a torn or stale snapshot.
* **Import save…** replaces the active database from such a file, but only after
  an integrity check, a successful safety backup, and an explicit confirmation
  that shows the user both saves' contents side by side.
* **The migration** protects and rescues whatever the removed feature left in
  ``collection.media``. See ``run_media_migration``.

Every destructive step is gated conjunctively: verified source, successful
backup, explicit user confirmation, atomic replace. Any failure aborts with the
local save byte-identical.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from aqt import mw
from aqt.utils import askUser, showInfo, showWarning
from PyQt6.QtWidgets import QFileDialog

from ..resources import user_path
from ..utils import close_anki

# The media copy is named with a LEADING UNDERSCORE on purpose. Anki's media
# check reports a file as unused (and offers to delete it, propagating that
# deletion to every other device) only when
# ``!file.starts_with('_') && !references.contains_key(&file)`` — nothing in a
# collection will ever reference a save file, so the underscore is the only
# thing standing between it and "Delete Unused Files". The shipped code wrote
# the bare name ``ankimon.db``, and ``_migrate_legacy_files`` actively moved
# users from the protected legacy name TO that unprotected one.
MEDIA_SAVE_NAME = "_ankimon_save.db"

# Developer mode keeps its own ``ankimonDEV.db``, and it needs its own protected
# name too. Sharing one destination let a developer-mode run write dev data into
# _ankimon_save.db, which _target_db_for then reads back as a NORMAL-partition
# candidate (the name carries no "ankimonDEV"), so a test save could be ranked
# against — and offered over — the real one. collection.media syncs, so the
# contaminated file reached other devices as well.
DEV_MEDIA_SAVE_NAME = "_ankimon_save_dev.db"

# Bare names the removed feature (and its pre-SQLite ancestors) left behind in
# collection.media. Only ``.db`` entries are candidates for rescue; the JSON
# files predate the SQLite migration and are already imported into ankimon.db.
LEGACY_MEDIA_DB_NAMES = ("ankimon.db", "ankimonDEV.db")

# Config rows that are machine-local and must never ride along in an exported
# save. The export exists to be carried to another computer (or handed to
# someone else), and a full SQLite backup would otherwise take the user's
# leaderboard credential with it. Only genuine secrets belong here — the
# username is not one.
EXPORT_EXCLUDED_CONFIG_KEYS = ("leaderboard.api_key",)

# Set once per Anki PROFILE, not once per add-on install: ``user_files`` is
# add-on-scoped and shared by every profile, while ``collection.media`` is
# per-profile, so a single global flag would clean the first profile opened and
# silently skip all the others. ``mw.pm.profile`` is itself per-profile, so
# storing the flag there gives the right scoping for free — and it lives in
# prefs21.db, outside the collection and outside user_files, so restoring a
# backup of the save cannot rewind it and re-trigger the migration.
_MIGRATION_FLAG = "ankimonMediaSyncRemovedV1"

# SETTLE POLICY — the one rule this migration turns on.
#
# Settle (burn the per-profile one-shot) ONLY on a positive resolution: every
# candidate that was discovered could be read, and the rescue reached a terminal
# answer — declined, or not needed because the local save is already level.
#
# Stay ARMED on every absence or uncertainty: an empty folder, a candidate that
# would not open, an unreadable protected copy, or an accepted rescue (which
# re-runs on the next boot and settles then).
#
# There is deliberately no "a media sync finished, so the folder has had its
# chance" signal, because Anki does not offer one. In aqt/mediasync.py:77-86:
#
#     gui_hooks.media_sync_did_start_or_stop(False)   # :80 — fires FIRST
#     exc = future.exception()                        # :82 — inspected AFTER
#
# the hook fires before Anki looks at the future, so False means "the worker
# stopped", never "it succeeded" — a dropped network, a user abort and a
# media-sync-disabled profile all raise the identical signal. Settling an empty
# folder on it burned the one-shot for exactly the two-device user the rescue
# exists to serve, and the process-global that carried it also leaked across
# profile switches (Anki does not re-import add-on modules on a switch), so
# profile B inherited profile A's "a sync finished" and settled before its own
# download had landed.
#
# Staying armed costs almost nothing: an empty folder is three stat calls and a
# glob, with no SQLite opens at all.

# How long the AUTOMATIC migration will wait on a locked file. It runs on the
# profile-open stack, so this is time Anki's startup is frozen — with the old
# unset sqlite3 default (5 s) in _verify_sqlite_integrity, a single locked media
# save stalled boot by 5.2 s measured. Failing fast is free here because a
# skipped file leaves the migration armed and it is retried on the next pass.
# User-initiated Export/Import keep the full 30 s, where waiting out a passing
# lock is exactly what the user wants.
MIGRATION_PROBE_TIMEOUT = 0.5

# Reload safety, same pattern as the sync hook: the (hook, handler) pair this
# module last registered, anchored on the services registry so it survives a
# re-execution of this module and can be removed before re-appending.
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

    ``timeout`` is generous (30 s) for the user-initiated Export/Import, where
    waiting out a passing lock is exactly what the user wants. The automatic
    migration passes ``MIGRATION_PROBE_TIMEOUT`` instead: it runs on the
    profile-open stack, so it must fail fast and rescan later rather than freeze
    Anki's startup on a locked file.
    """
    try:
        if not Path(db_path).is_file():
            return None
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
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
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)};")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        def _scalar(sql: str, default=0):
            try:
                row = conn.execute(sql).fetchone()
                return row[0] if row and row[0] is not None else default
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
            def _cfg(key, default):
                try:
                    row = conn.execute(
                        "SELECT value FROM config WHERE key = ?", (key,)
                    ).fetchone()
                    return row[0] if row else default
                except Exception:
                    return default

            stats["trainer_name"] = _cfg("trainer.name", "-")
            try:
                stats["trainer_level"] = int(_cfg("trainer.level", 0))
            except Exception:
                pass
            try:
                stats["trainer_cash"] = int(_cfg("trainer.cash", 0))
            except Exception:
                pass
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
    """Monotone-only progress fingerprint, for ordering two saves.

    Deliberately excludes cash and level: both can legitimately go DOWN (spending
    in the shop; a level recomputed from a changed XP curve), so including them
    would let a save that merely spent money look 'older'. Counts of captured
    Pokemon, achieved badges and history rows only ever grow.
    """
    if stats is None:
        return (-1, -1, -1)
    return (stats["pokemon"], stats["badges"], stats["history"])


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


def _sqlite_backup(source: Path, dest: Path) -> None:
    """Copy ``source`` to ``dest`` via SQLite's online-backup API.

    This is the reason the export is trustworthy: ``Connection.backup`` takes a
    transactionally consistent snapshot whatever the journal mode, so it cannot
    produce the torn or WAL-stale file a plain byte copy can. The previous code
    tried to approximate this with ``PRAGMA wal_checkpoint(TRUNCATE)`` before a
    ``copy2``, which was a documented no-op here anyway (``AnkimonDB`` is
    constructed with ``wal=False``) and silently returned busy when another
    connection held a snapshot.
    """
    src_conn = sqlite3.connect(str(source), timeout=30)
    try:
        src_conn.execute("PRAGMA busy_timeout = 30000;")
        dest_conn = sqlite3.connect(str(dest), timeout=30)
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


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


def _strip_local_secrets(db_path: Path, logger=None) -> None:
    """Remove machine-local credentials from a save that is about to travel.

    The export is a full SQLite backup, and Ankimon's settings live in the same
    database's ``config`` table (``settings.set`` → ``db.set_config_value``), so
    without this the user's leaderboard API key rides along in a file whose
    whole purpose is to be copied to another computer — or handed to someone
    else. Runs on the temp copy, before verification, so the live save is never
    touched and a failure discards the export rather than shipping the key.

    The VACUUM is load-bearing, not tidiness. DELETE only unlinks the row; the
    bytes stay in the freed page until something reuses it, so ``strings`` on
    the exported file still recovers the key. VACUUM rebuilds the database and
    drops the old pages with it.
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "config" not in tables:
            return
        conn.execute("PRAGMA secure_delete = ON;")
        conn.executemany(
            "DELETE FROM config WHERE key = ?",
            [(k,) for k in EXPORT_EXCLUDED_CONFIG_KEYS],
        )
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

    # Build into a temp file beside the destination and move it into place only
    # after it verifies, so an interrupted or failed export can never leave a
    # half-written file the user might later import over a good save.
    tmp = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="ankimon-export-", suffix=".db", dir=str(dest.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        # mkstemp created an empty file; sqlite3 backup needs to write into it,
        # which it does happily, but an existing zero-byte file is fine as a
        # backup target.
        _sqlite_backup(source, tmp)
        _strip_local_secrets(tmp)

        if not _verify_sqlite_integrity(tmp):
            showWarning(
                "Export failed: the exported file did not pass an integrity "
                "check, so it was discarded. Your save is unchanged."
            )
            return False

        # Same lock ladder the import path uses. Exporting over an existing
        # file inside a OneDrive/Dropbox folder is the obvious thing a
        # two-desktop user does, and a bare os.replace there throws WinError 5
        # with no actionable text (issue #636).
        _retry_on_lock(lambda: os.replace(tmp, dest))
        tmp = None
    except Exception as e:
        if _is_lock_error(e):
            showWarning(SYNC_LOCK_MESSAGE)
        else:
            showWarning(f"Export failed: {e}\n\nYour save is unchanged.")
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

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
    from .ankimon_sync import _verify_sqlite_integrity

    parent = parent or mw
    target = _active_db_path()
    if target is None:
        showWarning("The Ankimon database is not ready yet; try again once Anki has finished loading.")
        return False

    src_str, _ = QFileDialog.getOpenFileName(
        parent, "Import Ankimon save", str(Path.home()), "Ankimon save (*.db)"
    )
    if not src_str:
        return False
    source = Path(src_str)

    if not _verify_sqlite_integrity(source):
        showWarning(
            "That file is not a valid Ankimon save (it failed an integrity "
            "check, or is missing Ankimon's data). Nothing was changed."
        )
        return False

    if source.resolve() == Path(target).resolve():
        showWarning("That file is the save Ankimon is already using. Nothing was changed.")
        return False

    incoming = get_db_stats(source)
    current = get_db_stats(target)
    if not askUser(
        "Replace your current Ankimon save with this file?\n\n"
        f"FILE YOU CHOSE\n{_format_stats(incoming)}\n\n"
        f"YOUR CURRENT SAVE\n{_format_stats(current)}\n\n"
        "Your current save will be backed up first, and Anki will close so the "
        "new save is loaded cleanly.",
        parent=parent,
        defaultno=True,
    ):
        return False

    return _replace_active_save(source, target, "Import")


def _replace_active_save(source: Path, target: Path, what: str) -> bool:
    """Back up, then atomically put ``source`` in place of the live save.

    Order is load-bearing. The backup happens first and a failed backup REFUSES
    the replacement — never overwrite the live save with no recovery path. The
    replace itself goes through ``_atomic_replace``, which closes the live
    connection, writes a temp on the same volume, ``os.replace``s it into place
    (retrying a transient OneDrive/antivirus lock) and reaps stale ``-wal`` /
    ``-shm`` sidecars belonging to the old file.

    Anki is closed afterwards, and that is deliberate rather than lazy: the next
    boot re-runs ``profile_did_open``, whose ``watermark == 0`` re-derivation
    from ``MAX(id) FROM revlog`` is what stops the imported save's mobile
    watermark from queueing thousands of already-handled reviews as fresh mobile
    battles. It also rehydrates every singleton holding the old connection.
    """
    from .ankimon_sync import get_ankimon_sync, _handle_manual_sync_error

    sync = get_ankimon_sync()
    if not sync._backup_before_overwrite(Path(target).name):
        showWarning(
            f"{what} aborted: a safety backup of your current save could not be "
            "created, so nothing was replaced. Your save is unchanged."
        )
        return False

    try:
        sync._atomic_replace(Path(source), Path(target))
    except Exception as e:
        return _handle_manual_sync_error(e, f"{what} failed")

    showInfo(
        f"{what} complete. Anki will now close — please reopen it to start "
        "playing with the restored save."
    )
    close_anki()
    return True


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


def _migration_done() -> bool:
    try:
        return bool(mw.pm.profile.get(_MIGRATION_FLAG))
    except Exception:
        # No profile dict means no profile is loaded; treat as "not yet", the
        # caller will simply run again next time.
        return False


def _mark_migration_done() -> None:
    try:
        mw.pm.profile[_MIGRATION_FLAG] = True
        mw.pm.save()
    except Exception:
        pass


def _target_db_for(candidate: Path) -> str:
    """Which local database a media candidate belongs to.

    Developer mode keeps a separate ``ankimonDEV.db``, and the old sync wrote
    both under their own media names. Ranking them in one list would let a
    developer save with more test captures be crowned "best", become the single
    protected copy, and be offered as a rescue over the real save (or the
    reverse, in developer mode). Compare like with like instead.
    """
    name = candidate.name
    # The protected names are matched EXPLICITLY, before the substring test.
    # "_ankimon_save_dev.db" carries no "ankimonDEV", so a substring check alone
    # hands the developer partition's protected copy to the normal one.
    if name == DEV_MEDIA_SAVE_NAME:
        return "ankimonDEV.db"
    if name == MEDIA_SAVE_NAME:
        return "ankimon.db"
    return "ankimonDEV.db" if "ankimonDEV" in name else "ankimon.db"


def _protected_name_for(target_db: str) -> str:
    """The protected filename belonging to ``target_db``'s partition.

    Each partition gets its own, so a developer-mode run can never write test
    progress into the name the normal-mode scan reads back.
    """
    return DEV_MEDIA_SAVE_NAME if target_db == "ankimonDEV.db" else MEDIA_SAVE_NAME


def _find_media_saves(media_dir: Path, target_db: str) -> tuple:
    """Every Ankimon save in collection.media belonging to ``target_db``.

    Returns ``(candidates, unreadable)``. A file that exists but will not open
    lands in ``unreadable`` rather than being silently dropped: it may be a real
    save merely locked by another process this second, and the caller must stay
    armed and rescan rather than settle as though the folder held nothing.

    The legacy underscore names are GLOBBED rather than reconstructed. The old
    code built them from ``Path(__file__).parents[2].name``, which is
    ``addons21`` in a normal install and ``src`` in a git checkout, and real
    profiles have been seen carrying the numeric package id instead — so the
    exact prefix cannot be computed after the fact, only matched.
    """
    from .ankimon_sync import _verify_sqlite_integrity

    candidates = []
    unreadable = []
    seen = set()

    def _add(path: Path):
        try:
            resolved = path.resolve()
        except Exception:
            return
        if resolved in seen or not path.is_file():
            return
        # Only ever touch a file that positively identifies as an Ankimon save.
        # Bare names like ankimon.db are specific enough, but the check also
        # guards against a same-named file another tool put there.
        if not _verify_sqlite_integrity(path, timeout=MIGRATION_PROBE_TIMEOUT):
            # Unreadable OR merely locked right now — either way this scan
            # cannot judge it, so record that and let the caller stay armed.
            unreadable.append(path)
            return
        seen.add(resolved)
        candidates.append(path)

    if target_db == "ankimon.db":
        _add(media_dir / MEDIA_SAVE_NAME)
        _add(media_dir / "ankimon.db")
        pattern = "_*_ankimon.db"
    else:
        _add(media_dir / DEV_MEDIA_SAVE_NAME)
        _add(media_dir / "ankimonDEV.db")
        pattern = "_*_ankimonDEV.db"

    try:
        for path in sorted(media_dir.glob(pattern)):
            # glob("_*_ankimon.db") also matches "_*_ankimonDEV.db"? No — but a
            # future name could collide, so re-check the partition explicitly.
            if _target_db_for(path) == target_db:
                _add(path)
    except Exception:
        pass
    return candidates, unreadable


def _offer_rescue_later(protected: Path, target: Path) -> None:
    """Run the rescue off the current call stack.

    The rescue ends in ``close_anki()`` → ``mw.close()``, whose ``closeEvent``
    starts ``unloadProfileAndExit()``. Calling that from inside
    ``profile_did_open`` would begin tearing the collection down and then return
    into ``loadProfile``, which proceeds straight to
    ``maybe_auto_sync_on_open_close`` — starting a sync against a collection
    that is being unloaded. Deferring by one event-loop turn keeps the whole
    thing in a settled session, the way the menu-driven Import already is.
    """
    def _go():
        try:
            _replace_active_save(protected, target, "Rescue")
        except Exception:
            pass

    try:
        mw.progress.single_shot(0, _go, False)
    except Exception:
        _go()


def _settle(logger) -> None:
    """Conclude the migration for this profile: notify, then mark done.

    Called only from a genuine terminal state — every candidate readable and the
    rescue answered. The paths that stay armed (an empty folder, an unreadable
    candidate, an unreadable local or protected save) call
    ``_notify_affected_user`` on its own instead: the users most likely to have
    nothing left in media are also the most likely to have had the feature on,
    and they still need to hear it is gone. That notice deletes the config row
    it keys off, so running it on every pass cannot repeat it.
    """
    _notify_affected_user(logger)
    _mark_migration_done()


def _notify_affected_user(logger) -> None:
    """Tell the users who actually had the feature ON that it is gone.

    Without this the removal is silent for exactly the population it affects: a
    two-device user learns nothing unless the rescue prompt happens to fire, and
    that only fires when the media copy is STRICTLY ahead on the monotone
    counters — two devices that happen to be level produce no message at all.

    The stored ``misc.ankiweb_sync`` row is the only reliable way to identify
    them. The key is gone from DEFAULT_CONFIG, but the row persists in the
    config table (writes were INSERT OR REPLACE only, nothing ever deleted), so
    it can still be read here. It is deleted afterwards so this cannot re-fire.
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
            "Your save on this computer is untouched, and any copy in your media "
            "folder has been preserved.\n\n"
            "To move a save between computers now, use Ankimon → Export Save "
            "File… on one and Import Save File… on the other."
        )
        try:
            db.execute("DELETE FROM config WHERE key = ?", ("misc.ankiweb_sync",))
        except Exception:
            # Non-fatal: the worst case is the notice appearing once more.
            logger.log("info", "Could not clear the legacy misc.ankiweb_sync row.")
    except Exception as e:
        try:
            logger.log("error", f"Could not show the sync-removal notice: {e}")
        except Exception:
            pass


def run_media_migration(settings_obj, logger, *, after_media_sync: bool = False) -> None:
    """Protect, and offer to rescue, whatever the removed sync left in media.

    Runs until it RESOLVES for a profile, not merely once. Two jobs, in order:

    1. **Protect.** Copy the best save found in ``collection.media`` to the
       partition's protected name (``_ankimon_save.db``, or
       ``_ankimon_save_dev.db`` in developer mode) if that name does not already
       hold something at least as good. Every name the old feature used except
       the pre-2024 legacy one lacks the leading underscore, so today Anki's
       "Delete Unused Files" lists the user's save and deletes it — and that
       deletion propagates to their other devices. Nothing is deleted here; the
       unprotected copies are left where they are, now harmlessly, because a
       protected copy exists.

    2. **Rescue.** If the media save is strictly further along than the local one
       on monotone counters, offer to import it. That is the only way a user who
       relied on the removed feature can get back progress that only ever made
       it to AnkiWeb. Declining is remembered, so this asks at most once.

    Called from ``profile_did_open`` and again whenever the media-sync worker
    stops (``after_media_sync=True``). That second signal is a RESCAN TRIGGER
    only — Anki fires it before it inspects the future, so it says nothing about
    whether the sync succeeded. See the SETTLE POLICY note at the top of this
    module for what may and may not burn the one-shot flag.

    Any failure is logged and swallowed: this runs during profile open and must
    never be able to stop Ankimon from loading.
    """
    del after_media_sync        # a rescan trigger; carries no success meaning
    try:
        if _migration_done():
            return
        media_dir = _media_dir()
        if media_dir is None or not media_dir.is_dir():
            return          # no profile / no media folder — retry later

        target = _active_db_path()
        target_db = Path(target).name if target else "ankimon.db"
        saves, unreadable = _find_media_saves(media_dir, target_db)

        if not saves:
            # An absence is never a resolution. The folder may simply not have
            # received the peer's save yet — Anki gives no trustworthy "the
            # download finished" signal (see the SETTLE POLICY note above) — so
            # stay armed and rescan on the next boot or media-sync event.
            #
            # The removal notice is independent of that and is safe to run on
            # every pass: it fires only for users who had the feature ON, and it
            # deletes the config row it keys off, so it cannot repeat.
            _notify_affected_user(logger)
            return

        protected = media_dir / _protected_name_for(target_db)

        # An existing protected copy that will not open is UNKNOWN, not empty.
        # _progress_key floors it to (-1,-1,-1), so the old ranking let any
        # readable candidate — including a badly stale one — win and overwrite
        # it. A protected copy with a valid SQLite header and a damaged body is
        # writable, so that overwrite really did destroy saves; only a file of
        # pure garbage survived, and then only because SQLite refused the
        # destination connection.
        if protected.is_file() and get_db_stats(protected, timeout=MIGRATION_PROBE_TIMEOUT) is None:
            logger.log(
                "info",
                f"Ankimon: {protected.name} could not be read this pass; leaving "
                "it untouched and rescanning later.",
            )
            return

        best = max(
            saves,
            key=lambda p: _progress_key(get_db_stats(p, timeout=MIGRATION_PROBE_TIMEOUT)),
        )

        if best != protected:
            best_key = _progress_key(get_db_stats(best, timeout=MIGRATION_PROBE_TIMEOUT))
            protected_key = _progress_key(
                get_db_stats(protected, timeout=MIGRATION_PROBE_TIMEOUT)
            )
            # Both sides are known readable here, so this is a real comparison.
            if protected.is_file() and protected_key >= best_key:
                best = protected
            else:
                try:
                    _sqlite_backup(best, protected)
                    logger.log(
                        "info",
                        f"Ankimon: preserved {best.name} as {protected.name} "
                        "(protected from Anki's Delete Unused Files).",
                    )
                except Exception as e:
                    logger.log("error", f"Could not preserve {best.name} in media: {e}")
                    # The protected name was not written, so nothing here is
                    # resolved. Rank against the candidate itself for the rescue
                    # offer, but do not settle — retry the preserve next pass.
                    protected = best
                    unreadable = list(unreadable) + [media_dir / _protected_name_for(target_db)]

        media_stats = get_db_stats(protected, timeout=MIGRATION_PROBE_TIMEOUT)
        local_stats = (
            get_db_stats(target, timeout=MIGRATION_PROBE_TIMEOUT) if target else None
        )

        # The LOCAL save gets the same UNKNOWN treatment as the protected copy.
        # _progress_key floors an unreadable save to (-1,-1,-1), so a local save
        # merely locked this second — the OneDrive/antivirus case this add-on
        # already has a lock ladder for — would lose to any readable media copy,
        # be offered against a side the dialog itself renders as "could not read
        # this file", and then, if the user sensibly declined, fall through to
        # _settle() and burn the one-shot on a comparison that never happened.
        if target is not None and local_stats is None:
            logger.log(
                "info",
                f"Ankimon: {Path(target).name} could not be read this pass; "
                "not comparing saves, rescanning later.",
            )
            return

        if target is not None and _progress_key(media_stats) > _progress_key(local_stats):
            if askUser(
                "Ankimon's automatic AnkiWeb save-sync has been removed — it "
                "could not tell reliably which device's save was newer, and "
                "sometimes overwrote the wrong one.\n\n"
                "A save left in your Anki media folder (synced from AnkiWeb, if "
                "media sync is on) looks further along than the save on this "
                "computer:\n\n"
                f"IN YOUR MEDIA FOLDER\n{_format_stats(media_stats)}\n\n"
                f"ON THIS COMPUTER\n{_format_stats(local_stats)}\n\n"
                "Load the media-folder copy? Your current save will be backed up "
                "first, and Anki will close so it can be loaded cleanly.\n\n"
                "If you say no, nothing changes and you will not be asked again "
                "— the copy stays in your media folder either way.",
                parent=mw,
                defaultno=True,
            ):
                # Deliberately NOT marked done here. On success the replace
                # closes Anki; the next boot re-runs this, finds the media copy
                # no longer ahead of the (now equal) local save, skips the
                # prompt and marks it done then. On FAILURE — a refused backup,
                # a persisting file lock — the flag is still unset, so the user
                # is offered the rescue again next launch instead of silently
                # losing their only route back to that data.
                _offer_rescue_later(protected, Path(target))
                return
            # Declined: remember that, so this asks at most once.

        if unreadable:
            # Something in the folder exists but could not be judged this pass —
            # a lock, or damage that may yet be repaired. Notify, but stay armed
            # so a later scan can still protect and offer it.
            _notify_affected_user(logger)
            logger.log(
                "info",
                "Ankimon: "
                + ", ".join(sorted(p.name for p in unreadable))
                + " could not be read this pass; rescanning later.",
            )
            return

        _settle(logger)
    except Exception as e:
        try:
            logger.log("error", f"AnkiWeb sync-removal migration failed: {e}")
        except Exception:
            pass


def register_media_migration_hooks(settings_obj, logger) -> None:
    """Run the migration at profile open AND after every media sync.

    The post-sync pass is the load-bearing one: ``profile_did_open`` fires one
    line before Anki starts its own sync (``aqt/main.py:568-569``), so the boot
    scan on a second device runs against a media folder the peer's save has not
    reached yet. ``media_sync_did_start_or_stop(False)`` is the only signal that
    a download has actually landed.

    That hook fires on failure and abort as well as success, and even fires
    degenerately when media syncing is switched off in preferences — which is
    fine here, because it is treated only as "rescan now", never as "it worked".

    Handlers are removed before re-appending (the F31 registry-anchored pattern)
    so an in-session add-on reload cannot stack a second copy. The body is
    exception-proof because Anki permanently unregisters a gui-hook callback
    that raises, which would silently disable the rescan for the rest of the
    session.
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
            if running:
                return
            run_media_migration(settings_obj, logger, after_media_sync=True)
        except Exception:
            pass

    handlers = ((gui_hooks.media_sync_did_start_or_stop, on_media_sync_state),)
    for hook, handler in handlers:
        hook.append(handler)
    setattr(services, _MIGRATION_HOOK_RECORD, handlers)

    run_media_migration(settings_obj, logger)
