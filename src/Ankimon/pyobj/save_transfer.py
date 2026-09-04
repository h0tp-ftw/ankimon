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
  ``collection.media``. It preserves by CONTENT — every distinct save gets its
  own ``_ankimon_save_<digest>.db`` — so it can add a protected copy but never
  choose between two saves. See ``run_media_migration``.

Every destructive step is gated conjunctively: verified source, successful
backup, explicit user confirmation, atomic replace. Any failure aborts with the
local save byte-identical.

Public surface: ``export_save`` / ``import_save`` (menu), ``get_db_stats``,
``register_media_migration_hooks`` (profile open), and the two migration entry
points ``start_media_migration`` / ``run_media_migration``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
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

# Where the migration preserves a media save: ``<prefix><digest>.db``, one
# prefix per partition (developer mode keeps its own ``ankimonDEV.db``, and the
# old sync wrote both). The LEADING UNDERSCORE is the whole point: Anki's media
# check offers to delete a file only when
# ``!file.starts_with('_') && !references.contains_key(&file)``, and nothing in
# a collection ever references a save, so the bare ``ankimon.db`` the removed
# feature wrote is one "Delete Unused Files" away from being gone on every
# device.
#
# CONTENT-ADDRESSED, because there is nothing else honest to key a name on. A
# fixed name can hold one save, so a second one arriving forces a choice between
# overwriting it and leaving the newcomer under the deletable bare name — and
# the only evidence available for that choice is the progress counters, which
# are aggregates: ``(3 pokemon, 2 badges, 101 history) >= (2, 2, 100)`` says
# nothing about whether those three Pokemon INCLUDE the two, equal counters can
# share no rows at all, and captures are not even monotone
# (``AnkimonDB.delete_pokemon``). A digest of the file's own bytes gives every
# distinct save its own name instead: dedupe is a ``stat``, the Nth divergent
# save gets a home like the first, nothing is ever overwritten, and two devices
# that receive one save through media sync converge on one filename. The cost —
# one file per distinct save that ever passed through the folder — is the
# user's own data, and the right side of the trade against deleting one.
_SAVE_PREFIX = {"ankimon.db": "_ankimon_save_", "ankimonDEV.db": "_ankimon_save_dev_"}

# Half a SHA-256, in hex: collisions between one user's saves do not happen,
# and the name stays readable.
_DIGEST_CHARS = 32

# SETTLE POLICY — the one rule this migration turns on.
#
# The per-profile flag (``mw.pm.profile``, so it is scoped to the profile that
# owns ``collection.media`` rather than to the add-on install that every profile
# shares, and lives in prefs21.db where restoring a save backup cannot rewind
# it) is written ONLY on a positive resolution: every discovered candidate could
# be read, and the rescue reached a terminal answer — declined, unnecessary, or
# reported as a divergence. Every absence or uncertainty stays ARMED: an empty
# folder, a candidate that would not open, an unreadable local save, or an
# accepted rescue (which re-runs on the next boot and settles then).
#
# A settle is SCOPED TO WHAT WAS SEEN, never permanent: the flag stores a
# stat-only FINGERPRINT (name, size, mtime) of the partition's media files as
# the scan examined them, and the profile counts as finished only while that
# still matches. ``profile_did_open`` fires one line before Anki starts its own
# sync (aqt/main.py:568-569), so a boot scan can honestly resolve a stale save
# and settle seconds before the peer's newer copy lands on top of it; a
# fingerprint re-arms the moment the file changes.
#
# There is deliberately no "a media sync finished" signal, because Anki has
# none: ``media_sync_did_start_or_stop(False)`` fires (aqt/mediasync.py:80)
# BEFORE the future is inspected (:82), identically on success, a dropped
# network, a user abort, and a profile with media sync off. It is a rescan
# trigger, nothing more. Staying armed costs a few stat calls and a glob; a
# settled profile costs less, because the fingerprint check short-circuits
# before any scan is dispatched.
_MIGRATION_FLAG = "ankimonMediaSyncRemovedV1"

# The fingerprint of the folder whose comparison the user already ANSWERED. Kept
# apart from the settle because a folder holding one unreadable file beside a
# readable save that is ahead of the local one must stay armed (to retry the
# unreadable file) and yet not greet the user with the same rescue prompt on
# every profile open. A folder that changes is asked afresh.
_MIGRATION_ANSWERED_FLAG = "ankimonMediaSyncRemovedAnsweredV1"

