import threading

import requests

from ..services import services


ANKIMON_LEADERBOARD_API_URL = "https://leaderboard-api.ankimon.com/update_stats"

# Outcome of the last completed push ("ok", "http:503", "network", ...). The sync
# is no longer a once-per-session event — TrainerCard.sync_leaderboard() runs it
# from the gameplay write path — so the result is logged on transitions only: the
# first push of a session and every change after it, rather than a line a minute
# for the whole time a player is reviewing.
_last_sync_outcome = None


def sync_data_to_leaderboard(data):
    """Synchronize player statistics with the Ankimon leaderboard server.

    Credentials come from Settings and the HTTP request runs in a daemon thread
    so review-time UI work is never blocked by the network.
    """
    if services.settings is None or not services.settings.get("misc.leaderboard"):
        return

    try:
        username = services.settings.get("leaderboard.username", "")
        api_key = services.settings.get("leaderboard.api_key", "")
        if not username or not api_key:
            print("Ankimon: Leaderboard sync skipped - credentials missing in settings")
            return

        request_data = {
            "username": username,
            "api_key": api_key,
            "stats": data,
        }

        def send_request():
            global _last_sync_outcome
            try:
                response = requests.post(
                    ANKIMON_LEADERBOARD_API_URL,
                    json=request_data,
                    timeout=10,
                )
                if response.status_code == 200:
                    outcome = "ok"
                    message = "Ankimon: Data synced to leaderboard successfully"
                else:
                    outcome = f"http:{response.status_code}"
                    message = (
                        "Ankimon: Failed to sync data - "
                        f"Status: {response.status_code}"
                    )
            except requests.exceptions.RequestException as e:
                outcome = "network"
                message = f"Ankimon: Leaderboard sync network error: {e}"
            except Exception as e:
                outcome = "error"
                message = f"Ankimon: Unexpected leaderboard error: {e}"

            if outcome != _last_sync_outcome:
                _last_sync_outcome = outcome
                print(message)

        threading.Thread(target=send_request, daemon=True).start()
    except Exception as e:
        print(f"Ankimon: Unexpected error preparing leaderboard sync: {e}")


def migrate_credentials_from_db() -> bool:
    """Atomically move legacy leaderboard credentials into Settings.

    Existing Settings values win per field. The database transaction stores any
    missing replacements and retires the corresponding legacy rows together, so
    a write failure cannot destroy the user's original credentials.
    """
    if services.db is None or services.settings is None:
        return False

    migrated = services.db.migrate_user_data_to_config(
        {
            "username": "leaderboard.username",
            "api_key": "leaderboard.api_key",
        }
    )
    if not migrated:
        return False

    # The DB transaction has already persisted these values. Update the live
    # Settings object in place without issuing a second, non-atomic write.
    services.settings.config.update(migrated)
    services.settings.compute_gui_config()
    print("Ankimon: Migrated and cleared legacy leaderboard credentials")
    return True
