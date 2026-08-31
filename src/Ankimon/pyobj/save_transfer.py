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

# The media copy is named with a LEADING UNDERSCORE on purpose. Anki's media
# check reports a file as unused (and offers to delete it, propagating that
# deletion to every other device) only when
# ``!file.starts_with('_') && !references.contains_key(&file)`` — nothing in a
# collection will ever reference a save file, so the underscore is the only
# thing standing between it and "Delete Unused Files". The shipped code wrote
# the bare name ``ankimon.db``, and ``_migrate_legacy_files`` actively moved
# users from the protected legacy name TO that unprotected one.
#
# READ-ONLY as of the content-addressed scheme below: earlier builds of this
# migration wrote these fixed names, so they are still discovered, ranked and
# offered — but nothing writes them any more, because a fixed name is a name
# that eventually has to be overwritten.
MEDIA_SAVE_NAME = "_ankimon_save.db"

# Developer mode keeps its own ``ankimonDEV.db``, and it needs its own protected
# name too. Sharing one destination let a developer-mode run write dev data into
# _ankimon_save.db, which _target_db_for then reads back as a NORMAL-partition
# candidate (the name carries no "ankimonDEV"), so a test save could be ranked
# against — and offered over — the real one. collection.media syncs, so the
# contaminated file reached other devices as well.
DEV_MEDIA_SAVE_NAME = "_ankimon_save_dev.db"

# ...but the two fixed names above are what EARLIER builds wrote. They are kept
# only so those files are still FOUND — they already start with an underscore,
# so they are already safe, and nothing below ever writes to them again.
DIVERGED_MEDIA_SAVE_NAME = "_ankimon_save_diverged.db"
DEV_DIVERGED_MEDIA_SAVE_NAME = "_ankimon_save_dev_diverged.db"

# What this migration writes TODAY: ``_ankimon_save_<digest>.db``, where the
# digest is of the preserved file's own bytes.
#
# CONTENT-ADDRESSED, because there is nothing else honest to key a name on.
# A fixed name can hold one save, so a second one arriving forces a choice
# between overwriting (destruction) and leaving the newcomer under the bare,
# deletable name — and the only evidence available to make that choice is the
# progress counters, which cannot make it: they are aggregates, so
# ``(3 pokemon, 2 badges, 101 history) >= (2, 2, 100)`` says nothing about
# whether those three Pokemon INCLUDE the two, and captures are not even
# monotone (``AnkimonDB.delete_pokemon`` releases one; the duplicate prune drops
# rows). Equal counters are just as uninformative — two saves can agree on all
# three and share not a single row.
#
# So no aggregate ever authorises a write here. Identity is exact file content,
# and a digest gives every distinct save its own name:
#
# * dedupe is a stat call — if ``_ankimon_save_<digest>.db`` exists, that exact
#   save is already preserved, so a repeat scan is a no-op;
# * the third, fourth and Nth divergent save each get a protected home, instead
#   of the third being knowingly left under the name Anki's "Delete Unused
#   Files" can take;
# * nothing is ever overwritten, so no comparison can be wrong in a way that
#   costs the user data;
# * the name is derived from the bytes alone, so two devices that receive the
#   same save through media sync compute the SAME filename and converge on one
#   copy rather than multiplying them.
#
# The cost is that a folder can accumulate one file per distinct save that ever
# passed through it. That is bounded by how many genuinely different saves the
# user has, it is their own data, and it is the right side of the trade against
# deleting one.
MEDIA_SAVE_PREFIX = "_ankimon_save_"
DEV_MEDIA_SAVE_PREFIX = "_ankimon_save_dev_"

# Half a SHA-256, in hex. Long enough that an accidental collision between two
# of a single user's saves is not a thing that happens; short enough to stay a
# readable filename.
MEDIA_SAVE_DIGEST_CHARS = 32

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

