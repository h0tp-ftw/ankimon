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
import time
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

# Where a media save that has DIVERGED from the protected copy is preserved.
#
# Two saves diverge when neither is a superset of the other on the monotone
# counters — device A caught three more Pokemon, device B earned two more badges
# — which is the normal outcome of two machines that both played while the
# removed sync was picking a winner by mtime. The protected name can only hold
# one of them, and overwriting is destruction, so the other one gets its own
# underscore-prefixed name here. Only ONE extra name is ever needed: within a
# partition every candidate except the bare ``ankimon.db`` / ``ankimonDEV.db``
# already starts with an underscore and is therefore already safe from Anki's
# "Delete Unused Files", so at most one file per partition is at risk.
DIVERGED_MEDIA_SAVE_NAME = "_ankimon_save_diverged.db"
DEV_DIVERGED_MEDIA_SAVE_NAME = "_ankimon_save_dev_diverged.db"

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
# Settle (record the per-profile one-shot) ONLY on a positive resolution: every
# candidate that was discovered could be read, and the rescue reached a terminal
# answer — declined, not needed because the local save already holds everything
# the media copy does, or reported as a divergence only the user can resolve.
#
# Stay ARMED on every absence or uncertainty: an empty folder, a candidate that
# would not open, an unreadable protected copy, an unreadable local save, or an
# accepted rescue (which re-runs on the next boot and settles then).
#
# A settle is SCOPED TO WHAT WAS SEEN, never permanent. The flag stores a
# FINGERPRINT of the partition's media files (name, size, mtime) instead of a
# bare True, and ``_migration_done()`` agrees the profile is finished only while
# that fingerprint still matches. That is what closes the second half of the
# boot-ordering trap. profile_did_open fires one line before Anki starts its own
# sync (aqt/main.py:568-569), so the boot scan can find a STALE save already
# sitting in the folder — a leftover from the removed feature, or last week's
# copy — judge it honestly against the local save, resolve, and settle seconds
# before the peer's newer copy is downloaded on top of it. A bare one-shot could
# never look at that folder again: the newer save would be protected by nobody
# and offered to nobody, for exactly the two-device user the rescue exists to
# serve. A fingerprint re-arms the moment the file changes, and the post-sync
# pass picks it up.
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
# glob, with no SQLite opens at all — and a SETTLED profile costs less still,
# because the fingerprint check is stat-only and short-circuits before any scan
# is dispatched.

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
    """Monotone-only progress counters, for RANKING candidates.

    Deliberately excludes cash and level: both can legitimately go DOWN (spending
    in the shop; a level recomputed from a changed XP curve), so including them
    would let a save that merely spent money look 'older'. Counts of captured
    Pokemon, achieved badges and history rows only ever grow.

    This is a ranking heuristic and NOT an ordering of saves. Comparing the
    tuples directly compares them lexicographically, which invents a winner
    between two saves that merely diverged: (10, 0, 50) > (8, 3, 200) purely
    because 10 > 8 decides it before badges or history are ever looked at, and
    acting on that answer throws away three badges and 150 history rows. Nothing
    that overwrites or replaces a save may use ``>`` on these tuples — use
    ``_dominates`` for that. Ranking a list to pick a most-advanced candidate is
    fine, because picking one destroys nothing.
    """
    if stats is None:
        return (-1, -1, -1)
    return (stats["pokemon"], stats["badges"], stats["history"])


