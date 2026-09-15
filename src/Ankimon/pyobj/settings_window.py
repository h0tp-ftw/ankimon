import inspect
import json
import os
from typing import Union
from aqt.qt import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QHBoxLayout,
    QMainWindow,
    QScrollArea,
    QButtonGroup,
    QComboBox,
    QMessageBox,
    QPixmap,
    QPainter,
    QPainterPath,
    Qt,
    QRectF,
)

from aqt.utils import showWarning
from aqt import mw
from aqt.theme import theme_manager
from ..services import services
from ..utils import is_alive
from .settings import HUD_TOGGLE_AUTO_SYNC_KEYS


# create_rounded_pixmap function remains the same
def create_rounded_pixmap(source_pixmap, radius):
    if source_pixmap.isNull():
        return QPixmap()
    rounded = QPixmap(source_pixmap.size())
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    rect = QRectF(source_pixmap.rect())
    path.addRoundedRect(rect, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, source_pixmap)
    painter.end()
    return rounded


class SettingsWindow(QMainWindow):
    def __init__(
        self, config, set_config_callback, save_config_callback, load_config_callback
    ):
        super().__init__()
        self.config = config
        self.original_config = config.copy()
        self.save_config_callback = save_config_callback
        self.load_config = load_config_callback
        self.setWindowTitle("Settings")
        self.setMaximumWidth(600)
        self.setMaximumHeight(900)
        self.parent = mw
        self.explicit_hud_toggle_overrides = set()

        self.descriptions = self.load_descriptions()
        self.friendly_names = self.load_friendly_names()
        self.key_map = {v: k for k, v in self.friendly_names.items()}

        self.group_widgets = {}
        self.group_states = {}
        self.searchable_settings = []
        self.title_buttons = {}  # To store references to title buttons
        self.input_widgets = {}  # To store references to input widgets

        self.setup_ui()

    @property
    def is_dark_mode(self):
        """Checks if Anki is in dark mode."""
        return theme_manager.night_mode

    def _apply_stylesheet(self):
        """Applies the appropriate stylesheet based on the current theme."""
        if self.is_dark_mode:
            self.setStyleSheet("""
                QMainWindow, QWidget {
                    background-color: #2e2e2e;
                    color: #f0f0f0;
                }
                QLabel[class="setting-label"] {
                    font-weight: bold;
                    margin-top: 5px;
                    color: #f0f0f0;
                }
                QLabel[class="description-label"] {
                    color: #aaaaaa;
                    padding-left: 5px;
                }
                QRadioButton {
                    color: #f0f0f0;
                }
                QLineEdit {
                    background-color: #3c3c3c;
                    color: #f0f0f0;
                    border: 1px solid #555555;
                    padding: 4px;
                }
                QPushButton {
                    background-color: #4a4a4a;
                    border: 1px solid #555555;
                    padding: 5px;
                }
                QPushButton:hover {
                    background-color: #5a5a5a;
                }
                QPushButton[class="title-button"] {
                    font-weight: bold;
                    text-align: left;
                    border: none;
                    background-color: transparent;
                }
                QPushButton[class="title-button"][level="1"] {
                    font-size: 18px;
                    margin-top: 15px;
                    margin-bottom: 5px;
                    color: #87CEEB;
                }
                QPushButton[class="title-button"][level="2"] {
                    font-size: 14px;
                    margin-top: 10px;
                    padding-left: 15px;
                    color: #ADD8E6;
                }
            """)
        else:  # Light Mode
            self.setStyleSheet("""
                QMainWindow, QWidget {
                    background-color: #f5f5f5;
                    color: #212121;
                }
                QLabel[class="setting-label"] {
                    font-weight: bold;
                    margin-top: 5px;
                    color: #212121;
                }
                QLabel[class="description-label"] {
                    color: #666666;
                    padding-left: 5px;
                }
                QRadioButton {
                    color: #212121;
                }
                QLineEdit {
                    background-color: #ffffff;
                    color: #212121;
                    border: 1px solid #adadad;
                    padding: 4px;
                }
                QPushButton {
                    background-color: #e1e1e1;
                    border: 1px solid #adadad;
                    padding: 5px;
                }
                QPushButton:hover {
                    background-color: #cacaca;
                }
                QPushButton[class="title-button"] {
                    font-weight: bold;
                    text-align: left;
                    border: none;
                    background-color: transparent;
                }
                QPushButton[class="title-button"][level="1"] {
                    font-size: 18px;
                    margin-top: 15px;
                    margin-bottom: 5px;
                    color: #253D5B;
                }
                QPushButton[class="title-button"][level="2"] {
                    font-size: 14px;
                    margin-top: 10px;
                    padding-left: 15px;
                    color: #355882;
                }
            """)

    def load_descriptions(self):
        descriptions_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "lang",
            "setting_description.json",
        )
        if os.path.exists(descriptions_file):
            try:
                with open(descriptions_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                showWarning(f"Error reading descriptions file: {e}")
        return {}

    def load_friendly_names(self):
        names_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "lang", "setting_name.json"
        )
        if os.path.exists(names_file):
            try:
                with open(names_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                showWarning(f"Error reading friendly names file: {e}")
        return {}

    def _create_setting(self, key, layout):
        # A friendly name that is not yet mapped to a config key (e.g. its
        # setting_name.json entry has not landed) resolves to None — skip it
        # gracefully rather than raising.
        if key is None:
            return [], None, None
        value = self.config.get(key)
        friendly_name = self.friendly_names[key]
        description = self.descriptions.get(key, "No description available.")

        created_widgets = []
        label = QLabel(friendly_name)
        label.setProperty("class", "setting-label")
        description_label = QLabel(description)
        description_label.setWordWrap(True)
        description_label.setProperty("class", "description-label")
        description_label.setMaximumWidth(self.width() - 50)
        layout.addWidget(label)
        layout.addWidget(description_label)
        created_widgets.extend([label, description_label])

        if key == "misc.active_region":
            region_options = [
                (None, "No Region"),
                ("kanto", "Kanto (Gen 1)"),
                ("johto", "Johto (Gen 2)"),
                ("hoenn", "Hoenn (Gen 3)"),
                ("sinnoh", "Sinnoh (Gen 4)"),
                ("unova", "Unova (Gen 5)"),
                ("kalos", "Kalos (Gen 6)"),
                ("alola", "Alola (Gen 7)"),
                ("galar", "Galar (Gen 8)"),
                ("hisui", "Hisui (Gen 8)"),
                ("paldea", "Paldea (Gen 9)"),
            ]
            combo = QComboBox()
            for val, region_label in region_options:
                combo.addItem(region_label, userData=val)
            # Set current selection from config (may be None or a region
            # string). Deliberately NOT connected to currentIndexChanged:
            # self.config is the live Settings dict, so a live write would
            # leak the selection even when the user never saves. on_save reads
            # the widget via currentData() instead.
            current = self.config.get(key)
            for i, (val, _) in enumerate(region_options):
                if val == current:
                    combo.setCurrentIndex(i)
                    break
            self.input_widgets[key] = combo
            layout.addWidget(combo)
            created_widgets.append(combo)
            return created_widgets, friendly_name, description

        if isinstance(value, bool):
            radio_container = QWidget()
            h_layout = QHBoxLayout(radio_container)
            h_layout.setContentsMargins(0, 0, 0, 0)
            true_radio = QRadioButton("Enabled")
            false_radio = QRadioButton("Disabled")
            true_radio.setChecked(value)
            false_radio.setChecked(not value)
            button_group = QButtonGroup(self)
            button_group.addButton(true_radio, 1)
            button_group.addButton(false_radio, 0)
            if key.startswith("misc.gen"):
                button_group.buttonClicked.connect(lambda: self._on_gen_toggled())
            if key in HUD_TOGGLE_AUTO_SYNC_KEYS:
                button_group.buttonClicked.connect(
                    lambda _, hud_key=key: self._on_hud_toggle_clicked(hud_key)
                )
            h_layout.addWidget(true_radio)
            h_layout.addWidget(false_radio)
            layout.addWidget(radio_container)
            created_widgets.append(radio_container)
            self.input_widgets[key] = button_group
        elif isinstance(value, (int, str, float)):
            line_edit = QLineEdit(str(value))
            # Mask the API Key field if it's ever rendered in the legacy window
            if key == "leaderboard.api_key":
                line_edit.setEchoMode(QLineEdit.EchoMode.Password)
            layout.addWidget(line_edit)
            created_widgets.append(line_edit)
            self.input_widgets[key] = line_edit

        return created_widgets, friendly_name, description

    def _create_title(self, text, level=1):
        button = QPushButton(f" {text}")
        button.setCheckable(True)
        button.setChecked(True)
        button.setProperty("class", "title-button")
        button.setProperty("level", str(level))
        return button

    def setup_ui(self):
        """
        Builds the settings window interface, including the logo, search bar, hierarchical settings sections, and save control.
        """
        self.setMinimumSize(450, 600)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        self._apply_stylesheet()

        layout = QVBoxLayout(central_widget)
        image_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "user_files",
            "web",
            "images",
            "ankimon_logo.png",
        )
        image_label = QLabel()
        if os.path.exists(image_path):
            pixmap = QPixmap(image_path)
            scaled_pixmap = pixmap.scaledToWidth(
                250, Qt.TransformationMode.SmoothTransformation
            )
            rounded_pixmap = create_rounded_pixmap(scaled_pixmap, 15)
            image_label.setPixmap(rounded_pixmap)
        else:
            image_label.setText("Ankimon Logo Not Found")
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(image_label)
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search settings...")
        self.search_bar.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_bar)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area_content = QWidget()
        scroll_area_layout = QVBoxLayout(scroll_area_content)
        scroll_area.setWidget(scroll_area_content)
        hierarchical_groups = {
            "General": {
                "settings": [
                    "Trainer Name",
                    "Language",
                    "Show Tip of the Day On Startup",
                ],
                "subgroups": {
                    "Technical Settings": {
                        "settings": [
                            "SSH Access",
                            "Prevent Ankimon News on Startup",
                            "AnkiWeb Sync",
                            "Developer Mode",
                        ]
                    },
                    "Discord Integration": {
                        "settings": [
                            "Discord Rich Presence - Ankimon",
                            "Discord Rich Presence - Quote Type",
                        ]
                    },
                },
            },
            "Battle": {
                "settings": [
                    "Automatic Battle",
                    "Always Catch Wishlist",
                    "Always Catch: Legendary",
                    "Always Catch: Mythical",
                    "Always Catch: Ultra Beast",
                    "Always Catch: Starter",
                    "Always Catch: Mega Evolution",
                    "Always Catch: Gigantamax",
                    "Always Catch: Regional Form",
                    "Cards per Round",
                    "Review Based Damage",
                    "Friendship & Time Evolution",
                    "Auto-detect Time Zone",
                    "Time Zone UTC Offset",
                ],
                "subgroups": {
                    "Fight Hotkeys": {
                        "settings": [
                            "Key for Defeat",
                            "Key for Catching",
                            "Key for Team Cycling",
                            "Key for Opening/Closing Ankimon",
                            "Allow Choosing Moves",
                        ]
                    },
                    "Level Settings": {
                        "settings": [
                            "Remove Level Cap",
                        ]
                    },
                },
            },
            "Styling": {
                "settings": [
                    "Team Overview in Deck Overview",
                    "Animate Time",
                    "Show GIFs in Collection",
                    "Show Sprites Across Ankimon",
                ]
            },
            "HUD and Reviewer": {
                "settings": [
                    "Show Main Pokémon in Reviewer",
                    "Hide HUD on Reviewer Startup",
                    "Show Pokémon Buttons",
                    "Message Box Display Time",
                    "HP Bar Thickness",
                    "Reviewer Image as GIF",
                    "View Main Pokémon Front",
                    "XP Bar Location",
                    "Pop-Up on Defeat",
                    "Pop-Up on Item Receive",
                ],
                "subgroups": {
                    "HUD Element Toggles": {
                        "settings": [
                            "Show Player Sprite",
                            "Show Enemy Sprite",
                            "Show XP Progress Bar",
                            "Show HP Bars",
                            "Show HP Text",
                            "Show Pokémon ID",
                            "Show Pokémon Generation",
                            "Show Pokémon Level",
                            "Show Pokémon Name",
                            "Show Status Badge",
                            "Show Pokeball Icon",
                            "Show Enemy Shiny Star",
                            "Show Player Shiny Star",
                            "Show Text Message Box in Reviewer",
                            "Styling",
                        ]
                    }
                }
            },
            "Sound": {
                "settings": [
                    "Enable Sound Effects",
                    "Enable Sounds",
                    "Enable Battle Sounds",
                    "Volume",
                ]
            },
            "Study": {
                "settings": [
                    "Goal of Daily Average Cards",
                    "Card Max Time",
                    "Cash Reward Per Interval",
                    "Cards Per Cash Reward",
                ]
            },
            "Generations": {
                "settings": [
                    "Active Region",
                    "Generation 1",
                    "Generation 2",
                    "Generation 3",
                    "Generation 4",
                    "Generation 5",
                    "Generation 6",
                    "Generation 7",
                    "Generation 8",
                    "Generation 9",
                ]
            },
            "Leaderboard": {
                "settings": [
                    "Enable Leaderboard Sync",
                    "Username",
                    "API Key",
                ]
            },
        }
        for l1_title, l1_data in hierarchical_groups.items():
            self.group_states[l1_title] = True
            l1_widgets = []
            l1_button = self._create_title(l1_title, level=1)
            scroll_area_layout.addWidget(l1_button)
            self.title_buttons[l1_title] = l1_button
            for friendly_name in l1_data.get("settings", []):
                key = self.key_map.get(friendly_name)
                widgets, name, desc = self._create_setting(key, scroll_area_layout)
                if widgets:
                    l1_widgets.extend(widgets)
                    self.searchable_settings.append(
                        {
                            "widgets": widgets,
                            "friendly_name": name,
                            "description": desc,
                            "l1_title": l1_title,
                            "l2_title": None,
                        }
                    )
            if "subgroups" in l1_data:
                for l2_title, l2_data in l1_data["subgroups"].items():
                    self.group_states[l2_title] = True
                    l2_widgets = []
                    l2_button = self._create_title(l2_title, level=2)
                    scroll_area_layout.addWidget(l2_button)
                    self.title_buttons[l2_title] = l2_button
                    l1_widgets.append(l2_button)
                    for friendly_name in l2_data.get("settings", []):
                        key = self.key_map.get(friendly_name)
                        widgets, name, desc = self._create_setting(
                            key, scroll_area_layout
                        )
                        if widgets:
                            l1_widgets.extend(widgets)
                            l2_widgets.extend(widgets)
                            self.searchable_settings.append(
                                {
                                    "widgets": widgets,
                                    "friendly_name": name,
                                    "description": desc,
                                    "l1_title": l1_title,
                                    "l2_title": l2_title,
                                }
                            )
                    self.group_widgets[l2_title] = l2_widgets
                    l2_button.clicked.connect(
                        lambda _, t=l2_title, b=l2_button: (
                            self._toggle_group_visibility(t, b)
                        )
                    )
            self.group_widgets[l1_title] = l1_widgets
            l1_button.clicked.connect(
                lambda _, t=l1_title, b=l1_button: self._toggle_group_visibility(t, b)
            )
        scroll_area_layout.addStretch()
        layout.addWidget(scroll_area)
        save_button = QPushButton("Save")
        save_button.setToolTip("Click to save your settings.")
        save_button.clicked.connect(self.on_save)
        layout.addWidget(save_button)

    def show_window(self):
        self._apply_stylesheet()
        self.config = self.load_config()
        self._refresh_widgets()
        self.show()
        self.raise_()

    def _refresh_widgets(self):
        """Update all input widgets with the current values from self.config."""
        for key, widget in self.input_widgets.items():
            if key not in self.config:
                continue
            value = self.config[key]
            if isinstance(widget, QLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, QButtonGroup):
                # Buttons are registered with id 1 = Enabled, 0 = Disabled.
                button = widget.button(1 if bool(value) else 0)
                if button is not None:
                    button.setChecked(True)
            elif isinstance(widget, QComboBox):
                for i in range(widget.count()):
                    if widget.itemData(i) == value:
                        widget.setCurrentIndex(i)
                        break

        # Gate the region options on the generation toggles only after ALL
        # widgets reflect self.config (the gating reads the gen widgets).
        region_combo = self.input_widgets.get("misc.active_region")
        if region_combo is not None:
            self._refresh_region_dropdown(region_combo)

    def _refresh_live_windows(self):
        """Refresh native Ankimon windows that depend on sprite visibility."""
        from ..utils import is_alive
        from .. import singletons

        try:
            if is_alive(services.pokemon_pc):
                services.pokemon_pc.refresh_gui()
        except Exception:
            pass

        try:
            if is_alive(services.reviewer):
                services.reviewer.refresh_hud()
        except Exception:
            pass

        try:
            if services.trainer_card is not None:
                services.trainer_card.refresh()
        except Exception:
            pass

        try:
            achievement_win = singletons._WINDOW_CACHE.get("achievement_bag")
            if is_alive(achievement_win):
                achievement_win.renewWidgets()
        except Exception:
            pass

        try:
            settings_win = singletons._WINDOW_CACHE.get("settings_window")
            if is_alive(settings_win) and settings_win is not self:
                settings_win.config = settings_win.load_config()
                settings_win._refresh_widgets()
        except Exception:
            pass

        # Refresh the live AnkimonItemsWeb view if it is open. The web shell
        # displays the reviewer and PC box content, both of which depend on
        # the sprite visibility setting.
        try:
            items_web = singletons._items_web_window
            if is_alive(items_web):
                show_sprites = self.config.get(
                    "gui.show_sprites_across_ankimon", True
                )
                # The web shell exposes refresh_content() to rebuild its UI
                # based on the current sprite setting.
                if hasattr(items_web, "refresh_content"):
                    items_web.refresh_content()
                elif hasattr(items_web, "set_sprite_visibility"):
                    items_web.set_sprite_visibility(show_sprites)
                else:
                    # Fallback: apply sprite visibility first, then refresh data
                    if hasattr(items_web, "_apply_sprite_visibility"):
                        items_web._apply_sprite_visibility(show_sprites)
                    if hasattr(items_web, "update_ui_data"):
                        items_web.update_ui_data()
        except Exception:
            pass

    def _gen_enabled_in_ui(self, gen_key):
        """Current (possibly unsaved) state of a generation toggle, read from
        its widget; falls back to the stored config when no widget exists."""
        widget = self.input_widgets.get(gen_key)
        if isinstance(widget, QButtonGroup):
            return widget.checkedId() == 1
        return bool(self.config.get(gen_key, True))

    def _on_gen_toggled(self):
        # Refresh the region dropdown to reflect the (unsaved) generation
        # toggles. Deliberately does NOT write the toggles into self.config:
        # that dict is the live Settings config, so mutating it here would
        # leak unsaved UI state (on_save reads the widgets when saving).
        region_combo = self.input_widgets.get("misc.active_region")
        if region_combo is not None:
            self._refresh_region_dropdown(region_combo)

    def _on_hud_toggle_clicked(self, key):
        # Track explicit HUD toggle changes so the autosync rule does not
        # override a user choice made in the same save transaction.
        self.explicit_hud_toggle_overrides.add(key)

    def _refresh_region_dropdown(self, combo):
        region_to_gen = {
            "kanto": "misc.gen1",
            "johto": "misc.gen2",
            "hoenn": "misc.gen3",
            "sinnoh": "misc.gen4",
            "unova": "misc.gen5",
            "kalos": "misc.gen6",
            "alola": "misc.gen7",
            "galar": "misc.gen8",
            "hisui": "misc.gen8",
            "paldea": "misc.gen9",
        }
        model = combo.model()
        current_region = combo.itemData(combo.currentIndex())

        should_reset = False
        for i in range(combo.count()):
            val = combo.itemData(i)
            if val in region_to_gen:
                is_gen_enabled = self._gen_enabled_in_ui(region_to_gen[val])
                model.item(i).setEnabled(is_gen_enabled)
                if not is_gen_enabled and val == current_region:
                    should_reset = True

        if should_reset:
            combo.setCurrentIndex(0)  # Reset to "No Region"

    def _on_search_changed(self, text):
        search_term = text.lower().strip()
        if not search_term:
            for setting in self.searchable_settings:
                for widget in setting["widgets"]:
                    widget.setVisible(True)
            for title, button in self.title_buttons.items():
                button.setVisible(True)
                is_expanded = self.group_states.get(title, True)
                for w in self.group_widgets.get(title, []):
                    w.setVisible(is_expanded)
            return

        for setting in self.searchable_settings:
            for widget in setting["widgets"]:
                widget.setVisible(False)
        for button in self.title_buttons.values():
            button.setVisible(False)

        titles_to_show = set()
        for setting in self.searchable_settings:
            name = setting["friendly_name"].lower()
            desc = setting["description"].lower()
            if search_term in name or search_term in desc:
                for widget in setting["widgets"]:
                    widget.setVisible(True)
                titles_to_show.add(setting["l1_title"])
                if setting["l2_title"]:
                    titles_to_show.add(setting["l2_title"])

        for title in titles_to_show:
            if title in self.title_buttons:
                self.title_buttons[title].setVisible(True)

    def _toggle_group_visibility(self, title, button):
        is_expanded = not self.group_states.get(title, True)
        self.group_states[title] = is_expanded
        if title in self.group_widgets:
            for widget in self.group_widgets[title]:
                widget.setVisible(is_expanded)

    def on_save(self) -> Union[int, str]:
        # Load the CURRENT live config as our baseline
        live_config = self.load_config()
        # Create a detached working copy for all validation/coercion
        working_config = dict(live_config)
        original_config = dict(live_config)

        # Update working_config from the current state of all UI widgets
        for key, widget in self.input_widgets.items():
            original_value = original_config.get(key)

            if isinstance(widget, QLineEdit):
                new_text = widget.text().strip()

                if key == "battle.cards_per_round":
                    # Single Value
                    try:
                        new_value = int(new_text)
                        working_config[key] = 1 if new_value == 0 else new_value
                    # Range Value
                    except ValueError:
                        if "-" in new_text:
                            try:
                                first_val, second_val = map(int, new_text.split("-", 1))
                                low = min(first_val, second_val)
                                high = max(first_val, second_val)
                                working_config[key] = f"{low}-{high}"
                            except ValueError:
                                working_config[key] = original_value
                        else:
                            # Cannot decode input – fallback
                            working_config[key] = original_value

                # Standard handling for other settings
                elif type(original_value) is int:
                    try:
                        working_config[key] = int(new_text)
                    except ValueError:
                        working_config[key] = original_value
                elif type(original_value) is float:
                    try:
                        working_config[key] = float(new_text)
                    except ValueError:
                        working_config[key] = original_value
                else:
                    working_config[key] = str(new_text)
            elif isinstance(widget, QButtonGroup):
                working_config[key] = widget.checkedId() == 1
            elif isinstance(widget, QComboBox):
                working_config[key] = widget.currentData()

        # --- Enforce bounds for cash rewards ---
        has_adjustments = False
        adjustment_msg = ""

        # 1. Validate Interval
        if "trainer.cash_reward_interval" in working_config:
            try:
                orig_val = int(working_config["trainer.cash_reward_interval"])
                new_val = max(5, min(100, orig_val))
                if new_val != orig_val:
                    working_config["trainer.cash_reward_interval"] = new_val
                    has_adjustments = True
                    adjustment_msg += (
                        f"- Reward Interval: Adjusted to {new_val} (Range: 5-100)\n"
                    )
            except (ValueError, TypeError):
                working_config["trainer.cash_reward_interval"] = 10

        # 2. Validate Amount & Cheat Threshold
        if "trainer.cash_reward_amount" in working_config:
            try:
                orig_amount = int(working_config["trainer.cash_reward_amount"])
                # Hard bounds
                new_amount = max(10, min(400, orig_amount))

                # Cheat Threshold
                interval = int(working_config.get("trainer.cash_reward_interval", 10))
                daily_average = int(working_config.get("battle.daily_average", 100))
                if daily_average <= 0:
                    daily_average = 100
                max_per_card = 400.0 / daily_average
                max_allowed = max(1, int(interval * max_per_card))
                if new_amount > max_allowed:
                    new_amount = max_allowed
                    has_adjustments = True
                    adjustment_msg += f"- Reward Amount: Capped at {new_amount}¥ to maintain the maximum daily economy limit.\n"
                elif new_amount != orig_amount:
                    has_adjustments = True
                    adjustment_msg += (
                        f"- Reward Amount: Adjusted to {new_amount}¥ (Range: 10-400)\n"
                    )

                working_config["trainer.cash_reward_amount"] = new_amount
            except (ValueError, TypeError):
                working_config["trainer.cash_reward_amount"] = 100

        if has_adjustments:
            # Update UI widgets to reflect capped values
            for key in ["trainer.cash_reward_interval", "trainer.cash_reward_amount"]:
                if key in self.input_widgets and isinstance(
                    self.input_widgets[key], QLineEdit
                ):
                    self.input_widgets[key].setText(str(working_config[key]))

            QMessageBox.warning(
                self,
                "Settings Adjusted",
                f"Some values were adjusted to stay within fair play bounds:\n\n{adjustment_msg}",
            )

        # Check if all generations are disabled
        gen_keys = [f"misc.gen{i}" for i in range(1, 10)]
        all_gens_disabled = all(working_config.get(key) is False for key in gen_keys)

        if all_gens_disabled:
            showWarning(
                "You must enable at least one Pokémon generation. Reverting generations to previous settings."
            )
            for key in gen_keys:
                # Revert logic
                working_config[key] = original_config.get(key, True)
                # Update UI widgets
                if key in self.input_widgets and isinstance(
                    self.input_widgets[key], QButtonGroup
                ):
                    group = self.input_widgets[key]
                    for button in group.buttons():
                        if button.text() == "Enabled" and working_config[key]:
                            button.setChecked(True)
                        elif button.text() == "Disabled" and not working_config[key]:
                            button.setChecked(True)

        # Preserve HUD toggles explicitly changed by the user in this save.
        # This avoids overwriting manual overrides when the Show Sprites setting
        # also triggers automatic sync behavior.
        explicit_overrides = set(self.explicit_hud_toggle_overrides)

        # Only persist if there are actual changes
        changed = any(
            working_config.get(key) != original_config.get(key)
            for key in working_config
        )

        if changed:
            self.save_config_callback(working_config, explicit_overrides)
            self.explicit_hud_toggle_overrides.clear()
            # Reload the final config state from the live settings object
            self.config = self.load_config()
            self._refresh_widgets()

            # Refresh the reviewer UI so hotkey changes (incl. team-cycle) take
            # effect without a restart. Reviewer builds that support team cycling
            # take a 4th argument; detect that by signature arity rather than by
            # calling and catching TypeError — an *internal* TypeError would also
            # trigger such a fallback, re-running the setup and double-wrapping
            # Reviewer._shortcutKeys.
            try:
                from ..reviewer_ui import setup_reviewer_ui

                catch_key = self.config.get("controls.catch_key", "6")
                defeat_key = self.config.get("controls.defeat_key", "5")
                pokemon_buttons = self.config.get("controls.pokemon_buttons", True)
                team_cycle_key = self.config.get("controls.team_cycle_key", "9")
                if len(inspect.signature(setup_reviewer_ui).parameters) >= 4:
                    setup_reviewer_ui(
                        catch_key, defeat_key, pokemon_buttons, team_cycle_key
                    )
                else:
                    setup_reviewer_ui(catch_key, defeat_key, pokemon_buttons)
            except Exception as e:
                print(f"Ankimon: Failed to refresh hotkeys: {e}")

            # Emit a shared settings-change notification for diagnostics only.
            try:
                from ..events import events
                # Pass a detached copy so buffered events retain values from each save
                events.emit("settings_changed", config=dict(self.config))
            except Exception as e:
                # Best-effort — settings still saved even if the event fails.
                print(f"Ankimon: Failed to emit settings_changed event: {e}")

            # Refresh already-open native windows that depend on sprite visibility.
            self._refresh_live_windows()

            # Show confirmation message using the original_config baseline
            excluded_patterns = {
                "mypokemon",
                "mainpokemon",
                "pokemon_collection",
                "trainer.cash",
                "misc.last_tip_index",
                "trainer.xp_share",
            }
            changed_settings = {
                key: self.config[key]
                for key in self.config
                if not any(pattern in key for pattern in excluded_patterns)
                and (
                    self.config[key] != original_config.get(key)
                    or type(self.config[key]) is not type(original_config.get(key))
                )
            }

            if changed_settings:
                from ..ankimon_items_web.settings_schema import display_setting_value

                friendly_changed = {
                    self.friendly_names.get(k, k): display_setting_value(k, v)
                    for k, v in changed_settings.items()
                }
                changed_message = "\n".join(
                    [f"{key}: {value}" for key, value in friendly_changed.items()]
                )
                QMessageBox.information(
                    self, "Settings Saved", "Your settings have been saved successfully."
                )
                QMessageBox.information(
                    self, "Config changes", f"Changed settings:\n{changed_message}"
                )
                # Update baseline only after successful comparison
                self.original_config = self.config.copy()
            else:
                QMessageBox.information(self, "No Changes", "No settings were changed.")
                # Still update baseline if no changes were detected
                self.original_config = self.config.copy()
        else:
            # Clear stale HUD override keys when the user restores a toggle before saving
            self.explicit_hud_toggle_overrides.clear()
            QMessageBox.information(self, "No Changes", "No settings were changed.")