# The fingerprint of the folder whose comparison the user has already ANSWERED,
# kept apart from the settle above because the two expire on different things.
# A folder holding one save that will not open plus one readable save that is
# ahead of the local one reaches a real comparison AND has to stay armed to
# retry the unreadable file — and staying armed means never settling, so without
# this the same rescue prompt greets the user on every single profile open.
# Recording the answer separately suppresses only the repeated question; the
# retry survives, and a folder that changes gets asked afresh.
_MIGRATION_ANSWERED_FLAG = "ankimonMediaSyncRemovedAnsweredV1"

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

        def _fatal(exc: Exception) -> None:
            """Re-raise the failures that mean the read was CUT SHORT.

            The queries below are wrapped so that a save written by an older
            schema — a missing column, a renamed table — still reports the
            fields it does have instead of reading as unreadable. A statement
            the progress handler ABORTED is not that: it says the probe budget
            ran out mid-count, and swallowing it returns the ``default`` (0) for
            a table that may hold thousands of rows. "Unknown" would then be
            indistinguishable from "empty", which is the one thing this function
            exists to keep apart — and a false (0, 0, 0) is dominated by every
            real save, so it feeds straight into a comparison that can authorise
            a rescue over the save that actually holds the progress.

            The deadline is the primary test because it holds however SQLite
            spells the abort; the message check catches an interruption raised
            by some other means (``interrupt()``, a handler a test installed)
            before the wall clock has run out.
            """
            if time.monotonic() > _deadline:
                raise exc
            if isinstance(exc, sqlite3.OperationalError) and "interrupt" in str(exc).lower():
                raise exc

        def _scalar(sql: str, default=0):
            try:
                row = conn.execute(sql).fetchone()
                return row[0] if row and row[0] is not None else default
            except Exception as e:
                _fatal(e)
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
                except Exception as e:
                    _fatal(e)
                    return default

            def _as_int(value, default=0):
                # Tolerates only the CONVERSION failing — a level stored as ''
                # or 'None' by an older build. Wrapping the _cfg call itself
                # would put the swallow-everything back one level up and undo
                # what _fatal is for.
                try:
                    return int(value)
                except Exception:
                    return default

            stats["trainer_name"] = _cfg("trainer.name", "-")
            stats["trainer_level"] = _as_int(_cfg("trainer.level", 0))
            stats["trainer_cash"] = _as_int(_cfg("trainer.cash", 0))
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
    OFFERING. It may not authorise a write, and neither may ``_dominates``,
    which is built from it — see that function.

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
    share no rows at all, and the counters are not monotone in the first place
    (``delete_pokemon``). Equality is no better — same counts, possibly
    disjoint contents.

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


def _mark_migration_done(fingerprint: str) -> None:
    """Record WHAT was resolved, not merely THAT something was.

    ``fingerprint`` comes from the scan that reached this resolution — it
    describes the folder AS EXAMINED, not as it stands now. Those differ: the
    scan runs on a worker while Anki's media sync is running, so a download can
    land between the read and this call. Storing the folder's current state
    there would settle on a file nothing ever looked at, which is the exact
    failure the fingerprint exists to prevent.

    A fingerprint that could not be computed is stored as the empty string,
    which ``_migration_done`` reads as not-settled: the cost of that is one more
    scan, where the cost of wrongly settling is a save nobody offers back.
    """
    try:
        mw.pm.profile[_MIGRATION_FLAG] = fingerprint
        mw.pm.save()
    except Exception:
        pass


def _comparison_answered(fingerprint: str) -> bool:
    """Has the user already answered the comparison for THIS exact folder?"""
    if not fingerprint:
        return False
    try:
        return mw.pm.profile.get(_MIGRATION_ANSWERED_FLAG) == fingerprint
    except Exception:
        return False


def _remember_comparison_answered(fingerprint: str) -> None:
    if not fingerprint:
        return
    try:
        mw.pm.profile[_MIGRATION_ANSWERED_FLAG] = fingerprint
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
    return _join_fingerprint(_media_fingerprint_entries(media_dir, target_db))


