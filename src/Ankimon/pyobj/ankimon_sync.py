import base64
import contextlib
import errno
import filecmp
import gc
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Any

from aqt import mw, gui_hooks
from aqt.utils import showInfo, showWarning, tooltip
from ..pyobj.error_handler import show_warning_with_traceback

from ..resources import user_path, addon_dir
from ..utils import close_anki

from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QTextEdit, QPushButton, QDialog, QHBoxLayout, QScrollArea, QWidget


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


def _lock_tooltip(action: str, unchanged: bool = False) -> None:
    """Non-blocking tooltip for a transient file lock on an AUTOMATIC sync path
    (it self-heals on the next sync, so this never becomes a traceback dialog).
    Shares one sync-client list with ``SYNC_LOCK_MESSAGE`` so the guidance can't
    drift between the manual and automatic paths."""
    tail = "Your local data is unchanged; it" if unchanged else "It"
    tooltip(
        f"Ankimon: couldn't {action} right now — a program such as OneDrive, "
        "Google Drive, Dropbox, or an antivirus is holding the file open. "
        f"{tail} will retry on the next sync."
    )


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


class ImprovedPokemonDataSync(QDialog):
    """
    Improved Pokemon data sync dialog using the new AnkimonDataSync system.
    Provides better file comparison and uses Anki's media sync for reliable syncing.
    """

    def __init__(self, settings_obj, logger):
        super().__init__(mw)
        self.config = settings_obj
        self.logger = logger
        self.sync_handler = AnkimonDataSync()

        self.setup_ui()
        self.check_for_differences()

    def setup_ui(self):
        """Set up the user interface."""
        self.setWindowTitle("Ankimon Data Sync")
        self.setMinimumSize(800, 600)

        # Main layout
        main_layout = QVBoxLayout()

        # Header message
        header_text = (
            "Sync your Pokemon data between devices using AnkiWeb.\n"
            "Choose to export your local data to AnkiWeb or import data from AnkiWeb to your device."
        )
        self.header_label = QLabel(header_text)
        main_layout.addWidget(self.header_label)

        # Button layout
        button_layout = QHBoxLayout()

        self.export_button = QPushButton("Export Local Data to AnkiWeb")
        self.import_button = QPushButton("Import Data from AnkiWeb")
        self.refresh_button = QPushButton("Refresh Comparison")

        self.export_button.clicked.connect(self.export_to_ankiweb)
        self.import_button.clicked.connect(self.import_from_ankiweb)
        self.refresh_button.clicked.connect(self.check_for_differences)

        button_layout.addWidget(self.export_button)
        button_layout.addWidget(self.import_button)
        button_layout.addWidget(self.refresh_button)

        main_layout.addLayout(button_layout)

        # Comparison area
        comparison_layout = QHBoxLayout()

        # Local data area
        local_widget = QWidget()
        local_layout = QVBoxLayout(local_widget)
        local_layout.addWidget(QLabel("Local Data:"))

        self.local_text_area = QTextEdit()
        self.local_text_area.setReadOnly(True)
        self.local_text_area.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        local_layout.addWidget(self.local_text_area)

        # AnkiWeb data area
        web_widget = QWidget()
        web_layout = QVBoxLayout(web_widget)
        web_layout.addWidget(QLabel("AnkiWeb Data:"))

        self.web_text_area = QTextEdit()
        self.web_text_area.setReadOnly(True)
        self.web_text_area.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        web_layout.addWidget(self.web_text_area)

        comparison_layout.addWidget(local_widget)
        comparison_layout.addWidget(web_widget)

        main_layout.addLayout(comparison_layout)

        self.setLayout(main_layout)

    def check_for_differences(self):
        """Check for differences between local and AnkiWeb data."""
        try:
            differences = self.sync_handler.get_file_differences()

            if not differences:
                self.header_label.setText(
                    "Ankimon Data Sync:\n"
                    "✅ All data is synchronized. No differences found."
                )
                self.local_text_area.setPlainText("No differences found.")
                self.web_text_area.setPlainText("No differences found.")
                self.export_button.setEnabled(False)
                self.import_button.setEnabled(False)
                return

            self.header_label.setText(
                f"⚠️ Found differences in {len(differences)} file(s). Please choose sync direction:\n"
            )
            self.export_button.setEnabled(True)
            self.import_button.setEnabled(True)

            self._display_differences(differences)
            self.show()

        except Exception as e:
            self.logger.log("error", f"Failed to check for differences: {str(e)}")
            show_warning_with_traceback(parent=self, exception=e, message="Error checking for differences")

    def _display_differences(self, differences: Dict[str, Dict]):
        """Display improved JSON differences, showing only what changed per file with specific key differences."""
        import json
        from typing import Any, Dict, List, Tuple, Set

        def format_value(value: Any) -> str:
            """Format a value for display."""
            if isinstance(value, str):
                return f'"{value}"'
            elif isinstance(value, (int, float)):
                return str(value)
            elif isinstance(value, bool):
                return str(value).lower()
            elif isinstance(value, list):
                if len(value) <= 3:
                    return f"[{', '.join(format_value(v) for v in value)}]"
                else:
                    return f"[{', '.join(format_value(v) for v in value[:2])}, ... +{len(value)-2} more]"
            elif isinstance(value, dict):
                if len(value) <= 2:
                    items = [f"{k}: {format_value(v)}" for k, v in value.items()]
                    return "{" + ", ".join(items) + "}"
                else:
                    items = list(value.items())[:2]
                    formatted = [f"{k}: {format_value(v)}" for k, v in items]
                    return "{" + ", ".join(formatted) + f", ... +{len(value)-2} more" + "}"
            else:
                return str(value)[:50] + ("..." if len(str(value)) > 50 else "")

        def compare_databases(filename: str) -> Tuple[List[str], List[str]]:
            """Returns stats-based comparison for the database."""
            local_lines = []
            remote_lines = []
            
            def get_db_stats(db_path: Path) -> Dict[str, Any]:
                stats = {
                    "pokemon": 0,
                    "items": 0,
                    "history": 0,
                    "badges": 0,
                    "trainer_name": "N/A",
                    "trainer_level": 1,
                    "trainer_cash": 0
                }
                if not db_path.is_file():
                    return stats
                conn = None
                try:
                    import sqlite3
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = {row["name"] for row in cursor.fetchall()}
                    
                    if "captured_pokemon" in tables:
                        cursor.execute("SELECT COUNT(*) as count FROM captured_pokemon")
                        stats["pokemon"] = cursor.fetchone()["count"]
                    
                    if "items" in tables:
                        cursor.execute("SELECT SUM(quantity) as count FROM items")
                        res = cursor.fetchone()
                        stats["items"] = res["count"] if res and res["count"] is not None else 0
                    
                    if "pokemon_history" in tables:
                        cursor.execute("SELECT COUNT(*) as count FROM pokemon_history")
                        stats["history"] = cursor.fetchone()["count"]
                        
                    if "badges" in tables:
                        cursor.execute("SELECT COUNT(*) as count FROM badges WHERE achieved = 1")
                        stats["badges"] = cursor.fetchone()["count"]
                        
                    if "config" in tables:
                        cursor.execute("SELECT value FROM config WHERE key = 'trainer.name'")
                        row = cursor.fetchone()
                        if row:
                            stats["trainer_name"] = row["value"]
                            
                        cursor.execute("SELECT value FROM config WHERE key = 'trainer.level'")
                        row = cursor.fetchone()
                        if row:
                            stats["trainer_level"] = int(row["value"])
                            
                        cursor.execute("SELECT value FROM config WHERE key = 'trainer.cash'")
                        row = cursor.fetchone()
                        if row:
                            stats["trainer_cash"] = int(row["value"])
                    
                except Exception as e:
                    self.logger.log("error", f"Failed to get stats for {db_path.name}: {e}")
                finally:
                    if conn is not None:
                        conn.close()
                return stats

            source_file = self.sync_handler._get_source_path(filename)
            media_file = self.sync_handler._get_media_path(filename)
            
            local_stats = get_db_stats(source_file)
            
            local_lines.append(f"Trainer: {local_stats['trainer_name']}")
            local_lines.append(f"Level: {local_stats['trainer_level']}")
            local_lines.append(f"Cash: {local_stats['trainer_cash']}")
            local_lines.append(f"Captured Pokemon: {local_stats['pokemon']}")
            local_lines.append(f"Total Items: {local_stats['items']}")
            local_lines.append(f"Badges: {local_stats['badges']}")
            local_lines.append(f"History: {local_stats['history']}")
            
            if media_file.is_file():
                remote_stats = get_db_stats(media_file)
                remote_lines.append(f"Trainer: {remote_stats['trainer_name']}")
                remote_lines.append(f"Level: {remote_stats['trainer_level']}")
                remote_lines.append(f"Cash: {remote_stats['trainer_cash']}")
                remote_lines.append(f"Captured Pokemon: {remote_stats['pokemon']}")
                remote_lines.append(f"Total Items: {remote_stats['items']}")
                remote_lines.append(f"Badges: {remote_stats['badges']}")
                remote_lines.append(f"History: {remote_stats['history']}")
            else:
                remote_lines.append("(No database file exists on AnkiWeb)")
                remote_lines.extend([""] * 6)
                
            return local_lines, remote_lines

        def detect_structure_and_compare(local_data: Any, remote_data: Any, filename: str) -> Tuple[List[str], List[str]]:
            """Detect the data structure and apply appropriate comparison."""
            if filename == 'ankimon.db':
                return compare_databases(filename)
            
            return ["(Settings file)"], ["(Settings file)"]

        # Main display logic
        local_content = []
        web_content = []

        for filename, diff_info in differences.items():
            local_content.append(f"=== {filename} ===")
            web_content.append(f"=== {filename} ===")

            if diff_info.get('error'):
                error_msg = f"❌ Error: {diff_info['error']}"
                local_content.append(error_msg)
                web_content.append(error_msg)
                local_content.append("")
                web_content.append("")
                continue

            local_exists = diff_info.get('local_exists', False)
            media_exists = diff_info.get('media_exists', False)

            # Show file existence status
            local_content.append(f"Local file exists: {local_exists}")
            web_content.append(f"AnkiWeb file exists: {media_exists}")

            if filename.endswith(('.json', '.obf')) or filename == 'ankimon.db':
                local_data = diff_info.get('local_data')
                media_data = diff_info.get('media_data')

                # Use smart comparison
                local_lines, remote_lines = detect_structure_and_compare(local_data, media_data, filename)

                if local_lines or remote_lines:
                    local_content.append("Differences:")
                    web_content.append("Differences:")

                    # Pad the shorter list to align output
                    max_lines = max(len(local_lines), len(remote_lines))
                    local_lines.extend(["" ] * (max_lines - len(local_lines)))
                    remote_lines.extend(["" ] * (max_lines - len(remote_lines)))

                    local_content.extend(local_lines)
                    web_content.extend(remote_lines)
                else:
                    local_content.append("No differences detected")
                    web_content.append("No differences detected")
            else:
                local_content.append("(Binary/Non-JSON file - cannot show detailed diff)")
                web_content.append("(Binary/Non-JSON file - cannot show detailed diff)")

            local_content.append("")
            web_content.append("")

        self.local_text_area.setPlainText("\n".join(local_content))
        self.web_text_area.setPlainText("\n".join(web_content))

    def _format_json_data(self, data: Any, filename: str) -> List[str]:
        """Format JSON data for display, showing key differences."""
        lines = []

        if filename in ['mypokemon.json', 'mainpokemon.json']:
            # Special handling for Pokemon data
            if isinstance(data, list):
                lines.append(f"Pokemon count: {len(data)}")
                for i, pokemon in enumerate(data[:3]):  # Show first 3
                    if isinstance(pokemon, dict):
                        lines.extend(self._format_pokemon_data(pokemon, i))
                if len(data) > 3:
                    lines.append(f"... and {len(data) - 3} more Pokemon")
            else:
                lines.append("Invalid Pokemon data format")
        else:
            # Generic JSON formatting
            try:
                if isinstance(data, dict):
                    lines.append(f"Keys: {list(data.keys())}")
                    for key, value in list(data.items())[:5]:  # Show first 5 items
                        if isinstance(value, (str, int, float, bool)):
                            lines.append(f"  {key}: {value}")
                        else:
                            lines.append(f"  {key}: {type(value).__name__}")
                elif isinstance(data, list):
                    lines.append(f"Array with {len(data)} items")
                    for i, item in enumerate(data[:3]):
                        lines.append(f"  [{i}]: {type(item).__name__}")
                else:
                    lines.append(str(data)[:100] + "..." if len(str(data)) > 100 else str(data))
            except Exception as e:
                lines.append(f"Error formatting data: {str(e)}")

        return lines

    def _format_pokemon_data(self, pokemon: Dict, index: int) -> List[str]:
        """Format Pokemon data for display showing all relevant fields."""
        lines = [f"Pokemon {index + 1}:"]

        # Core identification
        if 'name' in pokemon:
            lines.append(f"  Name: {pokemon['name']}")
        if 'individual_id' in pokemon:
            lines.append(f"  ID: {pokemon['individual_id'][:8]}...")
        if 'level' in pokemon:
            lines.append(f"  Level: {pokemon['level']}")

        # Stats and characteristics
        important_fields = [
            'gender', 'ability', 'type', 'current_hp', 'xp', 'friendship',
            'pokemon_defeated', 'shiny', 'tier', 'everstone', 'captured_date'
        ]

        for field in important_fields:
            if field in pokemon:
                value = pokemon[field]
                if isinstance(value, list):
                    lines.append(f"  {field.capitalize()}: {', '.join(map(str, value))}")
                else:
                    lines.append(f"  {field.capitalize()}: {value}")

        # Complex fields summary
        if 'stats' in pokemon and isinstance(pokemon['stats'], dict):
            lines.append(f"  Stats: {len(pokemon['stats'])} stat values")
        if 'ev' in pokemon and isinstance(pokemon['ev'], dict):
            ev_total = sum(pokemon['ev'].values()) if pokemon['ev'] else 0
            lines.append(f"  EVs: {ev_total} total")
        if 'iv' in pokemon and isinstance(pokemon['iv'], dict):
            iv_avg = sum(pokemon['iv'].values()) / len(pokemon['iv']) if pokemon['iv'] else 0
            lines.append(f"  IVs: {iv_avg:.1f} average")
        if 'attacks' in pokemon and isinstance(pokemon['attacks'], list):
            lines.append(f"  Moves: {len(pokemon['attacks'])} moves")

        return lines

    def export_to_ankiweb(self):
        """Export local data to AnkiWeb."""
        try:
            success = self.sync_handler.force_sync_to_media()
            if not success:
                # force_sync_to_media already told the user WHY nothing was
                # exported (a file-lock warning, or a traceback for a genuine
                # error). Don't re-raise it into a SECOND alarming dialog stacked
                # on top of that message — mirror import_from_ankiweb's contract.
                return

            # Enable automatic sync after a successful manual export.
            from .ankimon_sync import enable_automatic_sync
            enable_automatic_sync()

            tooltip("Data exported to AnkiWeb successfully! Automatic sync is now enabled.")
            self.close()
        except Exception as e:
            self.logger.log("error", f"Failed to export to AnkiWeb: {str(e)}")
            show_warning_with_traceback(parent=self, exception=e, message="Error exporting to AnkiWeb")

    def import_from_ankiweb(self):
        """Import data from AnkiWeb to local storage."""
        try:
            success = self.sync_handler.force_sync_from_media()
            if not success:
                # force_sync_from_media already told the user WHY nothing was
                # imported (integrity/backup abort, nothing-to-import, or a
                # traceback for a genuine error). Don't enable auto-sync, close
                # Anki, or stack a second alarming dialog on top of that message.
                return

            # Enable automatic sync after a successful manual import.
            from .ankimon_sync import enable_automatic_sync
            enable_automatic_sync()

            tooltip("Data imported from AnkiWeb successfully! Automatic sync is now enabled.")
            self.close()
            close_anki()
        except Exception as e:
            self.logger.log("error", f"Failed to import from AnkiWeb: {str(e)}")
            show_warning_with_traceback(parent=self, exception=e, message="Error importing from AnkiWeb")

    def auto_sync_on_close(self):
        """Automatically sync data when Anki closes."""
        try:
            synced_files = self.sync_handler.save_configs()
            if synced_files:
                tooltip(f"Synced {len(synced_files)} Ankimon files to AnkiWeb")
        except Exception as e:
            self.logger.log("error", f"Auto-sync failed: {str(e)}")