# How long the AUTOMATIC migration waits on a locked file, and the wall-clock
# budget for each statement it runs against a save. The scan runs on a worker
# but stays short: a file that will not open this second is rescanned on the
# next pass, and a post-sync rescan request is coalesced behind an in-flight
# scan, so every second spent here delays the rescan that matters. (The
# synchronous fallback pays this on the calling thread, where sqlite3's unset
# 5 s default once stalled a boot by 5.2 s on one locked file.) User-initiated
# Export/Import keep the full 30 s, where waiting out a passing lock is exactly
# what the user wants.
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
    """Monotone-only progress counters, for RANKING candidates.

    Deliberately excludes cash and level: both can legitimately go DOWN (spending
    in the shop; a level recomputed from a changed XP curve), so including them
    would let a save that merely spent money look 'older'. Captures, achieved
    badges and history rows are the closest thing to a progress signal the save
    has — though not a monotone one either: ``AnkimonDB.delete_pokemon`` releases
    a Pokemon and the duplicate prune drops rows, so even this tuple can fall.

    A RANKING HEURISTIC, and nothing more. It has exactly two jobs: choose which
    media candidate to show the user, and decide whether a rescue is worth
    OFFERING. It may not authorise a write, and neither may ``_dominates``.

    Comparing the tuples directly compares them lexicographically, which invents
    a winner between two saves that merely diverged: (10, 0, 50) > (8, 3, 200)
    purely because 10 > 8 decides it before badges or history are ever looked
    at. That is why the rescue offer goes through ``_dominates`` rather than
    ``>``; ranking a list to pick a candidate to LOOK at is fine, because
    picking one destroys nothing.
    """
    if stats is None:
        return (-1, -1, -1)
    return (stats["pokemon"], stats["badges"], stats["history"])


def _dominates(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    """True when ``a`` is ahead of ``b`` on every count Ankimon can compare.

    NOT a containment test, and nothing destructive may be built on it. These
    are aggregate counts: three captures are not evidence of WHICH three, so
    ``(3, 2, 101) >= (2, 2, 100)`` is entirely consistent with two saves that
    share no rows at all, and equal counts may share none either.

    What it is good for is deciding whether a rescue is worth OFFERING. A media
    save that is behind or level on all three has nothing to give the user, so
    asking would be noise; one that is ahead on all three might, so the question
    gets asked — with both saves' contents shown, the local save backed up
    first, and the user's explicit yes. Files in the media folder are preserved
    by ``_preserve``, which asks this function nothing.

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


def _strip_local_secrets(db_path: Path) -> None:
    """Remove machine-local credentials from a save that is about to travel.

    The export is a full SQLite backup, and Ankimon's settings live in the same
    database's ``config`` table (``settings.set`` → ``db.set_config_value``), so
    without this the user's leaderboard API key rides along in a file whose
    whole purpose is to be copied to another computer — or handed to someone
    else. Only genuine secrets are stripped; the username is not one. Runs on
    the temp copy, before verification, so the live save is never touched and a
    failure discards the export rather than shipping the key.

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
        conn.execute("DELETE FROM config WHERE key = ?", ("leaderboard.api_key",))
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

    Order is load-bearing. The source is verified HERE, at the moment of the
    write, not only when it was chosen: Import checks its file a few lines
    before this, but the rescue checked its candidate on a worker thread and
    then waited for the user to read a dialog — a window in which Anki's media
    sync can replace a media file underneath the offer. Then the backup, and a
    failed backup REFUSES the replacement — never overwrite the live save with
    no recovery path. The replace itself goes through ``_atomic_replace``, which
    closes the live connection, writes a temp on the same volume,
    ``os.replace``s it into place (retrying a transient OneDrive/antivirus lock)
    and reaps stale ``-wal`` / ``-shm`` sidecars belonging to the old file.

    Anki is closed afterwards, and that is deliberate rather than lazy: the next
    boot re-runs ``profile_did_open``, whose ``watermark == 0`` re-derivation
    from ``MAX(id) FROM revlog`` is what stops the imported save's mobile
    watermark from queueing thousands of already-handled reviews as fresh mobile
    battles. It also rehydrates every singleton holding the old connection.
    """
    from .ankimon_sync import (
        get_ankimon_sync, _handle_manual_sync_error, _verify_sqlite_integrity,
    )

    if not _verify_sqlite_integrity(Path(source)):
        showWarning(
            f"{what} aborted: the save file no longer passes an integrity check "
            "(it may have changed since it was checked), so nothing was "
            "replaced. Your save is unchanged."
        )
        return False

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
    SETTLE POLICY note above). Absent — or the bare ``True`` an earlier build
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
    is a save nobody offers back.
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
    sha1 differs. A file that cannot be stat'ed is simply not part of the
    signature, so the migration re-arms rather than settling on a folder it
    could not read.

    Returned as a dict so the scan can amend it with only the files it wrote
    ITSELF — which is what separates "the folder the scan resolved" from "the
    folder as it stands after an unrelated download".
    """
    entries: Dict[str, str] = {}
    for path in _media_candidate_paths(media_dir, target_db):
        entry = _fingerprint_entry(path)
        if entry is not None:
            entries[path.name] = entry
    return entries


def _fingerprint_entry(path: Path) -> Optional[str]:
    try:
        stat = path.stat()
    except Exception:
        return None
    return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def _join_fingerprint(entries: Dict[str, str]) -> str:
    return "|".join(sorted(entries.values()))


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
    """Hex digest of ``path``'s bytes, or ``None`` if it could not be read.

    Exact file content is the ONLY identity this migration trusts. Two files
    with the same digest are the same save and one copy is enough; anything else
    is treated as a distinct save and gets its own protected name — a
    conservative direction, because the worst it can do is keep a redundant copy
    of a save that some other measure might have called equal.
    """
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:_DIGEST_CHARS]
    except Exception:
        return None


