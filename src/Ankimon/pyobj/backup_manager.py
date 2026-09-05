
import base64
import json
import os
import shutil
import datetime
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
            if backup_dir.is_dir():
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

    def create_backup(self, manual=False, required_file: str = None) -> bool:
        """Creates a new backup.

        Returns ``True`` only if the backup directory was written AND contains
        the file the caller depends on. ``required_file`` names that file (the
        one a pre-overwrite caller is protecting, e.g. ``ankimon.db``); when
        omitted, the active-mode database is used. Callers that back up *before*
        a destructive overwrite rely on this to refuse the overwrite when no
        recoverable backup of THAT file was actually made — so one unrelated
        file's copy failure must not blank another file's success (each copy is
        isolated below)."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_dir = self.backups_path / f"backup_{timestamp}"

        success = False
        try:
            # Checkpoint the active database first to flush all WAL changes to disk,
            # so the single-file copy below captures the latest committed state.
            if services.db is not None:
                try:
                    services.db.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    self.logger.log("info", "Checkpoint database before backup.")
                except Exception as e:
                    self.logger.log("error", f"Failed to checkpoint database before backup: {e}")

            backup_dir.mkdir()

            # For manual backups, only back up the currently active database.
            files_to_copy = self.FILES_TO_BACKUP
            if manual and services.db is not None:
                files_to_copy = [services.db.db_path.name]

            for filename in files_to_copy:
                source_path = self.user_files_path / filename
                if source_path.exists():
                    # Isolate each copy: a failure on ankimonDEV.db must not mark
                    # a successful ankimon.db backup as failed (which would
                    # needlessly abort a safe import), and vice versa.
                    try:
                        shutil.copy2(source_path, backup_dir / filename)
                    except Exception as e:
                        self.logger.log("error", f"Failed to back up {filename}: {e}")

            summary = self._generate_summary(backup_dir)
            summary['manual'] = manual
            with open(backup_dir / "summary.json", 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=4)

            self.logger.log("info", f"Created backup: {backup_dir.name}")

            # Success = the SPECIFIC file the caller relies on landed in the
            # backup dir. Defaults to the active-mode DB when unspecified.
            needed = required_file or (
                services.db.db_path.name if services.db is not None else "ankimon.db"
            )
            success = (backup_dir / needed).is_file()

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

        self.cleanup_backups()
        return success

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
        normal_stats = self._get_db_file_stats(backup_dir / "ankimon.db")
        dev_stats = self._get_db_file_stats(backup_dir / "ankimonDEV.db")

        normal_exists = (backup_dir / "ankimon.db").exists()
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

    def restore_backup(self, backup_path_str: str):
        """Restores a selected backup (only the currently active database)."""
        backup_path = Path(backup_path_str)
        if not backup_path.is_dir():
            showWarning("Selected backup path does not exist.")
            return

        if not askUser(
            "Are you sure you want to restore this backup? This will overwrite your current Ankimon data. Anki will be closed to apply the changes."
        ):
            return

        try:
            # Without an initialized database service we cannot tell which mode
            # (normal vs dev) is active, so refuse the destructive restore rather
            # than guessing or crashing on ``None.db_path``.
            if services.db is None:
                showWarning("The Ankimon database is not initialized yet; cannot restore a backup.")
                return
            active_db = services.db.db_path.name
            backup_file = backup_path / active_db
            if backup_file.exists():
                # We MUST close all database connections before overwriting the file.
                # Otherwise, replacing an active DB file causes SQLite to enter a
                # malformed state, preventing successful loading on the next boot.
                try:
                    services.db.close(wait_seconds=2.0)
                except Exception as e:
                    self.logger.log("error", f"Failed to gracefully close connections before restore: {e}")
                    showWarning(f"Failed to gracefully close the database. Aborting restore to prevent corruption: {e}")
                    return

                shutil.copy2(backup_file, self.user_files_path / active_db)
            else:
                showWarning(
                    f"The selected backup does not contain a backup for the active database ({active_db})."
                )
                return

            showInfo("Backup restored successfully. Anki will now close. Please restart Anki to see the changes.")
            close_anki()

        except Exception as e:
            self.logger.log("error", f"Failed to restore backup: {e}")
            showWarning(f"Failed to restore backup: {e}")

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

    def cleanup_backups(self):
        """Deletes old backups based on retention policy."""
        # Get only directories and sort them by modification time
        backups = sorted([p for p in self.backups_path.iterdir() if p.is_dir()], key=os.path.getmtime)

        backups_to_keep = []
        for backup_dir in backups:
            backup_time = datetime.datetime.fromtimestamp(os.path.getmtime(backup_dir))
            if (datetime.datetime.now() - backup_time).days > self.MAX_BACKUP_AGE_DAYS:
                shutil.rmtree(backup_dir)
                self.logger.log("info", f"Deleted old backup: {backup_dir.name}")
            else:
                backups_to_keep.append(backup_dir)

        # Keep only the latest MAX_BACKUPS, unless in developer mode
        if not self.settings_obj.get("misc.developer_mode"):
            while len(backups_to_keep) > self.MAX_BACKUPS:
                oldest_backup = backups_to_keep.pop(0)
                shutil.rmtree(oldest_backup)
                self.logger.log("info", f"Deleted oldest backup to maintain max count: {oldest_backup.name}")

    def on_anki_close(self):
        """Creates a backup when Anki is about to close."""
        # This logic can be expanded with the developer mode setting
        self.create_backup(manual=False)
