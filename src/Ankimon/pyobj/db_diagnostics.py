import sqlite3
from aqt import mw
from aqt.utils import showInfo, showWarning, askUser

def trigger_database_diagnostics():
    # 1. Safety Guard: Block if cards are being reviewed
    if getattr(mw.reviewer, "card", None) is not None:
        showWarning(
            "Diagnostics Unavailable\n\n"
            "Please finish or exit your current card review session before running database diagnostics to avoid database locks."
        )
        return

    from ..services import services
    db = services.db
    if not db:
        showWarning("Database connection is not available.")
        return

    # Phase 1: Read-only Diagnostics
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        integrity_result = cursor.fetchone()[0]
        is_corrupt = (integrity_result != "ok")

        cursor.execute("""
            SELECT COUNT(*) FROM (
                SELECT individual_id FROM captured_pokemon 
                GROUP BY individual_id HAVING COUNT(*) > 1
            )
        """)
        duplicate_count = cursor.fetchone()[0]

    except Exception as e:
        showWarning(f"Failed to run diagnostics: {e}")
        return

    # Scenario A: Database is completely healthy
    if not is_corrupt and duplicate_count == 0:
        showInfo(
            "Database Integrity: Healthy\n\n"
            "No index corruption or duplicate Pokemon records were detected. Your database is fully healthy!"
        )
        return

    # Scenario B: Issues found, prompt to repair
    issue_desc = ""
    if duplicate_count > 0:
        issue_desc += f"• {duplicate_count} duplicate Pokemon sharing unique IDs\n"
    if is_corrupt:
        issue_desc += f"• Database index pages are malformed/corrupted (integrity status: {integrity_result})\n"

    msg = (
        "Issues Detected in Database\n\n"
        f"We found the following issues in your database:\n{issue_desc}\n"
        "Would you like to repair your database now? This will rebuild indexes and merge duplicates "
        "by keeping the copy with the highest level/XP progress."
    )

    if not askUser(msg):
        return

    # Phase 2: Execute Repair
    try:
        db.repair_database()
        
        # Verify the repair succeeded
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        check_res = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) FROM (
                SELECT individual_id FROM captured_pokemon 
                GROUP BY individual_id HAVING COUNT(*) > 1
            )
        """)
        new_dup_count = cursor.fetchone()[0]
        
        if check_res == "ok" and new_dup_count == 0:
            showInfo(
                "Repair Successful\n\n"
                "Database has been successfully recovered and re-indexed. Duplicates have been pruned.\n\n"
                "Anki will now reload your profile to apply changes."
            )
            # Reload profile to safely reinitialize connections (handles both old and new Anki versions)
            def reload_profile_callback():
                mw.loadProfile()
                
            try:
                mw.unloadProfile(reload_profile_callback)
            except TypeError:
                mw.unloadProfile()
                mw.loadProfile()
        else:
            showWarning(
                "Repair Completed with Warnings\n\n"
                f"The repair script finished but some issues might remain. (Integrity check: {check_res}, duplicates: {new_dup_count})"
            )
    except PermissionError:
        showWarning(
            "Repair Failed: File Locked\n\n"
            "Could not write the repaired database because the file is locked by another program (e.g. OneDrive, Dropbox, or antivirus).\n\n"
            "Please temporarily pause file sync/scanners and try again."
        )
    except Exception as e:
        showWarning(f"Repair process encountered an error: {e}")
