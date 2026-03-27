import os
import json
from aqt import QDialog, QVBoxLayout, QWebEngineView, mw
from PyQt6.QtCore import QUrlQuery
from aqt.qt import Qt, QFile, QUrl, QFrame, QPushButton
from aqt.utils import showInfo


class Pokedex(QDialog):
    def __init__(self, addon_dir, ankimon_tracker):
        super().__init__()
        self.addon_dir = addon_dir
        self.ankimon_tracker = ankimon_tracker
        self.owned_pokemon_ids = ankimon_tracker.owned_pokemon_ids
        self.setWindowTitle("Pokedex - Ankimon")

        # Remove default background to make it transparent
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Set a default size for the dialog
        self.resize(900, 800)  # Width: 900px, Height: 800px

        # Create the layout with no margins
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)  # Remove margins
        self.layout.setSpacing(0)  # Remove spacing between widgets

        # Frame for WebEngineView
        self.frame = QFrame()
        self.frame.setContentsMargins(0, 0, 0, 0)  # Remove margins
        self.frame.setFrameStyle(QFrame.Shape.NoFrame)  # Remove frame border

        self.layout.addWidget(self.frame)
        self.setLayout(self.layout)

        # WebEngineView setup
        self.webview = QWebEngineView()
        self.webview.setContentsMargins(0, 0, 0, 0)  # Remove margins
        self.frame.setLayout(QVBoxLayout())
        self.frame.layout().setContentsMargins(0, 0, 0, 0)  # Remove margins in frame layout
        self.frame.layout().setSpacing(0)  # Remove spacing
        self.frame.layout().addWidget(self.webview)

        # Remove the online/offline buttons since we’re focusing on the local Pokédex
        self.load_html()

    def load_html(self):
        self.ankimon_tracker.get_ids_in_collection()
        self.owned_pokemon_ids = self.ankimon_tracker.owned_pokemon_ids
        #print("POKEDEX_DEBUG: Caught Pokémon IDs:", self.owned_pokemon_ids)

        # Convert caught IDs to string
        str_owned_pokemon_ids = ",".join(map(str, self.owned_pokemon_ids)) if self.owned_pokemon_ids else ""
        #print("POKEDEX_DEBUG: Caught IDs string:", str_owned_pokemon_ids)
        
        db = mw.ankimon_db

        # 1. Total caught count
        total_caught_count = db.get_pokemon_count()
        
        # 2. Defeated count from captured pokemon
        cursor = db.execute("SELECT SUM(CAST(json_extract(data, '$.pokemon_defeated') AS INTEGER)) FROM captured_pokemon")
        defeated_caught = cursor.fetchone()[0] or 0
        
        # 3. Shiny pokemon IDs
        cursor = db.execute("SELECT DISTINCT pokedex_id FROM captured_pokemon WHERE shiny = 1 AND pokedex_id IS NOT NULL")
        shiny_pokemon_ids = [row[0] for row in cursor.fetchall()]
        
        # 4. Released count and defeated count from history
        cursor = db.execute("SELECT COUNT(*), SUM(CAST(json_extract(data, '$.pokemon_defeated') AS INTEGER)) FROM pokemon_history")
        row = cursor.fetchone()
        released_count = row[0] or 0
        defeated_released = row[1] or 0
        
        defeated_count = defeated_caught + defeated_released

        file_path = os.path.join(self.addon_dir, "pokedex", "pokedex.html").replace("\\", "/")
        #print("POKEDEX_DEBUG: Loading HTML from:", file_path)
        url = QUrl.fromLocalFile(file_path)

        query = QUrlQuery()
        query.addQueryItem("numbers", str_owned_pokemon_ids)
        query.addQueryItem("defeated", str(defeated_count))
        # Pass released count separately so HTML can add it correctly
        query.addQueryItem("released", str(released_count))
        # Pass total caught count (instances, not unique IDs) for accurate "Seen" calculation
        query.addQueryItem("caught_total", str(total_caught_count))
        # Add shiny Pokémon IDs
        str_shiny_pokemon_ids = ",".join(map(str, shiny_pokemon_ids)) if shiny_pokemon_ids else ""
        query.addQueryItem("shinies", str_shiny_pokemon_ids)
        url.setQuery(query)
        #print("POKEDEX_DEBUG: Final URL:", url.toString())

        self.webview.setUrl(url)

    def showEvent(self, event):
        self.load_html()
        self.webview.reload()
