import os
import orjson
from aqt import QDialog, QVBoxLayout, QWebEngineView, mw
from aqt.qt import QPushButton, QCheckBox, QFrame, Qt
from PyQt6.QtCore import QUrl, QUrlQuery
from PyQt6.QtGui import QGuiApplication
from pathlib import Path

class AchievementsDialog(QDialog):
    def __init__(self, addon_dir, data_handler):
        super().__init__()
        self.addon_dir = addon_dir
        self.data_handler = data_handler
        self.setWindowTitle("Achievements & Badges")

        screen = QGuiApplication.primaryScreen()
        avail_geom = screen.availableGeometry()
        avail_height = avail_geom.height()
        target_height = min(900, avail_height)
        self.setMinimumSize(800, target_height)

        # Remove window frame (commented out for now as it might cause issues)
        # self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)  # Remove layout margins
        self.setLayout(self.layout)

        self.webview = QWebEngineView()
        self.layout.addWidget(self.webview)

        self.load_html()

    def load_html(self):
        # Load badge definitions
        badges_path = self.addon_dir / "addon_files" / "badges.json"
        with open(badges_path, "rb") as f:
            badge_definitions = orjson.loads(f.read())

        # Load user's unlocked badges
        unlocked_badges = getattr(self.data_handler, "badges", [])

        # Construct absolute path to HTML file
        html_path = self.addon_dir / "achievements" / "achievements.html"

        # Create URL with proper encoding
        url = QUrl.fromLocalFile(html_path.as_posix())

        # Create and encode query parameters
        query = QUrlQuery()
        query.addQueryItem("addon_name", mw.addonManager.addonFromModule(__name__))
        query.addQueryItem(
            "unlocked_badges",
            orjson.dumps(unlocked_badges).decode('utf-8')
        )
        query.addQueryItem(
            "badge_definitions",
            orjson.dumps(badge_definitions).decode('utf-8')
        )

        url.setQuery(query.query(QUrl.ComponentFormattingOption.FullyEncoded))

        self.webview.setUrl(url)