def _media_candidate_paths(media_dir: Path, target_db: str) -> list:
    """Every path in ``media_dir`` that could hold a save for ``target_db``.

    ONE definition, shared by the scanner and by the settle fingerprint. If
    those two ever drift, the fingerprint stops noticing a file the scanner
    would have acted on — which silently re-opens the stale-settle hole the
    fingerprint exists to close.

    Three kinds of name: the bare one the removed feature wrote; the
    content-addressed copies this migration writes; and the pre-2024 legacy
    names, which are GLOBBED rather than reconstructed. The old code built them
    from ``Path(__file__).parents[2].name``, which is ``addons21`` in a normal
    install and ``src`` in a git checkout, and real profiles have been seen
    carrying the numeric package id instead — so the exact prefix cannot be
    computed after the fact, only matched.
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
    on every pass and cannot repeat.
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
            # Through the committing helper, not a bare ``db.execute``: that
            # left the DELETE in an open transaction — invisible to the next
            # boot, so the notice repeated, and holding this connection's write
            # lock until some unrelated write happened to commit it.
            db.delete_config_value("misc.ankiweb_sync")
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

    1. **Protect.** Copy the bare ``ankimon.db`` / ``ankimonDEV.db`` — the only
       name in the partition that Anki's "Delete Unused Files" can take, and the
       name the removed feature wrote — to ``_ankimon_save_<digest>.db``. The
       name comes from the file's own content, so this can only ever collide
       with a copy of the same save: nothing is deleted, nothing is overwritten,
       and no comparison decides anything. A second, third or tenth divergent
       save each get their own protected name.

    2. **Judge.** Read the local save and hand the comparison back. Whether to
       offer a rescue is decided in ``_apply_migration_result``, because it ends
       in a dialog.
    """
    notes: list = []
    unreadable: list = []
    written: list = []
    target_db = Path(target).name if target else "ankimon.db"

    # Taken BEFORE anything is read or written, and amended below with only the
    # files this scan wrote itself, so the settle describes the folder this pass
    # actually examined — a peer's save landing mid-scan is not recorded as
    # resolved.
    entries = _media_fingerprint_entries(media_dir, target_db)

    saves, integrity_failures = _find_media_saves(media_dir, target_db)
    unreadable.extend(integrity_failures)

    def _result(outcome: str, **extra) -> Dict[str, Any]:
        for path in written:
            entry = _fingerprint_entry(path)
            if entry is None:
                entries.pop(path.name, None)
            else:
                entries[path.name] = entry
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
        }
        base.update(extra)
        return base

    if not saves:
        # An absence is never a resolution: the folder may simply not have
        # received the peer's save yet, so stay armed and rescan on the next
        # boot or media-sync event. The removal notice is independent of that
        # and safe to run on every pass.
        return _result("armed", notify=True)

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

    # PROTECT. Exactly ONE file in this partition can be taken by Anki's "Delete
    # Unused Files": the bare ``ankimon.db`` / ``ankimonDEV.db``. Every other
    # candidate already begins with an underscore. So the protect step has one
    # job, on one file, and it is unconditional: it does not rank, does not
    # compare, and does not write over anything.
    at_risk = media_dir / target_db
    preserved = (
        _preserve(at_risk, media_dir, target_db, notes, unreadable, written)
        if at_risk in stats
        else None
    )

    # JUDGE. Ranking chooses which candidate to SHOW the user; picking one
    # destroys nothing, so the raw counters are allowed here. Whether that
    # candidate is worth offering at all is decided in _apply_migration_result.
    best = max(stats, key=lambda p: _progress_key(stats[p]))
    media_path, media_stats = best, stats[best]
    if best == at_risk and preserved is not None and (
        preserved in written or preserved in stats
    ):
        # Byte-for-byte the same save, under the name that will still be there
        # after a media check — so that is the one to name in the dialog and to
        # rescue from. Only when this pass either WROTE it (and verified it
        # before publishing) or read it: an older copy of the same content that
        # would not open this pass is still preserved, but it is not something
        # to offer as a replacement for the live save.
        media_path = preserved

    local_stats = (
        get_db_stats(target, timeout=MIGRATION_PROBE_TIMEOUT) if target else None
    )

    # A save that will not open is UNKNOWN, never empty — the distinction the
    # whole comparison rests on, and one _progress_key cannot make on its own,
    # since it floors an unreadable save to (-1, -1, -1). A local save merely
    # locked this second would otherwise lose to any readable media copy, be
    # offered against a side the dialog renders as "could not read this file",
    # and then, if the user sensibly declined, settle the profile on a
    # comparison that never happened.
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


