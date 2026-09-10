from aqt import mw
from aqt.utils import showInfo, showWarning, askUser


def _count_invalid_base_stats(db, cursor) -> int:
    """Count records rejected by the shared base-stat validator."""
    from ..functions.pokedex_functions import is_valid_base_stats

    cursor.execute("SELECT data FROM captured_pokemon")
    invalid_count = 0
    for row in cursor.fetchall():
        pokemon_data = db._deobfuscate(row[0])
        if not pokemon_data or not is_valid_base_stats(
            pokemon_data.get("base_stats")
        ):
            invalid_count += 1
    return invalid_count


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

    import os
    
    def run_check(col):
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

        missing_base_stats_count = _count_invalid_base_stats(db, cursor)
        
        return integrity_result, is_corrupt, duplicate_count, missing_base_stats_count

    def on_check_done(res):
        integrity_result, is_corrupt, duplicate_count, missing_base_stats_count = res
        
        # Scenario A: Database is completely healthy
        if not is_corrupt and duplicate_count == 0 and missing_base_stats_count == 0:
            showInfo(
                "Database Integrity: Healthy\n\n"
                "No index corruption, duplicate Pokemon, or legacy schema records were detected. Your database is fully healthy!"
            )
            return

        # Scenario B: Issues found, prompt to repair
        issue_desc = ""
        if duplicate_count > 0:
            issue_desc += f"• {duplicate_count} duplicate Pokemon sharing unique IDs\n"
        if missing_base_stats_count > 0:
            issue_desc += f"• {missing_base_stats_count} Pokémon missing base stats information (legacy database format)\n"
        if is_corrupt:
            issue_desc += f"• Database index pages are malformed/corrupted (integrity status: {integrity_result})\n"

        msg = (
            "Issues Detected in Database\n\n"
            f"We found the following issues in your database:\n{issue_desc}\n"
            "Would you like to repair your database now? This will rebuild indexes, normalize legacy Pokémon records, and merge duplicates "
            "by keeping the copy with the highest level/XP progress."
        )

        if not askUser(msg):
            return

        def run_repair(col):
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
            
            new_missing_stats_count = _count_invalid_base_stats(db, cursor)
            return check_res, new_dup_count, new_missing_stats_count

        def on_repair_done(repair_res):
            check_res, new_dup_count, new_missing_stats_count = repair_res
            if check_res == "ok" and new_dup_count == 0 and new_missing_stats_count == 0:
                showInfo(
                    "Repair Successful\n\n"
                    "Database has been successfully recovered, re-indexed, and normalized. Duplicates have been pruned.\n\n"
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
                    f"The repair script finished but some issues might remain. (Integrity check: {check_res}, duplicates: {new_dup_count}, unresolved base stats: {new_missing_stats_count})"
                )

        if "PYTEST_CURRENT_TEST" in os.environ:
            try:
                res = run_repair(None)
                on_repair_done(res)
            except PermissionError:
                showWarning(
                    "Repair Failed: File Locked\n\n"
                    "Could not write the repaired database because the file is locked by another program.\n\n"
                    "Please temporarily pause file sync/scanners and try again."
                )
            except Exception as e:
                showWarning(f"Repair process encountered an error: {e}")
        else:
            from aqt.operations import QueryOp
            QueryOp(
                parent=mw,
                op=run_repair,
                success=on_repair_done
            ).without_collection().run_in_background()

    if "PYTEST_CURRENT_TEST" in os.environ:
        try:
            res = run_check(None)
            on_check_done(res)
        except Exception as e:
            showWarning(f"Failed to run diagnostics: {e}")
    else:
        from aqt.operations import QueryOp
        QueryOp(
            parent=mw,
            op=run_check,
            success=on_check_done
        ).without_collection().run_in_background()
