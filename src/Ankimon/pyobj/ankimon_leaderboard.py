import sys
import json
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton
from aqt.utils import showInfo
from ..resources import user_path_credentials, mypokemon_path
import json
import requests
from aqt import mw # import setting values direct from init file

#ANKIMON_LEADERBOARD_API_URL = "https://ankimon.com/api/leaderboard"  # Replace with the actual API URL
ANKIMON_LEADERBOARD_API_URL = "https://leaderboard-api.ankimon.com/update_stats"  # Replace with the actual API URL

class ApiKeyDialog(QDialog):
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
        username = self.username_input.text()
        api_key = self.api_key_input.text()

        if username and api_key:
            credentials = {
                "username": username,
                "api_key": api_key
            }
            self.save_credentials(credentials)
            self.accept()  # Close the dialog if everything is entered
        else:
            showInfo("Both fields must be filled out.")

    def save_credentials(self, credentials):
        try:
            # Save the new credentials to the database
            for key, value in credentials.items():
                mw.ankimon_db.set_user_data(key, value)
            showInfo("Credentials saved successfully!")
        except Exception as e:
            showInfo(f"Error saving credentials: {e}")

def sync_data_to_leaderboard(data):

        # First check if leaderboard is enabled in config
        if not mw.settings_obj.get("misc.leaderboard"):
            return

        try:
            # Load credentials from the database
            username = mw.ankimon_db.get_user_data("username")
            api_key = mw.ankimon_db.get_user_data("api_key")

            # Validate credentials
            if not username or not api_key:
                showInfo("Error: Missing credentials for Ankimon leaderboard. Please set up leaderboard from Ankimon menu or turn off in Settings.")
                return


            # Check if both username and api_key are available
            if username and api_key:
                request_data = {
                    "username": username,
                    "api_key": api_key,
                    "stats": data
                }

                # Send a POST request to the leaderboard API
                response = requests.post(
                    ANKIMON_LEADERBOARD_API_URL,
                    json=request_data
                )

                #showInfo(response.text)  # Show the response text for debugging

                # Check if the request was successful
                #if response.status_code == 200:
                #    mw.logger.log("log","Data synced successfully to leaderboard!")
                #else:
                #    mw.logger.log("log",f"Failed to sync data to leaderboard. Status code: {response.status_code}")
            #else:
                #mw.logger.log("Credentials are missing (username or api_key)")

        except requests.exceptions.RequestException as e:
            showInfo(f"Error: Missing credentials for Ankimon leaderboard. Please set up leaderboard from Ankimon menu or turn off in Settings.\n\n {e}")
        except Exception as e:
            showInfo(f"Error: Missing credentials for Ankimon leaderboard. Please set up leaderboard from Ankimon menu or turn off in Settings.\n\n {e}")



def show_api_key_dialog():
    dialog = ApiKeyDialog()  # Create the dialog instance
    dialog.exec()  # Show the dialog

