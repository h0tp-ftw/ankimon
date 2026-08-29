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

# Bare names the removed feature (and its pre-SQLite ancestors) left behind in
# collection.media. Only ``.db`` entries are candidates for rescue; the JSON
# files predate the SQLite migration and are already imported into ankimon.db.
LEGACY_MEDIA_DB_NAMES = ("ankimon.db", "ankimonDEV.db")

# Set once per Anki PROFILE, not once per add-on install: ``user_files`` is
# add-on-scoped and shared by every profile, while ``collection.media`` is
# per-profile, so a single global flag would clean the first profile opened and
# silently skip all the others. ``mw.pm.profile`` is itself per-profile, so
# storing the flag there gives the right scoping for free — and it lives in
# prefs21.db, outside the collection and outside user_files, so restoring a
# backup of the save cannot rewind it and re-trigger the migration.
_MIGRATION_FLAG = "ankimonMediaSyncRemovedV1"


# ---------------------------------------------------------------------------
# Reading a save without opening it read-write
# ---------------------------------------------------------------------------

def get_db_stats(db_path: Path) -> Optional[Dict[str, Any]]:
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
    """
    try:
        if not Path(db_path).is_file():
            return None
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
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
        conn.execute("PRAGMA busy_timeout = 30000;")
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


def export_save(parent=None) -> bool:
    """Write the ACTIVE Ankimon save to a file the user chooses."""
    from .ankimon_sync import _verify_sqlite_integrity

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

        if not _verify_sqlite_integrity(tmp):
            showWarning(
                "Export failed: the exported file did not pass an integrity "
                "check, so it was discarded. Your save is unchanged."
            )
            return False

        os.replace(tmp, dest)
        tmp = None
    except Exception as e:
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


def _find_media_saves(media_dir: Path) -> list:
    """Every Ankimon save left in collection.media, protected name first.

    The legacy underscore names are GLOBBED rather than reconstructed. The old
    code built them from ``Path(__file__).parents[2].name``, which is
    ``addons21`` in a normal install and ``src`` in a git checkout, and real
    profiles have been seen carrying the numeric package id instead — so the
    exact prefix cannot be computed after the fact, only matched.
    """
    from .ankimon_sync import _verify_sqlite_integrity

    candidates = []
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
        if not _verify_sqlite_integrity(path):
            return
        seen.add(resolved)
        candidates.append(path)

    _add(media_dir / MEDIA_SAVE_NAME)
    for name in LEGACY_MEDIA_DB_NAMES:
        _add(media_dir / name)
    for pattern in ("_*_ankimon.db", "_*_ankimonDEV.db"):
        try:
            for path in sorted(media_dir.glob(pattern)):
                _add(path)
        except Exception:
            continue
    return candidates


def run_media_migration(settings_obj, logger) -> None:
    """Protect, and offer to rescue, whatever the removed sync left in media.

    Runs once per Anki profile. Two jobs, in this order:

    1. **Protect.** Copy the best save found in ``collection.media`` to
       ``_ankimon_save.db`` if that protected name does not already hold it.
       Every name the old feature used except the pre-2024 legacy one lacks the
       leading underscore, so today Anki's "Delete Unused Files" lists the user's
       cloud save and deletes it — and that deletion propagates to their other
       devices. Nothing is deleted here; the unprotected copies are left where
       they are, now harmlessly, because a protected copy exists.

    2. **Rescue.** If the media save is strictly further along than the local one
       on monotone counters, offer to import it. That is the only way a user who
       relied on the removed feature can get back progress that only ever made
       it to AnkiWeb. Declining is remembered, so this asks at most once.

    Any failure is logged and swallowed: this runs during profile open and must
    never be able to stop Ankimon from loading.
    """
    try:
        if _migration_done():
            return
        media_dir = _media_dir()
        if media_dir is None or not media_dir.is_dir():
            # No profile / no media folder yet — don't burn the one-shot flag,
            # just try again next launch.
            return

        saves = _find_media_saves(media_dir)
        if not saves:
            _mark_migration_done()
            return

        best = max(saves, key=lambda p: _progress_key(get_db_stats(p)))

        protected = media_dir / MEDIA_SAVE_NAME
        if best != protected:
            try:
                _sqlite_backup(best, protected)
                logger.log(
                    "info",
                    f"Ankimon: preserved {best.name} as {MEDIA_SAVE_NAME} "
                    "(protected from Anki's Delete Unused Files).",
                )
            except Exception as e:
                logger.log("error", f"Could not preserve {best.name} in media: {e}")
                protected = best

        target = _active_db_path()
        media_stats = get_db_stats(protected)
        local_stats = get_db_stats(target) if target else None

        if target is not None and _progress_key(media_stats) > _progress_key(local_stats):
            if askUser(
                "Ankimon's automatic AnkiWeb save-sync has been removed — it "
                "could not tell reliably which device's save was newer, and "
                "sometimes overwrote the wrong one.\n\n"
                "The copy left on AnkiWeb looks further along than the save on "
                "this computer:\n\n"
                f"ON ANKIWEB\n{_format_stats(media_stats)}\n\n"
                f"ON THIS COMPUTER\n{_format_stats(local_stats)}\n\n"
                "Load the AnkiWeb copy? Your current save will be backed up "
                "first, and Anki will close so it can be loaded cleanly.\n\n"
                "If you say no, nothing changes and you will not be asked again "
                "— the AnkiWeb copy stays in your media folder either way.",
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
                _replace_active_save(protected, Path(target), "Rescue")
                return
            # Declined: remember that, so this asks at most once.

        _mark_migration_done()
    except Exception as e:
        try:
            logger.log("error", f"AnkiWeb sync-removal migration failed: {e}")
        except Exception:
            pass