def _preserve(at_risk: Path, media_dir: Path, target_db: str,
              notes: list, unreadable: list, written: list) -> Optional[Path]:
    """Give the one deletable save in this folder a protected, permanent home.

    Copies ``at_risk`` — the bare ``ankimon.db`` / ``ankimonDEV.db``, the only
    name in the partition Anki's "Delete Unused Files" can take — to
    ``_ankimon_save_<digest>.db``. Returns that path, whether this pass wrote it
    or found it already there; ``None`` if the copy could not be made.

    Three properties, in the order they matter:

    * **It never overwrites.** The destination name is derived from the source's
      own bytes, so the only file it can collide with is one holding that exact
      save. There is no case in which this function has to choose between two
      different saves, which means there is no case in which it can choose
      wrong.
    * **It is idempotent.** Once the copy exists, its name is a statement about
      its content, so the next pass recognises the steady state from a digest
      and a ``stat``, and writes nothing.
    * **It leaves the source alone.** A plain byte copy, deliberately: the media
      file is static (nothing here has it open, and Anki's media sync replaces
      files rather than writing into them), so ``Connection.backup`` would buy
      nothing — while opening it read-write, which on a file carrying a hot
      rollback journal runs recovery and MODIFIES the one thing this migration
      promised not to touch.

    The published name is taken from the TEMP's bytes, not from the probe above
    it, so a media download landing mid-copy cannot produce a file whose name
    describes a different save than its contents. The temp is verified before it
    is published — the protected copy is the one file here that has to be
    trustworthy — and ``_atomic_write_over`` (the same lock ladder and EXDEV
    handling as import/export, #639/#636) moves it into place, so an
    interruption cannot leave a half-written protected copy behind.
    """
    from .ankimon_sync import _atomic_write_over, _verify_sqlite_integrity

    tmp = None
    dest = None
    try:
        # Cheap path first: hash what is already on disk and see whether that
        # save has a home. In the steady state that is one read and one stat,
        # with no copy and no write at all.
        probe = _content_digest(at_risk)
        if probe is not None:
            settled = media_dir / _protected_copy_name(target_db, probe)
            if settled.is_file():
                return settled

        fd, name = tempfile.mkstemp(prefix="ankimon-protect-", suffix=".db")
        os.close(fd)
        tmp = Path(name)
        shutil.copy2(at_risk, tmp)
        digest = _content_digest(tmp)
        if digest is None:
            raise OSError(f"could not read back the copy of {at_risk.name}")
        dest = media_dir / _protected_copy_name(target_db, digest)
        if dest.is_file():
            return dest         # this exact save is already preserved
        if not _verify_sqlite_integrity(tmp, timeout=MIGRATION_PROBE_TIMEOUT):
            raise OSError(f"the copy of {at_risk.name} did not verify")
        _atomic_write_over(tmp, dest)
        written.append(dest)
        notes.append((
            "info",
            f"Ankimon: preserved {at_risk.name} as {dest.name} "
            "(protected from Anki's Delete Unused Files). Nothing was deleted "
            "or overwritten.",
        ))
        return dest
    except Exception as e:
        notes.append(("error", f"Could not preserve {at_risk.name} in media: {e}"))
        # Stay armed and retry next pass: the at-risk file is still sitting
        # there under a name a media check can delete.
        unreadable.append(dest if dest is not None else at_risk)
        return None
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


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
    fingerprint = result.get("fingerprint", "")
    # A pass that must stay armed for an unreadable file never settles, so the
    # answer is remembered on its own or the question repeats every boot.
    answered = bool(fingerprint) and _profile_flag(_MIGRATION_ANSWERED_FLAG) == fingerprint

    if target is not None and _dominates(media_stats, local_stats) and not answered:
        if askUser(
            "Ankimon's automatic AnkiWeb save-sync has been removed — it "
            "could not tell reliably which device's save was newer, and "
            "sometimes overwrote the wrong one.\n\n"
            "A save left in your Anki media folder (synced from AnkiWeb, if "
            "media sync is on) is further along than the save on this computer "
            "on every count Ankimon can compare:\n\n"
            f"IN YOUR MEDIA FOLDER\n{_format_stats(media_stats)}\n\n"
            f"ON THIS COMPUTER\n{_format_stats(local_stats)}\n\n"
            "Load the media-folder copy? Compare the two above first — Ankimon "
            "counts what each save holds, it cannot tell whether one contains "
            "the other. Your current save will be backed up before anything is "
            "replaced, and Anki will close so the copy can be loaded cleanly."
            "\n\n"
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
        # Declined: remembered for this media folder, whether or not this pass
        # goes on to settle.
        _set_profile_flag(_MIGRATION_ANSWERED_FLAG, fingerprint)
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
        _set_profile_flag(_MIGRATION_ANSWERED_FLAG, fingerprint)

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
    tests, and as the fallback when Anki's task manager is unavailable. Anki's
    own callers go through ``start_media_migration``, which keeps the file work
    off the profile-open stack.

    Runs until it RESOLVES for a profile, and re-arms whenever the media folder
    changes underneath a resolution — see the SETTLE POLICY note at the top of
    this module. Any failure is logged and swallowed: this runs during profile
    open and must never be able to stop Ankimon from loading.
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


def start_media_migration(settings_obj, logger) -> None:
    """Run the migration without blocking the thread that asked for it.

    The scan opens SQLite databases: ``PRAGMA quick_check`` reads the whole file
    and a locked save waits out its busy timeout. Both callers — ``profile_did
    _open`` and the media-sync hook — are on Anki's main thread, where that time
    is a frozen UI, so the file work goes to ``mw.taskman.run_in_background`` and
    only the decisions come back to the main thread. The cheap guards stay here,
    ahead of the dispatch: a settled profile is a handful of ``stat`` calls and
    starts no thread at all.

    ``run_in_background`` rather than ``QueryOp`` on purpose — this is silent
    background housekeeping and must not raise a progress window over Anki's
    startup, nor block on the collection it does not touch. The second half of
    that needs ``uses_collection=False``: the default executor is the single
    worker Anki's own collection sync and every ``QueryOp`` queue on, so a scan
    waiting out a locked file would hold the boot sync behind it, and the boot
    sync would hold the post-sync rescan behind it. Its callback is safe for
    the dialogs and the ``mw.pm`` write in ``_apply_migration_result`` because
    aqt wraps it (``aqt/taskman.py:86-88``)::

        if on_done is not None:
            fut.add_done_callback(
                lambda future: self.run_on_main(lambda: on_done(future))
            )
    """
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
            mw.taskman.run_in_background(_scan, _done, uses_collection=False)
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
    line before Anki starts its own sync, so the boot scan on a second device
    runs against a media folder the peer's save has not reached yet. The
    ``media_sync_did_start_or_stop(False)`` hook is treated only as "rescan
    now", never as "it worked" — see the SETTLE POLICY note for why it cannot
    mean more.

    A request that arrives while a scan is in flight is COALESCED into one more
    run rather than dropped, because the media-sync hook can fire during the
    boot scan — which is the very ordering the post-sync pass exists to cover.

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
            if not running:
                start_media_migration(settings_obj, logger)
        except Exception:
            pass

    handlers = ((gui_hooks.media_sync_did_start_or_stop, on_media_sync_state),)
    for hook, handler in handlers:
        hook.append(handler)
    setattr(services, _MIGRATION_HOOK_RECORD, handlers)

    start_media_migration(settings_obj, logger)