class AnkimonDataSync:
    """
    Handles syncing of Ankimon data files through Anki's media folder using a subfolder approach.
    This leverages Anki's built-in media sync to AnkiWeb while keeping files organized.
    """

    _OBFUSCATION_KEY = "H0tP-!s-N0t-4-C@tG!rL_v2"

    # Files to sync and their locations
    SYNC_FILES = {
        "ankimon.db": "user_files"
        # config.obf removed - now stored in ankimon.db
    }

    def __init__(self, addon_name: str = None):
        """Initialize with addon name for folder naming."""
        self.addon_name = addon_name or self._get_addon_name()
        self.addon_path = addon_dir
        self.user_files_path = user_path

        # Initialize paths as None - will be set when first accessed
        self._media_path = None
        self._media_sync_path = None
        self._sync_folder_name = None

    def _get_addon_name(self) -> str:
        """Get the addon name from the current addon folder."""
        try:
            current_file = Path(__file__)
            addon_dir = current_file.parents[2]  # Go up to addon root
            return addon_dir.name
        except:
            return "ankimon"  # fallback

    def _ensure_paths_initialized(self):
        """Ensure media paths are initialized. Call this before using any media path."""
        if self._media_path is None:
            profile_folder = mw.pm.profileFolder()
            if profile_folder is None:
                raise RuntimeError("No Anki profile loaded. Cannot initialize sync paths.")

            self._media_path = Path(profile_folder) / "collection.media"
            self._sync_folder_name = "Ankimon"
            self._media_sync_path = self._media_path

    @property
    def media_path(self) -> Path:
        """Get media path, initializing if needed."""
        self._ensure_paths_initialized()
        return self._media_path

    @property
    def media_sync_path(self) -> Path:
        """Get media sync path, initializing if needed."""
        self._ensure_paths_initialized()
        return self._media_sync_path

    @property
    def sync_folder_name(self) -> str:
        """Get sync folder name, initializing if needed."""
        self._ensure_paths_initialized()
        return self._sync_folder_name

    def _get_source_path(self, filename: str) -> Path:
        """Get the source path for a file based on its location."""
        location = self.SYNC_FILES.get(filename)
        if location == "addon_root" or filename == "meta.json":
            return self.addon_path / filename
        elif location == "user_files":
            return self.user_files_path / filename
        else:
            raise ValueError(f"Unknown location for file: {filename}")

    def _get_media_path(self, filename: str) -> Path:
        """Get the media subfolder path for a synced file."""
        return self.media_sync_path / filename

    def _get_legacy_media_path(self, filename: str) -> Path:
        """Get the old media folder path for migration from old format."""
        return self.media_path / f"_{self.addon_name}_{filename}"

    def _ensure_sync_folder_exists(self):
        """Ensure the sync subfolder exists in media directory."""
        try:
            self.media_sync_path.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            show_warning_with_traceback(parent=mw, exception=e, message="Failed to create sync folder")
            return False

    def _migrate_legacy_files(self) -> List[str]:
        """Migrate files from old flat structure to subfolder structure."""
        migrated_files = []

        for filename in self.SYNC_FILES.keys():
            legacy_path = self._get_legacy_media_path(filename)
            new_path = self._get_media_path(filename)

            # If legacy file exists and new file doesn't, migrate it
            if legacy_path.is_file() and not new_path.is_file():
                try:
                    if self._ensure_sync_folder_exists():
                        shutil.copy2(legacy_path, new_path)
                        os.remove(legacy_path)  # Remove old file
                        migrated_files.append(filename)
                except Exception as e:
                    show_warning_with_traceback(parent=mw, exception=e, message=f"Failed to migrate {filename}")

        return migrated_files

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

    

    

    def save_configs(self) -> List[str]:
        """
        Save configs from addon folder to media subfolder to trigger AnkiWeb sync.
        Returns list of files that were synced.
        """
        try:
            # First, migrate any legacy files
            migrated_files = self._migrate_legacy_files()
            if migrated_files:
                showInfo(f"Migrated {len(migrated_files)} files to new subfolder structure")

            # Ensure sync folder exists
            if not self._ensure_sync_folder_exists():
                return []

            synced_files = []

            for filename in self.SYNC_FILES.keys():
                try:
                    source_file = self._get_source_path(filename)
                    dest_file = self._get_media_path(filename)

                    # Skip if source file doesn't exist
                    if not source_file.is_file():
                        continue

                    # Flush any WAL sidecar into the main DB file first, or the
                    # single-file copy below would export a stale snapshot.
                    if filename.endswith(".db"):
                        self._checkpoint_live_db(source_file)

                    # Copy if destination doesn't exist or files differ.
                    needs_copy = False
                    if not dest_file.is_file():
                        needs_copy = True
                    elif os.path.getmtime(source_file) >= os.path.getmtime(dest_file):
                        # Source is at least as new as the cloud copy (an exact
                        # mtime tie can't be ordered, so it falls through here
                        # rather than being silently skipped — a genuinely
                        # older source is still correctly excluded below).
                        needs_copy = not filecmp.cmp(source_file, dest_file, shallow=False)

                    if needs_copy:
                        # Write atomically (temp on the same volume + os.replace,
                        # retrying a transient OneDrive/antivirus lock) so a lock
                        # or interruption can't leave a half-written media DB, and
                        # so this automatic pre-sync export tolerates the same
                        # locks the import side does (issue #636).
                        _atomic_write_over(source_file, dest_file)
                        synced_files.append(filename)

                except Exception as e:
                    # AUTOMATIC pre-sync export: a transient lock self-heals on
                    # the next sync, so surface it as a NON-blocking tooltip
                    # rather than a raw traceback dialog that would pop on every
                    # sync. Genuine errors still get the traceback.
                    if _is_lock_error(e):
                        _lock_tooltip(f"stage {filename} for AnkiWeb")
                        continue
                    show_warning_with_traceback(parent=mw, exception=e, message=f"Failed to sync {filename}")
                    continue

            return synced_files
        except RuntimeError as e:
            # Profile not loaded yet
            return []

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

    def _checkpoint_live_db(self, source_file: Path) -> None:
        """If ``services.db`` holds a live WAL connection to ``source_file``,
        checkpoint it before the single-file copy below reads it — WAL commits
        live in a ``-wal`` sidecar that ``shutil.copy2`` would miss, so without
        this an export could ship a stale ``ankimon.db``. Best-effort no-op.

        KNOWN LIMITATION (WAL-mode DBs only; fresh installs are non-WAL): a
        TRUNCATE checkpoint returns busy (does NOT raise) if another connection
        holds a snapshot — e.g. a background mobile-resolve thread mid-write — so
        the sidecar may not fully flush and the exported single file can be
        *stale* (still valid SQLite). The import side's integrity check catches
        the corrupt variant, not the stale one; a stale export self-corrects on
        the next export once the writer has finished. Accepted for this opt-in
        feature rather than pulling in the online-backup API / cross-thread
        connection coordination."""
        try:
            from ..services import services
            db = services.db
            if db is None:
                return
            db_path = getattr(db, "db_path", None)
            if db_path is None or Path(db_path).resolve() != Path(source_file).resolve():
                return
            try:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception:
                pass
        except Exception:
            pass

    @staticmethod
    def _verify_sqlite_integrity(db_file: Path) -> bool:
        """True only if ``db_file`` is a readable, non-empty Ankimon SQLite DB
        that passes a quick integrity check and carries the core
        ``captured_pokemon`` table. Guards the live save against being
        overwritten by a truncated / corrupt / half-synced / foreign media
        file."""
        try:
            if not db_file.is_file() or db_file.stat().st_size < 512:
                return False
            import sqlite3
            # Build the read-only URI via as_uri() so a profile path with spaces
            # or unicode (e.g. C:\Users\John Doe\...) is percent-encoded correctly
            # — a raw f-string URI would fail to open a perfectly valid DB and
            # wrongly refuse the import.
            uri = Path(db_file).resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
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

    def read_configs(self, media_sync_status: bool = False) -> List[str]:
        """
        Read configs from media subfolder and copy to addon folder.
        Returns list of files that were updated.
        """
        if media_sync_status:
            return []  # Don't read while sync is in progress

        try:
            # Check for legacy files first
            migrated_files = self._migrate_legacy_files()

            updated_files = []
            backed_up = False

            for filename in self.SYNC_FILES.keys():
                try:
                    source_file = self._get_source_path(filename)
                    media_file = self._get_media_path(filename)

                    # Skip if media file doesn't exist
                    if not media_file.is_file():
                        continue

                    # Ensure source directory exists
                    source_file.parent.mkdir(parents=True, exist_ok=True)

                    # Nothing to do if the files are already identical.
                    if source_file.is_file() and filecmp.cmp(source_file, media_file, shallow=False):
                        continue

                    # Don't let a STRICTLY OLDER cloud copy clobber local content
                    # that changed since. An exact mtime tie (e.g. two files
                    # written back-to-back within the filesystem's timestamp
                    # resolution) can't be ordered, so it falls through to the
                    # pre-existing content-differs check below rather than being
                    # silently skipped — still strictly safer than no mtime check
                    # at all, since a genuine age gap is still caught.
                    if source_file.is_file() and os.path.getmtime(media_file) < os.path.getmtime(source_file):
                        continue

                    is_db = filename.endswith(".db")

                    # SAFETY 1 — never overwrite the live save with a corrupt /
                    # truncated / half-synced / foreign media file. Skip loudly
                    # rather than clobber good local data with garbage.
                    if is_db and not self._verify_sqlite_integrity(media_file):
                        tooltip(
                            f"Ankimon: skipped importing {filename} from AnkiWeb — "
                            "the synced file failed an integrity check. Your local "
                            "data is unchanged."
                        )
                        continue

                    # SAFETY 2 — back the local save up ONCE before the first
                    # overwrite. If the backup can't be made, REFUSE to overwrite
                    # (symmetric with the integrity check): never clobber the live
                    # save with no recovery path.
                    if source_file.is_file() and not backed_up:
                        if not self._backup_before_overwrite(filename):
                            tooltip(
                                f"Ankimon: skipped importing {filename} from AnkiWeb "
                                "— couldn't create a safety backup of your local data "
                                "first. Your local data is unchanged."
                            )
                            continue
                        backed_up = True

                    # SAFETY 3 — atomic overwrite (temp + os.replace) with the
                    # live connection closed first, so an interrupted copy can
                    # never leave a half-written / malformed ankimon.db.
                    if is_db:
                        self._atomic_replace(media_file, source_file)
                    else:
                        self._close_live_db_connection(source_file)
                        _retry_on_lock(lambda: shutil.copy2(media_file, source_file))
                    updated_files.append(filename)

                except Exception as e:
                    # A locked file (OneDrive/antivirus) on this AUTOMATIC
                    # post-sync path is transient and self-heals on the next
                    # sync, so surface it as a NON-blocking tooltip rather than a
                    # raw traceback dialog that would pop on every background
                    # sync. The live save is untouched (the atomic replace aborts
                    # before overwriting). Genuine errors still get the traceback.
                    if _is_lock_error(e):
                        _lock_tooltip(f"import {filename} from AnkiWeb", unchanged=True)
                        continue
                    show_warning_with_traceback(parent=mw, exception=e, message=f"Failed to read {filename}")
                    continue

            return updated_files
        except RuntimeError as e:
            # Profile not loaded yet
            return []

    def get_file_differences(self) -> Dict[str, Dict]:
        """
        Compare local files with media files and return differences.
        Returns dict with file differences for UI display.
        """
        try:
            # Migrate legacy files first
            self._migrate_legacy_files()

            differences = {}

            for filename in self.SYNC_FILES.keys():
                source_file = self._get_source_path(filename)
                media_file = self._get_media_path(filename)

                # Skip if neither file exists
                if not source_file.is_file() and not media_file.is_file():
                    continue

                file_diff = {
                    'local_exists': source_file.is_file(),
                    'media_exists': media_file.is_file(),
                    'files_differ': False,
                    'local_data': None,
                    'media_data': None
                }

                # Check if the media file (from AnkiWeb) is strictly newer than the local file
                # If the local file is newer, it just means we haven't synced yet, which is not a conflict.
                if file_diff['local_exists'] and file_diff['media_exists']:
                    if os.path.getmtime(media_file) > os.path.getmtime(source_file):
                        file_diff['files_differ'] = not filecmp.cmp(source_file, media_file, shallow=False)
                elif file_diff['local_exists'] or file_diff['media_exists']:
                    file_diff['files_differ'] = True

                if file_diff['files_differ'] or file_diff.get('error'):
                    differences[filename] = file_diff

            return differences
        except RuntimeError as e:
            # Profile not loaded yet
            return {}

    def force_sync_to_media(self) -> bool:
        """Force sync all LOCAL files TO media subfolder (Export to AnkiWeb)."""
        try:
            if not self._ensure_sync_folder_exists():
                return False

            synced_files = []
            for filename in self.SYNC_FILES.keys():
                source_file = self._get_source_path(filename)  # LOCAL file
                dest_file = self._get_media_path(filename)     # MEDIA file

                if source_file.is_file():
                    # Flush WAL into the main DB before copying, else a stale
                    # snapshot is exported.
                    if filename.endswith(".db"):
                        self._checkpoint_live_db(source_file)

                    # Copy LOCAL to MEDIA (Export direction) atomically: write a
                    # temp on the same volume, then os.replace it over the media
                    # file (retrying a transient OneDrive/antivirus lock). This
                    # both tolerates the lock (issue #636) and removes the old
                    # non-atomic remove-then-copy window that could leave a
                    # half-written media DB for Anki to upload.
                    _atomic_write_over(source_file, dest_file)
                    synced_files.append(filename)

            # Report success ONLY if something was actually exported. With no
            # local source file present nothing is copied; returning True here
            # would make the caller (export_to_ankiweb) claim success, enable
            # auto-sync, and close the dialog despite a zero-file export.
            # Symmetric with force_sync_from_media's empty-updated_files guard.
            if not synced_files:
                showInfo("No local Ankimon data was found to export.")
                return False

            showInfo(f"Exported {len(synced_files)} files to AnkiWeb: {', '.join(synced_files)}")
            return True
        except Exception as e:
            # A locked media file (OneDrive/antivirus) that survived the retry
            # gets the single friendly message; a genuine error gets the
            # traceback. The caller (export_to_ankiweb) early-returns on False,
            # so this is NOT stacked with a second dialog.
            return _handle_manual_sync_error(e, "Failed to export to AnkiWeb")

    def force_sync_from_media(self) -> bool:
        """Force sync all MEDIA files FROM subfolder to local folder (Import from AnkiWeb)."""
        try:
            updated_files = []
            backed_up = False
            safety_aborted = False
            any_media = False
            for filename in self.SYNC_FILES.keys():
                media_file = self._get_media_path(filename)    # MEDIA file
                source_file = self._get_source_path(filename)  # LOCAL file

                if media_file.is_file():
                    any_media = True
                    # Ensure source directory exists
                    source_file.parent.mkdir(parents=True, exist_ok=True)

                    is_db = filename.endswith(".db")

                    # SAFETY — refuse to import a corrupt/foreign DB over the save.
                    if is_db and not self._verify_sqlite_integrity(media_file):
                        showWarning(
                            f"Import aborted for {filename}: the file on AnkiWeb "
                            "failed an integrity check. Your local data is unchanged."
                        )
                        safety_aborted = True
                        continue

                    # SAFETY — back up the local save once before overwriting;
                    # abort this file if the backup could not be made.
                    if source_file.is_file() and not backed_up:
                        if not self._backup_before_overwrite(filename):
                            showWarning(
                                f"Import aborted for {filename}: could not create a "
                                "safety backup of your local data first. Your local "
                                "data is unchanged."
                            )
                            safety_aborted = True
                            continue
                        backed_up = True

                    # Copy MEDIA to LOCAL (Import direction), atomically for the DB.
                    if is_db:
                        self._atomic_replace(media_file, source_file)
                    else:
                        self._close_live_db_connection(source_file)
                        _retry_on_lock(lambda: shutil.copy2(media_file, source_file))
                    updated_files.append(filename)

            # Report success ONLY if something was actually imported. A safety
            # abort (integrity/backup failure) or an absent media file leaves
            # updated_files empty — returning True there would make the caller
            # (import_from_ankiweb) claim success, enable auto-sync, and CLOSE
            # Anki despite nothing having been imported. Give the user a clear
            # reason for the two benign empty cases; a safety abort already
            # showed its own specific warning above.
            if not updated_files:
                if not any_media and not safety_aborted:
                    showInfo(
                        "No Ankimon data found on AnkiWeb to import yet. Export "
                        "from another device first, then sync this one."
                    )
                return False

            showInfo(f"Imported {len(updated_files)} files from AnkiWeb: {', '.join(updated_files)}\n\nAnki will now close. Please reopen Anki to apply changes!")
            return True
        except Exception as e:
            # A locked local DB (OneDrive/antivirus) that survived the retry gets
            # the single friendly message; a genuine error gets the traceback.
            # The live save is untouched — the atomic replace aborts before
            # overwriting — and returning False keeps import_from_ankiweb from
            # enabling auto-sync or closing Anki.
            return _handle_manual_sync_error(e, "Failed to import from AnkiWeb")

    def get_sync_folder_info(self) -> Dict[str, str]:
        """Get information about the sync folder for debugging."""
        try:
            return {
                'sync_folder_path': str(self.media_sync_path),
                'sync_folder_exists': self.media_sync_path.exists(),
                'files_in_sync_folder': [f.name for f in self.media_sync_path.iterdir()] if self.media_sync_path.exists() else [],
                'addon_name': self.addon_name,
                'media_path': str(self.media_path)
            }
        except RuntimeError as e:
            return {
                'error': str(e),
                'addon_name': self.addon_name,
                'media_path': 'Not initialized (no profile loaded)'
            }


