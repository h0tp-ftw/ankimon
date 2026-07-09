import json
import uuid
from typing import Any, Callable

from aqt import mw, gui_hooks
from aqt.qt import (
    Qt,
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QGridLayout,
    QPixmap,
)

from aqt.theme import theme_manager  # Check if light / dark mode in Anki

from PyQt6.QtWidgets import (
    QLineEdit,
    QComboBox,
    QCheckBox,
    QMenu,
    QWidget,
    QScrollArea,
    QFrame,
    QRadioButton,
    QButtonGroup,
    QStyle,
)
from PyQt6.QtCore import QSize, pyqtSignal, QTimer, QByteArray
from PyQt6.QtGui import QIcon, QFont, QAction, QMovie, QCloseEvent, QResizeEvent

from ..services import services
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.reviewer_obj import Reviewer_Manager
from ..pyobj.test_window import TestWindow
from ..pyobj.translator import Translator
from ..pyobj.collection_dialog import MainPokemon
from ..gui_classes.pokemon_details import PokemonCollectionDetailsSplit, remember_attack
from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.move_picker import MovePickerDialog
from ..pyobj.evolution_window import EvoWindow

from ..pyobj.settings import Settings
from ..functions.friendship_evolution import current_time_label, evolution_readiness
from ..functions.sprite_functions import get_sprite_path
from ..utils import (
    load_custom_font,
    get_tier_by_id,
    is_alive,
    format_move_name,
    format_pokemon_name,
)
from ..resources import (
    icon_path,
    items_path,
    csv_file_items_cost,
    poke_evo_path,
    pokemon_tm_learnset_path,
    addon_dir,
)
from ..business import calculate_cp_from_dict
from ..functions.pokedex_functions import (
    find_details_move,
    get_all_pokemon_moves,
    format_lore_name,
    get_pretty_name_for_name,
    search_pokedex_by_id,
)
from ..functions.gui_functions import type_icon_path, move_category_path

MOVE_TYPE_COLORS = {
    "Normal": "#A8A878",
    "Fire": "#F08030",
    "Water": "#6890F0",
    "Electric": "#F8D030",
    "Grass": "#78C850",
    "Ice": "#98D8D8",
    "Fighting": "#C03028",
    "Poison": "#A040A0",
    "Ground": "#E0C068",
    "Flying": "#A890F0",
    "Psychic": "#F85888",
    "Bug": "#A8B820",
    "Rock": "#B8A038",
    "Ghost": "#705898",
    "Dragon": "#7038F8",
    "Dark": "#705848",
    "Steel": "#B8B8D0",
    "Fairy": "#EE99AC",
}

NATURE_EFFECTS = {
    "Hardy": ("", ""),
    "Docile": ("", ""),
    "Serious": ("", ""),
    "Bashful": ("", ""),
    "Quirky": ("", ""),  # neutral natures
    "Lonely": ("attack", "defense"),
    "Brave": ("attack", "speed"),
    "Adamant": ("attack", "special-attack"),
    "Naughty": ("attack", "special-defense"),
    "Bold": ("defense", "attack"),
    "Relaxed": ("defense", "speed"),
    "Impish": ("defense", "special-attack"),
    "Lax": ("defense", "special-defense"),
    "Timid": ("speed", "attack"),
    "Hasty": ("speed", "defense"),
    "Jolly": ("speed", "special-attack"),
    "Naive": ("speed", "special-defense"),
    "Modest": ("special-attack", "attack"),
    "Mild": ("special-attack", "defense"),
    "Quiet": ("special-attack", "speed"),
    "Rash": ("special-attack", "special-defense"),
    "Calm": ("special-defense", "attack"),
    "Gentle": ("special-defense", "defense"),
    "Sassy": ("special-defense", "speed"),
    "Careful": ("special-defense", "special-attack"),
}

STAT_KEY_TO_DISPLAY = {
    "hp": "HP",
    "attack": "Attack",
    "defense": "Defense",
    "special-attack": "Sp. Atk",
    "special-defense": "Sp. Def",
    "speed": "Speed",
}
DISPLAY_TO_STAT_KEY = {v: k for k, v in STAT_KEY_TO_DISPLAY.items()}

from PyQt6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QTabWidget,
)
from PyQt6.QtGui import QColor


