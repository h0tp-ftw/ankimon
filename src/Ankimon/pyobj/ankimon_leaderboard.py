import sys
import json
import threading
import requests
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton
from aqt.utils import showInfo
from aqt.qt import QMessageBox
from ..resources import user_path_credentials, mypokemon_path
from aqt import mw
from ..services import services

ANKIMON_LEADERBOARD_API_URL = "https://leaderboard-api.ankimon.com/update_stats"


class ApiKeyDialog(QDialog):
    """Legacy dialog - kept for backward compatibility but deprecated."""
    
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Enter API Key and Username")
        self.setGeometry(100, 100, 300, 200)

        # Layout
        layout = QVBoxLayout()

        # Username input
        self.username_label = QLabel("Username:")
        self.username_input = QLineEdit(self)
        self.username_input.setPlaceholderText("Enter your username")
        layout.addWidget(self.username_label)
        layout.addWidget(self.username_input)

        # API Key input
        self.api_key_label = QLabel("API Key:")
        self.api_key_input = QLineEdit(self)
        self.api_key_input.setPlaceholderText("Paste your API key")
        layout.addWidget(self.api_key_label)
        layout.addWidget(self.api_key_input)

        # Submit button
        self.submit_button = QPushButton("Submit", self)
        self.submit_button.clicked.connect(self.submit)
        layout.addWidget(self.submit_button)

        # Set layout
        self.setLayout(layout)

    def submit(self):
        """
        Handle the submission of username and API key from the legacy dialog.
        
        Validates that both fields are filled, then saves the credentials to
        the settings system rather than the legacy database. Enables leaderboard
        sync automatically upon successful credential storage.
        
        Returns:
            None: Displays appropriate info messages to the user via showInfo.
        """
        username = self.username_input.text()
        api_key = self.api_key_input.text()

        if username and api_key:
            # Save to settings instead of database
            if services.settings is not None:
                services.settings.set("leaderboard.username", username)
                services.settings.set("leaderboard.api_key", api_key)
                services.settings.set("misc.leaderboard", True)
                showInfo("Credentials saved successfully!")
                self.accept()
            else:
                showInfo("Error: Settings not initialized.")
        else:
            showInfo("Both fields must be filled out.")


def sync_data_to_leaderboard(data):
    """
    Synchronize player statistics with the Ankimon leaderboard server.
    
    This function retrieves leaderboard credentials from the settings system
    and sends a POST request to the leaderboard API with the provided stats.
    The network request is executed in a background thread to prevent UI
    freezing during network operations.
    
    Args:
        data (dict): A dictionary containing the player's statistics to be
            synchronized with the leaderboard. The structure should match
            what the leaderboard API expects.
    
    Returns:
        None: Results are logged to console; failures are handled silently
            with console output rather than user-facing dialogs.
    
    Note:
        - The function exits early if leaderboard sync is disabled in settings
        - Credentials are read from settings (not the legacy database)
        - Network timeouts are set to 10 seconds
        - All exceptions are caught and logged to console
    """
    
    # First check if leaderboard is enabled in config
    if services.settings is None or not services.settings.get("misc.leaderboard"):
        return

    try:
        # Get credentials from settings (NOT from database)
        username = services.settings.get("leaderboard.username", "")
        api_key = services.settings.get("leaderboard.api_key", "")

        # Validate credentials
        if not username or not api_key:
            # Silent fail - user will be notified through settings UI
            print("Ankimon: Leaderboard credentials missing in settings")
            return

        request_data = {
            "username": username,
            "api_key": api_key,
            "stats": data
        }

        def send_request():
            """
            Send the network request to the leaderboard API.
            
            This inner function executes the actual HTTP POST request and handles
            any network-related exceptions. It runs in a background thread to
            avoid blocking the main GUI thread.
            
            Returns:
                None: Results and errors are logged to console.
            """
            try:
                # Send POST request to leaderboard API
                response = requests.post(
                    ANKIMON_LEADERBOARD_API_URL,
                    json=request_data,
                    timeout=10  # Add timeout to prevent hanging
                )

                if response.status_code == 200:
                    print("Ankimon: Data synced to leaderboard successfully")
                else:
                    print(f"Ankimon: Failed to sync data - Status: {response.status_code}")
                    
            except requests.exceptions.RequestException as e:
                print(f"Ankimon: Leaderboard sync network error: {e}")
            except Exception as e:
                print(f"Ankimon: Unexpected leaderboard error: {e}")

        # Offload the network request to a background thread to prevent UI freezing
        threading.Thread(target=send_request, daemon=True).start()

    except Exception as e:
        print(f"Ankimon: Unexpected error preparing leaderboard sync: {e}")


def show_api_key_dialog():
    """
    Display a legacy dialog informing users about the new credential location.
    
    This function is a deprecated method that now shows an informational
    message directing users to the new Settings UI for managing leaderboard
    credentials. The legacy dialog is kept for backward compatibility but
    no longer collects credentials directly.
    
    Returns:
        None: Displays a QMessageBox with navigation instructions.
    
    Note:
        This method is deprecated; credentials should be managed through
        Ankimon → Ankimon Settings → Leaderboard.
    """
    # Show a dialog telling users where to find the new settings
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Icon.Information)
    msg.setWindowTitle("Leaderboard Credentials Moved")
    msg.setText(
        "Leaderboard credentials are now managed in Ankimon Settings.\n\n"
        "Please go to:\n"
        "Ankimon → Ankimon Settings → Leaderboard\n\n"
        "Enter your username and API key there."
    )
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg.exec()


def migrate_credentials_from_db():
    """
    Migrate leaderboard credentials from the legacy database to the settings system.
    
    This function performs a one-time migration of username and API key from
    the old database storage to the new settings-based storage system. It
    checks if credentials exist in the database and whether they have already
    been migrated to settings to avoid overwriting existing settings data.
    
    The migration runs during Anki startup and ensures a smooth transition
    for users who previously configured leaderboard credentials using the
    legacy system.
    
    Returns:
        None: Migration status is logged to console; failures are caught
            and logged without interrupting the startup process.
    
    Note:
        - Function exits early if database or settings services are unavailable
        - Only migrates if database has credentials AND settings don't
        - Preserves the leaderboard enabled/disabled state
    """
    if services.db is None or services.settings is None:
        return
    
    try:
        # Check if we have credentials in database
        username = services.db.get_user_data("username")
        api_key = services.db.get_user_data("api_key")
        
        # Check if we have them in settings already
        settings_username = services.settings.get("leaderboard.username", "")
        settings_api_key = services.settings.get("leaderboard.api_key", "")
        
        # If db has credentials but settings don't, migrate them
        if username and api_key and (not settings_username or not settings_api_key):
            services.settings.set("leaderboard.username", username)
            services.settings.set("leaderboard.api_key", api_key)
            
            # If leaderboard was enabled in old system, enable it in new system
            if services.settings.get("misc.leaderboard", False):
                services.settings.set("misc.leaderboard", True)
                
            print("Ankimon: Migrated leaderboard credentials from database to settings")
            
    except Exception as e:
        print(f"Ankimon: Error migrating leaderboard credentials: {e}")
