"""
Migration Dialog for Ankimon Database

Shows a blocking dialog when migrating from JSON to SQLite storage.
The program is not usable until migration completes.
"""

import shutil
import traceback
import uuid
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QApplication,
    QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from pathlib import Path

from ..resources import user_path


class MigrationDialog(QDialog):
    """Blocking dialog for database migration."""

    def __init__(
        self,
        db,
        mypokemon_path,
        mainpokemon_path,
        items_path,
        badges_path,
        parent=None,
        team_path=None,
        history_path=None,
        data_path=None,
        rate_path=None,
    ):
        super().__init__(parent)
        self.db = db
        self.mypokemon_path = Path(mypokemon_path)
        self.mainpokemon_path = Path(mainpokemon_path)
        self.items_path = Path(items_path)
        self.badges_path = Path(badges_path)
        self.team_path = Path(team_path) if team_path else None
        self.history_path = Path(history_path) if history_path else None
        self.data_path = Path(data_path) if data_path else None
        self.rate_path = Path(rate_path) if rate_path else None

        self.migration_successful = False
        self.migration_running = False
        self.cancelled = False

        self.setWindowTitle("Ankimon Data Migration")
        self.setMinimumSize(500, 450)  # Increased height
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Title
        title = QLabel("📦 Database Migration Required")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Description
        desc = QLabel(
            "Ankimon is upgrading to a new, faster storage system!\n\n"
            "Your Pokemon collection, teams, and history will be migrated.\n"
            "This is a one-time process."
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Status label
        self.status_label = QLabel("Click 'Start Migration' to begin.")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Progress log
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(150)
        layout.addWidget(self.log_area)

        # Buttons
        button_layout = QHBoxLayout()

        self.start_button = QPushButton("🚀 Start Migration")
        self.start_button.setMinimumHeight(40)
        self.start_button.clicked.connect(self._run_migration)
        button_layout.addWidget(self.start_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumHeight(40)
        self.cancel_button.clicked.connect(self._on_cancel)
        button_layout.addWidget(self.cancel_button)

        self.continue_button = QPushButton("Continue")
        self.continue_button.setMinimumHeight(40)
        self.continue_button.hide()
        self.continue_button.clicked.connect(self.accept)
        button_layout.addWidget(self.continue_button)

        layout.addLayout(button_layout)

    def _update_progress(self, percent: int, message: str):
        """Update progress bar and log."""
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)
        self.log_area.append(message)
        QApplication.processEvents()

    def _on_cancel(self):
        """Handle cancel button click."""
        if self.migration_running:
            reply = QMessageBox.question(
                self,
                "Cancel Migration",
                "Migration is in progress. Are you sure you want to cancel?\n\n"
                "Note: Partial data may remain in the database.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.cancelled = True
                self._update_progress(0, "⚠ Migration cancelled by user.")
        else:
            self.reject()

    def _run_migration(self):
        """Run the shared, verified migration in the foreground."""
        if self.migration_running:
            return
        self.migration_running = True
        self.migration_successful = False
        self.cancelled = False
        self.start_button.setEnabled(False)
        self.start_button.setText("Migrating...")
        try:
            stats = self.db.migrate_from_json(
                self.mypokemon_path,
                self.mainpokemon_path,
                self.items_path,
                self.badges_path,
                self.team_path,
                self.history_path,
                self.data_path,
                self.rate_path,
                progress=self._update_progress,
                cancelled=lambda: self.cancelled,
            )
            if stats.get("cancelled"):
                self._finish_cancelled()
                return
            self.log_area.append(
                f"\nSummary:\n"
                f"- {stats['pokemon']} Pokemon verified\n"
                f"- {stats.get('pokemon_failed', 0)} Pokemon failed\n"
                f"- {stats['items']} Items\n"
                f"- {stats['badges']} Badges\n"
                f"- {stats['history']} History entries\n"
                f"- {stats['team']} Team members"
            )
            if stats.get("errors") or stats.get("integrity_issues"):
                for error in stats.get("errors", []):
                    self.log_area.append(error)
                self._update_progress(
                    0,
                    "Migration incomplete. Original files preserved. See errors above, then Retry.",
                )
                self.start_button.setEnabled(True)
                self.start_button.setText("🔄 Retry")
                return

            # The import is committed and verified. Do not accept a late cancel
            # from processEvents while archiving the successful migration.
            self.cancel_button.setEnabled(False)
            self._update_progress(96, "Backing up old JSON files...")
            self._cleanup_json_files()
            self._update_progress(100, "🎉 Migration complete!")
            self.migration_successful = True
            self.start_button.hide()
            self.cancel_button.hide()
            self.continue_button.show()
        except Exception as exc:
            self._update_progress(0, f"❌ Error: {exc}")
            self.log_area.append(
                f"\n--- Full Error Traceback ---\n{traceback.format_exc()}"
            )
            self.start_button.setEnabled(True)
            self.start_button.setText("🔄 Retry")
            self.cancel_button.setEnabled(True)
        finally:
            self.migration_running = False

    def _finish_cancelled(self):
        """Handle cancelled migration."""
        self.migration_running = False
        self.log_area.append("\n⚠ Migration was cancelled. Original files preserved.")
        self.start_button.setEnabled(True)
        self.start_button.setText("🔄 Retry")

    def _cleanup_json_files(self):
        """Move old JSON files to json/ subfolder after successful migration."""
        # Move to user_files/json/ - ensures path change breaks any remaining JSON usage
        backup_dir = self.mypokemon_path.parent / "json"

        # Determine the parent directory from available paths
        if not backup_dir.exists():
            try:
                # Try to use any available path to find the directory
                if self.mypokemon_path and self.mypokemon_path.parent.exists():
                    backup_dir = self.mypokemon_path.parent / "json"
                elif self.team_path and self.team_path.parent.exists():
                    backup_dir = self.team_path.parent / "json"
            except:
                pass

        backup_dir.mkdir(exist_ok=True)

        files_to_backup = [
            self.mypokemon_path,
            self.mainpokemon_path,
            self.items_path,
            self.badges_path,
            self.team_path,
            self.history_path,
            self.data_path,
            self.rate_path,
            user_path / "config.obf",  # Add config.obf to archiving
        ]

        for file_path in files_to_backup:
            if file_path and file_path.is_file():
                try:
                    # Move to backup instead of delete
                    dest = backup_dir / file_path.name
                    if dest.exists():
                        dest = (
                            backup_dir
                            / f"{file_path.stem}-{uuid.uuid4().hex}{file_path.suffix}"
                        )
                    shutil.move(str(file_path), str(dest))
                    self.log_area.append(f"  Backed up: {file_path.name}")
                except Exception as e:
                    self.log_area.append(f"  ⚠ Could not backup {file_path.name}: {e}")

        self.log_area.append(f"  Old files moved to: {backup_dir.name}/")

    def closeEvent(self, event):
        """Prevent closing until migration is complete or cancelled."""
        if self.migration_running:
            event.ignore()
            QMessageBox.warning(self, "Please Wait", "Migration is in progress.")
        else:
            event.accept()


def show_migration_dialog_if_needed(
    db,
    mypokemon_path,
    mainpokemon_path,
    items_path,
    badges_path,
    parent=None,
    team_path=None,
    history_path=None,
    data_path=None,
    rate_path=None,
) -> bool:
    """
    Shows the migration dialog if migration is needed.
    Blocks until migration is complete.
    """
    if db.is_migrated():
        return True

    # Check if there are actually any JSON files to migrate.
    # If not, this is likely a fresh install. We can simply mark it as migrated.
    from pathlib import Path

    files_to_check = [
        mypokemon_path,
        mainpokemon_path,
        items_path,
        badges_path,
        team_path,
        history_path,
        data_path,
        rate_path,
    ]

    has_legacy_files = any(Path(p).is_file() for p in files_to_check if p)

    if not has_legacy_files:
        # Fresh install, no need to migrate. Just mark as done.
        conn = db._get_connection()
        conn.cursor().execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated', 'true')"
        )
        conn.cursor().execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated_phase2', 'true')"
        )
        conn.commit()
        return True

    dialog = MigrationDialog(
        db,
        mypokemon_path,
        mainpokemon_path,
        items_path,
        badges_path,
        parent,
        team_path,
        history_path,
        data_path,
        rate_path,
    )
    dialog.exec()

    return dialog.migration_successful