# Global instance for easy access - but will be lazy initialized
_ankimon_sync_instance = None

def get_ankimon_sync() -> AnkimonDataSync:
    """Get the global AnkimonDataSync instance, creating it if needed."""
    global _ankimon_sync_instance
    if _ankimon_sync_instance is None:
        _ankimon_sync_instance = AnkimonDataSync()
    return _ankimon_sync_instance

def get_sync_info():
    """Get sync folder information for debugging."""
    try:
        return get_ankimon_sync().get_sync_folder_info()
    except Exception as e:
        return {'error': str(e)}

def check_and_sync_pokemon_data(settings_obj, logger):
    """
    Check for Pokemon data differences and show sync dialog ONLY if needed.
    Returns dialog instance only if differences exist.
    """
    ankiweb_sync = settings_obj.get("misc.ankiweb_sync")

    # Check if sync is disabled
    if not ankiweb_sync:
        logger.log("info", "AnkiWeb sync is disabled in settings - skipping sync check")
        return None

    try:
        sync_handler = AnkimonDataSync()
        differences = sync_handler.get_file_differences()

        if differences:
            # Show the sync dialog only if there are differences
            dialog = ImprovedPokemonDataSync(settings_obj, logger)
            dialog.show() # Show immediately
            return dialog
        else:
            # No differences found - enable automatic sync
            enable_automatic_sync()
            logger.log("info", "No sync differences found - automatic sync enabled")
            return None

    except Exception as e:
        logger.log("error", f"Failed to check Pokemon data sync: {str(e)}")
        return None