def _dominates(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    """True when ``a`` contains everything ``b`` does, and something more.

    The only comparison allowed to authorise a destructive step. Every counter
    in ``_progress_key`` is monotone, so a save that is genuinely descended from
    another is >= it on all three and > on at least one. Anything else is a
    DIVERGENCE — two saves that each hold progress the other lacks — and there
    is no honest way to pick a winner between them; the caller must preserve
    both and say so rather than overwrite one.

    An unreadable save (``None``) neither dominates nor is dominated: unknown is
    not the same as empty, and a file we merely failed to open this second must
    never lose to one we could.
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
    """Is this profile finished — for the media folder AS IT IS RIGHT NOW?

    The stored value is a fingerprint of the partition's media files, not a bare
    True, so "finished" expires the moment one of those files changes. See the
    SETTLE POLICY note at the top of this module for why: profile_did_open runs
    one line before Anki's own sync, so an honest resolution reached at boot can
    be made stale by a download that lands seconds later, and a permanent
    one-shot would never look again.
    """
    try:
        stored = mw.pm.profile.get(_MIGRATION_FLAG)
    except Exception:
        # No profile dict means no profile is loaded; treat as "not yet", the
        # caller will simply run again next time.
        return False
    if not isinstance(stored, str) or not stored:
        # Absent — or the bare ``True`` an earlier build of this migration
        # wrote. That build settled permanently, including on a stale pre-sync
        # save, so a profile still carrying one is re-armed for a single pass and
        # re-settles with a fingerprint straight away unless there is real work.
        return False
    try:
        return stored == _current_media_fingerprint()
    except Exception:
        return False


def _mark_migration_done() -> None:
    """Record WHAT was resolved, not merely THAT something was.

    Written after every file this pass creates, so the fingerprint describes the
    folder the resolution actually covered. A fingerprint that cannot be
    computed is stored as the empty string, which ``_migration_done`` reads as
    not-settled: the cost of that is one more scan, where the cost of wrongly
    settling is a save nobody ever offers back.
    """
    try:
        mw.pm.profile[_MIGRATION_FLAG] = _current_media_fingerprint()
        mw.pm.save()
    except Exception:
        pass


def _current_media_fingerprint() -> str:
    try:
        target = _active_db_path()
        return _media_fingerprint(
            _media_dir(), Path(target).name if target else "ankimon.db"
        )
    except Exception:
        return ""


def _media_fingerprint(media_dir: Optional[Path], target_db: str) -> str:
    """A stat-only signature of the partition's media saves.

    Deliberately cheap — no SQLite, no reads — because it runs on the
    profile-open stack before anything is dispatched. Size and mtime together
    are enough: Anki stamps a downloaded media file's mtime from the local clock
    at the moment it writes it (``add_file_from_ankiweb``), so a save that
    arrives from a peer always looks different from the one it replaced, and it
    only downloads at all when the sha1 differs.

    Every failure path yields a signature that will not match a stored one, so
    the migration re-arms rather than settling on a folder it could not read.
    """
    if media_dir is None:
        return ""
    parts = []
    for path in _media_candidate_paths(media_dir, target_db):
        try:
            stat = path.stat()
        except Exception:
            continue        # absent, or unstattable: not part of the signature
        parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(sorted(parts))


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
    # None of them carries the string "ankimonDEV" — "_ankimon_save_dev.db" and
    # "_ankimon_save_dev_diverged.db" spell it differently — so a substring check
    # alone hands the developer partition's own files to the normal partition.
    explicit = {
        DEV_MEDIA_SAVE_NAME: "ankimonDEV.db",
        DEV_DIVERGED_MEDIA_SAVE_NAME: "ankimonDEV.db",
        MEDIA_SAVE_NAME: "ankimon.db",
        DIVERGED_MEDIA_SAVE_NAME: "ankimon.db",
    }
    if name in explicit:
        return explicit[name]
    return "ankimonDEV.db" if "ankimonDEV" in name else "ankimon.db"


def _protected_name_for(target_db: str) -> str:
    """The protected filename belonging to ``target_db``'s partition.

    Each partition gets its own, so a developer-mode run can never write test
    progress into the name the normal-mode scan reads back.
    """
    return DEV_MEDIA_SAVE_NAME if target_db == "ankimonDEV.db" else MEDIA_SAVE_NAME


def _diverged_name_for(target_db: str) -> str:
    """Where this partition keeps a save that diverged from the protected one."""
    return (
        DEV_DIVERGED_MEDIA_SAVE_NAME
        if target_db == "ankimonDEV.db"
        else DIVERGED_MEDIA_SAVE_NAME
    )


def _media_candidate_paths(media_dir: Path, target_db: str) -> list:
    """Every path in ``media_dir`` that could hold a save for ``target_db``.

    ONE definition, shared by the scanner and by the settle fingerprint. If
    those two ever drift, the fingerprint stops noticing a file the scanner
    would have acted on — which silently re-opens the stale-settle hole the
    fingerprint exists to close.

    The legacy underscore names are GLOBBED rather than reconstructed. The old
    code built them from ``Path(__file__).parents[2].name``, which is
    ``addons21`` in a normal install and ``src`` in a git checkout, and real
    profiles have been seen carrying the numeric package id instead — so the
    exact prefix cannot be computed after the fact, only matched.
    """
    if target_db == "ankimonDEV.db":
        explicit = (DEV_MEDIA_SAVE_NAME, DEV_DIVERGED_MEDIA_SAVE_NAME, "ankimonDEV.db")
        pattern = "_*_ankimonDEV.db"
    else:
        explicit = (MEDIA_SAVE_NAME, DIVERGED_MEDIA_SAVE_NAME, "ankimon.db")
        pattern = "_*_ankimon.db"

    paths = [media_dir / name for name in explicit]
    try:
        for path in sorted(media_dir.glob(pattern)):
            # The globs are disjoint today, but a future name could collide, so
            # re-check the partition explicitly rather than trusting the pattern.
            if _target_db_for(path) == target_db and path not in paths:
                paths.append(path)
    except Exception:
        pass
    return paths


def _find_media_saves(media_dir: Path, target_db: str) -> tuple:
    """Every Ankimon save in collection.media belonging to ``target_db``.

    Returns ``(candidates, unreadable)``. A file that exists but will not open
    lands in ``unreadable`` rather than being silently dropped: it may be a real
    save merely locked by another process this second, and the caller must stay
    armed and rescan rather than settle as though the folder held nothing.
    """
    from .ankimon_sync import _verify_sqlite_integrity

    candidates = []
    unreadable = []
    seen = set()

    for path in _media_candidate_paths(media_dir, target_db):
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
            # Unreadable OR merely locked right now — either way this scan
            # cannot judge it, so record that and let the caller stay armed.
            unreadable.append(path)
            continue
        seen.add(resolved)
        candidates.append(path)
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
    """Conclude the migration for this profile: notify, then record what was seen.

    Called only from a genuine terminal state — every candidate readable and the
    rescue answered (accepted, declined, unnecessary, or reported as a
    divergence). What is recorded is a fingerprint of the media folder, so this
    concludes the migration FOR THAT FOLDER and re-arms by itself if a different
    save lands in it later. The paths that stay armed (an empty folder, an unreadable
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


def _migration_scan(media_dir: Path, target: Optional[Path]) -> Dict[str, Any]:
    """The FILE half of the migration: discover, rank, preserve. No UI.

    Deliberately free of Qt, dialogs and ``mw`` so it can be dispatched to a
    background thread — ``PRAGMA quick_check`` scans a whole database and a
    locked save waits out its busy timeout, and neither may happen on the
    profile-open stack (see ``start_media_migration``). Everything that needs the
    main thread comes back as data and is carried out by
    ``_apply_migration_result``: the prompts, the removal notice, and the settle.

    Two jobs, in order:

    1. **Protect.** Copy the most advanced save found in ``collection.media`` to
       the partition's protected name (``_ankimon_save.db``, or
       ``_ankimon_save_dev.db`` in developer mode) when that name does not
       already hold it. Every name the old feature used except the pre-2024
       legacy one lacks the leading underscore, so today Anki's "Delete Unused
       Files" lists the user's save and deletes it — and that deletion
       propagates to their other devices. Nothing is deleted here, and nothing
       is overwritten that is not strictly superseded; a save that merely
       DIVERGED from the protected copy is preserved beside it instead.

    2. **Judge.** Read the local save and hand the comparison back. Whether to
       offer a rescue is decided in ``_apply_migration_result``, because it ends
       in a dialog.
    """
    notes: list = []
    unreadable: list = []
    target_db = Path(target).name if target else "ankimon.db"
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
        }
        base.update(extra)
        return base

    if not saves:
        # An absence is never a resolution. The folder may simply not have
        # received the peer's save yet — Anki gives no trustworthy "the download
        # finished" signal (see the SETTLE POLICY note above) — so stay armed and
        # rescan on the next boot or media-sync event.
        #
        # The removal notice is independent of that and is safe to run on every
        # pass: it fires only for users who had the feature ON, and it deletes
        # the config row it keys off, so it cannot repeat.
        return _result("armed", notify=True)

    # Read each candidate exactly ONCE. Every one of these is a SQLite open on
    # a file that may be locked, so re-reading a path to re-compare it (which
    # earlier cuts of this function did, three times for the protected copy)
    # multiplies the worst case by the number of comparisons.
    stats: Dict[Path, Dict[str, Any]] = {}
    for path in saves:
        summary = get_db_stats(path, timeout=MIGRATION_PROBE_TIMEOUT)
        if summary is None:
            unreadable.append(path)
        else:
            stats[path] = summary

    protected = media_dir / _protected_name_for(target_db)

    # An existing protected copy that will not open is UNKNOWN, not empty.
    # _progress_key floors it to (-1, -1, -1), so ranking it against a readable
    # candidate lets any candidate — including a badly stale one — win and
    # overwrite it. A protected copy with a valid SQLite header and a damaged
    # body is writable, so that overwrite really did destroy saves.
    if protected.is_file() and protected not in stats:
        notes.append((
            "info",
            f"Ankimon: {protected.name} could not be read this pass; leaving it "
            "untouched and rescanning later.",
        ))
        return _result("armed")

    if not stats:
        notes.append((
            "info",
            "Ankimon: no save in the media folder could be read this pass; "
            "rescanning later.",
        ))
        return _result("armed")

    # Ranking is allowed to use the raw counters — picking a candidate to look
    # at more closely destroys nothing. Every step below that WRITES uses
    # _dominates instead.
    best = max(stats, key=lambda p: _progress_key(stats[p]))
    media_path, media_stats = best, stats[best]
    protected_stats = stats.get(protected)
    protected_now = protected_stats     # what the protected NAME holds after this pass

    if best != protected:
        if not protected.is_file() or _dominates(stats[best], protected_stats):
            # Either there is nothing to lose, or the candidate strictly
            # supersedes what is there — everything the protected copy holds is
            # in the candidate too, so replacing it loses nothing.
            media_path, media_stats = _preserve(
                best, protected, stats[best], notes, unreadable
            )
            protected_now = stats[best] if media_path == protected else protected_stats
        elif _dominates(protected_stats, stats[best]) or _progress_key(
            protected_stats
        ) == _progress_key(stats[best]):
            # The protected copy already holds this progress, or more.
            media_path, media_stats = protected, protected_stats
        else:
            # DIVERGED: each side holds progress the other lacks, and there is no
            # honest way to name a winner, so the protected copy is left alone
            # and the other side is preserved beside it below.
            media_path, media_stats = protected, protected_stats

    # Whatever the protected name ended up holding, a save that diverges from it
    # is still unprotected — and that is just as true when the protected copy is
    # the one that ranked highest, which is why this is not inside the branch
    # above. Skipped only when the protect step failed, because then the pass is
    # already staying armed to retry it.
    if protected_now is not None:
        _preserve_diverged(
            media_dir, target_db, stats, protected_now, notes, unreadable
        )

    local_stats = (
        get_db_stats(target, timeout=MIGRATION_PROBE_TIMEOUT) if target else None
    )

    # The LOCAL save gets the same UNKNOWN treatment as the protected copy. A
    # local save merely locked this second — the OneDrive/antivirus case this
    # add-on already has a lock ladder for — would otherwise lose to any readable
    # media copy, be offered against a side the dialog itself renders as "could
    # not read this file", and then, if the user sensibly declined, settle the
    # profile on a comparison that never happened.
    if target is not None and local_stats is None:
        notes.append((
            "info",
            f"Ankimon: {Path(target).name} could not be read this pass; "
            "not comparing saves, rescanning later.",
        ))
        return _result("armed", media_path=media_path, media_stats=media_stats)

    return _result(
        "compare",
        media_path=media_path,
        media_stats=media_stats,
        local_stats=local_stats,
    )


def _preserve(source: Path, protected: Path, source_stats: Dict[str, Any],
              notes: list, unreadable: list) -> tuple:
    """Copy ``source`` to the partition's protected name.

    Returns the (path, stats) the rescue comparison should use. On failure that
    is the candidate itself — the rescue is still worth offering from where the
    file actually is — and the protected name joins ``unreadable`` so the pass
    stays armed and retries the copy next time.
    """
    try:
        _sqlite_backup(source, protected)
        notes.append((
            "info",
            f"Ankimon: preserved {source.name} as {protected.name} "
            "(protected from Anki's Delete Unused Files).",
        ))
        return protected, source_stats
    except Exception as e:
        notes.append(("error", f"Could not preserve {source.name} in media: {e}"))
        unreadable.append(protected)
        return source, source_stats


def _preserve_diverged(media_dir: Path, target_db: str,
                       stats: Dict[Path, Dict[str, Any]],
                       protected_stats: Optional[Dict[str, Any]],
                       notes: list, unreadable: list) -> None:
    """Give the AT-RISK save a protected home without touching the protected copy.

    "At risk" is a precise, small set: within a partition every candidate but the
    bare ``ankimon.db`` / ``ankimonDEV.db`` already carries a leading underscore,
    and Anki's media check only offers to delete a file when
    ``!file.starts_with('_')``. So there is exactly one file per partition that
    "Delete Unused Files" can take, and one spare protected name is always
    enough — as long as it is that file this looks at, not whichever candidate
    happened to rank highest.
    """
    protected_name = _protected_name_for(target_db)
    at_risk = media_dir / target_db
    at_risk_stats = stats.get(at_risk)

    if at_risk_stats is None:
        # The divergence is between two files that are both already protected —
        # the steady state after an earlier pass wrote the diverged name, which
        # is why that copy is not made again on every boot.
        notes.append((
            "info",
            "Ankimon: the saves in your media folder have diverged — each holds "
            "progress the other does not. All are kept; none is overwritten.",
        ))
        return

    if protected_stats is not None and (
        _dominates(protected_stats, at_risk_stats)
        or _progress_key(protected_stats) == _progress_key(at_risk_stats)
    ):
        return          # already safe: the protected copy holds all of it

    diverged = media_dir / _diverged_name_for(target_db)
    occupant = (
        get_db_stats(diverged, timeout=MIGRATION_PROBE_TIMEOUT)
        if diverged.is_file()
        else None
    )
    if diverged.is_file() and occupant is None:
        # Unknown is not empty, and not something to write over. Retry later.
        notes.append((
            "info",
            f"Ankimon: {diverged.name} could not be read this pass; leaving "
            f"{at_risk.name} where it is and rescanning later.",
        ))
        unreadable.append(diverged)
        return
    if diverged.is_file() and not _dominates(at_risk_stats, occupant):
        # A third, equally incomparable save. Nothing here may be overwritten and
        # inventing further names would not terminate, so leave all of it in
        # place — untouched is worse than protected, but it is not destroyed.
        notes.append((
            "info",
            f"Ankimon: {at_risk.name} diverges from both {protected_name} and "
            f"{diverged.name}; all three are left exactly as they are. Nothing "
            "is deleted, but do not use Anki's Delete Unused Files here.",
        ))
        return
    try:
        _sqlite_backup(at_risk, diverged)
        notes.append((
            "info",
            f"Ankimon: {at_risk.name} diverged from {protected_name} — each holds "
            f"progress the other does not — so it was preserved as "
            f"{diverged.name} rather than written over it.",
        ))
    except Exception as e:
        notes.append(("error", f"Could not preserve {at_risk.name} in media: {e}"))
        unreadable.append(diverged)


def _apply_migration_result(result: Dict[str, Any], logger) -> None:
    """The MAIN-THREAD half: log, prompt, and settle or stay armed.

    Everything in here either touches Qt or writes the profile flag, so it runs
    on the callback side of ``start_media_migration`` — never on the worker.
    """
    for level, message in result.get("log", ()):
        try:
            logger.log(level, message)
        except Exception:
            pass

    if result.get("notify"):
        _notify_affected_user(logger)

    if result.get("outcome") != "compare":
        return

    target = result.get("target")
    media_stats = result.get("media_stats")
    local_stats = result.get("local_stats")

    if target is not None and _dominates(media_stats, local_stats):
        if askUser(
            "Ankimon's automatic AnkiWeb save-sync has been removed — it "
            "could not tell reliably which device's save was newer, and "
            "sometimes overwrote the wrong one.\n\n"
            "A save left in your Anki media folder (synced from AnkiWeb, if "
            "media sync is on) contains everything the save on this computer "
            "does, and more:\n\n"
            f"IN YOUR MEDIA FOLDER\n{_format_stats(media_stats)}\n\n"
            f"ON THIS COMPUTER\n{_format_stats(local_stats)}\n\n"
            "Load the media-folder copy? Your current save will be backed up "
            "first, and Anki will close so it can be loaded cleanly.\n\n"
            "If you say no, nothing changes — the copy stays in your media "
            "folder either way, and you will not be asked about it again "
            "unless a different copy arrives there.",
            parent=mw,
            defaultno=True,
        ):
            # Deliberately NOT settled here. On success the replace closes Anki;
            # the next boot re-runs this, finds the media copy no longer ahead of
            # the (now equal) local save, skips the prompt and settles then. On
            # FAILURE — a refused backup, a persisting file lock — the flag is
            # still unset, so the user is offered the rescue again next launch
            # instead of silently losing their only route back to that data.
            _offer_rescue_later(Path(result["media_path"]), Path(target))
            return
        # Declined: settling below remembers that for this media folder.
    elif (
        target is not None
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
            "There is a save in your Anki media folder that has DIVERGED from "
            "the one on this computer: each contains progress the other does "
            "not, so neither can simply replace the other.\n\n"
            f"IN YOUR MEDIA FOLDER ({Path(result['media_path']).name})\n"
            f"{_format_stats(media_stats)}\n\n"
            f"ON THIS COMPUTER\n{_format_stats(local_stats)}\n\n"
            "Nothing has been changed and nothing has been deleted. If you want "
            "the media-folder copy, copy it out of your collection.media folder "
            "and load it with Ankimon → Import Save File… — your current save is "
            "backed up before it is replaced."
        )

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

    _settle(logger)


def run_media_migration(settings_obj, logger, *, after_media_sync: bool = False) -> None:
    """Protect, and offer to rescue, whatever the removed sync left in media.

    The SYNCHRONOUS form: scan and act on the calling thread. Used directly by
    tests, and as the fallback when Anki's task manager is unavailable. Anki's
    own callers go through ``start_media_migration``, which keeps the file work
    off the profile-open stack.

    Runs until it RESOLVES for a profile, and re-arms whenever the media folder
    changes underneath a resolution — see ``_migration_done`` and the SETTLE
    POLICY note at the top of this module.

    Called from ``profile_did_open`` and again whenever the media-sync worker
    stops (``after_media_sync=True``). That second signal is a RESCAN TRIGGER
    only — Anki fires it before it inspects the future, so it says nothing about
    whether the sync succeeded.

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


def start_media_migration(settings_obj, logger, *, after_media_sync: bool = False) -> None:
    """Run the migration without blocking the thread that asked for it.

    The scan opens SQLite databases: ``PRAGMA quick_check`` reads the whole file
    and a locked save waits out its busy timeout. Both callers — ``profile_did
    _open`` and the media-sync hook — are on Anki's main thread, where that time
    is a frozen UI, so the file work goes to ``mw.taskman.run_in_background`` and
    only the decisions come back to the main thread.

    The cheap guards stay here, ahead of the dispatch: a settled profile is a
    handful of ``stat`` calls and starts no thread at all.

    ``run_in_background`` rather than ``QueryOp`` on purpose — this is silent
    background housekeeping and must not raise a progress window over Anki's
    startup, nor block on the collection it does not touch.
    """
    del after_media_sync        # a rescan trigger; carries no success meaning
    try:
        if _migration_done():
            return
        media_dir = _media_dir()
        if media_dir is None or not media_dir.is_dir():
            return
        target = _active_db_path()

        if _MIGRATION_SCAN_STATE["running"]:
            _MIGRATION_SCAN_STATE["rerun"] = True
            return

        def _scan():
            return _migration_scan(media_dir, target)

        def _done(future) -> None:
            try:
                result = future.result()
                # A profile switch during the scan would leave us applying one
                # profile's media folder to another's flag and another's save.
                # mw.pm has already moved on by the time this runs, so compare.
                if result is not None and _media_dir() == media_dir:
                    _apply_migration_result(result, logger)
            except Exception as e:
                try:
                    logger.log("error", f"AnkiWeb sync-removal migration failed: {e}")
                except Exception:
                    pass
            finally:
                _MIGRATION_SCAN_STATE["running"] = False
                if _MIGRATION_SCAN_STATE["rerun"]:
                    _MIGRATION_SCAN_STATE["rerun"] = False
                    start_media_migration(settings_obj, logger)

        _MIGRATION_SCAN_STATE["running"] = True
        try:
            mw.taskman.run_in_background(_scan, _done)
        except Exception:
            # No task manager (or it refused): correctness beats responsiveness.
            _MIGRATION_SCAN_STATE["running"] = False
            run_media_migration(settings_obj, logger)
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

    Both entry points go through ``start_media_migration``: this one is called
    from ``profile_did_open`` and the other from a gui hook, so both are on the
    main thread, where a locked or oversized save must not be waited on.
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
            start_media_migration(settings_obj, logger, after_media_sync=True)
        except Exception:
            pass

    handlers = ((gui_hooks.media_sync_did_start_or_stop, on_media_sync_state),)
    for hook, handler in handlers:
        hook.append(handler)
    setattr(services, _MIGRATION_HOOK_RECORD, handlers)

    start_media_migration(settings_obj, logger)
