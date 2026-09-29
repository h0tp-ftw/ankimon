
import base64
import json
import hashlib
import os
import shutil
import datetime
import sqlite3
import tempfile
import time
import uuid
import threading
from copy import copy
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
    MIGRATING_PREFIX = ".migrating_"
    MIGRATION_RECORD = ".legacy-migration.json"

    def __init__(self, logger, settings_obj):
        self.logger = logger
        self.settings_obj = settings_obj
        self.user_files_path = user_path
        self.addon_path = addon_dir
        self.backups_path: Optional[Path] = None
        self._profile_work_lock = threading.RLock()
        self._migrated_paths = {}
        self._startup_backup_pending = not getattr(services, "_is_reloading", False)
        self.refresh_profile_path()

    @staticmethod
    def _active_profile_folder() -> Optional[Path]:
        try:
            from aqt import mw
            profile_manager = getattr(mw, "pm", None)
            profile_folder = profile_manager.profileFolder() if profile_manager else None
            return Path(profile_folder) if profile_folder else None
        except Exception:
            return None

    @staticmethod
    def _is_link(path: Path) -> bool:
        from ..save_import import _is_link

        return _is_link(path)

    @staticmethod
    def _publish_via_staging(source: Path, destination: Path, relocations=None,
                             before_publish=None) -> None:
        """Publish a complete copy; leave the original for the caller to remove."""
        from ..save_import import _fsync_directory

        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name(
            f"{BackupManager.MIGRATING_PREFIX}{uuid.uuid4().hex[:8]}_{destination.name}"
        )
        try:
            BackupManager._copy_migration_entry(source, staging, destination, relocations)
            if destination.exists() or BackupManager._is_link(destination):
                raise FileExistsError(f"Backup migration collision: {destination}")
            if before_publish is not None:
                before_publish(staging)
            os.replace(str(staging), str(destination))
            _fsync_directory(destination.parent)
        except Exception:
            # Staging is only a copy, including after an interrupted publication.
            BackupManager._discard_migration_staging(staging)
            raise

    @staticmethod
    def _copy_migration_entry(source, staging, destination, relocations):
        """Copy without following links; rebase links for their final location."""
        from ..save_import import _fsync_directory, _fsync_file

        if BackupManager._is_link(source):
            target = BackupManager._relocate_link(source, destination, relocations)
            if target is None:
                raise OSError(f"Cannot preserve linked backup entry: {source}")
            os.symlink(target, staging, target_is_directory=source.is_dir())
        elif source.is_dir():
            staging.mkdir()
            for child in source.iterdir():
                BackupManager._copy_migration_entry(
                    child, staging / child.name, destination / child.name, relocations,
                )
            shutil.copystat(source, staging, follow_symlinks=False)
            _fsync_directory(staging)
        else:
            # Sync with write access before applying a source's read-only mode.
            # Windows FlushFileBuffers rejects read-only handles.
            shutil.copyfile(source, staging, follow_symlinks=False)
            _fsync_file(staging)
            shutil.copystat(source, staging, follow_symlinks=False)

    @staticmethod
    def _discard_migration_staging(staging: Path) -> None:
        try:
            if staging.is_dir() and not BackupManager._is_link(staging):
                shutil.rmtree(staging, ignore_errors=True)
            else:
                staging.unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _relocate_link(item: Path, destination: Path, relocations=None) -> Optional[str]:
        try:
            referent = item.resolve(strict=False)
        except (OSError, RuntimeError):
            return None
        # Resolve while every original still exists. Longest roots win so a
        # collision-renamed backup overrides the mapping of the legacy root.
        for original, relocated in sorted(
            (relocations or {}).items(), key=lambda pair: len(pair[0].parts), reverse=True,
        ):
            try:
                suffix = referent.relative_to(original)
            except ValueError:
                continue
            referent = relocated / suffix
            break
        try:
            return os.path.relpath(referent, destination.parent)
        except ValueError:
            # Relative paths cannot cross Windows drives.
            return str(referent)

    @staticmethod
    def _migration_record(source: Path, destination: Path) -> Dict[str, Any]:
        record = destination / BackupManager.MIGRATION_RECORD
        if not record.exists():
            return {"source": str(source.resolve()), "entries": {}, "digests": {}}
        data = json.loads(record.read_text(encoding="utf-8"))
        if data["source"] != str(source.resolve()):
            raise ValueError("Backup migration record belongs to another source")
        plan = data["entries"]
        if not isinstance(plan, dict) or any(
            not isinstance(name, str) or name in ("", ".", "..")
            or Path(name).name != name or "/" in name or "\\" in name
            for pair in plan.items() for name in pair
        ):
            raise ValueError("Invalid backup migration record")
        digests = data.setdefault("digests", {})
        if not isinstance(digests, dict) or any(
            name not in plan or not isinstance(digest, str)
            or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            for name, digest in digests.items()
        ):
            raise ValueError("Invalid backup migration digests")
        return data

    @staticmethod
    def _migration_plan(source: Path, destination: Path) -> Dict[str, str]:
        return BackupManager._migration_record(source, destination)["entries"]

    @staticmethod
    def _write_migration_record(destination: Path, data) -> None:
        from ..save_import import _fsync_directory

        record = destination / BackupManager.MIGRATION_RECORD
        temporary = record.with_name(f"{record.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                json.dump(data, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, record)
            _fsync_directory(destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _migration_digest(path: Path) -> str:
        """Fingerprint a publication's names, bytes and links without following links.

        Saved BEFORE publication, this also identifies a complete copy after a
        crash between publication and source deletion. Runs only on the worker.
        """
        digest = hashlib.sha256()

        def field(value):
            encoded = value.encode("utf-8", errors="surrogatepass")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)

        def visit(entry):
            if BackupManager._is_link(entry):
                field("link")
                field(os.readlink(entry))
            elif entry.is_dir():
                field("directory")
                for child in sorted(entry.iterdir(), key=lambda item: item.name):
                    field(child.name)
                    visit(child)
                field("end")
            else:
                field("file")
                content = hashlib.sha256()
                with entry.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        content.update(block)
                digest.update(content.digest())

        visit(path)
        return digest.hexdigest()

    @staticmethod
    def _move_backup_contents(source: Path, destination: Path, on_published=None) -> None:
        """Publish verified copies before deleting any originals.

        A reserved name is not proof of publication: another backup can occupy
        it after a failed copy. Persist a digest of the completed staging tree
        before renaming it, and require that proof on retries.
        """
        data = BackupManager._migration_record(source, destination)
        plan, digests = data["entries"], data["digests"]
        reserved = {destination / name for name in plan.values()}
        reserved.add(destination / BackupManager.MIGRATION_RECORD)
        for item in source.iterdir():
            if item.name in plan:
                continue
            target = destination / item.name
            while target in reserved or target.exists() or BackupManager._is_link(target):
                target = BackupManager._legacy_collision_name(destination / item.name)
            plan[item.name] = target.name
            reserved.add(target)
        BackupManager._write_migration_record(destination, data)
        planned = [(source / name, destination / target) for name, target in plan.items()]
        relocations = {source.resolve(): destination.resolve()}
        relocations.update({
            item.resolve(): target.absolute()
            for item, target in planned if not BackupManager._is_link(item)
        })
        for item, target in planned:
            def record_copy(staging):
                digests[item.name] = BackupManager._migration_digest(staging)
                BackupManager._write_migration_record(destination, data)

            if target.exists() or BackupManager._is_link(target):
                expected = digests.get(item.name)
                if expected is None:
                    # Upgrade old journals only when the still-present original
                    # proves which complete snapshot belongs at this target.
                    staging = destination / f"{BackupManager.MIGRATING_PREFIX}{uuid.uuid4().hex}"
                    try:
                        BackupManager._copy_migration_entry(item, staging, target, relocations)
                        expected = BackupManager._migration_digest(staging)
                    finally:
                        BackupManager._discard_migration_staging(staging)
                if BackupManager._migration_digest(target) != expected:
                    raise FileExistsError(f"Unverified backup migration destination: {target}")
                digests[item.name] = expected
                BackupManager._write_migration_record(destination, data)
            else:
                BackupManager._publish_via_staging(item, target, relocations, record_copy)
            if on_published is not None:
                on_published(item, target)

        for item, target in planned:
            if BackupManager._migration_digest(target) != digests[item.name]:
                raise OSError(f"Backup migration destination changed: {target}")
            if item.is_symlink():
                item.unlink()
            elif BackupManager._is_link(item):
                item.rmdir()
            elif item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)

    @staticmethod
    def _legacy_collision_name(target: Path) -> Path:
        return target.with_name(f"{target.name}__legacy_{uuid.uuid4().hex[:8]}")

    def _migrate_legacy_backups(self, profile_folder: Path) -> None:
        legacy_path = self.addon_path.parent / "ankimon_backups"
        new_path = profile_folder / "Ankimon_Backups"
        if legacy_path == new_path:
            return
        if not legacy_path.exists():
            # A crash may occur between removing the empty source root and
            # removing its record. No source remains to retry in that case.
            if not self._is_link(new_path):
                (new_path / self.MIGRATION_RECORD).unlink(missing_ok=True)
            return
        if self._is_link(legacy_path):
            self.logger.log(
                "error",
                f"Refusing to migrate linked legacy backups directory: {legacy_path}",
            )
            return
        if self._is_link(new_path):
            self.logger.log(
                "error",
                f"Refusing to migrate into linked backups directory: {new_path}",
            )
            return

        new_path.mkdir(parents=True, exist_ok=True)
        for original in list(self._migrated_paths):
            if original.parent == legacy_path:
                del self._migrated_paths[original]
        self._move_backup_contents(
            legacy_path, new_path,
            lambda original, relocated: self._migrated_paths.__setitem__(original, relocated),
        )
        legacy_path.rmdir()
        (new_path / self.MIGRATION_RECORD).unlink(missing_ok=True)

    def refresh_profile_path(self) -> None:
        self.backups_path = None

        profile_folder = self._active_profile_folder()
        if profile_folder is None:
            return

        candidate = profile_folder / "Ankimon_Backups"
        if self._is_link(candidate):
            self.logger.log(
                "error",
                f"Refusing to activate linked backups directory: {candidate}",
            )
            return
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except Exception as error:
            self.logger.log("error", f"Failed to create backup directory: {error}")
            return
        self.backups_path = candidate

    def run_profile_backup_tasks(self) -> None:
        """Run on a worker; serialize startup and profile-open requests."""
        with self._profile_work_lock:
            if self.backups_path is None:
                return  # Keep the startup request pending until a profile opens.
            worker = copy(self)
            try:
                worker._migrate_legacy_backups(worker.backups_path.parent)
            except Exception as error:
                self.logger.log("error", f"Failed to migrate legacy backups: {error}")
            if self.backups_path != worker.backups_path:
                return
            if self._startup_backup_pending:
                if not self.settings_obj.get("misc.developer_mode"):
                    self._startup_backup_pending = False
                elif worker.create_backup(manual=False):
                    self._startup_backup_pending = False

    def schedule_profile_backup_tasks(self) -> None:
        """Called on the GUI thread after resolving the profile path."""
        from aqt import mw

        def completed(future):
            try:
                future.result()
            except Exception as error:
                self.logger.log("error", f"Background backup work failed: {error}")

        try:
            mw.taskman.run_in_background(
                self.run_profile_backup_tasks, completed, uses_collection=False,
            )
        except TypeError as error:
            if "unexpected keyword argument 'uses_collection'" not in str(error):
                raise
            # Anki 2.1.66 predates collection-specific workers and this keyword.
            mw.taskman.run_in_background(self.run_profile_backup_tasks, completed)

    def _backup_directories(self, required_file=None):
        """Keep originals discoverable until their complete copies are published."""
        roots = [self.backups_path]
        legacy = self.addon_path.parent / "ankimon_backups"
        plan = {}
        if legacy.is_dir() and not self._is_link(legacy):
            roots.append(legacy)
            try:
                plan = self._migration_plan(legacy, self.backups_path)
            except (OSError, ValueError, KeyError, TypeError):
                pass  # A damaged record must not hide the original backups.
        for root in roots:
            try:
                entries = sorted(root.iterdir(), reverse=True)
            except OSError:
                continue
            for entry in entries:
                if root == legacy and entry.name in plan:
                    target = self.backups_path / plan[entry.name]
                    if self._resolve_backup_path(entry, required_file) == target:
                        continue
                yield entry

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
        if self.backups_path is None:
            return backups
        # If the database service isn't initialized yet (e.g. early boot or a
        # headless environment), there is no active mode to filter on — return an
        # empty list rather than crashing on ``None.db_path``.
        if services.db is None:
            return backups
        active_db = services.db.db_path.name
        for backup_dir in self._backup_directories(active_db):
            if backup_dir.name.startswith("backup_") and backup_dir.is_dir():
                # Only show a backup if it contains the database for the active mode.
                if not (backup_dir / active_db).exists():
                    continue
                summary_path = backup_dir / "summary.json"
                if summary_path.exists():
                    try:
                        with open(summary_path, 'r', encoding='utf-8') as f:
                            summary = json.load(f)
                            # Shape the summary to match what the UI expects for the active DB.
                            stats_key = "dev_stats" if active_db == "ankimonDEV.db" else "normal_stats"
                            db_stats = summary.get(stats_key, {})

                            # Merge DB-specific stats into the root summary object for the UI.
                            summary.update(db_stats)
                            summary['path'] = str(backup_dir)
                            backups.append(summary)
                    except (OSError, json.JSONDecodeError):
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
        snapshot of ``required_file`` (e.g. ``ankimon.db``), or of the
        active-mode database when it is omitted. The result is about THAT file:
        each file is snapshotted in isolation below, so one file's failure
        neither blanks another file's success nor is hidden by it."""
        if self.backups_path is None:
            self.logger.log("error", "Cannot create backup without an active profile path")
            if manual:
                showWarning(
                    "Manual backup failed: no active Anki profile folder is "
                    "available. Open a profile and try again."
                )
            return False

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_dir = self.backups_path / f"backup_{timestamp}"
        if backup_dir in self._migration_protected_paths():
            backup_dir = backup_dir.with_name(f"{backup_dir.name}_{uuid.uuid4().hex[:8]}")
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
                    # a successful ankimon.db backup as failed, and vice versa.
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
            # completed, verified snapshot of the needed file counts as a backup.
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
                from ..save_import import _retry_on_file_lock

                # On Windows a scanner still reading a snapshot just written
                # blocks renaming its folder. Retry the rename, within the
                # shutdown budget when there is one.
                _retry_on_file_lock(lambda: staging_dir.rename(backup_dir), deadline)
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
        if not success:
            # While backups keep failing on a locked folder, each attempt can
            # leave another whole save copy behind, so the leftovers are swept
            # anyway. Retention still waits for a success: evicting old backups
            # with nothing replacing them would leave none. Same hook, same budget.
            try:
                self._sweep_leftovers(deadline)
            except Exception as error:
                self.logger.log("error", f"Backup cleanup did not finish: {error}")
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
        # Imported before the temporary exists: the cleanup below needs it.
        from ..save_import import (
            _remove_owned_copy, _retry_on_file_lock, _sqlite_uri, _verify_save,
        )

        deadline = time.monotonic() + timeout
        fd, name = tempfile.mkstemp(prefix=".snapshot-", suffix=".db", dir=destination_path.parent)
        os.close(fd)
        temporary = Path(name)

        def check_deadline(status, remaining, total):
            # backup() retries SQLITE_BUSY beyond connect's timeout.
            if time.monotonic() > deadline:
                raise TimeoutError("Timed out taking a database backup")

        try:
            uri = _sqlite_uri(source_path, "ro")
            with closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as source, \
                 closing(sqlite3.connect(temporary, timeout=timeout)) as snapshot:
                source.backup(snapshot, pages=256, progress=check_deadline, sleep=0.05)
                # A backup is a single file: do not require WAL sidecars on restore.
                # It also lets the read-only check below open the file alone.
                snapshot.execute("PRAGMA journal_mode=DELETE")
            # The import code's check, on the closed file as it will be
            # published, and bounded by the same deadline.
            _verify_save(temporary, deadline)
            check_deadline(0, 0, 0)
            _retry_on_file_lock(lambda: os.replace(temporary, destination_path), deadline)
        finally:
            _remove_owned_copy(temporary)

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
        """Report an armed import without letting the presenter's failure escape."""
        try:
            services.ui.warn(message)
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
        backup_path = self._resolve_backup_path(backup_path_str)
        if not backup_path.is_dir():
            # Publication can complete between resolving and checking the row.
            backup_path = self._resolve_backup_path(backup_path_str)
            if not backup_path.is_dir():
                showWarning("Selected backup path does not exist.")
                return

        if not askUser(
            "Prepare this backup for restore? It will replace the current Ankimon "
            "save on the next full Anki restart. The final current save will be "
            "retained as a separate recovery copy first."
        ):
            return

        # Imported before the guard, because its handlers name these: a name
        # first bound inside the try is unbound there when anything before the
        # import raises, and the handler itself then raises UnboundLocalError.
        from ..save_import import (
            ImportAlreadyPendingError, ImportStagedError,
            damaged_save_question, describe_recovery_destination,
            pending_import_is_installed, should_confirm_unverified_copy, stage_import,
        )

        try:
            if services.db is None:
                showWarning("The Ankimon database is not initialized yet; cannot restore a backup.")
                return

            target = Path(services.db.db_path)
            # A damaged save cannot give the verified copy the question above
            # promised, so its replacement needs its own answer.
            retain_unverified = should_confirm_unverified_copy(target)
            if retain_unverified and not askUser(
                damaged_save_question(target, "the selected backup"), defaultno=True
            ):
                return

            # Every confirmation can run the event loop while migration moves
            # the selection. Resolve after ALL dialogs and hold its source
            # stable until staging has finished, without waiting on the GUI.
            if not self._profile_work_lock.acquire(blocking=False):
                showWarning("Backups are being moved. Please try restoring again shortly.")
                return
            try:
                backup_file = self._resolve_backup_path(backup_path_str, target.name) / target.name
                if not backup_file.is_file():
                    showWarning(
                        "The selected backup does not contain a backup for the active "
                        f"database ({target.name})."
                    )
                    return
                # Even a read-only SQLite connection can create WAL/SHM files
                # beside a backup. Keep the journaled migration tree unchanged
                # by opening only a private copy, including committed WAL data.
                # Resolve links just as stage_import's SQLite URI does, so the
                # journals come from the actual database's directory.
                source = backup_file.resolve()
                private = Path(tempfile.mkdtemp(prefix="ankimon-backup-restore-"))
                try:
                    snapshot = self._copy_restore_source(source, private)
                    pending = stage_import(
                        snapshot, target, sanitize_credentials=False,
                        retain_unverified=retain_unverified,
                    )
                finally:
                    # Cleanup cannot turn a successfully armed restore into a
                    # reported staging failure. Windows may still hold a file.
                    self._discard_migration_staging(private)
            finally:
                self._profile_work_lock.release()
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
                    "not be cleared.\n\nUse Ankimon → Game → Cancel Pending Save Import "
                    "to clear that record, then restore this backup again. Nothing "
                    "will be installed a second time."
                )
                return
            if installed is None:
                # The record or the save could not be read in time to tell, so
                # claim neither.
                self._warn_about_pending_import(
                    "A save import is already recorded for this save, and Ankimon "
                    "could not tell whether it has already installed."
                    "\n\nUse Ankimon → Game → Cancel Pending Save Import to "
                    "clear that record, then restore this backup again."
                )
                return
            self._warn_about_pending_import(
                "A save import is already pending and will install at the next "
                "full Anki restart.\n\nUse Ankimon → Game → Cancel Pending Save Import "
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
                "PENDING and will install at the next full Anki restart. The "
                "current save's final progress will still be retained in a "
                "recovery copy first. Use "
                "Ankimon → Game → Cancel Pending Save Import if you do not want it."
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
                f"{describe_recovery_destination(pending)}\n\n"
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
            # The restore is staged by now; report it like any other armed import.
            self._warn_about_pending_import(
                f"Anki could not close: {error}. Your current save is still "
                "active and the prepared restore remains pending."
            )

    @staticmethod
    def _copy_restore_source(source: Path, private: Path) -> Path:
        """Copy a stable SQLite file family without opening the source in SQLite.

        A linked backup may still be open in another process. A checkpoint
        between copying its main file and WAL can otherwise produce a valid
        but stale save. Verify identities, timestamps, membership and bytes;
        retry changes before allowing stage_import to see the private copy.
        """
        from ..save_import import _digest

        family = [Path(str(source) + suffix) for suffix in ("", "-wal", "-journal")]
        deadline = time.monotonic() + 30.0

        def signatures():
            result = []
            for member in family:
                try:
                    stat = member.stat()
                except FileNotFoundError:
                    result.append(None)
                else:
                    result.append((stat.st_dev, stat.st_ino, stat.st_size,
                                   stat.st_mtime_ns, stat.st_ctime_ns))
            return result

        for _ in range(3):
            if time.monotonic() >= deadline:
                break
            # A retry must not reuse a WAL that disappeared after the last copy.
            for member in family:
                (private / member.name).unlink(missing_ok=True)
            before = signatures()
            try:
                if before[0] is None:
                    raise FileNotFoundError(source)
                present = [member for member, stamp in zip(family, before) if stamp is not None]
                for member in present:
                    shutil.copyfile(member, private / member.name)
                if signatures() != before:
                    continue
                if any(_digest(member, deadline) != _digest(private / member.name, deadline)
                       for member in present):
                    continue
                if signatures() == before:
                    return private / source.name
            except FileNotFoundError:
                # Checkpoints and journal cleanup may remove a member mid-copy.
                continue
        raise OSError("The selected backup changed while being copied. Please try restoring again.")

    def _resolve_backup_path(self, path, required_file=None) -> Path:
        """Keep selections made before migration usable for this session."""
        path = Path(path)
        relocated = self._migrated_paths.get(path, path)
        # A published directory can contain a rebased link to a sibling that
        # failed to publish. Retain the usable original until that dependency
        # exists; top-level directory links need the same fallback.
        original_entry = path / required_file if required_file else path
        relocated_entry = relocated / required_file if required_file else relocated
        if not relocated_entry.exists() and original_entry.exists():
            return path
        return relocated

    def _migration_protected_paths(self):
        if self.backups_path is None:
            return set()
        legacy = self.addon_path.parent / "ankimon_backups"
        try:
            plan = self._migration_plan(legacy, self.backups_path)
        except (OSError, ValueError, KeyError, TypeError):
            # Unknown metadata cannot justify deleting possible recovery copies.
            return set(self.backups_path.glob("*")) | set(legacy.glob("*"))
        return {legacy / name for name in plan} | {
            self.backups_path / name for name in plan.values()
        }

    def delete_backup(self, backup_path_str: str):
        if not self._profile_work_lock.acquire(blocking=False):
            showWarning("Backup migration is running. Please try again shortly.")
            return
        try:
            return self._delete_backup(backup_path_str)
        finally:
            self._profile_work_lock.release()

    def _delete_backup(self, backup_path_str: str):
        """Deletes a selected backup.

        Renamed out of the ``backup_`` namespace first, as retention does. A
        removal that failed part-way in place restamped the directory's mtime,
        and retention, which orders by mtime, then kept those hidden remains as
        the newest backup and evicted a good one to make room for them.
        """
        backup_path = self._resolve_backup_path(backup_path_str)
        if backup_path in self._migration_protected_paths():
            showWarning("This backup is needed while legacy migration is incomplete.")
            return
        if not backup_path.is_dir():
            showWarning("Selected backup path does not exist.")
            return
        try:
            doomed = backup_path.rename(self._discard_name(backup_path))
        except Exception as e:
            self.logger.log("error", f"Failed to delete backup: {e}")
            showWarning(f"Failed to delete backup: {e}")
            return
        try:
            self._remove_tree(doomed, None)
            self.logger.log("info", f"Deleted backup: {backup_path.name}")
        except Exception as e:
            # Out of the listing and the count already; retention finishes it.
            self.logger.log("error", f"Backup {backup_path.name} is deleted, but some "
                            f"of its files remain until the next backup: {e}")
        showInfo("Backup deleted successfully.")

    def _discard_name(self, directory: Path) -> Path:
        return directory.with_name(
            f"{self.DISCARD_PREFIX}{uuid.uuid4().hex[:8]}_{directory.name.lstrip('.')}")

    def _remove_tree(self, directory: Path, deadline) -> bool:
        """Delete a directory one entry at a time, stopping at the deadline.

        ``shutil.rmtree`` cannot be interrupted, and a backup holds whole save
        copies, so one large or stalled deletion could keep Anki closing after
        the shutdown budget had run out. Checking between entries bounds the
        overrun to the single unlink already in flight. Returns False when the
        deadline stopped it; filesystem errors propagate.

        A link, the root included, is removed as a link. ``shutil.rmtree``
        refuses one; walking it would delete the files it points at, outside
        the backups folder.
        """
        if self._is_link(directory):
            directory.unlink()
            return True
        if deadline is not None and time.monotonic() >= deadline:
            # Past the deadline, list nothing: iterdir reads the whole directory
            # before it yields the first entry, so a check inside the loop only
            # runs after that read. An empty directory -- a failed attempt's
            # staging folder, typically -- still goes, in the one call that needs
            # no listing.
            try:
                directory.rmdir()
            except OSError:
                return False
            return True
        for entry in directory.iterdir():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            if entry.is_dir() and not self._is_link(entry):
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
            doomed = self._discard_name(directory)
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
        if not self._profile_work_lock.acquire(blocking=False):
            return
        try:
            return self._cleanup_backups(deadline=deadline)
        finally:
            self._profile_work_lock.release()

    def _cleanup_backups(self, deadline: float = None):
        """Deletes old backups based on retention policy."""
        if self.backups_path is None:
            return
        protected = self._migration_protected_paths()
        # Taken before this pass renames anything, so a removal that fails now
        # is retried by the next pass rather than twice in this one.
        leftovers = self._discarded()
        # Only published backups enter retention. Failed or interrupted staging
        # directories must not displace recoverable saves even if they remain.
        backups = sorted(
            [p for p in self.backups_path.iterdir()
             if p.name.startswith("backup_") and p.is_dir() and p not in protected],
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

        self._sweep_leftovers(deadline, leftovers)

    def _discarded(self) -> List[Path]:
        if self.backups_path is None:
            return []
        # A link counts even when dangling: _remove_tree removes the link itself.
        return [p for p in self.backups_path.glob(f"{self.DISCARD_PREFIX}*")
                if p.is_dir() or p.is_symlink()]

    def _sweep_leftovers(self, deadline: float = None, leftovers: List[Path] = None):
        # Failed backup attempts also call this directly, outside retention's
        # lock. Do not race a worker publishing new migration entries.
        if not self._profile_work_lock.acquire(blocking=False):
            return
        try:
            return self._sweep_leftovers_locked(deadline, leftovers)
        finally:
            self._profile_work_lock.release()

    def _sweep_leftovers_locked(self, deadline: float = None, leftovers: List[Path] = None):
        """Remove abandoned staging directories and unfinished removals.

        Neither is a backup anyone can list or restore, so unlike retention this
        also runs after a failed attempt. ``leftovers`` is the ``.discard_`` list
        a caller took before renaming anything itself; by default it is taken
        here, before the staging sweep renames anything.
        """
        if self.backups_path is None:
            return
        protected = self._migration_protected_paths()
        if leftovers is None:
            leftovers = self._discarded()

        # An attempt whose own rmtree failed leaves a dot-prefixed staging
        # directory holding a full copy of the save. Listing and retention both
        # filter on "backup_", by design -- an incomplete attempt must never
        # displace a recoverable one -- so nothing else would ever remove it.
        # Age-gated because the name is only reserved while an attempt is live,
        # and a snapshot takes seconds, never an hour.
        for staging in self.backups_path.glob(".backup_*"):
            if staging in protected:
                continue
            try:
                if not staging.is_dir():
                    continue
                if time.time() - os.path.getmtime(staging) < self.STALE_STAGING_AGE:
                    continue
            except OSError:
                continue
            if not self._discard(staging, "incomplete backup", deadline):
                return

        migrating_entries = [] if (self.backups_path / self.MIGRATION_RECORD).exists() else (
            self.backups_path.glob(f"{self.MIGRATING_PREFIX}*")
        )
        for migrating in migrating_entries:
            if migrating in protected:
                continue
            try:
                if not migrating.is_dir() or self._is_link(migrating):
                    continue
                if time.time() - os.path.getmtime(migrating) < self.STALE_STAGING_AGE:
                    continue
            except OSError:
                continue
            if not self._discard(migrating, "incomplete migration copy", deadline):
                return

        # What an earlier pass renamed out of the listing but could not finish
        # deleting -- stopped by the deadline, or by a locked file -- ends here.
        for leftover in leftovers:
            if leftover in protected:
                continue
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
