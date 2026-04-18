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
    QMessageBox,
    QPixmap,
    QPainter,
    QPainterPath,
    Qt,
    QRectF,
    QComboBox,
)

from aqt.utils import showWarning
from aqt import mw
from aqt.theme import theme_manager
from .update_manager import UpdateManager


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
        value = self.config[key]
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

        if isinstance(value, bool):
            radio_container = QWidget()
            h_layout = QHBoxLayout(radio_container)
            h_layout.setContentsMargins(0, 0, 0, 0)
            true_radio = QRadioButton("Enabled")
            false_radio = QRadioButton("Disabled")
            true_radio.setChecked(value)
            false_radio.setChecked(not value)
            button_group = QButtonGroup(self)
            button_group.addButton(true_radio)
            button_group.addButton(false_radio)
            h_layout.addWidget(true_radio)
            h_layout.addWidget(false_radio)
            layout.addWidget(radio_container)
            created_widgets.append(radio_container)
            self.input_widgets[key] = button_group
        elif isinstance(value, (int, str, float)):
            line_edit = QLineEdit(str(value))
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
                            "Ankimon Leaderboard",
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
                    "Cards per Round",
                    "Show Main Pokémon in Reviewer",
                    "Show Pokémon Buttons",
                    "Pop-Up on Defeat",
                    "Show Text Message Box in Reviewer",
                    "Message Box Display Time",
                    "Review Based Damage",
                ],
                "subgroups": {
                    "Fight Hotkeys": {
                        "settings": [
                            "Key for Defeat",
                            "Key for Catching",
                            "Key for Opening/Closing Ankimon",
                            "Allow Choosing Moves",
                        ]
                    },
                    "HP, XP and Level Settings": {
                        "settings": [
                            "HP Bar Configuration",
                            "XP Bar Configuration",
                            "XP Bar Location",
                            "Remove Level Cap",
                        ]
                    },
                },
            },
            "Styling": {
                "settings": [
                    "Styling in Reviewer",
                    "Team Overview in Deck Overview",
                    "Animate Time",
                    "HP Bar Thickness",
                    "Reviewer Image as GIF",
                    "View Main Pokémon Front",
                    "Show GIFs in Collection",
                ]
            },
            "Sound": {
                "settings": [
                    "Enable Sound Effects",
                    "Enable Sounds",
                    "Enable Battle Sounds",
                    "Volume",
                ]
            },
            "Study": {"settings": ["Goal of Daily Average Cards", "Card Max Time"]},
            "Generations": {
                "settings": [
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
            
        # ---- Update Ankimon Widget ----
        self.update_manager = UpdateManager(mw.logger)
        update_group = QWidget()
        update_layout = QVBoxLayout(update_group)
        
        update_title = QLabel("Update Ankimon")
        update_title.setProperty("class", "setting-label")
        update_title.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 10px;")
        update_layout.addWidget(update_title)
        
        curr_version_info = self.update_manager.get_current_version_info()
        self.version_label = QLabel(f"Current: {curr_version_info}")
        update_layout.addWidget(self.version_label)
        
        self.check_latest_btn = QPushButton("Check for Updates")
        self.check_latest_btn.clicked.connect(self._do_check_latest)
        update_layout.addWidget(self.check_latest_btn)
        
        self.update_btn = QPushButton("Update to Latest Experimental")
        self.update_btn.clicked.connect(self._do_update_standard)
        update_layout.addWidget(self.update_btn)
        
        self.show_dev_opts_btn = QPushButton("Show Developer Update Options")
        self.show_dev_opts_btn.clicked.connect(self._toggle_dev_update_opts)
        self.show_dev_opts_btn.setVisible(self.config.get("misc.developer_mode", False))
        update_layout.addWidget(self.show_dev_opts_btn)
        
        self.dev_update_group = QWidget()
        dev_layout = QVBoxLayout(self.dev_update_group)
        dev_layout.setContentsMargins(0,0,0,0)
        
        dev_label = QLabel("Developer Git Update")
        dev_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        dev_layout.addWidget(dev_label)
        
        source_hlayout = QHBoxLayout()
        source_hlayout.setContentsMargins(0,0,0,0)
        self.source_type_cb = QComboBox()
        self.source_type_cb.addItems(["Current Main", "Pull Request", "Past Versions (Tags)", "Branch", "Hash"])
        self.source_type_cb.currentTextChanged.connect(self._on_source_type_change)
        source_hlayout.addWidget(self.source_type_cb)
        
        self.source_value_cb = QComboBox()
        self.source_value_cb.setEditable(True)
        source_hlayout.addWidget(self.source_value_cb)
        self.source_value_cb.hide()
        
        self.source_value_input = QLineEdit()
        self.source_value_input.setPlaceholderText("Enter Git Hash...")
        self.source_value_input.hide()
        source_hlayout.addWidget(self.source_value_input)
        
        dev_layout.addLayout(source_hlayout)
        
        self.dev_update_btn = QPushButton("Update via Git")
        self.dev_update_btn.clicked.connect(self._do_update_git)
        dev_layout.addWidget(self.dev_update_btn)
        
        update_layout.addWidget(self.dev_update_group)
        self.dev_update_group.setVisible(False)
        
        scroll_area_layout.addWidget(update_group)
        
        # We need to refresh the Github refs if Dev Mode is open, but do it asynchronously or on click usually.
        # For simplicity, we can load it on demand or leave it empty so the user can just type the branch name as editable.
        # But we'll try to populate MAIN immediately.

        scroll_area_layout.addStretch()
        layout.addWidget(scroll_area)
        save_button = QPushButton("Save")
        save_button.setToolTip("Click to save your settings.")
        save_button.clicked.connect(self.on_save)
        layout.addWidget(save_button)

    def show_window(self):
        self._apply_stylesheet()
        self.config = self.load_config()
        self.show()
        self.raise_()

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
        # Update self.config from the current state of all UI widgets
        for key, widget in self.input_widgets.items():
            original_value = self.original_config.get(key)

            if isinstance(widget, QLineEdit):
                new_text = widget.text().strip()

                if key == "battle.cards_per_round":
                    # Single Value
                    try:
                        new_value = int(new_text)
                        self.config[key] = 1 if new_value == 0 else new_value
                    # Range Value
                    except ValueError:
                        if "-" in new_text:
                            try:
                                first_val, second_val = map(int, new_text.split("-", 1))
                                low = min(first_val, second_val)
                                high = max(first_val, second_val)
                                self.config[key] = f"{low}-{high}"
                            except ValueError:
                                self.config[key] = 2
                        else:
                            # Cannot decode input – fallback
                            self.config[key] = original_value

                # Standard handling for other settings
                elif isinstance(original_value, int):
                    try:
                        self.config[key] = int(new_text)
                    except ValueError:
                        self.config[key] = original_value
                elif isinstance(original_value, float):
                    try:
                        self.config[key] = float(new_text)
                    except ValueError:
                        self.config[key] = original_value
                else:
                    self.config[key] = str(new_text)
            elif isinstance(widget, QButtonGroup):
                self.config[key] = widget.checkedButton().text() == "Enabled"

        # Now that self.config is up-to-date, call the save callback
        self.save_config_callback(self.config)

        # The rest is for showing the confirmation message
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
            and self.config[key] != self.original_config.get(key)
        }

        if changed_settings:
            friendly_changed = {
                self.friendly_names.get(k, k): v for k, v in changed_settings.items()
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
            self.original_config = self.config.copy()
        else:
            QMessageBox.information(self, "No Changes", "No settings were changed.")

    def _on_source_type_change(self, text):
        from aqt.operations import QueryOp
        self.source_value_input.hide()
        self.source_value_cb.hide()
        
        if text == "Current Main":
            pass # No input needed
        elif text == "Hash":
            self.source_value_input.show()
        elif text in ["Past Versions (Tags)", "Branch", "Pull Request"]:
            self.source_value_cb.clear()
            self.source_value_cb.show()
            self.source_value_cb.addItem("Loading...")
            
            def fetch_items():
                if text == "Past Versions (Tags)": return self.update_manager.fetch_github_tags()
                elif text == "Branch": return self.update_manager.fetch_github_branches()
                elif text == "Pull Request": return self.update_manager.fetch_github_prs()
                return []
                
            def on_done(items):
                self.source_value_cb.clear()
                self.source_value_cb.addItems(items)
                
            QueryOp(parent=self, op=lambda _: fetch_items(), success=on_done).without_collection().run_in_background()

    def _toggle_dev_update_opts(self):
        if self.dev_update_group.isVisible():
            self.dev_update_group.setVisible(False)
            self.show_dev_opts_btn.setText("Show Developer Update Options")
        else:
            if not self.update_manager.check_for_git():
                QMessageBox.warning(self, "Git Required", "Git is not installed or not available in your system path.\n\nDeveloper update options require Git.")
                return
            self.dev_update_group.setVisible(True)
            self.show_dev_opts_btn.setText("Hide Developer Update Options")

    def _do_check_latest(self):
        from aqt.operations import QueryOp
        self.check_latest_btn.setText("Checking...")
        self.check_latest_btn.setEnabled(False)
        def fetch():
            return self.update_manager.get_latest_experimental_tag()
        def on_done(tag):
            self.check_latest_btn.setEnabled(True)
            self.check_latest_btn.setText("Check for Updates")
            if tag:
                QMessageBox.information(self, "Update Check", f"Latest experimental release: {tag}\n\nClick 'Update to Latest...' to install it.")
                self.update_btn.setText(f"Update to {tag}")
            else:
                QMessageBox.warning(self, "Update Check", "Failed to fetch latest release from GitHub.")
                
        QueryOp(parent=self, op=lambda _: fetch(), success=on_done).without_collection().run_in_background()

    def _do_update_standard(self):
        reply = QMessageBox.question(self, 'Confirm Update', 'Would you like to fetch and install the latest experimental version?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._execute_update(is_git=False)

    def _do_update_git(self):
        reply = QMessageBox.question(self, 'Confirm Update', 'Would you like to perform a git fetch and override the current installation?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._execute_update(is_git=True)
            
    def _execute_update(self, is_git):
        from aqt.operations import QueryOp
        mw.progress.start(label="Updating Ankimon...", immediate=True)
        
        def run_update():
            # 1. create backup
            if not self.update_manager.create_pre_update_backup():
                return False, "Failed to create pre-update backup."
                
            # 2. fetch new source
            if is_git:
                if not self.update_manager.check_for_git():
                    return False, "Git is not installed or accessible on your system."
                source_type = self.source_type_cb.currentText()
                
                type_map = {
                    "Current Main": "MAIN",
                    "Past Versions (Tags)": "TAG",
                    "Branch": "BRANCH",
                    "Pull Request": "PR",
                    "Hash": "HASH"
                }
                backend_type = type_map.get(source_type, "MAIN")
                
                if backend_type in ["TAG", "BRANCH", "PR"]:
                    source_value = self.source_value_cb.currentText()
                elif backend_type == "HASH":
                    source_value = self.source_value_input.text()
                else: 
                    source_value = "main"
                success, result = self.update_manager.fetch_git_update(backend_type, source_value)
            else:
                success, result = self.update_manager.fetch_standard_update()
                
            if not success:
                return False, result
                
            # 3. Apply update
            extracted_folder = result
            return self.update_manager.apply_update(extracted_folder)

        def on_done(res):
            mw.progress.finish()
            success, msg = res
            if success:
                QMessageBox.information(self, "Update Successful", msg)
                mw.close()
            else:
                QMessageBox.critical(self, "Update Failed", msg)

        QueryOp(parent=self, op=lambda _: run_update(), success=on_done).without_collection().run_in_background()
