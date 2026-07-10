import os
import json
from aqt import QDialog, QVBoxLayout, QWebEngineView
from aqt.qt import Qt, QUrl, QFrame, QWebEngineProfile
from ..services import services


class Ankidex(QDialog):
    def __init__(self, addon_dir, ankimon_tracker):
        """
        Initialize the Ankidex dialog and configure its embedded web frontend.
        
        Parameters:
        	addon_dir: Directory containing the Ankidex frontend files.
        	ankimon_tracker: Tracker used to retrieve Ankidex data.
        """
        super().__init__()
        self.addon_dir = addon_dir
        self.ankimon_tracker = ankimon_tracker
        self.setWindowTitle("Ankidex")

        # Premium feel: larger default size
        # Disabled WA_TranslucentBackground to prevent heavy window-level repaint
        # flickering under Windows DWM when QWebEngineView re-composes or updates.
        # self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.resize(1200, 720)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.frame = QFrame()
        self.frame.setContentsMargins(0, 0, 0, 0)
        self.frame.setFrameStyle(QFrame.Shape.NoFrame)

        self.layout.addWidget(self.frame)
        self.setLayout(self.layout)

        from ..ankimon_items_web.shop_obj import SafeWebEnginePage
        self.profile = QWebEngineProfile()
        self.webview = QWebEngineView()
        self.webview.setPage(SafeWebEnginePage(self.profile, "ankidex_standalone", services.logger, self.webview))

        self.frame.setLayout(QVBoxLayout())
        self.frame.layout().setContentsMargins(0, 0, 0, 0)
        self.frame.layout().addWidget(self.webview)

        # Initial load
        self.webview.loadFinished.connect(self.update_ui_data)
        self.load_initial_html()

    def get_ankidex_data(self):
        """Fetch comprehensive collection data for Ankidex.

        Delegates to the widget-free ``ankidex_data`` builder so the query logic
        does not depend on this QDialog and can be unit-tested / reused without a
        hidden QWebEngineView.
        """
        from .ankidex_data import get_ankidex_data

        return get_ankidex_data(services.db, services.settings, self.ankimon_tracker)

    def load_initial_html(self):
        file_path = os.path.join(self.addon_dir, "ankidex", "ankidex.html").replace(
            "\\", "/"
        )
        url = QUrl.fromLocalFile(file_path)

        # Instead of URL query, we push data via JS
        self.webview.setUrl(url)

    def update_ui_data(self):
        """Pushes data to the frontend."""
        data = self.get_ankidex_data()
        data_js = json.dumps(data)

        js_code = f"if (window.initializeAnkidex) window.initializeAnkidex({data_js});"
        self.webview.page().runJavaScript(js_code)

    def show(self, *args):
        # Removed redundant update_ui_data() as it's called by showEvent()
        super().show()

    def showEvent(self, event):
        # Refresh data when window becomes visible
        self.update_ui_data()
        super().showEvent(event)

    def closeEvent(self, event):
        """Save UI preferences on close."""
        self.save_preferences()
        super().closeEvent(event)

    def save_preferences(self):
        def on_state_ready(state):
            if state and isinstance(state, dict) and services.settings is not None:
                for key, val in state.items():
                    services.settings.set(f"ankidex.{key}", val)

        if self.webview:
            self.webview.page().runJavaScript(
                "if (window.getAnkidexState) window.getAnkidexState();", on_state_ready
            )