class MoveManagerWidget(QWidget):
    def __init__(self, individual_id, pkmn_id, logger, save_fn, parent=None):
        super().__init__(parent)
        self.individual_id = individual_id
        self.pkmn_id = pkmn_id
        self.logger = logger
        self.save_fn = save_fn
        self.full_data = None
        self.setup_ui()
        self.refresh()

    def setup_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(6)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.header = QLabel("MOVES")
        self.header.setStyleSheet(
            "color: #94a3b8; font-size: 10px; font-weight: bold; letter-spacing: 1px;"
        )
        self.layout.addWidget(self.header)

        self.slots = []
        for i in range(4):
            slot_container = QWidget()
            slot_layout = QHBoxLayout(slot_container)
            slot_layout.setContentsMargins(0, 2, 0, 2)

            type_chip = QLabel("---")
            type_chip.setFixedSize(42, 18)
            type_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            type_chip.setStyleSheet(
                "border-radius: 4px; font-size: 9px; font-weight: bold; color: white;"
            )

            name_label = QPushButton("— empty —")
            name_label.setFlat(True)
            name_label.setCursor(Qt.CursorShape.PointingHandCursor)
            name_label.setStyleSheet("""
                QPushButton { 
                    text-align: left; 
                    font-size: 12px; 
                    color: #475569; 
                    font-style: italic; 
                    border: none; 
                    padding: 4px 8px; 
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #1e293b;
                }
            """)

            forget_btn = QPushButton("✕")
            forget_btn.setFixedSize(24, 24)
            forget_btn.setFlat(True)

            learn_btn = QPushButton("＋")
            learn_btn.setFixedSize(24, 24)
            learn_btn.setFlat(True)

            slot_layout.addWidget(type_chip)
            slot_layout.addWidget(name_label)
            slot_layout.addStretch()
            slot_layout.addWidget(forget_btn)
            slot_layout.addWidget(learn_btn)

            self.layout.addWidget(slot_container)
            self.slots.append(
                {
                    "container": slot_container,
                    "layout": slot_layout,
                    "type_chip": type_chip,
                    "name_label": name_label,
                    "forget_btn": forget_btn,
                    "learn_btn": learn_btn,
                    "move": None,
                }
            )

            forget_btn.clicked.connect(
                lambda checked, idx=i: self.on_forget_clicked(idx)
            )
            learn_btn.clicked.connect(lambda checked, idx=i: self.on_learn_clicked(idx))
            name_label.clicked.connect(
                lambda checked, idx=i: self.on_learn_clicked(idx)
            )

        # Add TM button
        self.tm_btn = QPushButton(" LEARN FROM TMs")
        tm_icon_path = items_path / "Bag_TM_normal_SV_Sprite.png"
        if tm_icon_path.exists():
            self.tm_btn.setIcon(QIcon(str(tm_icon_path)))
            self.tm_btn.setIconSize(QSize(18, 18))

        self.tm_btn.setMinimumHeight(32)
        self.tm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tm_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2563eb, stop:1 #1d4ed8);
                color: #f8fafc;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 0.5px;
                border: 1px solid #1e40af;
                border-radius: 6px;
                margin-top: 12px;
                padding: 4px 8px;
                text-align: center;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3b82f6, stop:1 #2563eb);
                border-color: #3b82f6;
                margin-top: 12px;
                padding: 4px 8px;
                text-align: center;
            }
            QPushButton:pressed {
                background: #1e40af;
                margin-top: 12px;
                padding: 5px 8px 3px 8px;
                text-align: center;
            }
        """)
        self.tm_btn.clicked.connect(self.on_tm_clicked)
        self.layout.addWidget(self.tm_btn)

    def _fetch_full(self, individual_id):
        try:
            cursor = services.db.execute(
                "SELECT data FROM captured_pokemon WHERE individual_id = ?",
                (individual_id,),
            )
            row = cursor.fetchone()
            if row:
                data = json.loads(row[0])
                # Ensure all_attacks is fetched
                if "all_attacks" not in data:
                    from ..functions.pokedex_functions import get_all_pokemon_moves

                    data["all_attacks"] = get_all_pokemon_moves(
                        data.get("name"), data.get("level")
                    )
                return data
        except Exception:
            pass
        return {}

    def refresh(self):
        self.full_data = self._fetch_full(self.individual_id)
        attacks = self.full_data.get("attacks", [])
        all_moves = self.full_data.get("all_attacks", []) or self.full_data.get(
            "learnable_moves", []
        )

        for i in range(4):
            slot = self.slots[i]
            if i < len(attacks):
                move = attacks[i]
                slot["move"] = move
                slot["name_label"].setText(format_move_name(move))
                slot["name_label"].setStyleSheet("""
                    QPushButton { 
                        text-align: left; 
                        font-size: 12px; 
                        color: #e6edf3; 
                        font-weight: 600; 
                        border: none; 
                        padding: 4px 8px; 
                        border-radius: 4px;
                    } 
                    QPushButton:hover { 
                        color: #60a5fa; 
                        background-color: #1e293b;
                    }
                """)
                slot["forget_btn"].setVisible(len(attacks) > 1)
                slot["learn_btn"].setText("⤵")
                slot["learn_btn"].setVisible(bool(all_moves))

                move_data = find_details_move(move)
                m_type = move_data.get("type", "Normal")
                color = MOVE_TYPE_COLORS.get(m_type, "#777")
                slot["type_chip"].setText(m_type.upper())
                slot["type_chip"].setStyleSheet(
                    f"background-color: {color}; border-radius: 4px; font-size: 9px; font-weight: bold; color: white;"
                )
                slot["type_chip"].show()
            else:
                slot["move"] = None
                slot["name_label"].setText("— empty —")
                slot["name_label"].setStyleSheet(
                    "QPushButton { text-align: left; font-size: 12px; color: #475569; font-style: italic; border: none; padding: 0; }"
                )
                slot["forget_btn"].hide()
                slot["learn_btn"].setText("＋")
                slot["learn_btn"].setVisible(bool(all_moves))
                slot["type_chip"].hide()

        if not all_moves:
            if not hasattr(self, "no_moves_label"):
                self.no_moves_label = QLabel("No learnable moves data")
                self.no_moves_label.setStyleSheet(
                    "color: #64748b; font-size: 11px; font-style: italic;"
                )
                self.layout.addWidget(self.no_moves_label)
        elif hasattr(self, "no_moves_label"):
            self.no_moves_label.setParent(None)
            del self.no_moves_label

    def on_forget_clicked(self, idx):
        move = self.slots[idx]["move"]
        if not move:
            return

        # Confirmation UI
        slot_container = self.slots[idx]["container"]
        old_layout = self.slots[idx]["layout"]

        conf_widget = QWidget()
        conf_layout = QHBoxLayout(conf_widget)
        conf_layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel(f"Forget {move.capitalize()}?")
        label.setStyleSheet("font-size: 11px; font-weight: bold;")

        yes_btn = QPushButton("Yes")
        yes_btn.setFixedSize(40, 22)
        yes_btn.setStyleSheet("font-size: 10px;")

        no_btn = QPushButton("No")
        no_btn.setFixedSize(40, 22)
        no_btn.setStyleSheet("font-size: 10px;")

        conf_layout.addWidget(label)
        conf_layout.addStretch()
        conf_layout.addWidget(yes_btn)
        conf_layout.addWidget(no_btn)

        # Swap layouts
        slot_container.hide()
        self.layout.insertWidget(idx + 1, conf_widget)  # +1 because of header

        def on_yes():
            attacks = self.full_data.get("attacks", [])
            if move in attacks:
                attacks.remove(move)
                self.full_data["attacks"] = attacks
                self.save_fn(self.full_data)
                self.refresh()
            conf_widget.setParent(None)
            slot_container.show()

        def on_no():
            conf_widget.setParent(None)
            slot_container.show()

        yes_btn.clicked.connect(on_yes)
        no_btn.clicked.connect(on_no)

    def on_learn_clicked(self, idx):
        all_moves = self.full_data.get("all_attacks", []) or self.full_data.get(
            "learnable_moves", []
        )
        current_moves = self.full_data.get("attacks", [])

        nickname = self.full_data.get("nickname")
        raw_name = self.full_data.get("name")
        species_name = get_pretty_name_for_name(raw_name)

        def normalize_n(s):
            if not s:
                return ""
            return "".join(c for c in str(s).lower() if c.isalnum())

        is_redundant = (
            not nickname
            or not str(nickname).strip()
            or normalize_n(nickname) == normalize_n(species_name)
            or normalize_n(nickname) == normalize_n(raw_name)
        )

        pretty_name = species_name if is_redundant else f"{nickname} ({species_name})"

        dialog = MovePickerDialog(
            pretty_name, all_moves, current_moves, self, force_show_current=True
        )
        if dialog.exec():
            new_move = dialog.get_selected_move()
            if new_move:
                attacks = self.full_data.get("attacks", [])
                if idx < len(attacks):
                    attacks[idx] = new_move
                else:
                    attacks.append(new_move)
                self.full_data["attacks"] = attacks
                self.save_fn(self.full_data)
                self.refresh()

    def on_tm_clicked(self):
        # 1. Get species/base name for TM lookup
        internal_name = search_pokedex_by_id(self.pkmn_id)
        # search_pokedex_by_id returns the sentinel "Pokémon not found" (a truthy
        # string), not None/"", when the id is missing — guard against it explicitly.
        if not internal_name or internal_name == "Pokémon not found":
            self.logger.log_and_showinfo(
                "error", f"Could not find Pokémon data for ID: {self.pkmn_id}"
            )
            return

        # Normalize: strip hyphens and everything after the first hyphen to try base species
        # e.g. "venusaur-mega" -> "venusaur"
        base_name = internal_name.split("-")[0].lower()
        internal_name = internal_name.lower()

        # 2. Load TM learnsets
        try:
            with open(pokemon_tm_learnset_path, "r", encoding="utf-8") as f:
                tm_learnsets = json.load(f)
        except Exception as e:
            self.logger.log_and_showinfo("error", f"Failed to load TM learnsets: {e}")
            return

        # 3. Get valid TMs for this species (check specific form then base species)
        valid_tms = tm_learnsets.get(internal_name) or tm_learnsets.get(base_name)
        if not valid_tms:
            self.logger.log_and_showinfo(
                "info", f"This Pokémon cannot learn any moves from TMs."
            )
            return

        # 4. Get owned TMs from DB
        all_items = services.db.get_all_items()
        owned_tm_moves = [
            item["item_name"]
            for item in all_items
            if (item.get("extra_data") or {}).get("type") == "TM"
        ]

        # 5. Filter valid TMs by ownership
        learnable_tm_moves = [move for move in valid_tms if move in owned_tm_moves]
        if not learnable_tm_moves:
            self.logger.log_and_showinfo(
                "info", "You don't own any TMs that this Pokémon can learn."
            )
            return

        # 6. UI: Use MovePickerDialog
        nickname = self.full_data.get("nickname")
        raw_name = self.full_data.get("name")
        species_name = get_pretty_name_for_name(raw_name)

        def normalize_n(s):
            if not s:
                return ""
            return "".join(c for c in str(s).lower() if c.isalnum())

        is_redundant = (
            not nickname
            or not str(nickname).strip()
            or normalize_n(nickname) == normalize_n(species_name)
            or normalize_n(nickname) == normalize_n(raw_name)
        )

        pretty_name = species_name if is_redundant else f"{nickname} ({species_name})"
        title = f"TM Learning: {pretty_name}"

        current_moves = self.full_data.get("attacks", [])
        dialog = MovePickerDialog(title, learnable_tm_moves, current_moves, self)
        dialog.setWindowTitle("Learn from TMs")

        if dialog.exec():
            new_move = dialog.get_selected_move()
            if new_move:
                self.learn_move_from_tm(new_move)

    def learn_move_from_tm(self, move_name):
        remember_attack(
            self.individual_id,
            self.full_data.get("attacks", []),
            move_name,
            self.logger,
            refresh_callback=lambda: self.save_fn(
                services.db.get_pokemon(self.individual_id)
            ),
        )


def format_item_name(item_name: str) -> str:
    return item_name.replace("-", " ").title()


def clear_layout(layout):
    """
    Recursively removes all widgets and nested layouts from a given layout.

    This function iterates through all items in the provided layout, removes
    each widget or sub-layout, and ensures proper deletion and memory cleanup.

    Args:
        layout (QLayout): The layout to be cleared. Can contain widgets and/or nested layouts.
    """
    if not is_alive(layout):
        return
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def _refresh_open_item_windows():
    """Refresh the item/bag window and the web shell if they are already open.

    The item window and the unified web shell live only in ``singletons``' own
    caches — they are never assigned onto ``mw``. The previous
    ``hasattr(mw, "item_window")`` / ``hasattr(mw, "items_web_window")`` guards
    were therefore permanently dead. This peeks the singletons caches directly
    (never constructing a window just to refresh it), mirroring
    ``singletons.swap_ankimon_account``.
    """
    from .. import singletons

    item_win = singletons._WINDOW_CACHE.get("item_window")
    if is_alive(item_win):
        item_win.renewWidgets()

    web_win = singletons._items_web_window
    if is_alive(web_win) and web_win.isVisible():
        web_win.update_ui_data()


class PokemonSlotButton(QPushButton):
    rightClicked = pyqtSignal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit()
        super().mouseReleaseEvent(event)


class ScaledMovieLabel(QLabel):
    def __init__(self, gif_path, width, height, parent=None):
        super().__init__(parent)
        self.target_width = width
        self.target_height = height
        self.movie = QMovie(gif_path, parent=self)
        self.movie.frameChanged.connect(self.on_frame_changed)
        self.setFixedSize(width, height)

    def showEvent(self, event):
        super().showEvent(event)
        self.movie.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.movie.stop()

    def on_frame_changed(self, frame_number):
        # Get current frame pixmap
        pixmap = self.movie.currentPixmap()

        # Scale pixmap to target size (keep aspect ratio if you want)
        scaled_pixmap = pixmap.scaled(
            self.target_width,
            self.target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        self.setPixmap(scaled_pixmap)


class PokemonPC(QDialog):
    def __init__(
        self,
        logger: ShowInfoLogger,
        translator: Translator,
        reviewer_obj: Reviewer_Manager,
        test_window: TestWindow,
        settings: Settings,
        main_pokemon: PokemonObject,
        parent=mw,
    ):
        super().__init__(parent)

        self.logger = logger
        self.translator = translator
        self.reviewer_obj = reviewer_obj
        self.test_window = test_window
        self.settings = settings
        self.main_pokemon = main_pokemon
        self.main_pokemon_function_callback = lambda _pokemon_data: MainPokemon(
            _pokemon_data, main_pokemon, logger, translator, reviewer_obj, test_window
        )

        self.n_cols = 5
        self.n_rows = 6
        self.current_box_idx = 0  # Index of current displayed box
        self.gif_in_collection = settings.get("gui.gif_in_collection")

        self.slot_size = 75  # Side length in pixels of a PC slot

        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(
            self.on_resize_timeout
        )  # Debounce resize events to avoid excessive refreshes during window resizing

        # PERFORMANCE: Database result caching
        self._pokemon_cache = None
        self._last_filter_state = None

        # LIVE SEARCH: Timer for debouncing
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(lambda: self.go_to_box(0))

        self.main_layout = QHBoxLayout()  # Main horizontal layout for split panels
        self.details_layout = QVBoxLayout()  # Layout for details panel
        self.details_widget = QWidget()  # Widget to hold details
        self.pokemon_details_layout = None

        # Widgets for filtering and sorting
        self.search_edit = None
        self.type_combo = None
        self.generation_combo = None
        self.tier_combo = None
        self.filter_favorites = None
        self.filter_is_holding_item = None
        self.filter_shiny = None
        self.sort_by_id = None
        self.sort_by_name = None
        self.sort_by_level = None
        self.sort_by_date = None
        self.sort_group = None
        self.selected_sort_key = "cp"  # stable English key (see sort_combo userData)
        self.sort_combo = None
        self.desc_sort = None  # Sort by descending order
        self.current_stats_tab_index = 0  # Remember selected tab (Stats/IV/EV)

        self._selected_individual_id = None
        self._total_pokemon_count = 0

        self._bff_id = None
        self._bff_dirty = True
        self.time_label = None

        # Splitter persistence keys
        self.GEOMETRY_KEY = "ankimon.pc_box.geometry"
        self.SPLITTER_KEY = "ankimon.pc_box.splitter"

        # Subscribe to theme change hook to update UI dynamically
        gui_hooks.theme_did_change.append(self.on_theme_change)

        self.ensure_data_integrity()  # Necessary for legacy reasons

        self.grid_container = None
        self.pokemon_grid = None
        self.curr_box_label = None

        self.create_gui()
        self._restore_geometry()
        self.refresh_pokemon_grid()

        # Real-time clock for friendship evolutions
        self.time_timer = QTimer(self)
        self.time_timer.timeout.connect(self._update_time_display)
        self.time_timer.start(60000)  # Every minute

    def on_theme_change(self):
        """
        Callback function triggered when Anki's theme changes (light to dark or vice versa).
        Refreshes the GUI to apply the new theme settings.
        """
        if not is_alive(self):
            try:
                gui_hooks.theme_did_change.remove(self.on_theme_change)
            except (ValueError, RuntimeError):
                pass
            return
        self.refresh_gui()

    def create_gui(self):
        """
        Builds and sets up the main graphical user interface for displaying and managing Pokémon.

        This method initializes the GUI layout, including:
        - Navigation controls to switch between Pokémon storage boxes
        - A grid display for showing Pokémon in the current box
        - Filters and sorting options to refine the displayed Pokémon
        - Optional animated sprites or static images based on user settings
        - A right-hand details panel with flexible width

        The GUI components include:
        - Navigation buttons and current box label
        - A dynamically populated grid of Pokémon buttons with sprite icons
        - Filtering options (search by name, type, generation, tier, favorites)
        - Sorting options (by ID, name, level, ascending/descending)
        - A flexible-width details panel on the right

        All components are added to the main layout and displayed within a resizable window.

        Side Effects:
            - Modifies the instance's layout and widget properties.
            - Connects UI elements to their corresponding interaction handlers.
        """
        self.setWindowTitle("Pokémon PC")

        # Make the window non-modal (floating) and add maximize/minimize buttons
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setWindowModality(Qt.WindowModality.NonModal)

        # Determine theme based on Anki's night mode
        is_dark_mode = theme_manager.night_mode  # Correctly checks Anki's theme

        # Define authentic Pokémon-themed color palettes
        if is_dark_mode:
            # Dark Mode
            background_color = "#003A70"
            text_color = "#E0E0E0"
            button_bg = "#3B4CCA"
            button_border = "#6A73D9"
            hover_color = "#6A73D9"
            favorite_color = (
                "#998A3D"  # Muted antique brass/gold (classy, low-luminance)
            )
            favorite_hover_color = "#8A7C36"
            input_bg = "#002B5A"  # Slightly lighter than background for input fields
            slot_bg_color = "#002B5A"
        else:
            # Light Mode
            background_color = "#E6F3FF"
            text_color = "#003A70"
            button_bg = "#3D7DCA"
            button_border = "#003A70"
            hover_color = "#A8D8FF"
            favorite_color = "#EAD180"  # Soft honey-gold / desaturated sand gold (warm, mild brightness)
            favorite_hover_color = "#D9BF70"
            input_bg = "#FFFFFF"  # White background for input fields
            slot_bg_color = "#CCE5FF"

        # Set stylesheet for the entire dialog, now correctly using all theme variables
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {background_color};
            }}
            QWidget {{
                color: {text_color};
            }}
            QPushButton {{
                background-color: {button_bg};
                border: 1px solid {button_border};
                border-radius: 5px;
                padding: 5px;
                color: {text_color};
            }}
            QPushButton:hover {{
                background-color: {hover_color};
            }}
            QLineEdit, QComboBox {{
                background-color: {input_bg};
                border: 1px solid {button_border};
                border-radius: 3px;
                padding: 3px;
                color: {text_color};
            }}
            QLabel {{
                color: {text_color};
            }}
            QTabWidget {{
                background-color: transparent;
                border: none;
            }}
            QTabBar {{
                background: transparent;
                qproperty-drawBase: 0;
            }}
            QTabWidget::pane {{
                border: 1px solid {button_border};
                border-radius: 5px;
                background: transparent;
                padding: 3px;
            }}
            QTabBar::tab {{
                background: {button_bg};
                border: 1px solid {button_border};
                padding: 6px;
                color: {text_color};
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
                /* No bottom margin needed if background is unified */
            }}
            QTabBar::tab:selected {{
                background: {hover_color};
            }}
            QTabBar::tab:!selected {{
                margin-top: 2px;
            }}
        """)

        # Theme variables for building the grid
        self.theme_vars = {
            "button_border": button_border,
            "background_color": background_color,
            "slot_bg_color": slot_bg_color,
            "favorite_color": favorite_color,
            "favorite_hover_color": favorite_hover_color,
            "hover_color": hover_color,
        }

        # Clear existing layout if this is called via refresh_gui
        if self.layout():
            clear_layout(self.layout())
        else:
            self.main_layout = QHBoxLayout(self)

        self.main_container = QWidget()
        self.main_container_layout = QHBoxLayout(self.main_container)
        self.main_container_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(self.main_container)

        pokemon_list = self.fetch_filtered_pokemon()
        max_box_idx = (len(pokemon_list) - 1) // (self.n_rows * self.n_cols)

        # Collection panel
        collection_layout = QVBoxLayout()
        collection_layout.setContentsMargins(20, 10, 20, 10)  # Consistent margins
        box_selector_layout = QHBoxLayout()
        box_selector_layout.setContentsMargins(0, 0, 0, 10)
        prev_box_button = QPushButton("◀")
        next_box_button = QPushButton("▶")
        prev_box_button.setFixedSize(70, 50)
        next_box_button.setFixedSize(70, 50)
        prev_box_button.setFont(QFont("System", 25))
        next_box_button.setFont(QFont("System", 25))
        # Max box idx is updated in refresh_pokemon_grid
        prev_box_button.clicked.connect(lambda: self.navigate_box(-1))
        next_box_button.clicked.connect(lambda: self.navigate_box(1))
        self.curr_box_label = QLabel(
            self.translator.translate(
                "pc_box_label",
                current=1,
                total=1,
            )
        )
        self.curr_box_label.setFixedSize(150, 50)
        self.curr_box_label.setFont(
            load_custom_font(20, int(self.settings.get("misc.language")))
        )
        self.curr_box_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.curr_box_label.setStyleSheet(
            f"border: 1px solid {button_border}; background-color: {background_color};"
        )

        # Day/night time indicator
        self.time_label = QLabel(current_time_label())
        self.time_label.setFixedHeight(50)
        self.time_label.setFont(
            load_custom_font(16, int(self.settings.get("misc.language")))
        )
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setStyleSheet(
            f"border: 1px solid {button_border}; background-color: {background_color}; padding: 0px 8px;"
        )
        if not self.settings.get("evolution.friendship_time_enabled", True):
            self.time_label.hide()

        box_selector_layout.addStretch(1)  # Push buttons to center
        box_selector_layout.addWidget(prev_box_button)
        box_selector_layout.addWidget(self.curr_box_label)
        box_selector_layout.addWidget(next_box_button)
        box_selector_layout.addStretch(1)  # Push buttons to center
        box_selector_layout.addWidget(self.time_label)
        collection_layout.addLayout(box_selector_layout)

        # Grid Container in a Scroll Area to allow window shrinking
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.scroll_area.setMinimumSize(200, 200)  # Minimum size required for shrinking
        # Enforce pagination by turning off scrollbars
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.pokemon_grid = QGridLayout(self.grid_container)
        self.pokemon_grid.setSpacing(5)
        self.pokemon_grid.setContentsMargins(0, 0, 0, 0)
        self.pokemon_grid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidget(self.grid_container)

        self.count_label = QLabel("", self)
        self.count_label.setObjectName("countLabel")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        collection_layout.addWidget(self.count_label)

        collection_layout.addWidget(self.scroll_area, 1)
        self.setup_filters_layout(collection_layout)

        collection_widget = QWidget()
        collection_widget.setLayout(collection_layout)
        self.main_container_layout.addWidget(collection_widget, 3)  # Grid takes 3/5

        self.setup_details_panel(background_color)

        self._apply_qss()

    def _apply_qss(self):
        self.setStyleSheet(
            self.styleSheet()
            + """
            #countLabel {
                font-size: 11px;
                color: #94a3b8;
                padding: 2px 8px 4px 8px;
                letter-spacing: 0.3px;
            }
            
            /* Unselected slot */
            QPushButton#pokemonSlot {
                border-radius: 5px;
            }

            /* Selected slot */
            QPushButton#pokemonSlot[selected="true"] {
                border: 2px solid #60a5fa;
                background: rgba(96, 165, 250, 0.12);
                border-radius: 8px;
            }

            /* Selected slot hover */
            QPushButton#pokemonSlot[selected="true"]:hover {
                background: rgba(96, 165, 250, 0.20);
                border-color: #93c5fd;
            }
        """
        )

    def _restore_geometry(self):
        import base64

        try:
            geo = mw.pm.profile.get(self.GEOMETRY_KEY)
            if geo:
                self.restoreGeometry(QByteArray(base64.b64decode(geo)))
        except Exception:
            pass

    def closeEvent(self, event: QCloseEvent):
        import base64

        try:
            mw.pm.profile[self.GEOMETRY_KEY] = base64.b64encode(
                bytes(self.saveGeometry())
            ).decode()
        except Exception:
            pass

        for attr in ("resize_timer", "search_timer"):
            t = getattr(self, attr, None)
            if t and t.isActive():
                t.stop()

        self.on_window_close()
        event.accept()

    def showEvent(self, event):
        """
        Triggered when the PC box is shown.
        Refreshes the grid to ensure newly caught Pokémon are visible.
        """
        super().showEvent(event)
        
        # Avoid clearing/rebuilding the layout (which causes black screens)
        # if the grid is already built and the count of Pokémon hasn't changed.
        try:
            cursor = services.db.execute("SELECT COUNT(*) FROM captured_pokemon")
            current_count = cursor.fetchone()[0]
        except Exception:
            current_count = 0

        grid_populated = self.pokemon_grid is not None and self.pokemon_grid.count() > 0
        if grid_populated and current_count == self._total_pokemon_count:
            return

        self.refresh_pokemon_grid()

    def setup_filters_layout(self, parent_layout):
        """
        Build and attach filter/sort controls below the PC grid.

        The method preserves previous control state when rebuilding the GUI,
        reconnects all filter/sort signals, and appends the resulting layout
        to ``parent_layout``.

        Args:
            parent_layout (QLayout): Layout that receives the filter controls.
        """
        # Bottom part to filter the Pokémon displayed
        filters_layout = QGridLayout()
        # Name filtering
        prev_text = self.search_edit.text() if self.search_edit is not None else ""
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search Pokémon (by nickname, name)")
        self.search_edit.setText(prev_text)

        # LIVE SEARCH: Trigger refresh after 300ms of inactivity
        self.search_edit.textChanged.connect(lambda: self.search_timer.start(300))
        self.search_edit.returnPressed.connect(lambda: self.go_to_box(0))

        search_button = QPushButton("Search")
        search_button.clicked.connect(lambda: self.go_to_box(0))
        # Type filtering
        prev_idx = self.type_combo.currentIndex() if self.type_combo is not None else 0
        self.type_combo = QComboBox()
        self.type_combo.addItem("All types")
        self.type_combo.addItems(
            [
                "Normal",
                "Fire",
                "Water",
                "Electric",
                "Grass",
                "Ice",
                "Fighting",
                "Poison",
                "Ground",
                "Flying",
                "Psychic",
                "Bug",
                "Rock",
                "Ghost",
                "Dragon",
                "Dark",
                "Steel",
                "Fairy",
            ]
        )
        self.type_combo.setCurrentIndex(prev_idx)
        self.type_combo.currentIndexChanged.connect(lambda: self.go_to_box(0))
        # Generation filtering
        prev_idx = (
            self.generation_combo.currentIndex()
            if self.generation_combo is not None
            else 0
        )
        self.generation_combo = QComboBox()
        self.generation_combo.addItem("All gens")
        self.generation_combo.addItems([f"Gen {i}" for i in range(1, 10, 1)])
        self.generation_combo.setCurrentIndex(prev_idx)
        self.generation_combo.currentIndexChanged.connect(lambda: self.go_to_box(0))
        # Tier filtering
        prev_idx = self.tier_combo.currentIndex() if self.tier_combo is not None else 0
        self.tier_combo = QComboBox()
        self.tier_combo.addItem("All tiers")
        self.tier_combo.addItems(
            [
                "Normal",
                "Legendary",
                "Mythical",
                "Baby",
                "Ultra",
                "Fossil",
                "Starter",
                "Mega",
                "Gmax",
            ]
        )
        self.tier_combo.setCurrentIndex(prev_idx)
        self.tier_combo.currentIndexChanged.connect(lambda: self.go_to_box(0))
        # Sorting by favorites
        is_checked = (
            self.filter_favorites.isChecked()
            if self.filter_favorites is not None
            else False
        )
        self.filter_favorites = QCheckBox("Favorites")
        self.filter_favorites.setChecked(is_checked)
        self.filter_favorites.stateChanged.connect(lambda: self.go_to_box(0))
        # Filtering Pokemon who hold items
        is_checked = (
            self.filter_is_holding_item.isChecked()
            if self.filter_is_holding_item is not None
            else False
        )
        self.filter_is_holding_item = QCheckBox("Holds item")
        self.filter_is_holding_item.setChecked(is_checked)
        self.filter_is_holding_item.stateChanged.connect(lambda: self.go_to_box(0))
        # Shiny filter
        is_checked = (
            self.filter_shiny.isChecked() if self.filter_shiny is not None else False
        )
        self.filter_shiny = QCheckBox("Shiny")
        self.filter_shiny.setChecked(is_checked)
        self.filter_shiny.stateChanged.connect(lambda: self.go_to_box(0))
        # Sorting options
        sort_label = QLabel("Sort by:")

        prev_sort = (
            self.selected_sort_key if hasattr(self, "selected_sort_key") else "Date"
        )
        self.sort_combo = QComboBox()
        # (display label, stable English key). fetch_filtered_pokemon matches on
        # the key, so the key is stored as userData and the localized label is
        # shown. Storing the display text directly (the old behaviour) broke every
        # translated sort — e.g. German "WP"/"Freundschaft" never matched the
        # hardcoded "cp"/"friendship" branches and silently fell back to Date.
        sort_options = [
            (self.translator.translate("Date"), "date"),
            (self.translator.translate("ID"), "id"),
            (self.translator.translate("Name"), "name"),
            (self.translator.translate("Level"), "level"),
            (self.translator.translate("cp_label"), "cp"),
            (self.translator.translate("friendship_label"), "friendship"),
            ("IV (Total)", "iv (total)"),
            ("EV (Total)", "ev (total)"),
            ("HP", "hp"),
            (self.translator.translate("Attack"), "attack"),
            (self.translator.translate("Defense"), "defense"),
            ("Sp. Atk", "sp. atk"),
            ("Sp. Def", "sp. def"),
            (self.translator.translate("Speed"), "speed"),
        ]
        for label, key in sort_options:
            self.sort_combo.addItem(label, key)

        # Restore previous selection by its stable key (not display text).
        index = self.sort_combo.findData(prev_sort)
        if index >= 0:
            self.sort_combo.setCurrentIndex(index)
        else:
            self.sort_combo.setCurrentIndex(0)  # Date

        self.sort_combo.currentTextChanged.connect(self.on_sort_changed)

        sort_combo_layout = QHBoxLayout()
        sort_combo_layout.addWidget(sort_label)
        sort_combo_layout.addWidget(self.sort_combo)
        sort_combo_widget = QWidget()
        sort_combo_widget.setLayout(sort_combo_layout)

        # Checkboxes for other options
        is_checked = self.desc_sort.isChecked() if self.desc_sort is not None else True
        self.desc_sort = QCheckBox("Descending")
        self.desc_sort.setChecked(is_checked)
        self.desc_sort.stateChanged.connect(lambda: self.go_to_box(0))

        # Adding the widgets to the layout
        filters_layout.addWidget(self.search_edit, 0, 0, 1, 4)
        filters_layout.addWidget(search_button, 0, 4, 1, 1)
        filters_layout.addWidget(self.type_combo, 1, 0, 1, 2)
        filters_layout.addWidget(self.generation_combo, 1, 2, 1, 2)
        filters_layout.addWidget(self.tier_combo, 1, 4, 1, 1)

        checkboxes_layout = QHBoxLayout()
        checkboxes_layout.addWidget(self.filter_favorites)
        checkboxes_layout.addWidget(self.filter_is_holding_item)
        checkboxes_layout.addWidget(self.filter_shiny)
        checkboxes_layout.addWidget(self.desc_sort)  # Moved here
        checkboxes_widget = QWidget()
        checkboxes_widget.setLayout(checkboxes_layout)

        filters_layout.addWidget(checkboxes_widget, 2, 0, 1, 5)
        filters_layout.addWidget(sort_combo_widget, 3, 0, 1, 5)
        parent_layout.addLayout(filters_layout)

    def setup_details_panel(self, background_color):
        """Initializes the details panel with a persistent stack to avoid window flickering/resizing."""
        from PyQt6.QtWidgets import QStackedWidget

        self.details_panel_stack = QStackedWidget()
        self.details_panel_stack.setMinimumWidth(470)
        self.main_container_layout.addWidget(self.details_panel_stack, 2)

        # Index 0: Placeholder
        self._placeholder_widget = self._create_placeholder_widget()
        self.details_panel_stack.addWidget(self._placeholder_widget)

        # Index 1: Actual Details View (Container for header/stats/footer stacks)
        self.details_widget = QWidget()
        self.details_widget.setObjectName("persistentDetails")
        self.details_container_layout = QVBoxLayout(self.details_widget)
        self.details_container_layout.setContentsMargins(0, 0, 0, 0)
        self.details_container_layout.setSpacing(0)

        from PyQt6.QtWidgets import QGraphicsOpacityEffect

        self.header_stack = QStackedWidget()
        self.stats_stack = QStackedWidget()
        self.footer_stack = QStackedWidget()

        # Lock the heights to prevent window jumping
        self.header_stack.setMinimumHeight(420)
        self.stats_stack.setFixedHeight(220)
        self.footer_stack.setMinimumHeight(90)

        self.details_container_layout.addWidget(self.header_stack)
        self.details_container_layout.addWidget(self.stats_stack)
        self.details_container_layout.addWidget(self.footer_stack)

        # Setup opacity effects for header
        self.header_opacity = QGraphicsOpacityEffect(self.header_stack)
        self.header_stack.setGraphicsEffect(self.header_opacity)

        self.details_panel_stack.addWidget(self.details_widget)

        # Initial state: Show placeholder
        self.details_panel_stack.setCurrentIndex(0)

    def _create_placeholder_widget(self):
        """Creates and returns the beautiful placeholder widget."""
        widget = QWidget()
        widget.setMinimumHeight(700)
        widget.setObjectName("detailsPlaceholder")

        # Theme-aware styling
        is_dark_mode = theme_manager.night_mode
        bg_color = "#002B5A" if is_dark_mode else "#CCE5FF"
        text_color = "#94a3b8" if is_dark_mode else "#003A70"

        widget.setStyleSheet(f"""
            #detailsPlaceholder {{
                background-color: {bg_color};
                border-left: 1px solid {self.theme_vars.get("button_border", "#6A73D9")};
                border-radius: 12px;
                margin: 8px;
            }}
        """)

        layout = QVBoxLayout(widget)
        layout.setContentsMargins(40, 20, 40, 20)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Pokéball Icon
        icon_label = QLabel()
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                180,
                180,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_label.setPixmap(scaled_pixmap)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Main Prompt
        prompt_label = QLabel("Choose a Pokémon to view its stats")
        prompt_label.setWordWrap(True)
        prompt_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_label.setStyleSheet(
            f"color: {text_color}; font-size: 20px; font-weight: 800; margin-top: 25px;"
        )

        # Subtext
        count = getattr(self, "_total_pokemon_count", 0)
        summary_label = QLabel(f"PC Inventory: {count} Pokémon")
        summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        summary_label.setStyleSheet(
            "color: #64748b; font-size: 13px; font-style: italic; margin-top: 10px;"
        )

        layout.addStretch(1)
        layout.addWidget(icon_label)
        layout.addWidget(prompt_label)
        layout.addWidget(summary_label)
        layout.addStretch(1)
        return widget

    def _show_placeholder_details(self):
        """Switches the details panel to placeholder mode."""
        if hasattr(self, "details_panel_stack"):
            self.details_panel_stack.setCurrentIndex(0)
        self._selected_individual_id = None
        self._refresh_slot_selection()

    def refresh_pokemon_grid(self, recompute_bff: bool = True):
        """
        Clears and rebuilds the grid.
        """
        if not is_alive(self.pokemon_grid):
            return

        self._pokemon_cache = None  # Invalidate database cache
        clear_layout(self.pokemon_grid)
        self.gif_in_collection = self.settings.get("gui.gif_in_collection")

        # The day/night clock and badges are part of the friendship/time feature
        friendship_time_enabled = self.settings.get(
            "evolution.friendship_time_enabled", True
        )

        self._filtered_pokemon = self.fetch_filtered_pokemon()
        pokemon_list = self._filtered_pokemon
        max_box_idx = max(0, (len(pokemon_list) - 1) // (self.n_rows * self.n_cols))

        if self.current_box_idx > max_box_idx:
            self.current_box_idx = max_box_idx

        if self.curr_box_label:
            self.curr_box_label.setText(
                self.translator.translate(
                    "pc_box_label",
                    current=self.current_box_idx + 1,
                    total=max_box_idx + 1,
                )
            )

        # Update day/night label
        if self.time_label is not None:
            if friendship_time_enabled:
                self.time_label.setText(current_time_label())
                self.time_label.show()
            else:
                self.time_label.hide()

        # Resolve the BFF (highest-friendship Pokémon)
        if recompute_bff or self._bff_dirty:
            self._update_bff()
        bff_id = self._bff_id

        self._update_count_label()

        start_index = self.current_box_idx * self.n_rows * self.n_cols
        pokemon_list_slice = pokemon_list[
            start_index : start_index + self.n_rows * self.n_cols
        ]

        theme_vars = self.theme_vars
        border = theme_vars["button_border"]

        for row in range(self.n_rows):
            for col in range(self.n_cols):
                pokemon_idx = row * self.n_cols + col
                if pokemon_idx >= len(pokemon_list_slice):
                    empty_label = QLabel(self.grid_container)
                    empty_label.setFixedSize(self.slot_size, self.slot_size)
                    self.pokemon_grid.addWidget(
                        empty_label, row, col, alignment=Qt.AlignmentFlag.AlignCenter
                    )
                    continue

                pokemon = pokemon_list_slice[pokemon_idx]
                pkmn_image_path = get_sprite_path(
                    "front",
                    "gif" if self.gif_in_collection else "png",
                    pokemon["id"],
                    pokemon.get("shiny", False),
                    pokemon["gender"],
                    pokemon.get("name"),
                )
                pokemon_button = PokemonSlotButton("", self.grid_container)
                pokemon_button.setObjectName("pokemonSlot")
                pokemon_button.setFixedSize(self.slot_size, self.slot_size)

                # BFF (highest friendship) takes visual precedence
                is_bff = bff_id is not None and pokemon.get("individual_id") == bff_id

                if is_bff:
                    bg = "#FF69B4"  # Hot pink for BFF
                    h_bg = "#FF8DC7"
                elif pokemon.get("is_favorite"):
                    bg = theme_vars["favorite_color"]
                    h_bg = theme_vars["favorite_hover_color"]
                else:
                    bg = theme_vars["slot_bg_color"]
                    h_bg = theme_vars["hover_color"]

                # Store base colours so _refresh_slot_selection can build the full sheet
                pokemon_button._base_bg = bg
                pokemon_button._hover_bg = h_bg
                pokemon_button._border = border
                pokemon_button.setStyleSheet(
                    f"QPushButton {{ background-color: {bg}; border: 1px solid {border}; border-radius: 5px; }}"
                    f" QPushButton:hover {{ background-color: {h_bg}; }}"
                )

                # Connect signals
                # Left click: Show details
                pokemon_button.clicked.connect(
                    lambda checked, pkmn=pokemon: self.show_pokemon_details(pkmn)
                )
                # Right click: Show actions menu
                pokemon_button.rightClicked.connect(
                    lambda pb=pokemon_button, pkmn=pokemon: self.show_actions_submenu(
                        pb, pkmn
                    )
                )
                pokemon_button._individual_id = pokemon["individual_id"]
                self.pokemon_grid.addWidget(
                    pokemon_button, row, col, alignment=Qt.AlignmentFlag.AlignCenter
                )

                if self.gif_in_collection:
                    scaled_movie_label = ScaledMovieLabel(
                        pkmn_image_path,
                        self.slot_size - 10,
                        self.slot_size - 10,
                        self.grid_container,
                    )
                    scaled_movie_label.setAttribute(
                        Qt.WidgetAttribute.WA_TransparentForMouseEvents
                    )
                    self.pokemon_grid.addWidget(
                        scaled_movie_label,
                        row,
                        col,
                        alignment=Qt.AlignmentFlag.AlignCenter,
                    )
                else:
                    pokemon_button.setIcon(QIcon(pkmn_image_path))
                    pokemon_button.setIconSize(
                        QSize(self.slot_size - 10, self.slot_size - 10)
                    )

                # Overlays (Heart for BFF, Star/Moon/Sun for Evolution)
                badge_tooltips = []
                readiness = evolution_readiness(pokemon)

                if is_bff:
                    heart_badge = QLabel("💖", self.grid_container)
                    heart_badge.setAttribute(
                        Qt.WidgetAttribute.WA_TransparentForMouseEvents
                    )
                    heart_badge.setStyleSheet(
                        "QLabel {"
                        "  margin-top: 5px;"
                        "  margin-left: 5px;"
                        "  background: transparent;"
                        "}"
                    )
                    self.pokemon_grid.addWidget(
                        heart_badge,
                        row,
                        col,
                        alignment=Qt.AlignmentFlag.AlignTop
                        | Qt.AlignmentFlag.AlignLeft,
                    )
                    badge_tooltips.append(self.translator.translate("bff_tooltip"))

                if readiness["ready"] and (
                    readiness["method"] == "level" or friendship_time_enabled
                ):
                    evo_badge = QLabel(self.grid_container)
                    evo_badge.setAttribute(
                        Qt.WidgetAttribute.WA_TransparentForMouseEvents
                    )
                    evo_badge.setFixedSize(
                        23, 23
                    )  # Slightly larger size to accommodate margins cleanly

                    # Load the generated high-quality PNG asset
                    badge_path = addon_dir / "addon_sprites" / "evolution_indicator.png"
                    if badge_path.exists():
                        pixmap = QPixmap(str(badge_path))
                        scaled_pixmap = pixmap.scaled(
                            18,
                            18,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        evo_badge.setPixmap(scaled_pixmap)
                        evo_badge.setStyleSheet(
                            "margin-top: 2px; margin-right: 1px; background: transparent;"
                        )
                    else:
                        # Fallback to plain text ⇈ if asset is not found
                        evo_badge.setText("⇈")
                        evo_badge.setStyleSheet(
                            "QLabel {"
                            "  color: #3b82f6;"
                            "  font-weight: bold;"
                            "  margin-top: 2px;"
                            "  margin-right: 1px;"
                            "  background: transparent;"
                            "}"
                        )

                    evo_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.pokemon_grid.addWidget(
                        evo_badge,
                        row,
                        col,
                        alignment=Qt.AlignmentFlag.AlignTop
                        | Qt.AlignmentFlag.AlignRight,
                    )
                    badge_tooltips.append(self.translator.translate("badge_ready"))
                elif (
                    friendship_time_enabled
                    and readiness["evolvable"]
                    and readiness["friendship_remaining"] == 0
                    and not readiness["time_ok"]
                    and readiness["required_time"]
                ):
                    wait_icon = "🌙" if readiness["required_time"] == "night" else "☀️"
                    wait_badge = QLabel(wait_icon, self.grid_container)
                    wait_badge.setAttribute(
                        Qt.WidgetAttribute.WA_TransparentForMouseEvents
                    )
                    wait_badge.setStyleSheet(
                        "QLabel {"
                        "  margin-top: 5px;"
                        "  margin-right: 5px;"
                        "  background: transparent;"
                        "}"
                    )
                    self.pokemon_grid.addWidget(
                        wait_badge,
                        row,
                        col,
                        alignment=Qt.AlignmentFlag.AlignTop
                        | Qt.AlignmentFlag.AlignRight,
                    )
                    key = (
                        "badge_wait_night"
                        if readiness["required_time"] == "night"
                        else "badge_wait_day"
                    )
                    badge_tooltips.append(self.translator.translate(key))
                if badge_tooltips:
                    pokemon_button.setToolTip("\n".join(badge_tooltips))
        self._update_count_label()
        self._refresh_slot_selection()

    def _update_count_label(self):
        if not hasattr(self, "count_label") or not is_alive(self.count_label):
            return
        shown = len(self._filtered_pokemon) if hasattr(self, "_filtered_pokemon") else 0
        total = self._total_pokemon_count
        self.count_label.setText(f"Showing {shown:,} / {total:,} Pokémon")

    def _refresh_slot_selection(self):
        for i in range(self.pokemon_grid.count()):
            item = self.pokemon_grid.itemAt(i)
            if not item:
                continue
            widget = item.widget()
            if isinstance(widget, PokemonSlotButton):
                pkmn_id = getattr(widget, "_individual_id", None)
                is_selected = pkmn_id == self._selected_individual_id
                bg = getattr(widget, "_base_bg", "transparent")
                h_bg = getattr(widget, "_hover_bg", "transparent")
                border = getattr(widget, "_border", "#888")
                if is_selected:
                    sheet = (
                        f"QPushButton {{ background-color: {bg}; border: 3px solid #ffffff;"
                        f" border-radius: 8px; }}"
                        f" QPushButton:hover {{ background-color: rgba(255,255,255,0.15);"
                        f" border-color: #f0f0f0; }}"
                    )
                else:
                    sheet = (
                        f"QPushButton {{ background-color: {bg}; border: 1px solid {border};"
                        f" border-radius: 5px; }}"
                        f" QPushButton:hover {{ background-color: {h_bg}; }}"
                    )
                widget.setStyleSheet(sheet)

    def navigate_box(self, delta):
        """
        Move to a different box relative to the current one.

        Applies active filters to compute the valid page range and then
        navigates with wrap-around behavior.

        Args:
            delta (int): Relative box movement (for example ``-1`` or ``+1``).
        """
        pokemon_list = self.fetch_filtered_pokemon()
        max_idx = max(0, (len(pokemon_list) - 1) // (self.n_rows * self.n_cols))
        self.looparound_go_to_box(self.current_box_idx + delta, max_idx)

    def resizeEvent(self, event: QResizeEvent):
        """
        Triggered when the dialog is resized.
        Uses a timer to debounce the GUI refresh.
        """
        super().resizeEvent(event)
        self.resize_timer.start(200)

    def on_resize_timeout(self):
        """
        Recalculates dimensions and refreshes the grid if they have changed.
        Ensures the current box index remains valid for the new grid capacity.
        """
        new_cols, new_rows = self.calculate_grid_dimensions()
        if new_cols != self.n_cols or new_rows != self.n_rows:
            self.n_cols = new_cols
            self.n_rows = new_rows
            self.refresh_pokemon_grid()

    def calculate_grid_dimensions(self):
        """
        Calculates how many columns and rows of slots fit in the current viewport.
        Ensures only fully visible slots are displayed by using scroll_area dimensions.
        """
        vw = self.scroll_area.viewport().width()
        vh = self.scroll_area.viewport().height()

        slot = self.slot_size
        spacing = 5
        new_cols = max(1, (vw + spacing) // (slot + spacing))
        new_rows = max(1, (vh + spacing) // (slot + spacing))

        return int(new_cols), int(new_rows)

    def refresh_gui(self):
        """
        Refreshes the user interface by populating the grid.
        Avoids calling create_gui() to prevent full layout rebuilds.
        """
        self._pokemon_cache = None  # Invalidate database cache
        if not self.layout():
            self.create_gui()
        else:
            self.refresh_pokemon_grid()
            # If no Pokémon is selected (e.g. after account swap), refresh the placeholder
            if self._selected_individual_id is None:
                self._show_placeholder_details()
        self.layout().invalidate()
        self.layout().activate()

    def go_to_box(self, idx: int):
        """
        Navigates to the specified Pokémon storage box and updates the GUI accordingly.

        Args:
            idx (int): The index of the box to navigate to.

        Side Effects:
            - Updates the current box index.
            - Refreshes the Pokémon grid to display the selected box's contents.
        """
        self.current_box_idx = idx
        self.refresh_pokemon_grid()

    def looparound_go_to_box(self, idx: int, max_idx: int):
        """
        Navigates to a box index with wrap-around behavior.

        If the provided index is less than 0, wraps around to the maximum index.
        If the index exceeds the maximum, wraps around to 0.
        Then updates the GUI to show the selected box.

        Args:
            idx (int): The target box index to navigate to.
            max_idx (int): The maximum valid box index.

        Side Effects:
            - Updates the current box index with wrapping.
            - Triggers a GUI refresh to display the selected box.
        """
        if idx < 0:
            idx = max_idx
        elif idx > max_idx:
            idx = 0
        self.go_to_box(idx)

    def adjust_pixmap_size(self, pixmap, max_width, max_height):
        """
        Scales a QPixmap to fit within the specified maximum width and height while maintaining aspect ratio.

        If the pixmap's width exceeds `max_width`, it is scaled down proportionally.
        Note: This implementation currently only scales based on width and does not consider `max_height`.

        Args:
            pixmap (QPixmap): The original pixmap to be resized.
            max_width (int): The maximum allowed width.
            max_height (int): The maximum allowed height (currently unused).

        Returns:
            QPixmap: The scaled pixmap, or the original if no scaling was needed.
        """
        original_width = pixmap.width()
        original_height = pixmap.height()

        if original_width > max_width:
            new_width = max_width
            new_height = (original_height * max_width) // original_width
            pixmap = pixmap.scaled(new_width, new_height)

        return pixmap

    def fetch_filtered_pokemon(self) -> list:
        """
        Dynamically builds a SQL query to filter and sort Pokemon, fetching only the lightweight stub data needed for the grid.
        Results are cached to improve performance when navigating boxes or selecting Pokémon.
        """
        # Always fetch latest count to detect DB changes
        try:
            cursor = services.db.execute("SELECT COUNT(*) FROM captured_pokemon")
            self._total_pokemon_count = cursor.fetchone()[0]
        except Exception:
            pass

        # Build current filter state for cache check
        current_state = {
            "db": services.db.db_path.name
            if services.db and getattr(services.db, "db_path", None)
            else "",
            "search": self.search_edit.text() if self.search_edit else "",
            "type": self.type_combo.currentText() if self.type_combo else "All types",
            "gen": self.generation_combo.currentText()
            if self.generation_combo
            else "All gens",
            "tier": self.tier_combo.currentText() if self.tier_combo else "All tiers",
            "shiny": self.filter_shiny.isChecked() if self.filter_shiny else False,
            "favorite": self.filter_favorites.isChecked()
            if self.filter_favorites
            else False,
            "holding": self.filter_is_holding_item.isChecked()
            if self.filter_is_holding_item
            else False,
            "sort": self.sort_combo.currentText() if self.sort_combo else "Date",
            "desc": self.desc_sort.isChecked() if self.desc_sort else False,
            "count": self._total_pokemon_count,  # Force refresh if count changes
        }

        if self._pokemon_cache is not None and self._last_filter_state == current_state:
            return self._pokemon_cache

        # Base query mapping direct virtual columns where available
        query_parts = [
            "SELECT individual_id, name, level, pokedex_id as id, shiny as shiny, "
            "rowid as original_index, json_extract(data, '$.nickname') as nickname, "
            "json_extract(data, '$.gender') as gender, json_extract(data, '$.is_favorite') as is_favorite, "
            "json_extract(data, '$.held_item') as held_item, "
            "json_extract(data, '$.captured_date') as captured_date, "
            "json_extract(data, '$.friendship') as friendship, "
            "json_extract(data, '$.everstone') as everstone, "
            "json_extract(data, '$.evolution_rejected') as evolution_rejected, "
            "json_extract(data, '$.iv') as iv_json, json_extract(data, '$.ev') as ev_json, "
            "json_extract(data, '$.base_stats') as base_stats_json, json_extract(data, '$.stats') as stats_json, json_extract(data, '$.nature') as nature, "
            "json_extract(data, '$.attacks') as attacks_json, "
            "json_extract(data, '$.pokemon_defeated') as pokemon_defeated "
            "FROM captured_pokemon WHERE 1=1"
        ]
        params = []

        # Name / Nickname filtering
        if self.search_edit is not None and self.search_edit.text():
            search_text = f"%{self.search_edit.text()}%"
            query_parts.append(
                "AND (name LIKE ? OR json_extract(data, '$.nickname') LIKE ?)"
            )
            params.extend([search_text, search_text])

        # Type filtering
        if self.type_combo is not None and self.type_combo.currentIndex() != 0:
            type_text = f"%{self.type_combo.currentText()}%"
            query_parts.append("AND json_extract(data, '$.type') LIKE ?")
            params.append(type_text)

        # Tier filtering
        if self.tier_combo is not None and self.tier_combo.currentIndex() != 0:
            query_parts.append("AND json_extract(data, '$.tier') = ?")
            params.append(self.tier_combo.currentText())

        # Favorites filtering
        if self.filter_favorites is not None and self.filter_favorites.isChecked():
            query_parts.append("AND json_extract(data, '$.is_favorite') = 1")

        # Held item filtering
        if (
            self.filter_is_holding_item is not None
            and self.filter_is_holding_item.isChecked()
        ):
            query_parts.append("AND json_extract(data, '$.held_item') IS NOT NULL")

        # Shiny filtering
        if self.filter_shiny is not None and self.filter_shiny.isChecked():
            query_parts.append("AND shiny = 1")

        # Generation filtering
        if self.generation_combo is not None:
            gen_idx = self.generation_combo.currentIndex()
            if gen_idx != 0:
                gen_ranges = {
                    1: (1, 151),
                    2: (152, 251),
                    3: (252, 386),
                    4: (387, 493),
                    5: (494, 649),
                    6: (650, 721),
                    7: (722, 809),
                    8: (810, 905),
                    9: (906, 1025),
                }
                if gen_idx in gen_ranges:
                    start_id, end_id = gen_ranges[gen_idx]
                    query_parts.append("AND pokedex_id BETWEEN ? AND ?")
                    params.extend([start_id, end_id])

        # Sorting
        sort_key_raw = (
            self.selected_sort_key if hasattr(self, "selected_sort_key") else "Date"
        )
        sort_key_str = sort_key_raw.lower()
        reverse = self.desc_sort is not None and self.desc_sort.isChecked()
        direction = "DESC" if reverse else "ASC"

        # Determine if we use SQL sorting or Python sorting
        use_python_sort = False
        stat_map = {
            "hp": "hp",
            "attack": "atk",
            "defense": "def",
            "sp. atk": "spa",
            "sp. def": "spd",
            "speed": "spe",
        }

        target_stat = stat_map.get(sort_key_str)

        if sort_key_str == "date":
            order_clause = f"ORDER BY captured_date {direction}, original_index {direction}"
        elif sort_key_str == "name":
            order_clause = f"ORDER BY name {direction}, json_extract(data, '$.nickname') {direction}"
        elif sort_key_str == "level":
            order_clause = f"ORDER BY level {direction}"
        elif sort_key_str == "id":
            order_clause = f"ORDER BY pokedex_id {direction}"
        elif sort_key_str == "cp":
            use_python_sort = True
            order_clause = f"ORDER BY original_index {direction}"
        elif sort_key_str in ["iv (total)", "ev (total)", "iv", "ev"]:
            # Fallback for legacy keys if they appear
            use_python_sort = True
            order_clause = f"ORDER BY original_index {direction}"
        elif target_stat:
            use_python_sort = True
            order_clause = f"ORDER BY original_index {direction}"
        elif sort_key_str == "friendship":
            use_python_sort = True
            order_clause = f"ORDER BY original_index {direction}"
        else:
            # Default to Date
            order_clause = f"ORDER BY original_index {direction}"

        query = " ".join(query_parts) + " " + order_clause

        try:
            cursor = services.db.execute(query, tuple(params))
            results = []
            for row in cursor.fetchall():
                p = {
                    "original_index": row["original_index"],
                    "individual_id": row["individual_id"],
                    "id": row["id"],
                    "name": row["name"],
                    "nickname": row["nickname"],
                    "shiny": bool(row["shiny"]),
                    "level": row["level"],
                    "gender": row["gender"],
                    "is_favorite": bool(row["is_favorite"]),
                    "held_item": row["held_item"],
                    "friendship": int(row["friendship"] or 0),
                    "everstone": bool(row["everstone"]),
                    "evolution_rejected": bool(row["evolution_rejected"]),
                    "attacks": json.loads(row["attacks_json"])
                    if row["attacks_json"]
                    else [],
                    "pokemon_defeated": int(row["pokemon_defeated"] or 0),
                }

                # Pre-calculate sums/stats for sorting if needed
                if use_python_sort:
                    if sort_key_str == "friendship":
                        p["_sort_value"] = int(row["friendship"] or 0)
                    elif "iv" in sort_key_str or "ev" in sort_key_str:
                        key = "iv" if "iv" in sort_key_str else "ev"
                        stats_json = row[f"{key}_json"]
                        stats_dict = json.loads(stats_json) if stats_json else {}
                        p["_sort_value"] = (
                            sum(stats_dict.values())
                            if isinstance(stats_dict, dict)
                            else sum(stats_dict)
                            if isinstance(stats_dict, list)
                            else 0
                        )
                    elif target_stat:
                        # Individual Stat sorting
                        level = row["level"]
                        nature = row["nature"] or "serious"

                        iv_dict = json.loads(row["iv_json"]) if row["iv_json"] else {}
                        ev_dict = json.loads(row["ev_json"]) if row["ev_json"] else {}
                        base_stats_dict = (
                            json.loads(row["base_stats_json"])
                            if row["base_stats_json"]
                            else {}
                        )
                        stats_dict = (
                            json.loads(row["stats_json"]) if row["stats_json"] else {}
                        )

                        # Use PokemonObject's calculation logic (with fallback for legacy stats)
                        base_val = (base_stats_dict or stats_dict).get(target_stat, 1)
                        iv_val = iv_dict.get(target_stat, 0)
                        ev_val = ev_dict.get(target_stat, 0)

                        p["_sort_value"] = PokemonObject.calc_stat(
                            target_stat, base_val, level, iv_val, ev_val, nature
                        )
                    elif sort_key_str == "cp":
                        # Re-calculate CP for sorting to ensure it uses the new formula
                        iv_dict = json.loads(row["iv_json"]) if row["iv_json"] else {}
                        ev_dict = json.loads(row["ev_json"]) if row["ev_json"] else {}
                        base_stats_dict = (
                            json.loads(row["base_stats_json"])
                            if row["base_stats_json"]
                            else {}
                        )
                        stats_dict = (
                            json.loads(row["stats_json"]) if row["stats_json"] else {}
                        )

                        # Mirror calculate_cp_from_dict's contract: prefer base_stats, else stats fallback
                        cp_dict = {"level": row["level"], "iv": iv_dict, "ev": ev_dict}
                        if base_stats_dict:
                            cp_dict["base_stats"] = base_stats_dict
                        else:
                            cp_dict["stats"] = stats_dict
                        p["_sort_value"] = calculate_cp_from_dict(cp_dict)

                results.append(p)

            # Perform Python sorting
            if use_python_sort:
                results.sort(key=lambda x: x.get("_sort_value", 0), reverse=reverse)

            # Cache the result
            self._pokemon_cache = results
            self._last_filter_state = current_state

            return results
        except Exception as e:
            if self.logger:
                self.logger.log("error", f"Error fetching filtered pokemon: {e}")
            return []

    def on_sort_changed(self, text):
        # Store the stable English key (userData), not the localized display text,
        # so fetch_filtered_pokemon's token matching works in every language.
        data = self.sort_combo.currentData() if self.sort_combo is not None else None
        self.selected_sort_key = data if data is not None else text
        self.go_to_box(0)

    def show_actions_submenu(self, button: QPushButton, pokemon: dict[str, Any]):
        """
        Displays a context menu with actions related to a specific Pokémon.

        The menu includes:
        - A non-interactive title showing the Pokémon's nickname, name, gender symbol, and level.
        - An option to view detailed information about the Pokémon.
        - An option to select the Pokémon as the main Pokémon.
        - An option to toggle the Pokémon's favorite status.

        Args:
            button (QPushButton): The button widget where the menu will be displayed.
            pokemon (dict[str, Any]): A dictionary containing Pokémon data, expected to include keys
                like "name", "nickname", "gender", "level", and "is_favorite".

        Side Effects:
            - Displays a popup menu aligned below the specified button.
            - Connects menu actions to respective handlers in the parent class.
        """
        menu = QMenu(self)

        # Emulate a window title for QMenu
        if pokemon.get("gender") == "M":
            gender_symbol = "♂"
        elif pokemon.get("gender") == "F":
            gender_symbol = "♀"
        else:
            gender_symbol = ""
        if pokemon.get("nickname"):
            title = f"{pokemon['nickname']} ({pokemon['name']}) {gender_symbol} - lvl {pokemon['level']}"
        else:
            title = f"{pokemon['name']} {gender_symbol} - lvl {pokemon['level']}"
        title_action = QAction(title, menu)
        title_action.setEnabled(False)  # Disabled, so it can't be clicked
        menu.addAction(title_action)
        menu.addSeparator()

        pokemon_details_action = QAction("Pokémon details", self)
        main_pokemon_action = QAction("Pick as main Pokémon", self)
        make_favorite_action = QAction(
            "Unmake favorite" if pokemon.get("is_favorite", False) else "Make favorite"
        )
        give_held_item = QAction("Give a held item", self)

        # Connect actions to methods or lambda functions
        pokemon_details_action.triggered.connect(
            lambda: self.show_pokemon_details(pokemon)
        )
        main_pokemon_action.triggered.connect(
            lambda: self.main_pokemon_function_callback(
                services.db.get_pokemon(pokemon["individual_id"])
            )
        )
        make_favorite_action.triggered.connect(lambda: self.toggle_favorite(pokemon))
        give_held_item.triggered.connect(lambda: self.give_held_item(pokemon))

        menu.addAction(pokemon_details_action)
        menu.addAction(main_pokemon_action)
        menu.addAction(make_favorite_action)
        menu.addAction(give_held_item)
        if pokemon.get("held_item"):
            remove_held_item = QAction(
                f"Remove held item : {format_item_name(pokemon['held_item'])}", self
            )
            remove_held_item.triggered.connect(lambda: self.remove_held_item(pokemon))
            menu.addAction(remove_held_item)

        # Show the menu at the button's position, aligned below the button
        menu.exec(button.mapToGlobal(button.rect().topRight()))

    def show_pokemon_details(self, pokemon_stub):
        """
        Displays detailed information about a specific Pokémon in the right-hand details panel.
        Only the header and footer parts animate; the stats box stays persistent for smooth bar sliding.
        """
        individual_id = pokemon_stub.get("individual_id")
        is_same_pokemon = individual_id is not None and individual_id == getattr(
            self, "_selected_individual_id", None
        )

        pokemon = services.db.get_pokemon(individual_id)
        if not pokemon:
            return

        if pokemon.get("base_stats"):
            detail_stats = {**pokemon["base_stats"], "xp": pokemon.get("xp", 0)}
        elif pokemon.get("stats"):
            detail_stats = {**pokemon["stats"], "xp": pokemon.get("xp", 0)}
        else:
            raise ValueError("Could not get the stats information of the Pokémon")

        # Switch to details view if not already there
        if hasattr(self, "details_panel_stack"):
            self.details_panel_stack.setCurrentIndex(1)

        # Get previous stats for sliding bar animation
        old_stats = getattr(self, "_last_pokemon_stats", None)

        def trigger_evo(method):
            """Manual evolution trigger from the PC Details panel."""
            readiness = evolution_readiness(pokemon)
            if readiness["ready"]:
                evo_window = EvoWindow(
                    self.logger,
                    self.settings,
                    self.main_pokemon,
                    self.translator,
                    self.reviewer_obj,
                    self.test_window,
                    services.achievements,
                )
                evo_window.ask_pokemon_evo(
                    pokemon["individual_id"], pokemon["id"], readiness["evo_id"]
                )

        # Build new widgets from components
        h_widget, stats_tabs, f_widget, current_stats = PokemonCollectionDetailsSplit(
            name=pokemon["name"],
            level=pokemon["level"],
            id=pokemon["id"],
            shiny=pokemon.get("shiny", False),
            ability=pokemon["ability"],
            type=pokemon["type"],
            detail_stats=detail_stats,
            attacks=pokemon["attacks"],
            base_experience=pokemon["base_experience"],
            growth_rate=pokemon["growth_rate"],
            ev=pokemon["ev"],
            iv=pokemon["iv"],
            gender=pokemon["gender"],
            nickname=pokemon.get("nickname"),
            individual_id=pokemon.get("individual_id"),
            pokemon_defeated=pokemon.get("pokemon_defeated", 0),
            everstone=pokemon.get("everstone", False),
            captured_date=pokemon.get("captured_date", "Missing"),
            language=int(self.settings.get("misc.language")),
            gif_in_collection=self.gif_in_collection,
            remove_levelcap=self.settings.get("misc.remove_level_cap"),
            logger=self.logger,
            refresh_callback=lambda: (
                self.refresh_gui(),
                self.show_pokemon_details(pokemon_stub),
            ),
            initial_tab_index=self.current_stats_tab_index,
            tab_changed_callback=self.on_stats_tab_changed,
            nature=pokemon.get("nature", "serious"),
            base_stats=pokemon.get("base_stats"),
            old_stats=old_stats,
            friendship=pokemon.get("friendship", 0),
            evolution_rejected=pokemon.get("evolution_rejected", False),
            trigger_evo_callback=trigger_evo,
            friendship_time_enabled=self.settings.get(
                "evolution.friendship_time_enabled", True
            ),
        )

        self._last_pokemon_stats = current_stats

        # Update Stacks (Zero Flicker Swap)
        def swap_stack_widget(stack, new_widget):
            old_widget = stack.currentWidget()
            stack.addWidget(new_widget)
            stack.setCurrentWidget(new_widget)
            if old_widget:
                stack.removeWidget(old_widget)
                old_widget.deleteLater()

        # 1. Update Header
        swap_stack_widget(self.header_stack, h_widget)

        # 2. Update Stats (No fade)
        swap_stack_widget(self.stats_stack, stats_tabs)

        # 3. Update Footer
        swap_stack_widget(self.footer_stack, f_widget)

        # --- Post-processing: Move Manager (Improvement B) ---
        self._integrate_move_manager(pokemon)

        # --- Post-processing: Nature Indicators (Improvement G) ---
        self._apply_nature_indicators(pokemon)

        # Set initial state for animation
        if not is_same_pokemon:
            self.header_opacity.setOpacity(0.0)

        self.details_widget.show()

        # Force layout recalculation
        self.details_container_layout.activate()

        # Animation Handling
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

        if not is_same_pokemon:
            # Header Animation
            self.header_fade = QPropertyAnimation(self.header_opacity, b"opacity")
            self.header_fade.setDuration(400)
            self.header_fade.setStartValue(0.0)
            self.header_fade.setEndValue(1.0)
            self.header_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.header_fade.start()
        else:
            self.header_opacity.setOpacity(1.0)

        # Selection indicator
        self._selected_individual_id = pokemon.get("individual_id")
        self._refresh_slot_selection()

    def _integrate_move_manager(self, pokemon):
        # Traverse layout to find TopR_layout_Box
        try:
            # NEW STRUCTURE: header_stack -> currentWidget (h_widget) -> layout -> first_layout -> TopR_layout_Box
            h_widget = self.header_stack.currentWidget()
            if h_widget:
                h_layout = h_widget.layout()
                # h_layout -> name_label (index 0), first_layout (index 1)
                if h_layout and h_layout.count() > 1:
                    top_r_layout = None
                    first_layout_item = h_layout.itemAt(1)
                    if first_layout_item and first_layout_item.layout():
                        first_layout = first_layout_item.layout()
                        # TopR_widget is at index 1 of first_layout (a QWidget wrapping TopR_layout_Box)
                        top_r_item = first_layout.itemAt(1)
                        if top_r_item and top_r_item.widget():
                            top_r_layout = top_r_item.widget().layout()

                    if top_r_layout is None:
                        return

                    # Add new Move Manager
                    def _save_pokemon(data):
                        services.db.save_pokemon(data)
                        # Keep the live main-pokemon singleton in step with a
                        # moveset edit, mirroring give_held_item/remove_held_item.
                        main_pkmn = services.main_pokemon
                        if (
                            main_pkmn is not None
                            and getattr(main_pkmn, "individual_id", None) == data.get("individual_id")
                        ):
                            main_pkmn.attacks = data.get("attacks", main_pkmn.attacks)
                        self.show_pokemon_details(pokemon)

                    # Retire the previous manager before building a new one.
                    # It is parented to the persistent details_widget (not the
                    # swapped header stack), so without this it would linger as
                    # an orphaned child and accumulate over the session.
                    if hasattr(self, "move_manager") and is_alive(self.move_manager):
                        self.move_manager.deleteLater()

                    # Initialize FIRST (this might be slightly heavy)
                    new_move_manager = MoveManagerWidget(
                        individual_id=pokemon["individual_id"],
                        pkmn_id=pokemon["id"],
                        logger=self.logger,
                        save_fn=_save_pokemon,
                        parent=self.details_widget,
                    )
                    self.move_manager = new_move_manager

                    # Hide old buttons and label ONLY after the new one is ready
                    evo_btn = None
                    for i in range(top_r_layout.count()):
                        item = top_r_layout.itemAt(i)
                        if item and item.widget():
                            if item.widget().objectName() == "evolveNowButton":
                                evo_btn = item.widget()
                                continue
                            item.widget().hide()

                    top_r_layout.addWidget(self.move_manager)
                    if evo_btn:
                        top_r_layout.removeWidget(evo_btn)
                        top_r_layout.addWidget(evo_btn)

        except Exception as e:
            self.logger.log("error", f"Error integrating move manager: {e}")

    def _apply_nature_indicators(self, pokemon):
        # Normalize to Title Case so lowercase DB values (e.g. "adamant") match
        raw_nature = pokemon.get("nature", "serious") or "serious"
        nature = raw_nature.strip().title()
        boosted, lowered = NATURE_EFFECTS.get(nature, ("", ""))
        if not boosted and not lowered:
            return

        try:
            # Use the persistent stack to find the active stats tab widget
            stats_tabs = self.stats_stack.currentWidget()
            if stats_tabs and isinstance(stats_tabs, QTabWidget):
                # The first tab is typically the "Base Stats" or "Stats" page
                stats_widget = stats_tabs.widget(0)
                labels = stats_widget.findChildren(QLabel)

                for label in labels:
                    # Strip any previously applied indicators before matching
                    raw_text = label.text()
                    clean_text = (
                        raw_text.replace(" ▲", "")
                        .replace(" ▼", "")
                        .replace(":", "")
                        .strip()
                    )
                    clean_text_lower = clean_text.lower()

                    stat_key = None
                    # First try exact match in lower case
                    for k, v in DISPLAY_TO_STAT_KEY.items():
                        if k.lower() == clean_text_lower:
                            stat_key = v
                            break

                    if not stat_key:
                        # Fallback for substring match (e.g. "Sp. Atk" in "Special-attack" or vice versa)
                        for k, v in DISPLAY_TO_STAT_KEY.items():
                            kl = k.lower()
                            if kl in clean_text_lower or clean_text_lower in kl:
                                stat_key = v
                                break

                    if stat_key:
                        if stat_key == boosted:
                            label.setText(f"{clean_text} ▲")
                            label.setStyleSheet("color: #4ade80; font-weight: bold;")
                        elif stat_key == lowered:
                            label.setText(f"{clean_text} ▼")
                            label.setStyleSheet("color: #f87171; font-weight: bold;")
        except Exception as e:
            self.logger.log("error", f"Error applying nature indicators: {e}")

    def on_stats_tab_changed(self, index: int):
        """Callback to remember which tab (Stats/IV/EV) is selected."""
        self.current_stats_tab_index = index

    def _update_bff(self):
        """Resolve the BFF (highest-friendship Pokémon) and update state."""
        self._bff_id = self._compute_bff_id()
        self._bff_dirty = False

    def _compute_bff_id(self):
        """Return the individual_id of the highest-friendship Pokémon."""
        try:
            cursor = services.db.execute(
                "SELECT individual_id FROM captured_pokemon "
                "WHERE CAST(json_extract(data, '$.friendship') AS INTEGER) > 0 "
                "ORDER BY CAST(json_extract(data, '$.friendship') AS INTEGER) DESC, "
                "rowid ASC LIMIT 1"
            )
            row = cursor.fetchone()
            return row["individual_id"] if row else None
        except Exception as e:
            if self.logger:
                self.logger.log("error", f"Error computing BFF: {e}")
            return None

    def _update_time_display(self):
        """Update the day/night label with current time."""
        if self.time_label is not None and is_alive(self.time_label):
            self.time_label.setText(current_time_label())

    def toggle_favorite(self, pokemon: dict[list, Any]):
        """
        Toggles the favorite status of a specific Pokémon in the saved Pokémon data.

        This method loads the current Pokémon list, finds the Pokémon by its unique individual ID,
        switches its "is_favorite" status, saves the updated list back to file, and refreshes the GUI.

        Args:
            pokemon (dict[list, Any]): A dictionary representing the Pokémon, expected to contain
                a unique "individual_id" key and a "name" key.

        Side Effects:
            - Updates the "is_favorite" status of the Pokémon in persistent storage.
            - Refreshes the GUI to reflect the change.
            - Logs an info message if the Pokémon is not found in the list.
        """
        target_pokemon = services.db.get_pokemon(pokemon["individual_id"])
        if target_pokemon:
            target_pokemon["is_favorite"] = not target_pokemon.get("is_favorite", False)
            services.db.save_pokemon(target_pokemon)
            self.refresh_gui()
            return

        if self.logger is not None:
            self.logger.log("info", f"Could not make/unmake {pokemon['name']} favorite")

    def give_held_item(self, pokemon_stub: dict):
        """
        Opens a window to select and give a held item to the specified Pokémon.

        This function reads the available items from the database, filters out
        non-holdable items (items with a non-None "type"), and presents the user with a
        selection window. Once an item is selected, it is assigned to the Pokémon, a
        confirmation message is shown, and the GUI is refreshed to reflect the change.

        Args:
            pokemon_stub (dict): A lightweight dictionary containing the pokemon's `individual_id`.

        Returns:
            None

        Side Effects:
            - Opens a modal `GiveItemWindow` for item selection.
            - Updates the Pokémon's held item via `PokemonObject.give_held_item`.
            - Logs and displays an info message using `ShowInfoLogger`.
            - Refreshes the GUI via `self.refresh_gui()`.
        """
        pokemon = services.db.get_pokemon(pokemon_stub["individual_id"])
        if not pokemon:
            return

        items_list = services.db.get_all_items()
        # Filter to holdable items (items without a type, stored in data field)
        items_names = []
        for item in items_list:
            item_data = item.get("data") or {}
            if item_data.get("type") is None:
                items_names.append(item.get("item_name") or item_data.get("item", ""))
        items_names = [n for n in items_names if n]  # Remove empty strings
        pokemon_obj = PokemonObject.from_dict(pokemon)

        def func(item_name: str):
            # Callback to handle item assignment and GUI refresh
            pokemon_obj.give_held_item(item_name)
            self.logger.log_and_showinfo(
                "info", f"{item_name} was given to {pokemon.get('name')}."
            )
            self.refresh_gui()

            # Refresh open item/bag/shop windows
            _refresh_open_item_windows()

        give_item_window = GiveItemWindow(
            item_list=items_names,
            give_item_func=lambda item_name: func(item_name),
            logger=self.logger,
        )
        give_item_window.exec()

    def remove_held_item(self, pokemon_stub: dict):
        """
        Removes the held item from the specified Pokémon.

        Converts the Pokémon dictionary into a `PokemonObject`, removes the held item,
        logs the change, and refreshes the GUI. If the Pokémon does not have a held item,
        raises a `ValueError`.

        Args:
            pokemon_stub (dict): A lightweight dictionary containing the pokemon's `individual_id`.

        Returns:
            None

        Raises:
            ValueError: If the Pokémon does not currently hold an item.

        Side Effects:
            - Updates the Pokémon's data to remove the held item.
            - Logs and displays an info message using `ShowInfoLogger`.
            - Refreshes the GUI via `self.refresh_gui()`.
        """
        pokemon = services.db.get_pokemon(pokemon_stub["individual_id"])
        if not pokemon:
            return

        pokemon_obj = PokemonObject.from_dict(pokemon)
        if pokemon.get("held_item") is None:
            raise ValueError("The pokemon does not hold an item.")
        pokemon_obj.remove_held_item()
        self.logger.log_and_showinfo(
            "info",
            f"{format_item_name(pokemon['held_item'])} was removed from {pokemon.get('name')}.",
        )

        # Refreshing the PC after giving the item is important in order to update the pokemon information without the held item
        self.refresh_gui()

        # Refresh open item/bag/shop windows
        _refresh_open_item_windows()

    def ensure_data_integrity(self):
        """
        Iterates through all Pokémon to ensure they have required non-stat fields,
        adding default values if fields are missing. This handles data
        from older addon versions. Stat-related fields are ignored.
        """
        pokemon_list = services.db.get_all_pokemon()
        if not pokemon_list:
            return

        # --- QUICK CHECK ---
        # First, quickly determine if any migration is needed at all.
        default_keys = {
            "nickname",
            "gender",
            "ability",
            "type",
            "attacks",
            "base_experience",
            "growth_rate",
            "everstone",
            "shiny",
            "captured_date",
            "individual_id",
            "mega",
            "special_form",
            "xp",
            "friendship",
            "pokemon_defeated",
            "tier",
            "is_favorite",
            "held_item",
            "cp",
        }

        is_migration_needed = any(
            key not in pokemon
            for pokemon in pokemon_list
            if isinstance(pokemon, dict)
            for key in default_keys
        )

        if not is_migration_needed:
            return  # All Pokémon are up-to-date, exit early.

        # --- FULL MIGRATION (only if needed) ---
        needs_update = False
        default_values = {
            "nickname": "",
            "gender": "N",
            "ability": "Illuminate",
            "type": ["Normal"],
            "attacks": ["Struggle"],
            "base_experience": 0,
            "growth_rate": "medium",
            "everstone": False,
            "shiny": False,
            "captured_date": None,
            "individual_id": lambda p: str(uuid.uuid4()),
            "mega": False,
            "special_form": None,
            "xp": 0,
            "friendship": 0,
            "pokemon_defeated": 0,
            "tier": lambda p: get_tier_by_id(p.get("id", 0)) or "Normal",
            "is_favorite": False,
            "held_item": None,
            "cp": lambda p: calculate_cp_from_dict(p),
        }

        for i, pokemon in enumerate(pokemon_list):
            if not isinstance(pokemon, dict):
                continue

            # Always recalculate CP to ensure it matches the current formula in business.py
            old_cp = pokemon.get("cp")
            new_cp = calculate_cp_from_dict(pokemon)
            if old_cp != new_cp:
                needs_update = True
                pokemon_list[i]["cp"] = new_cp

            for key, default_generator in default_values.items():
                if key not in pokemon:
                    needs_update = True
                    if callable(default_generator):
                        value = default_generator(pokemon)
                    else:
                        value = default_generator
                    pokemon_list[i][key] = value

        if needs_update:
            for pokemon in pokemon_list:
                services.db.save_pokemon(pokemon)

    def on_window_close(self):
        if self.pokemon_details_layout is not None:
            clear_layout(self.pokemon_details_layout)
            self.details_widget.setFixedSize(0, 0)
            self.pokemon_details_layout = None

    def reject(self):  # Called when pressing Escape
        import base64
        try:
            mw.pm.profile[self.GEOMETRY_KEY] = base64.b64encode(
                bytes(self.saveGeometry())
            ).decode()
        except Exception:
            pass
        self.on_window_close()
        super().reject()


class GiveItemWindow(QDialog):
    """
    Small window that opens up when the user gives an item to the Pokemon from a PC box
    """

    # Make it a class variable so it can be accessed from other classes
    NOT_YET_IMPLEMENTED_ITEMS = {
        "focus-sash",
        "focus-band",
        "white-herb",
        "mental-herb",
        "power-herb",
        "throat-spray",
        "weakness-policy",
    }

    def __init__(self, item_list: list[str], give_item_func: Callable, logger):
        super().__init__()
        self.setWindowTitle("Give an Item")
        self.resize(400, 400)

        # Outer layout for the dialog
        main_layout = QVBoxLayout(self)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        # Container widget inside scroll area
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        self.give_item_func = give_item_func
        self.logger = logger

        # Add item rows
        for item in item_list:
            row_layout = QHBoxLayout()

            item_label = QLabel(format_item_name(item))
            give_button = QPushButton(f"Give {format_item_name(item)}")
            give_button.clicked.connect(
                lambda clicked, i=item: self.expanded_give_item_func(i)
            )
            if (
                item in GiveItemWindow.NOT_YET_IMPLEMENTED_ITEMS
                or item.endswith("-berry")
                or item.endswith("-gem")
            ):
                # NOTE (Axil): As time of writing, single use items are not yet implemented.
                # It seems to me that, actually, they are not even implemented in the Poke-engine. Although
                # I haven't dug too much.
                # Therefore, for now, and hopefully as a not too permanent temporary fix, I will prevent the
                # user from giving out single-use items.
                give_button.setToolTip("Single use held items are not yet implemented.")
                give_button.setEnabled(False)
                give_button.clicked.connect(
                    lambda clicked: self.logger.log_and_showinfo(
                        "info", "Single use held items are not yet implemented."
                    )
                )

            row_layout.addWidget(item_label)
            row_layout.addStretch()
            row_layout.addWidget(give_button)

            # Optional: separate rows with a line
            row_frame = QFrame()
            row_frame.setLayout(row_layout)
            scroll_layout.addWidget(row_frame)

        scroll_content.setLayout(scroll_layout)
        scroll.setWidget(scroll_content)

        # Add scroll area to main layout
        main_layout.addWidget(scroll)
        self.setLayout(main_layout)

    def expanded_give_item_func(self, item_name: str):
        # Wrapper to close window after giving item
        self.give_item_func(item_name)
        self.close()