def _media_fingerprint_entries(media_dir: Path, target_db: str) -> Dict[str, str]:
    """``{filename: signature}`` for the partition, so a caller can amend it.

    The scan takes this before it touches anything and then replaces only the
    entries for files it wrote ITSELF, which is what separates "the folder the
    scan resolved" from "the folder as it stands after an unrelated download".
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
        return None         # absent, or unstattable: not part of the signature
    return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def _join_fingerprint(entries: Dict[str, str]) -> str:
    return "|".join(sorted(entries.values()))


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
    # Content-addressed copies carry no "ankimonDEV" either, and the normal
    # prefix is a prefix OF the developer one, so the developer test has to come
    # first. A digest can never spell "dev_" (v is not a hex digit), so the two
    # prefixes cannot be confused in the other direction.
    if name.startswith(DEV_MEDIA_SAVE_PREFIX):
        return "ankimonDEV.db"
    if name.startswith(MEDIA_SAVE_PREFIX):
        return "ankimon.db"
    return "ankimonDEV.db" if "ankimonDEV" in name else "ankimon.db"


def _protected_copy_prefix(target_db: str) -> str:
    """The content-addressed prefix belonging to ``target_db``'s partition.

    Each partition gets its own, so a developer-mode run can never write test
    progress into a name the normal-mode scan reads back.
    """
    return (
        DEV_MEDIA_SAVE_PREFIX if target_db == "ankimonDEV.db" else MEDIA_SAVE_PREFIX
    )


def _protected_copy_name(target_db: str, digest: str) -> str:
    """Where a save with content ``digest`` is preserved in this partition."""
    return f"{_protected_copy_prefix(target_db)}{digest}.db"


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
        return digest.hexdigest()[:MEDIA_SAVE_DIGEST_CHARS]
    except Exception:
        return None


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
        patterns = ("_*_ankimonDEV.db", DEV_MEDIA_SAVE_PREFIX + "*.db")
    else:
        explicit = (MEDIA_SAVE_NAME, DIVERGED_MEDIA_SAVE_NAME, "ankimon.db")
        patterns = ("_*_ankimon.db", MEDIA_SAVE_PREFIX + "*.db")

    paths = [media_dir / name for name in explicit]
    try:
        for pattern in patterns:
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


def _settle(logger, fingerprint: str) -> None:
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
    _mark_migration_done(fingerprint)


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
    # files this scan wrote itself. That is what makes the settle describe the
    # folder this pass actually examined: Anki's media sync runs concurrently
    # with this worker, so a peer's save can land between here and the settle,
    # and recording it as "resolved" would bury it exactly as a permanent
    # one-shot did.
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

    if not stats:
        notes.append((
            "info",
            "Ankimon: no save in the media folder could be read this pass; "
            "rescanning later.",
        ))
        return _result("armed")

    # PROTECT. Exactly ONE file in this partition can be taken by Anki's "Delete
    # Unused Files": the bare ``ankimon.db`` / ``ankimonDEV.db``. Every other
    # candidate — the fixed names earlier builds wrote, the pre-2024 legacy
    # names, the content-addressed copies — already begins with an underscore,
    # and the media check only offers to delete a file when
    # ``!file.starts_with('_')``. So the protect step has one job, on one file,
    # and it is unconditional: it does not rank, does not compare, and does not
    # write over anything. Nothing else in the folder is touched at all.
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
    # whole comparison rests on, and the one _progress_key cannot make on its
    # own, since it floors an unreadable save to (-1, -1, -1). A local save
    # merely locked this second — the OneDrive/antivirus case this
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
        # save has a home. In the steady state — the folder settled, this pass
        # armed only because something ELSE changed — that is one read and one
        # stat, with no copy and no write at all.
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
    # A pass that must stay armed for an unreadable file never reaches _settle,
    # so the answer is remembered on its own or the question repeats every boot.
    answered = _comparison_answered(fingerprint)

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
        _remember_comparison_answered(fingerprint)
    elif (
        answered is False
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
        _remember_comparison_answered(fingerprint)

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

    _settle(logger, result.get("fingerprint", ""))


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
    startup, nor block on the collection it does not touch. Its callback is safe
    for the dialogs and the ``mw.pm`` write in ``_apply_migration_result``
    because aqt wraps it (``aqt/taskman.py:86-88``)::

        if on_done is not None:
            fut.add_done_callback(
                lambda future: self.run_on_main(lambda: on_done(future))
            )
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

    A request that arrives while a scan is in flight is COALESCED into one more
    run rather than dropped, because the media-sync hook can fire during the
    boot scan — which is the very ordering the post-sync pass exists to cover.

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
