"""
Migration Dialog for Ankimon Database

Shows a blocking dialog when migrating from JSON to SQLite storage.
The program is not usable until migration completes.
"""

import json
import shutil
import traceback
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QApplication, QMessageBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from pathlib import Path
from typing import Dict, Any

from ..utils import show_warning_with_traceback


class MigrationDialog(QDialog):
    """Blocking dialog for database migration."""
    
    def __init__(self, db, mypokemon_path, mainpokemon_path, items_path, badges_path, 
                 parent=None, team_path=None, history_path=None, data_path=None, rate_path=None):
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
                self, "Cancel Migration",
                "Migration is in progress. Are you sure you want to cancel?\n\n"
                "Note: Partial data may remain in the database.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.cancelled = True
                self._update_progress(0, "⚠ Migration cancelled by user.")
        else:
            self.reject()
    
    def _run_migration(self):
        """Run migration in foreground (blocking)."""
        self.migration_running = True
        self.start_button.setEnabled(False)
        self.start_button.setText("Migrating...")
        stats = {"pokemon": 0, "main": 0, "items": 0, "badges": 0, 
                 "team": 0, "history": 0, "userdata": 0}
        
        try:
            # Check if Phase 1 is already done
            phase1_done = self.db.is_migrated_phase1()
            
            if phase1_done:
                self._update_progress(10, "Phase 1 (Collection) already completed. Skipping...")
            else:
                # Step 1: Migrate mypokemon.json
                if self.mypokemon_path.is_file() and not self.cancelled:
                    self._update_progress(5, "Loading Pokemon collection...")
                    with open(self.mypokemon_path, 'r', encoding='utf-8') as f:
                        pokemon_list = json.load(f)
                    
                    total = len(pokemon_list)
                    for i, pokemon in enumerate(pokemon_list):
                        if self.cancelled: break
                        if self.db.save_pokemon(pokemon):
                            stats["pokemon"] += 1
                        if total > 0 and (i % 20 == 0 or i == total - 1):
                            pct = 5 + int((i / total) * 45)  # Up to 50%
                            self._update_progress(pct, f"Migrating Pokemon {i + 1}/{total}...")
                    
                    if not self.cancelled:
                        self._update_progress(50, f"✓ Migrated {stats['pokemon']} Pokemon")
                elif not self.mypokemon_path.is_file():
                    self._update_progress(50, "No Pokemon collection found.")
                
                if self.cancelled:
                    self._finish_cancelled()
                    return
                
                # Step 2: Migrate mainpokemon.json
                if self.mainpokemon_path.is_file():
                    self._update_progress(52, "Migrating main Pokemon...")
                    with open(self.mainpokemon_path, 'r', encoding='utf-8') as f:
                        main_data = json.load(f)
                    if main_data:
                        main_pokemon = main_data[0] if isinstance(main_data, list) else main_data
                        if self.db.save_main_pokemon(main_pokemon):
                            stats["main"] = 1
                    self._update_progress(55, "✓ Migrated main Pokemon")
                
                # Step 3: Migrate items.json
                if self.items_path.is_file():
                    self._update_progress(56, "Migrating items...")
                    print(f"items_path: {self.items_path}")
                    with open(self.items_path, 'r', encoding='utf-8') as f:
                        items_list = json.load(f)
                    for item in items_list:
                        if self.cancelled: break
                        item_name = item.get("item") or item.get("item_name")
                        quantity = item.get("quantity", item.get("amount", 1))
                        if item_name:
                            self.db.save_item(item_name, quantity, item)
                            stats["items"] += 1
                    if not self.cancelled:
                        self._update_progress(60, f"✓ Migrated {stats['items']} items")
                
                # Step 4: Migrate badges.json
                if self.badges_path.is_file():
                    self._update_progress(61, "Migrating badges...")
                    with open(self.badges_path, 'r', encoding='utf-8') as f:
                        badges_list = json.load(f)
                    for badge in badges_list:
                        if self.cancelled: break
                        if isinstance(badge, int):
                            badge_id = str(badge); badge_data = {"id": badge}
                        else:
                            badge_id = str(badge.get("id", badge.get("badge_id", ""))); badge_data = badge
                        if badge_id:
                            self.db.save_badge(badge_id, badge_data)
                            stats["badges"] += 1
                    if not self.cancelled:
                        self._update_progress(65, f"✓ Migrated {stats['badges']} badges")
                
                # Mark Phase 1 done
                conn = self.db._get_connection()
                conn.cursor().execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated', 'true')")
                conn.commit()

            if self.cancelled:
                self._finish_cancelled()
                return

            # --- Phase 2: Team, History, UserData ---
            
            # Step 5: Migrate Team
            if self.team_path and self.team_path.is_file():
                self._update_progress(66, "Migrating team...")
                with open(self.team_path, 'r', encoding='utf-8') as f:
                    team_list = json.load(f)
                if self.db.save_team(team_list):
                    stats["team"] = len(team_list)
                self._update_progress(70, f"✓ Migrated team ({stats['team']} members)")

            # Step 6: Migrate History (This can be large)
            if self.history_path and self.history_path.is_file():
                self._update_progress(71, "Migrating release history...")
                with open(self.history_path, 'r', encoding='utf-8') as f:
                    history_list = json.load(f)
                
                total_hist = len(history_list)
                for i, pokemon in enumerate(history_list):
                    if self.cancelled: break
                    if self.db.add_to_history(pokemon):
                        stats["history"] += 1
                    if total_hist > 0 and (i % 50 == 0 or i == total_hist - 1):
                        pct = 71 + int((i / total_hist) * 20)  # Up to 91%
                        self._update_progress(pct, f"Migrating history {i + 1}/{total_hist}...")
                
                if not self.cancelled:
                    self._update_progress(91, f"✓ Migrated {stats['history']} history entries")

            # Step 7: Migrate User Data
            if self.data_path and self.data_path.is_file():
                self._update_progress(92, "Migrating user settings...")
                with open(self.data_path, 'r', encoding='utf-8') as f:
                    user_data = json.load(f)
                count = 0
                # Handle both dict and list formats
                if isinstance(user_data, dict):
                    items = user_data.items()
                elif isinstance(user_data, list):
                    # If list of dicts, merge them; otherwise wrap as indexed entries
                    merged = {}
                    for entry in user_data:
                        if isinstance(entry, dict):
                            merged.update(entry)
                    items = merged.items()
                else:
                    items = {}
                    self.log_area.append(f"  ⚠ Unexpected user data format: {type(user_data).__name__}")
                for key, value in items:
                    self.db.set_user_data(key, value)
                    count += 1
                    stats["userdata"] = count
                self._update_progress(95, f"✓ Migrated {stats['userdata']} settings")

            # Step 8: Migrate rate_this
            if self.rate_path and self.rate_path.is_file():
                try:
                    with open(self.rate_path, 'r', encoding='utf-8') as f:
                        rate_data = json.load(f)
                    
                    if isinstance(rate_data, dict) and rate_data.get("rate_this"):
                        self.db.set_user_data("rate_this", "true")
                        self._log_area.append("  Migrated rate_this.json")
                except:
                    pass

            # Mark Phase 2 done
            conn = self.db._get_connection()
            conn.cursor().execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated_phase2', 'true')")
            conn.commit()

            # Cleanup
            self._update_progress(96, "Cleaning up old JSON files...")
            self._cleanup_json_files()
            
            self._update_progress(100, "🎉 Migration complete!")
            
            summary = (f"\n📊 Summary:\n"
                       f"- {stats['pokemon']} Pokemon\n"
                       f"- {stats['items']} Items\n"
                       f"- {stats['badges']} Badges\n"
                       f"- {stats['history']} History entries\n"
                       f"- {stats['team']} Team members")
            self.log_area.append(summary)
            
            self.migration_successful = True
            self.migration_running = False
            self.start_button.hide()
            self.cancel_button.hide()
            self.continue_button.show()
            
        except Exception as e:
            self.migration_running = False
            self._update_progress(0, f"❌ Error: {e}")
            self.log_area.append(f"\n--- Full Error Traceback ---\n{traceback.format_exc()}")
            self.start_button.setEnabled(True)
            self.start_button.setText("🔄 Retry")
    
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
            self.mypokemon_path, self.mainpokemon_path, 
            self.items_path, self.badges_path,
            self.team_path, self.history_path, self.data_path,
            self.rate_path
        ]
        
        for file_path in files_to_backup:
            if file_path and file_path.is_file():
                try:
                    # Move to backup instead of delete
                    dest = backup_dir / file_path.name
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


def show_migration_dialog_if_needed(db, mypokemon_path, mainpokemon_path, 
                                     items_path, badges_path, parent=None,
                                     team_path=None, history_path=None, data_path=None, rate_path=None) -> bool:
    """
    Shows the migration dialog if migration is needed.
    Blocks until migration is complete.
    """
    if db.is_migrated():
        return True
    
    dialog = MigrationDialog(
        db, mypokemon_path, mainpokemon_path, items_path, badges_path, parent,
        team_path, history_path, data_path, rate_path
    )
    dialog.exec()
    
    return dialog.migration_successful
