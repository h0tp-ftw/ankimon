import os
import shutil
from datetime import datetime
import json
from aqt.utils import showInfo
from ..services import services
from ..resources import mypokemon_path, mainpokemon_path, itembag_path, badgebag_path, user_path_credentials, backup_root, user_path
# Define backup directory and files to back up
backup_folders = [os.path.join(backup_root, f"backup_{i}") for i in range(1, 4)]
files_to_backup = [mypokemon_path, mainpokemon_path, itembag_path, badgebag_path, user_path_credentials, user_path / "ankimon.db", user_path / "ankimonDEV.db"]  # Adjust as needed

def create_backup_folder(folder_path):
    """Creates a backup folder and places a timestamped text file inside."""
    # Checkpoint the active database first so WAL frames are flushed to the
    # main DB file before the single-file copy below.
    if services.db is not None:
        try:
            services.db.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception:
            pass

    os.makedirs(folder_path, exist_ok=True)

    # Create a timestamp file
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(folder_path, "backup_info.txt"), "w") as f:
        f.write(f"Backup created on: {timestamp}")

    # Copy the files into the new backup folder
    for file in files_to_backup:
        if os.path.exists(file):
            shutil.copy(file, folder_path)

def rotate_backups():
    """Manages the rolling backup system (backup_3 is deleted, 2 becomes 3, etc.)."""
    try:
        if os.path.exists(backup_folders[-1]):
            shutil.rmtree(backup_folders[-1])  # Delete oldest backup

        # Shift backups (backup_2 → backup_3, backup_1 → backup_2)
        for i in range(len(backup_folders) - 1, 0, -1):
            if os.path.exists(backup_folders[i - 1]):
                shutil.move(backup_folders[i - 1], backup_folders[i])
    except Exception as e:
        services.logger.log("error", f"Failed to rotate backups: {e}")
        pass

def is_backup_needed():
    """Checks if a new backup is required (every two weeks)."""
    if not os.path.exists(backup_folders[0]):
        return True  # No backups exist, so we need one

    with open(os.path.join(backup_folders[0], "backup_info.txt"), "r") as f:
        date_str = f.readline().replace("Backup created on: ", "").strip()
        last_backup_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

    return (datetime.now() - last_backup_date).days >= 14  # Check if 2 weeks have passed

def run_backup():
    """Main function to run the backup process."""
    if is_backup_needed():
        try:
            rotate_backups()
            create_backup_folder(backup_folders[0])
            services.logger.log("game","New backup created successfully.")
        except Exception as e:
            services.logger.log("error", f"Failed to create new backup: {e}")
    else:
        services.logger.log("game","No backup needed yet.")
