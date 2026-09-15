import os
import hashlib
import time
import json
import requests
from pathlib import Path, PurePosixPath

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QProgressBar, QLabel, QPushButton, QMessageBox, QHBoxLayout, QCheckBox

from ..resources import user_path_sprites


_EXCLUDED_ROOTS = {".github"}
_EXCLUDED_NAMES = {
    "LICENSE",
    "download_complete.flag",
    "sprites.zip",
    "sprites_temp.zip",
}


def _is_managed_sprite_path(path: str) -> bool:
    """Return whether a repository path belongs to the distributed sprite set."""
    normalized = path.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (
        not normalized
        or normalized.startswith("/")
        or not parts
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
    ):
        return False
    if parts[0] in _EXCLUDED_ROOTS:
        return False
    name = parts[-1]
    return name not in _EXCLUDED_NAMES and not name.lower().endswith(".md")


def _write_update_state(state_path: Path, commit_sha: str) -> None:
    """Persist updater state atomically so partial writes are never accepted."""
    temporary_path = state_path.with_name(f"{state_path.name}.tmp")
    temporary_path.write_text(
        json.dumps(
            {
                "commit_sha": commit_sha,
                "updated_at": time.time(),
                "snooze_until": 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(state_path)


def _fetch_remote_files(commit_sha: str) -> dict[str, str]:
    tree_url = (
        "https://api.github.com/repos/h0tp-ftw/ankimon-sprites/git/trees/"
        f"{commit_sha}?recursive=1"
    )
    response = requests.get(tree_url, timeout=15)
    response.raise_for_status()
    tree_data = response.json()
    if not isinstance(tree_data, dict):
        raise ValueError("Invalid GitHub tree response.")
    if tree_data.get("truncated"):
        raise ValueError("GitHub Git Tree response was truncated.")
    remote_tree = tree_data.get("tree")
    if not isinstance(remote_tree, list):
        raise ValueError("Invalid GitHub tree response.")

    remote_files = {}
    for item in remote_tree:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = item.get("path")
        sha = item.get("sha")
        if isinstance(path, str) and isinstance(sha, str) and _is_managed_sprite_path(path):
            remote_files[path] = sha
    return remote_files


class SpriteUpdateDiffThread(QThread):
    """
    Background thread to download only added and modified sprites from raw.githubusercontent.com,
    and remove any deleted files.
    """
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, added, modified, deleted, remote_sha, dest_dir: Path):
        super().__init__()
        self.added = added
        self.modified = modified
        self.deleted = deleted
        self.remote_sha = remote_sha
        self.dest_dir = dest_dir
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total_files = len(self.added) + len(self.modified)
        downloaded_count = 0

        # Ensure destination directory exists
        self.dest_dir.mkdir(parents=True, exist_ok=True)

        # 1. Download added & modified files
        with requests.Session() as session:
            for i, path in enumerate(self.added + self.modified):
                if self._is_cancelled:
                    self.finished_signal.emit(False, "Update cancelled.")
                    return

                if not _is_managed_sprite_path(path):
                    self.finished_signal.emit(False, f"Unsafe sprite path rejected: {path}")
                    return

                self.status_signal.emit(f"Downloading ({i + 1}/{total_files}): {path}")
                url = f"https://raw.githubusercontent.com/h0tp-ftw/ankimon-sprites/{self.remote_sha}/{path}"
                
                success = False
                for attempt in range(3):
                    if self._is_cancelled:
                        self.finished_signal.emit(False, "Update cancelled.")
                        return
                    try:
                        response = session.get(url, timeout=15)
                        response.raise_for_status()
                        dest_file = self.dest_dir / path
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        dest_file.write_bytes(response.content)
                        success = True
                        break
                    except Exception:
                        time.sleep(1)
                if not success:
                    self.finished_signal.emit(False, f"Failed to download sprite: {path}")
                    return

                downloaded_count += 1
                self.progress_signal.emit(int((downloaded_count / total_files) * 100))

        # 2. Clean up files confirmed obsolete by the previous remote revision.
        for path in self.deleted:
            if self._is_cancelled:
                self.finished_signal.emit(False, "Update cancelled.")
                return
            if not _is_managed_sprite_path(path):
                self.finished_signal.emit(False, f"Unsafe sprite path rejected: {path}")
                return
            file_path = self.dest_dir / path
            if file_path.exists():
                try:
                    file_path.unlink()
                except OSError as exc:
                    self.finished_signal.emit(
                        False, f"Failed to remove obsolete sprite {path}: {exc}"
                    )
                    return

        # 3. Record success only after every file operation completed.
        if self._is_cancelled:
            self.finished_signal.emit(False, "Update cancelled.")
            return
        try:
            _write_update_state(
                self.dest_dir.parent / "sprites_update_state.json", self.remote_sha
            )
        except OSError as exc:
            self.finished_signal.emit(False, f"Failed to save sprite update state: {exc}")
            return
        self.finished_signal.emit(True, f"Successfully updated {total_files} sprites!")


class SpriteUpdateDialog(QDialog):
    """
    QDialog shown to the user displaying sprite diff analysis progress,
    update confirmation, and download progress. Used only when performing the actual updates.
    """
    def __init__(self, parent=None, silent_on_up_to_date=False, precalculated_result=None):
        super().__init__(parent)
        self.dest_dir = Path(user_path_sprites)
        self.silent_on_up_to_date = silent_on_up_to_date
        self.precalculated_result = precalculated_result
        self.setWindowTitle("Ankimon Sprites Update")
        self.resize(450, 220)
        
        # Ensure it modals and raises on top of parent Anki window
        self.setWindowModality(Qt.WindowModality.WindowModal)
        
        self.init_ui()
        
        if self.precalculated_result:
            self.on_diff_finished(self.precalculated_result)

    def init_ui(self):
        layout = QVBoxLayout()

        self.status_label = QLabel("Ready to update...", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.snooze_checkbox = QCheckBox("Snooze this update for 7 days", self)
        self.snooze_checkbox.setVisible(False)
        layout.addWidget(self.snooze_checkbox)

        button_layout = QHBoxLayout()
        self.confirm_button = QPushButton("Yes, Update", self)
        self.confirm_button.setVisible(False)
        self.confirm_button.clicked.connect(self.start_download)
        button_layout.addWidget(self.confirm_button)

        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def on_diff_finished(self, result):
        status = result.get("status")
        if status == "up_to_date":
            if self.silent_on_up_to_date:
                self.accept()
            else:
                self.status_label.setText("Sprites are already up to date!")
                self.cancel_button.setText("Close")
                self.progress_bar.setValue(100)
        elif status == "error":
            if not self.silent_on_up_to_date:
                QMessageBox.warning(self, "Update Error", f"Failed to check for sprite updates:\n{result.get('error')}")
            self.reject()
        elif status == "update_available":
            added = result.get("added", [])
            modified = result.get("modified", [])
            deleted = result.get("deleted", [])
            self.remote_sha = result.get("remote_sha")

            total_changes = len(added) + len(modified)
            if total_changes == 0 and len(deleted) == 0:
                self.save_state_only(self.remote_sha)
                self.accept()
                return

            self.added = added
            self.modified = modified
            self.deleted = deleted

            msg = "A sprites update is available!\n\n"
            msg += f"  • New sprites: {len(added)}\n"
            msg += f"  • Modified sprites: {len(modified)}\n"
            if deleted:
                msg += f"  • Obsolete to remove: {len(deleted)}\n"
            msg += "\nWould you like to download the update now?"
            
            self.status_label.setText(msg)
            self.confirm_button.setVisible(True)
            self.cancel_button.setText("Later")
            self.snooze_checkbox.setVisible(True)
            self.adjustSize()
            
            # Ensure it is displayed on top
            self.raise_()
            self.activateWindow()

    def reject(self):
        # Handle 7-day snooze if user opted in and dismissed the update
        if hasattr(self, "snooze_checkbox") and self.snooze_checkbox.isVisible() and self.snooze_checkbox.isChecked():
            try:
                state_path = self.dest_dir.parent / "sprites_update_state.json"
                state_data = {}
                if state_path.exists():
                    try:
                        state_data = json.loads(state_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                state_data["snooze_until"] = time.time() + 7 * 24 * 60 * 60
                state_path.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
            except Exception:
                pass
        super().reject()

    def save_state_only(self, sha):
        try:
            state_path = self.dest_dir.parent / "sprites_update_state.json"
            state_path.write_text(json.dumps({
                "commit_sha": sha,
                "updated_at": time.time(),
                "snooze_until": 0
            }, indent=2), encoding="utf-8")
        except Exception:
            pass

    def start_download(self):
        self.confirm_button.setVisible(False)
        self.cancel_button.setVisible(False)
        self.snooze_checkbox.setVisible(False)
        
        self.cancel_button.setText("Cancel Download")
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.setVisible(True)

        self.update_thread = SpriteUpdateDiffThread(
            self.added, self.modified, self.deleted, self.remote_sha, self.dest_dir
        )
        self.update_thread.progress_signal.connect(self.progress_bar.setValue)
        self.update_thread.status_signal.connect(self.status_label.setText)
        self.update_thread.finished_signal.connect(self.on_download_finished)

        self.update_thread.start()

    def cancel_download(self):
        if hasattr(self, "update_thread") and self.update_thread.isRunning():
            self.update_thread.cancel()
            self.status_label.setText("Cancelling download...")

    def on_download_finished(self, success, message):
        if success:
            try:
                manifest_path = self.dest_dir.parent / "sprites_local_manifest.json"
                if manifest_path.exists():
                    manifest_path.unlink()
            except Exception:
                pass
            QMessageBox.information(self, "Update Complete", message)
            self.accept()
        else:
            QMessageBox.warning(self, "Update Failed", message)
            self.reject()


def get_local_sprites_manifest(dest_dir: Path) -> dict:
    """Retrieves local sprite files mapping to their Git blob SHAs, leveraging a size/mtime cache file."""
    manifest_path = dest_dir.parent / "sprites_local_manifest.json"
    cached_files = {}
    if manifest_path.exists():
        try:
            cached_files = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", {})
        except Exception:
            pass

    local_files = {}
    changed = False

    if dest_dir.exists():
        for root, dirs, files in os.walk(dest_dir):
            for f in files:
                full_path = Path(root) / f
                rel_path = str(full_path.relative_to(dest_dir)).replace("\\", "/")
                if not _is_managed_sprite_path(rel_path):
                    continue

                try:
                    stat_info = full_path.stat()
                    mtime = stat_info.st_mtime
                    size = stat_info.st_size
                    
                    # Leverage cache if size and mtime match
                    cache_entry = cached_files.get(rel_path)
                    if cache_entry and cache_entry.get("mtime") == mtime and cache_entry.get("size") == size:
                        local_files[rel_path] = cache_entry["sha"]
                    else:
                        hasher = hashlib.sha1()
                        hasher.update(f"blob {size}\0".encode("utf-8"))
                        with open(full_path, "rb") as fh:
                            while chunk := fh.read(1024 * 1024):
                                hasher.update(chunk)
                        sha = hasher.hexdigest()
                        local_files[rel_path] = sha
                        cached_files[rel_path] = {
                            "mtime": mtime,
                            "size": size,
                            "sha": sha
                        }
                        changed = True
                except Exception:
                    pass

        # Cleanup removed files from cache
        to_remove = [p for p in cached_files if p not in local_files]
        if to_remove:
            for p in to_remove:
                del cached_files[p]
            changed = True

        if changed:
            try:
                manifest_path.write_text(json.dumps({"files": cached_files}, indent=2), encoding="utf-8")
            except Exception:
                pass

    return local_files


def calculate_sprite_diff(dest_dir: Path, silent: bool = False, ignore_snooze: bool = False) -> dict:
    """Calculates local file Git SHA-1 hashes and diffs them against the remote repository tree."""
    try:
        # Read local state first to check for active snooze.
        local_sha = None
        snooze_until = None
        state_path = dest_dir.parent / "sprites_update_state.json"
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(state_data, dict):
                    local_sha = state_data.get("commit_sha")
                    snooze_until = state_data.get("snooze_until")
            except (OSError, ValueError):
                pass

        # Check if update prompts are currently snoozed and exit early without network requests
        if not ignore_snooze and silent and isinstance(snooze_until, (int, float)) and time.time() < snooze_until:
            return {"status": "snoozed", "remote_sha": local_sha}

        # 1. Fetch latest remote commit SHA
        res = requests.get("https://api.github.com/repos/h0tp-ftw/ankimon-sprites/commits/main", timeout=10)
        res.raise_for_status()
        commit_data = res.json()
        remote_sha = commit_data.get("sha") if isinstance(commit_data, dict) else None
        if not isinstance(remote_sha, str) or not remote_sha:
            return {"status": "error", "error": "Invalid API response for latest commit."}

        # If SHA matches and sprites exist, we are up to date.
        # Bypass this shortcut on manual checks (where ignore_snooze=True) to allow verification/repair.
        if not ignore_snooze and local_sha == remote_sha:
            return {"status": "up_to_date", "remote_sha": remote_sha}

        # 2. Fetch the current managed sprite set.
        remote_files = _fetch_remote_files(remote_sha)

        # Fetch local file SHA mappings using cache.
        local_files = get_local_sprites_manifest(dest_dir)

        added = sorted(path for path in remote_files if path not in local_files)
        modified = sorted(
            path
            for path, remote_file_sha in remote_files.items()
            if path in local_files and local_files[path] != remote_file_sha
        )

        # Delete only files proven to belong to the previously installed revision.
        # Unknown local files are user data and must not be inferred as obsolete.
        previous_remote_files = {}
        if isinstance(local_sha, str) and local_sha:
            if local_sha == remote_sha:
                previous_remote_files = remote_files
            else:
                try:
                    previous_remote_files = _fetch_remote_files(local_sha)
                except Exception:
                    previous_remote_files = {}
        deleted = sorted(
            path
            for path in previous_remote_files
            if path not in remote_files and path in local_files
        )

        if not added and not modified and not deleted:
            return {"status": "up_to_date", "remote_sha": remote_sha}

        return {
            "status": "update_available",
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "remote_sha": remote_sha
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def trigger_sprites_update_check(parent=None, silent=False):
    """Entry point using Anki's QueryOp utility for thread-safety and background execution."""
    from aqt.operations import QueryOp
    from aqt import mw
    
    dest_dir = Path(user_path_sprites)
    
    def bg_check(_col):
        print("Sprite update check: starting background check...")
        return calculate_sprite_diff(dest_dir, silent=silent, ignore_snooze=not silent)

    def on_done(result):
        status = result.get("status")
        print(f"Sprite update check: finished with status {status}")
        if status == "update_available":
            dialog = SpriteUpdateDialog(parent=parent or mw, silent_on_up_to_date=False, precalculated_result=result)
            dialog.exec()
        elif status == "up_to_date":
            if not silent:
                QMessageBox.information(parent or mw, "Ankimon Sprites Update", "Sprites are already up to date!")
        elif status == "error":
            print(f"Sprite update check error: {result.get('error')}")
            if not silent:
                QMessageBox.warning(parent or mw, "Update Error", f"Failed to check for sprite updates:\n{result.get('error')}")

    QueryOp(
        parent=parent or mw,
        op=bg_check,
        success=on_done
    ).without_collection().run_in_background()
