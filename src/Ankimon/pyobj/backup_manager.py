
import base64
import json
import os
import shutil
import datetime
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import List, Dict, Any, Optional

from aqt.utils import showInfo, showWarning, askUser

from ..services import services
from ..utils import close_anki
from ..resources import user_path, addon_dir

class BackupManager:
    """Handles creating, managing, and restoring Ankimon backups."""

    _OBFUSCATION_KEY = "H0tP-!s-N0t-4-C@tG!rL_v2"
    FILES_TO_BACKUP = [
        "ankimon.db",
        "ankimonDEV.db",
        # config.obf removed - now stored in ankimon.db
    ]
    MAX_BACKUPS = 5
    MAX_BACKUP_AGE_DAYS = 14
    # profile_will_close runs the automatic backup synchronously, so the whole
    # shutdown gets this budget once -- not one full SQLite timeout per file.
    SHUTDOWN_BACKUP_BUDGET = 30.0
    # How old an abandoned staging directory must be before retention sweeps it.
    STALE_STAGING_AGE = 3600.0
    # Retention renames a directory to this prefix before deleting inside it,
    # taking it out of the listing and the count; a later pass finishes it.
    DISCARD_PREFIX = ".discard_"

    def __init__(self, logger, settings_obj):
        self.logger = logger
        self.settings_obj = settings_obj
        self.user_files_path = user_path
        self.addon_path = addon_dir
        self.backups_path = self.addon_path.parent / "ankimon_backups"
        self.backups_path.mkdir(exist_ok=True)

    def _deobfuscate_data(self, obfuscated_str: str) -> Optional[Dict[str, Any]]:
        """De-obfuscates string back into a dictionary."""
        try:
            new_separator = "---DATA_START---"
            old_separator = "\n---"

            if new_separator in obfuscated_str:
                parts = obfuscated_str.split(new_separator)
                obfuscated_data = parts[1]
            elif old_separator in obfuscated_str:
                parts = obfuscated_str.split(old_separator)
                obfuscated_data = parts[1]
            else:
                obfuscated_data = obfuscated_str

            obfuscated_bytes = base64.b64decode(obfuscated_data)
            deobfuscated_bytes = bytearray()
            key_bytes = self._OBFUSCATION_KEY.encode('utf-8')
            for i, byte in enumerate(obfuscated_bytes):
                deobfuscated_bytes.append(byte ^ key_bytes[i % len(key_bytes)])
            return json.loads(deobfuscated_bytes.decode('utf-8'))
        except Exception as e:
            self.logger.log("error", f"Failed to deobfuscate data: {e}")
            return None

    def get_backups(self) -> List[Dict[str, Any]]:
        """Returns a list of available backups with their summary stats.

        Only backups that contain the database for the *currently active* mode
        (normal ``ankimon.db`` vs developer ``ankimonDEV.db``) are shown, and the
        per-DB stats section for the active mode is merged onto the root of the
        summary so the dialog can read them without knowing about dual-DB.
        """
        backups = []
        # If the database service isn't initialized yet (e.g. early boot or a
        # headless environment), there is no active mode to filter on — return an
        # empty list rather than crashing on ``None.db_path``.
        if services.db is None:
            return backups
        active_db = services.db.db_path.name
        for backup_dir in sorted(self.backups_path.iterdir(), reverse=True):
            if backup_dir.name.startswith("backup_") and backup_dir.is_dir():
                # Only show a backup if it contains the database for the active mode.
                if not (backup_dir / active_db).exists():
                    continue
                summary_path = backup_dir / "summary.json"
                if summary_path.exists():
                    with open(summary_path, 'r', encoding='utf-8') as f:
                        try:
                            summary = json.load(f)
                            # Shape the summary to match what the UI expects for the active DB.
                            stats_key = "dev_stats" if active_db == "ankimonDEV.db" else "normal_stats"
                            db_stats = summary.get(stats_key, {})

                            # Merge DB-specific stats into the root summary object for the UI.
                            summary.update(db_stats)
                            summary['path'] = str(backup_dir)
                            backups.append(summary)
                        except json.JSONDecodeError:
                            self.logger.log("error", f"Could not read summary for backup: {backup_dir.name}")
                elif active_db == "ankimon.db":
                    # Fallback for older backups without summary.json.
                    summary = {
                        "date": backup_dir.name.replace("backup_", "").replace("_", " "),
                        "path": str(backup_dir),
                    }
                    backups.append(summary)
        return backups

    def create_backup(self, manual=False, required_file: str = None,
                      deadline: float = None) -> bool:
        """Creates a new backup.

        ``deadline`` is an absolute ``time.monotonic()`` instant that bounds the
        WHOLE call rather than each file. Shutdown passes one so two locked
        databases cannot hold Anki open for a full timeout each; every other
        caller leaves it unset and keeps the per-file default.

        Returns ``True`` only if the backup directory contains a verified
        snapshot of the file the caller depends on. ``required_file`` names it (the
        one a pre-overwrite caller is protecting, e.g. ``ankimon.db``); when
        omitted, the active-mode database is used. Callers that back up *before*
        a destructive overwrite rely on this to refuse the overwrite when no
        recoverable backup of THAT file was actually made — so one unrelated
        file's snapshot failure must not blank another file's success (each file is
        isolated below)."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_dir = self.backups_path / f"backup_{timestamp}"
        staging_dir = self.backups_path / f".{backup_dir.name}"

        success = False
        created_directory = False
        try:
            if backup_dir.exists():
                raise FileExistsError(f"Backup already exists: {backup_dir.name}")
            staging_dir.mkdir()
            created_directory = True

            active_path = Path(services.db.db_path) if services.db is not None else None
            # For manual backups, only back up the currently active database.
            sources = {name: self.user_files_path / name for name in self.FILES_TO_BACKUP}
            if active_path is not None:
                # Profiles can choose a different directory or filename. Never
                # substitute the default save for the active file being protected.
                if manual:
                    sources = {active_path.name: active_path}
                else:
                    sources[active_path.name] = active_path

            completed = set()
            needed = required_file or (active_path.name if active_path else "ankimon.db")
            # A shared budget is spent on the file `success` depends on first,
            # so a locked companion database cannot starve the one that matters.
            order = sorted(sources, key=lambda name: name != needed) if deadline else sources
            for filename in order:
                source_path = sources[filename]
                if source_path.exists():
                    # Isolate each snapshot: a failure on ankimonDEV.db must not mark
                    # a successful ankimon.db backup as failed (which would
                    # needlessly abort a safe import), and vice versa.
                    try:
                        if deadline is None:
                            self._snapshot_database(source_path, staging_dir / filename)
                        else:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                # Refuse rather than start a snapshot whose own
                                # deadline check would fail it after the copy.
                                raise TimeoutError(
                                    "the shutdown backup budget expired before this file")
                            self._snapshot_database(source_path, staging_dir / filename,
                                                    timeout=remaining)
                        completed.add(filename)
                    except Exception as e:
                        self.logger.log("error", f"Failed to back up {filename}: {e}")

            # A failed snapshot can leave a partial temporary file; only a
            # completed, verified snapshot authorizes a destructive overwrite.
            # Summarise INSIDE that check: with no snapshot in staging,
            # _generate_summary falls back to the live database, and its own
            # busy timeout would re-enter the very lock wait `deadline` exists
            # to bound — describing a backup that is about to be discarded.
            if needed in completed and (staging_dir / needed).is_file():
                summary = self._generate_summary(staging_dir)
                summary['date'] = timestamp.replace("_", " ")
                summary['manual'] = manual
                with open(staging_dir / "summary.json", 'w', encoding='utf-8') as f:
                    json.dump(summary, f, indent=4)

                if backup_dir.exists():
                    raise FileExistsError(f"Backup already exists: {backup_dir.name}")
                staging_dir.rename(backup_dir)
                success = True
                self.logger.log("info", f"Created backup: {backup_dir.name}")

            # Report manual feedback based on the ACTUAL outcome — never claim
            # success when the DB copy failed (per-file copy errors are logged,
            # not raised), or the user would trust a backup that isn't there.
            if manual:
                if success:
                    showInfo("Manual backup created successfully.")
                else:
                    showWarning(
                        "Manual backup failed: the database could not be copied "
                        "(see the Ankimon log). No backup was created."
                    )

        except Exception as e:
            self.logger.log("error", f"Failed to create backup: {e}")
            if manual:
                showWarning(f"Failed to create backup: {e}")

        if success:
            # Retention is housekeeping, and this runs on Anki's synchronous
            # profile_will_close hook, which does not catch what its handlers
            # raise. An unremovable old backup -- a Windows lock, an antivirus
            # scan, a permission change -- must not abort the close, and must
            # not throw away the backup that was just published either. It also
            # gets what is left of the shutdown budget: deleting directories is
            # not work to do while the user waits for Anki to exit.
            try:
                self.cleanup_backups(deadline=deadline)
            except Exception as error:
                self.logger.log("error", f"Backup retention did not finish: {error}")
        elif created_directory:
            # Failed attempts stay outside listings and retention even if a
            # locked file prevents deletion. Remove only staging directories
            # created by this attempt, never a pre-existing timestamp collision.
            # Entry by entry under the shutdown deadline, as retention is: the
            # attempt may hold a whole companion save.
            try:
                if not self._remove_tree(staging_dir, deadline):
                    self.logger.log("error", "The shutdown budget ran out removing an "
                                    "incomplete backup; retention sweeps it once stale")
            except OSError as error:
                self.logger.log("error", f"Failed to remove incomplete backup: {error}")
        return success

    @staticmethod
    def _snapshot_database(source_path: Path, destination_path: Path, timeout: float = 30.0):
        """Publish a verified standalone snapshot, including committed WAL pages.

        A checkpoint can return busy while leaving committed pages in WAL, so
        copying the main file is never sufficient. Read-only online backup also
        works for an inactive database, and never writes to the save itself.

        It is not, however, inert: reading a WAL-mode source through SQLite
        materialises that source's ``-shm`` (and an empty ``-wal`` when none is
        there) beside it, because a WAL reader needs the shared index. Nothing
        in the save changes, but anything comparing stat signatures across such
        a read has to expect it -- ``_pending_media_protection`` in
        ``save_transfer`` leaves ``-shm`` out for exactly this reason.
        """
        deadline = time.monotonic() + timeout
        fd, name = tempfile.mkstemp(prefix=".snapshot-", suffix=".db", dir=destination_path.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            uri = source_path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as source, \
                 closing(sqlite3.connect(temporary, timeout=timeout)) as snapshot:
                def check_deadline(status, remaining, total):
                    # backup() retries SQLITE_BUSY beyond connect's timeout.
                    if time.monotonic() > deadline:
                        raise TimeoutError("Timed out taking a database backup")

                source.backup(snapshot, pages=256, progress=check_deadline, sleep=0.05)
                snapshot.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
                if snapshot.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("Database backup failed its integrity check")
                if not snapshot.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='captured_pokemon'"
                ).fetchone():
                    raise ValueError("Database backup is not an Ankimon save")
                # A backup is a single file: do not require WAL sidecars on restore.
                snapshot.execute("PRAGMA journal_mode=DELETE")
                check_deadline(0, 0, 0)
            os.replace(temporary, destination_path)
        finally:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)

    def _get_db_file_stats(self, db_file_path: Path) -> Dict[str, Any]:
        """Reads summary stats directly from one Ankimon SQLite backup file.

        Used to build the per-database (normal / dev) sections of a backup
        summary. Reads the backup file's OWN data (not the live database) so the
        summary reflects that backup's historical state.
        """
        stats = {
            "main_pokemon_name": "N/A",
            "main_pokemon_level": "N/A",
            "pokemon_count": 0,
            "trainer_name": "N/A",
            "trainer_cash": 0,
            "trainer_level": 1,
            "item_count": 0,
        }
        if not db_file_path.exists():
            return stats

        import sqlite3
        import json
        from contextlib import closing
        try:
            with closing(sqlite3.connect(str(db_file_path))) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("SELECT COUNT(*) AS count FROM captured_pokemon")
                stats["pokemon_count"] = cursor.fetchone()["count"]

                cursor.execute("SELECT COUNT(*) AS count FROM items")
                stats["item_count"] = cursor.fetchone()["count"]

                # Older backup files predate the ``is_main`` migration
                # (database_manager adds this column on upgrade), so reading such
                # a raw file directly can raise "no such column: is_main". Guard
                # it so the trainer/config read below still runs.
                try:
                    cursor.execute("SELECT data FROM captured_pokemon WHERE is_main = 1 LIMIT 1")
                    main_row = cursor.fetchone()
                    if main_row:
                        main_data = json.loads(main_row["data"])
                        stats["main_pokemon_name"] = main_data.get("name", "N/A")
                        stats["main_pokemon_level"] = main_data.get("level", "N/A")
                except sqlite3.OperationalError:
                    pass

                # Trainer info lives in the `config` table as flat dotted
                # key/value rows (e.g. key='trainer.name', value='Ash'). Guard
                # on the table existing so an older backup can't abort the counts.
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='config'"
                )
                if cursor.fetchone():
                    def _cfg(key):
                        cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
                        r = cursor.fetchone()
                        return r["value"] if r else None

                    name = _cfg("trainer.name")
                    if name is not None:
                        stats["trainer_name"] = name
                    for cfg_key, sum_key in (
                        ("trainer.cash", "trainer_cash"),
                        ("trainer.level", "trainer_level"),
                    ):
                        raw = _cfg(cfg_key)
                        if raw is not None:
                            try:
                                stats[sum_key] = int(raw)
                            except (ValueError, TypeError):
                                pass
        except Exception as e:
            self.logger.log("error", f"Failed to read stats from {db_file_path.name}: {e}")
        return stats

    def _generate_summary(self, backup_dir: Path) -> Dict[str, Any]:
        """Generates a summary for a backup, with per-database (normal/dev) stats."""
        active_db_name = (
            services.db.db_path.name if services.db is not None else "ankimon.db"
        )

        # Read stats from the database files stored inside this backup directory.
        normal_name = "ankimon.db" if active_db_name == "ankimonDEV.db" else active_db_name
        normal_stats = self._get_db_file_stats(backup_dir / normal_name)
        dev_stats = self._get_db_file_stats(backup_dir / "ankimonDEV.db")

        normal_exists = (backup_dir / normal_name).exists()
        dev_exists = (backup_dir / "ankimonDEV.db").exists()

        # If the backup folder has no DB files yet (e.g. a freshly-created dummy
        # in tests, or a summary generated for the live session), fall back to
        # the active database connection's current live state.
        db = services.db
        if not normal_exists and not dev_exists and db is not None:
            try:
                stats = db.get_stats()
                live_stats = {
                    "pokemon_count": stats.get("pokemon", 0),
                    "item_count": stats.get("items", 0),
                    "main_pokemon_name": "N/A",
                    "main_pokemon_level": "N/A",
                    "trainer_name": db.get_config_value("trainer.name", "N/A"),
                    "trainer_cash": db.get_config_value("trainer.cash", 0),
                    "trainer_level": db.get_config_value("trainer.level", 1),
                }
                main_pokemon = db.get_main_pokemon()
                if main_pokemon:
                    live_stats["main_pokemon_name"] = main_pokemon.get("name", "N/A")
                    live_stats["main_pokemon_level"] = main_pokemon.get("level", "N/A")

                if active_db_name == "ankimonDEV.db":
                    dev_stats = live_stats
                else:
                    normal_stats = live_stats
            except Exception as e:
                self.logger.log("error", f"Failed to get DB stats for backup summary: {e}")

        summary = {
            "date": backup_dir.name.replace("backup_", "").replace("_", " "),
            "normal_stats": normal_stats,
            "dev_stats": dev_stats,
        }

        # Merge the active DB's stats onto the root of the summary for backwards
        # compatibility with the UI (and the tests).
        stats_key = "dev_stats" if active_db_name == "ankimonDEV.db" else "normal_stats"
        summary.update(summary.get(stats_key, {}))

        # Fallback to legacy JSON for older backups or migration period, only when
        # there are no DB files in the backup and no live database to read from.
        if not normal_exists and not dev_exists and db is None:
            legacy_stats = {
                "main_pokemon_name": "N/A",
                "main_pokemon_level": "N/A",
                "pokemon_count": 0,
                "trainer_name": "N/A",
                "trainer_cash": 0,
                "trainer_level": 1,
                "item_count": 0,
            }

            # Read mainpokemon.json for main Pokémon info
            mainpokemon_path = backup_dir / "mainpokemon.json"
            if mainpokemon_path.exists():
                with open(mainpokemon_path, 'r', encoding='utf-8') as f:
                    try:
                        mainpokemon_data = json.load(f)
                        if mainpokemon_data:
                            legacy_stats["main_pokemon_name"] = mainpokemon_data[0].get("name", "N/A")
                            legacy_stats["main_pokemon_level"] = mainpokemon_data[0].get("level", "N/A")
                    except (json.JSONDecodeError, IndexError):
                        pass

            # Read mypokemon.json for total Pokémon count
            mypokemon_path = backup_dir / "mypokemon.json"
            if mypokemon_path.exists():
                with open(mypokemon_path, 'r', encoding='utf-8') as f:
                    try:
                        mypokemon_data = json.load(f)
                        legacy_stats["pokemon_count"] = len(mypokemon_data)
                    except json.JSONDecodeError:
                        pass

            # Read items.json for total item count
            items_path = backup_dir / "items.json"
            if items_path.exists():
                with open(items_path, 'r', encoding='utf-8') as f:
                    try:
                        items_data = json.load(f)
                        legacy_stats["item_count"] = sum(item.get('quantity', 0) for item in items_data)
                    except json.JSONDecodeError:
                        pass

            # Read config.obf for trainer info (legacy backups predate the DB config table)
            config_path = backup_dir / "config.obf"
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    obfuscated_data = f.read()
                config_data = self._deobfuscate_data(obfuscated_data)
                if config_data:
                    legacy_stats["trainer_name"] = config_data.get("trainer.name", "N/A")
                    legacy_stats["trainer_cash"] = config_data.get("trainer.cash", 0)
                    legacy_stats["trainer_level"] = config_data.get("trainer.level", 1)

            summary["normal_stats"] = legacy_stats
            summary.update(legacy_stats)

        return summary

    def _warn_about_pending_import(self, message: str) -> None:
        """Report an armed import without letting Qt's failure escape."""
        try:
            showWarning(message)
        except Exception as error:
            self.logger.log(
                "error",
                f"A pending save import could not be reported to the user: {error}",
            )

    def restore_backup(self, backup_path_str: str):
        """Stage the selected backup for the next full process start.

        Never copy over a database that the current runtime still has open.
        Restore uses the same crash-safe installation gate as manual Import,
        while retaining credentials because Backup Manager snapshots are private
        local recovery material rather than portable exports.
        """
        backup_path = Path(backup_path_str)
        if not backup_path.is_dir():
            showWarning("Selected backup path does not exist.")
            return

        if not askUser(
            "Prepare this backup for restore? It will replace the current Ankimon "
            "save on the next full Anki restart. The final current save will be "
            "retained as a separate recovery copy first."
        ):
            return

        try:
            if services.db is None:
                showWarning("The Ankimon database is not initialized yet; cannot restore a backup.")
                return

            target = Path(services.db.db_path)
            backup_file = backup_path / target.name
            if not backup_file.is_file():
                showWarning(
                    "The selected backup does not contain a backup for the active "
                    f"database ({target.name})."
                )
                return

            from ..save_import import (
                ImportAlreadyPendingError, ImportStagedError,
                pending_import_is_installed, stage_import,
            )

            pending = stage_import(
                backup_file, target, sanitize_credentials=False
            )
        except ImportAlreadyPendingError:
            # Guarded like the success notice below: showWarning reaches into
            # Qt, and an exception raised inside an except clause is not caught
            # by its siblings -- it would leave restore_backup entirely, with
            # an import armed and nothing said about it.
            installed = pending_import_is_installed(target)
            if installed:
                # The record outlived the import; that replacement has already
                # happened and no restart will repeat it.
                self._warn_about_pending_import(
                    "The previous save import has ALREADY installed and is the "
                    "save you are playing now. Only its leftover record could "
                    "not be cleared.\n\nUse Ankimon → Cancel Pending Save Import "
                    "to clear that record, then restore this backup again."
                )
                return
            if installed is None:
                # The save could not be read in time to tell, so claim neither.
                self._warn_about_pending_import(
                    "A save import is already recorded for this save, and Ankimon "
                    "could not read the save to tell whether it has already "
                    "installed.\n\nUse Ankimon → Cancel Pending Save Import to "
                    "clear that record, then restore this backup again."
                )
                return
            self._warn_about_pending_import(
                "A save import is already pending and will install at the next "
                "full Anki restart.\n\nUse Ankimon → Cancel Pending Save Import "
                "first if you want to restore this backup instead."
            )
            return
        except ImportStagedError as e:
            # The restore is published and will install; calling it a failure
            # to prepare would hide an armed replacement from the user.
            self.logger.log("error", f"Backup restore staged but unfinished: {e}")
            self._warn_about_pending_import(
                f"The backup restore could not be finished cleanly: {e}.\n\n"
                "Your current save is still active, but the restore is now "
                "PENDING and will install at the next full Anki restart. Use "
                "Ankimon → Cancel Pending Save Import if you do not want it."
            )
            return
        except Exception as e:
            self.logger.log("error", f"Failed to prepare backup restore: {e}")
            showWarning(f"Failed to prepare backup restore: {e}")
            return

        # Past staging, and outside the guard above: the restore is committed,
        # so a failure to announce it must never be reported as one to prepare
        # it. Import keeps its own notice outside its guard for the same reason.
        try:
            showInfo(
                "Backup restore prepared for the next full Anki restart.\n\n"
                "Your current save stays active until Anki exits. At the next "
                "start, its final state will be retained here before the selected "
                "backup is installed:\n"
                f"{pending['recovery_path']}\n\n"
                "If you choose Keep Editing, the restore remains pending until "
                "the next full restart."
            )
        except Exception as error:
            self.logger.log(
                "error",
                f"Backup restore is staged, but its notice could not be shown: {error}",
            )
        try:
            close_anki(raise_on_error=True)
        except Exception as error:
            showWarning(
                f"Anki could not close: {error}. Your current save is still "
                "active and the prepared restore remains pending."
            )

    def delete_backup(self, backup_path_str: str):
        """Deletes a selected backup."""
        backup_path = Path(backup_path_str)
        if not backup_path.is_dir():
            showWarning("Selected backup path does not exist.")
            return
        try:
            shutil.rmtree(backup_path)
            self.logger.log("info", f"Deleted backup: {backup_path.name}")
            showInfo("Backup deleted successfully.")
        except Exception as e:
            self.logger.log("error", f"Failed to delete backup: {e}")
            showWarning(f"Failed to delete backup: {e}")

    def _remove_tree(self, directory: Path, deadline) -> bool:
        """Delete a directory one entry at a time, stopping at the deadline.

        ``shutil.rmtree`` cannot be interrupted, and a backup holds whole save
        copies, so one large or stalled deletion could keep Anki closing after
        the shutdown budget had run out. Checking between entries bounds the
        overrun to the single unlink already in flight. Returns False when the
        deadline stopped it; filesystem errors propagate.
        """
        for entry in list(directory.iterdir()):
            if deadline is not None and time.monotonic() >= deadline:
                return False
            if entry.is_dir() and not entry.is_symlink():
                if not self._remove_tree(entry, deadline):
                    return False
            else:
                entry.unlink()
        directory.rmdir()
        return True

    def _discard(self, directory: Path, what: str, deadline) -> bool:
        """Remove one directory. Never raise, and never overrun the deadline.

        Returns False only when the deadline has passed, telling the caller to
        stop: every removal it skips is simply retried by the next backup. A
        removal that fails is logged and retention carries on. Every removal a
        pass attempts was due regardless of the others, so a directory that
        cannot be deleted costs one extra retained copy -- never the eviction
        of a backup the policy keeps.

        The directory leaves the ``backup_`` namespace by rename before anything
        inside it is deleted. Deleting entries updates a directory's mtime, and
        retention orders by mtime, so a removal cut short in place -- by the
        deadline, or by one locked file -- would leave remains that sort as the
        NEWEST backup and displace a good one at the next pass. A failed rename
        changes nothing; after a successful one, the worst left behind is a
        hidden ``.discard_`` directory that a later pass finishes.
        """
        if deadline is not None and time.monotonic() >= deadline:
            return False
        doomed = directory
        if not directory.name.startswith(self.DISCARD_PREFIX):
            doomed = directory.with_name(
                f"{self.DISCARD_PREFIX}{uuid.uuid4().hex[:8]}_{directory.name.lstrip('.')}")
            try:
                directory.rename(doomed)
            except Exception as error:
                self.logger.log("error", f"Failed to delete {what} {directory.name}: {error}")
                return True
        try:
            finished = self._remove_tree(doomed, deadline)
        except Exception as error:
            self.logger.log("error", f"Failed to finish deleting {what} {directory.name}: {error}")
            return True
        if finished:
            self.logger.log("info", f"Deleted {what}: {directory.name}")
        return finished

    def cleanup_backups(self, deadline: float = None):
        """Deletes old backups based on retention policy."""
        # Taken before this pass renames anything, so a removal that fails now
        # is retried by the next pass rather than twice in this one.
        leftovers = [p for p in self.backups_path.glob(f"{self.DISCARD_PREFIX}*")
                     if p.is_dir()]
        # Only published backups enter retention. Failed or interrupted staging
        # directories must not displace recoverable saves even if they remain.
        backups = sorted(
            [p for p in self.backups_path.iterdir()
             if p.name.startswith("backup_") and p.is_dir()],
            key=os.path.getmtime,
        )

        backups_to_keep = []
        for backup_dir in backups:
            backup_time = datetime.datetime.fromtimestamp(os.path.getmtime(backup_dir))
            if (datetime.datetime.now() - backup_time).days > self.MAX_BACKUP_AGE_DAYS:
                if not self._discard(backup_dir, "old backup", deadline):
                    return
            else:
                backups_to_keep.append(backup_dir)

        # Keep only the latest MAX_BACKUPS, unless in developer mode
        if not self.settings_obj.get("misc.developer_mode"):
            while len(backups_to_keep) > self.MAX_BACKUPS:
                oldest_backup = backups_to_keep.pop(0)
                if not self._discard(oldest_backup, "oldest backup", deadline):
                    return

        # An attempt whose own rmtree failed leaves a dot-prefixed staging
        # directory holding a full copy of the save. Listing and retention both
        # filter on "backup_", by design -- an incomplete attempt must never
        # displace a recoverable one -- so nothing else would ever remove it.
        # Age-gated because the name is only reserved while an attempt is live,
        # and a snapshot takes seconds, never an hour.
        for staging in self.backups_path.glob(".backup_*"):
            try:
                if not staging.is_dir():
                    continue
                if time.time() - os.path.getmtime(staging) < self.STALE_STAGING_AGE:
                    continue
            except OSError:
                continue
            if not self._discard(staging, "incomplete backup", deadline):
                return

        # What an earlier pass renamed out of the listing but could not finish
        # deleting -- stopped by the deadline, or by a locked file -- ends here.
        for leftover in leftovers:
            if not self._discard(leftover, "discarded backup", deadline):
                return

    def on_anki_close(self):
        """Creates a backup when Anki is about to close.

        One deadline covers every database this call snapshots: shutdown is
        synchronous, so a per-file timeout would let two locked saves stall the
        close for twice as long.
        """
        # This logic can be expanded with the developer mode setting
        self.create_backup(manual=False,
                           deadline=time.monotonic() + self.SHUTDOWN_BACKUP_BUDGET)