def save_ankimon_configs(settings_obj):
    """Convenience function to save configs - called before media sync."""
    ankiweb_sync = settings_obj.get("misc.ankiweb_sync")
    # Check if sync is disabled
    if not ankiweb_sync:
        return []

    try:
        sync_handler = get_ankimon_sync()
        return sync_handler.save_configs()
    except Exception as e:
        # Gracefully handle errors during startup
        return []

def read_ankimon_configs(settings_obj, media_sync_status: bool = False):
    """Convenience function to read configs - called after media sync."""
    ankiweb_sync = settings_obj.get("misc.ankiweb_sync")
    # Check if sync is disabled
    if not ankiweb_sync:
        return []

    try:
        sync_handler = get_ankimon_sync()
        return sync_handler.read_configs(media_sync_status)
    except Exception as e:
        # Gracefully handle errors during startup
        return []

# Global flag to track if automatic sync is enabled
_automatic_sync_enabled = False

# One-shot guard so a persistent mobile-detection failure surfaces a tooltip
# once per session instead of spamming it on every sync.
_mobile_detection_warned = False

# Reload safety (F31): the (hook, handler) pairs this module last registered,
# stored on the services registry so they survive a re-execution of this module
# (unlike a module-level flag) and can be removed before re-appending.
_SYNC_HOOK_RECORD = "_ankimon_sync_hook_handlers"

def setup_ankimon_sync_hooks(settings_obj, logger):
    """Register the AnkiWeb sync hooks.

    Registered UNCONDITIONALLY (not gated on the legacy ``misc.ankiweb_sync``
    file-sync toggle) so that mobile-review detection actually runs for every
    user. Mobile reviews arrive via Anki's own AnkiWeb sync — which is
    independent of Ankimon's file-sync toggle — so gating detection behind that
    toggle (default False, and never auto-enabled) meant ``on_sync_did_finish``
    was never attached and a mid-session sync never turned phone reviews into
    battles. The two behaviours inside these hooks keep their own narrower
    guards: the mobile-detection block self-gates on ``mobile.enabled``, and the
    file-based data-sync (subsystem B) stays dormant behind ``_automatic_sync_enabled``
    until the user opts in via the sync dialog — so registering here changes
    nothing for file-sync, it only lets mobile detection fire."""

    def on_sync_will_start():
        """Called before sync starts - only auto-sync if enabled."""
        if not _automatic_sync_enabled:
            logger.log("info", "Anki sync starting - automatic Ankimon sync disabled (awaiting manual sync)")
            return

        try:
            synced_files = save_ankimon_configs(settings_obj)
            if synced_files:
                logger.log("info", f"Prepared {len(synced_files)} files for sync")
        except Exception as e:
            logger.log("error", f"Failed to prepare files for sync: {str(e)}")

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

        # Only auto-read Ankimon configs if automatic sync is enabled
        if not _automatic_sync_enabled:
            logger.log("info", "Anki sync finished - automatic Ankimon sync disabled (awaiting manual sync)")
            return

        try:
            updated_files = read_ankimon_configs(settings_obj, media_sync_status=False)
            if updated_files:
                logger.log("info", f"Updated {len(updated_files)} files from sync")
                tooltip(f"Updated {len(updated_files)} Ankimon files from AnkiWeb")
        except Exception as e:
            logger.log("error", f"Failed to read files after sync: {str(e)}")

    # Register hooks (but they won't auto-sync until enabled). Reload safety
    # (F31 registry-anchored guard): a second boot in the same Anki session (the
    # F26 branch self-updater reloading add-on code, or any re-run of
    # register_profile_hooks) must not stack a second on_sync_did_finish — that
    # would double the dual-DB queueing pass, double the tooltip, and in auto
    # mode fire MobileBridge.resolveAll() twice per sync. Remove the previously
    # recorded handlers first; gui_hooks' remove() tolerates already-absent
    # callbacks, and the closures above are NEW objects each call, so only the
    # stored originals can be found and removed.
    from ..services import services
    for hook, handler in getattr(services, _SYNC_HOOK_RECORD, ()):
        hook.remove(handler)

    _handlers = (
        (gui_hooks.sync_will_start, on_sync_will_start),
        (gui_hooks.sync_did_finish, on_sync_did_finish),
    )
    for hook, handler in _handlers:
        hook.append(handler)
    setattr(services, _SYNC_HOOK_RECORD, _handlers)

    logger.log("info", "Ankimon sync hooks registered (automatic sync disabled until manual sync)")


def enable_automatic_sync():
    """Enable automatic sync after user has made their first manual sync decision."""
    global _automatic_sync_enabled
    _automatic_sync_enabled = True

def is_automatic_sync_enabled():
    """Check if automatic sync is enabled."""
    return _automatic_sync_enabled