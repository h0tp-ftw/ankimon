from pathlib import Path
import random
import json
import csv
from typing import Any, Optional

from aqt import mw
from aqt.qt import (
    QGridLayout,
    QPixmap,
    Qt,
)
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
    QComboBox,
    QLineEdit,
    QScrollArea,
    QInputDialog,
)

from ..pyobj.evolution_window import EvoWindow
from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.pc_box import GiveItemWindow
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.settings import Settings
from ..pyobj.starter_window import StarterWindow

from ..business import (
    get_id_and_description_by_item_name
)
from ..functions.pokedex_functions import (
    search_pokedex_by_id,
    return_id_for_item_name,
    check_evolution_by_item,
    find_details_move
)

from ..resources import icon_path, items_path, csv_file_items_cost, poke_evo_path
from ..functions.badges_functions import check_for_badge, receive_badge
from ..functions.pokemon_functions import save_fossil_pokemon
from ..services import services
from ..utils import play_effect_sound, is_alive
from .error_handler import show_warning_with_traceback

# At the moment when I write this line, "UserRole" is defined as UserRole 1000 in the Ankimon __init__.py file. IDK what it's about.
UserRole = 1000

class ItemWindow(QWidget):
    def __init__(
            self,
            logger: ShowInfoLogger,
            settings_obj: Settings,
            main_pokemon: PokemonObject,
            enemy_pokemon: PokemonObject,
            achievements: dict[str, bool],
            starter_window: StarterWindow,
            evo_window: EvoWindow,
            ):
        super().__init__()
        self.logger: ShowInfoLogger = logger
        self.settings_obj: Settings = settings_obj
        self.main_pokemon: PokemonObject = main_pokemon
        self.enemy_pokemon: PokemonObject = enemy_pokemon
        self.achievements: dict[str, bool] = achievements
        self.starter_window: StarterWindow = starter_window
        self.evo_window: EvoWindow = evo_window
        self._item_action_in_progress = False
        self._pokemon_choices_cache = None
        self.initUI()

    def initUI(self):
        self.hp_heal_items = {
            'potion': 20,
            'sweet-heart': 20,
            'berry-juice': 20,
            'fresh-water': 30,
            'soda-pop': 50,
            'super-potion': 60,
            'energy-powder': 60,
            'lemonade': 70,
            'moomoo-milk': 100,
            'hyper-potion': 120,
            'energy-root': 120,
            'full-restore': 1000,
            'max-potion': 1000
        }

        self.fossil_pokemon = {
            "helix-fossil": 138,
            "dome-fossil": 140,
            "old-amber": 142,
            "root-fossil": 345,
            "claw-fossil": 347,
            "skull-fossil": 408,
            "armor-fossil": 410,
            "cover-fossil": 564,
            "plume-fossil": 566
        }

        self.pokeball_chances = {
            'dive-ball': 11,      # Increased chance when fishing or underwater
            'dusk-ball': 11,      # Increased chance at night or in caves
            'great-ball': 12,     # Increased catch rate (original was 9, now 12)
            'heal-ball': 12,      # Same as a Poké Ball but heals the Pokémon
            'iron-ball': 12,      # Used for Steel-type Pokémon, 1.5x chance
            'light-ball': 1,      # Not actually used for catching Pokémon; it's an item
            'luxury-ball': 12,    # Same as a Poké Ball but increases happiness
            'master-ball': 100,   # Guarantees a successful catch (100% chance)
            'nest-ball': 12,      # Works better on lower-level Pokémon
            'net-ball': 12,       # Higher chance for Water- and Bug-type Pokémon
            'poke-ball': 8,       # Increased chance from 5 to 8
            'premier-ball': 8,    # Same as Poké Ball, but it's a special ball
            'quick-ball': 13,     # High chance if used at the start of battle
            'repeat-ball': 12,    # Higher chance on Pokémon that have been caught before
            'safari-ball': 8,     # Used in Safari Zone, with a fixed catch rate
            'smoke-ball': 1,      # Used to flee from wild battles, no catch chance
            'timer-ball': 13,     # Higher chance the longer the battle goes
            'ultra-ball': 13      # Increased catch rate (original was 10, now 13)
        }

        self.evolution_items = set()
        self.load_evolution_items()
        self.ability_items = {"ability-capsule", "ability-patch"}

        self.setWindowIcon(QIcon(str(icon_path)))  # Add a Pokeball icon
        self.setWindowTitle("Itembag")
        self.layout = QVBoxLayout()  # Main layout is now a QVBoxLayout

        # Search Filter
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search Items...")
        self.search_edit.returnPressed.connect(self.filter_items)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.filter_items)

        # Add dropdown menu for generation filtering
        self.category = QComboBox()
        self.category.addItem("All")
        self.category.addItems(["Fossils", "TMs and HMs", "Heal", "Evolution Items"])
        self.category.currentIndexChanged.connect(self.filter_items)

        # Add widgets to layout
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(self.search_edit)
        filter_layout.addWidget(self.search_button)
        filter_layout.addWidget(self.category)
        self.layout.addLayout(filter_layout)

        # Create the scroll area and its properties
        self.scrollArea = QScrollArea(self)
        self.scrollArea.setWidgetResizable(True)

        # Create a widget and layout for content inside the scroll area
        self.contentWidget = QWidget()
        self.contentLayout = QGridLayout()  # The layout for items

        # FIX 1: Set equal column stretches for consistent column widths
        self.contentLayout.setColumnStretch(0, 1)  # Column 0 gets stretch factor 1
        self.contentLayout.setColumnStretch(1, 1)  # Column 1 gets stretch factor 1
        self.contentLayout.setColumnStretch(2, 1)  # Column 2 gets stretch factor 1

        # FIX 2: Set minimum column widths to ensure consistent sizing
        min_column_width = 200  # Adjust this value as needed
        self.contentLayout.setColumnMinimumWidth(0, min_column_width)
        self.contentLayout.setColumnMinimumWidth(1, min_column_width)
        self.contentLayout.setColumnMinimumWidth(2, min_column_width)

        # Set uniform spacing
        self.contentLayout.setHorizontalSpacing(10)
        self.contentLayout.setVerticalSpacing(10)

        self.contentWidget.setLayout(self.contentLayout)

        # Add the content widget to the scroll area
        self.scrollArea.setWidget(self.contentWidget)

        # Add the scroll area to the main layout
        self.layout.addWidget(self.scrollArea)
        self.setLayout(self.layout)

        # FIX 3: Increase initial window size to better accommodate 3 columns
        # Calculate appropriate width: 3 columns * min_width + spacing + margins
        initial_width = 3 * min_column_width + 2 * 10 + 40  # 3 cols + 2 spacings + margins
        initial_height = 600  # Increased from 500
        self.resize(initial_width, initial_height)

    def renewWidgets(self):
        if not is_alive(self.contentLayout):
            return
        # Clear the existing widgets from the content layout
        for i in reversed(range(self.contentLayout.count())):
            widget = self.contentLayout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
        row, col = 0, 0
        max_items_per_row = 3

        itembag_list = self._normalize_item_entries(self.read_items_file())
        if not itembag_list:  # Simplified check
            empty_label = QLabel("You don't own any items yet.")
            self.contentLayout.addWidget(empty_label, 1, 1)
        else:
            for item in itembag_list:
                try:
                    item_widget = self.ItemLabel(item["item"], item["quantity"], item.get("type"))
                    item_widget.setMinimumWidth(180)
                    self.contentLayout.addWidget(item_widget, row, col)
                    col += 1
                    if col >= max_items_per_row:
                        row += 1
                        col = 0
                except Exception as e:
                    self.logger.log("error", f"Skipping invalid item '{item}': {e}")

    def filter_items(self):
        # Same stale-cached-window-after-reload guard as renewWidgets(): a search
        # box / category-dropdown callback can fire against a window whose C++
        # contentLayout was already deleted, so bail before touching it.
        if not is_alive(self.contentLayout):
            return
        search_text = self.search_edit.text().lower()
        category_index = self.category.currentIndex()
        # Clear the existing widgets from the content layout
        for i in reversed(range(self.contentLayout.count())):
            widget = self.contentLayout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
        row, col = 0, 0
        max_items_per_row = 3

        filtered_items = self._normalize_item_entries(self.read_items_file())
        if not filtered_items:
            empty_label = QLabel("You don't own any items yet.")
            self.contentLayout.addWidget(empty_label, 1, 1)
            return

        try:
            # Filter items based on category index
            if category_index == 1:  # Fossils
                filtered_items = [
                    item for item in filtered_items
                    if isinstance(item, dict) and "item" in item and item["item"] in self.fossil_pokemon
                ]
            elif category_index == 2:  # TMs and HMs
                filtered_items = list(filter(lambda item: item.get("type") == "TM", filtered_items))
            elif category_index == 3:  # Heal items
                filtered_items = [
                    item for item in filtered_items
                    if isinstance(item, dict) and "item" in item and item["item"] in self.hp_heal_items
                ]
            elif category_index == 4:  # Evolution items
                filtered_items = [
                    item for item in filtered_items
                    if isinstance(item, dict) and "item" in item and item["item"] in self.evolution_items
                ]
            elif category_index == 5: # Pokeballs
                filtered_items = [
                    item for item in filtered_items
                    if isinstance(item, dict) and "item" in item and item["item"] in self.pokeball_chances
                ]

            # Now filter by search
            filtered_items = list(filter(lambda item: search_text in item["item"].lower(), filtered_items))
        except Exception as e:
            filtered_items = []
            self.logger.log_and_showinfo("error", f"Error filtering items: {e}")

        if not filtered_items:
            empty_label = QLabel("Empty Search")
            self.contentLayout.addWidget(empty_label, 1, 1)
            return

        for item in filtered_items:
            try:
                item_widget = self.ItemLabel(item["item"], item["quantity"], item.get("type"))
                item_widget.setMinimumWidth(180)
                self.contentLayout.addWidget(item_widget, row, col)
                col += 1
                if col >= max_items_per_row:
                    row += 1
                    col = 0
            except Exception as e:
                self.logger.log("error", f"Skipping invalid item '{item}': {e}")

    def give_held_item(self, comboBox, item_name):
        individual_id = comboBox.itemData(comboBox.currentIndex(), role=UserRole)
        self._give_held_item_by_id(individual_id, item_name)

    def _give_held_item_by_id(self, individual_id, item_name):
        if not individual_id:
            self.logger.log_and_showinfo("error", "No Pokemon selected.")
            return
        try:
            db = services.db
            target_pokemon_data = db.get_pokemon(individual_id)

            if target_pokemon_data:
                pokemon_obj = PokemonObject.from_dict(target_pokemon_data)
                pokemon_obj.give_held_item(item_name)

                # Sync the main_pokemon singleton if it's the target
                if self.main_pokemon and self.main_pokemon.individual_id == individual_id:
                    self.main_pokemon.held_item = item_name

                self.logger.log_and_showinfo("info", f"{item_name} was given to {target_pokemon_data.get('name')}.")
                self.renewWidgets()

                # Refresh open PC Box window. Read services.pokemon_pc directly:
                # `from ..singletons import pokemon_pc` lands in the lazy __getattr__
                # factory and would force-construct a PC window the user never opened.
                if is_alive(services.pokemon_pc):
                    services.pokemon_pc.refresh_gui()
            else:
                self.logger.log_and_showinfo("error", "Could not find Pokemon data.")

        except Exception as e:
            self.logger.log_and_showinfo("error", f"Error giving item: {e}")

    def ItemLabel(self, item_name: str, quantity: int, item_type: Optional[str]):
        item_frame = QVBoxLayout()  # itemframe
        info_item_button = QPushButton("More Info")
        info_item_button.clicked.connect(lambda: self.more_info_button_act(item_name))
        
        item_name_for_label = item_name.replace("-", " ")  # Remove hyphens from item_name
        item_name_for_label = f"{item_name_for_label.capitalize()} x{quantity}"  # Display quantity
        if item_type == "TM":
            item_name_for_label = f"TM: {item_name_for_label}"
        item_name_label = QLabel(item_name_for_label)
        item_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if item_type == "TM":
            details = find_details_move(item_name)
            if not details or "type" not in details:
                self.logger.log_and_showinfo("error", f"Report the following: Missing details for item: {item_name!r}")
                tm_type = "normal"
            else:
                tm_type = details["type"].lower()

            item_file_path = items_path / f"Bag_TM_{tm_type}_SV_Sprite.png"
        else:
            item_file_path = items_path / f"{item_name}.png"
        item_picture_pixmap = QPixmap(str(item_file_path))
        item_picture_pixmap_scaled = item_picture_pixmap.scaled(
            96, 96,
            Qt.AspectRatioMode.KeepAspectRatio, # Important: Keeps the image from stretching
        )
        item_picture_label = QLabel()
        item_picture_label.setPixmap(item_picture_pixmap_scaled)
        item_picture_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        item_frame.addWidget(item_picture_label)
        item_frame.addWidget(item_name_label)

        item_name = item_name.lower()
        if item_name in self.hp_heal_items:
            use_item_button = QPushButton("Heal Mainpokemon")
            hp_heal = self.hp_heal_items[item_name]
            use_item_button.clicked.connect(lambda: self.Check_Heal_Item(self.main_pokemon.name, hp_heal, item_name, self.achievements))
        elif item_name in self.fossil_pokemon:
            fossil_id = self.fossil_pokemon[item_name]
            fossil_pokemon_name = search_pokedex_by_id(fossil_id)
            use_item_button = QPushButton(f"Evolve Fossil to {fossil_pokemon_name.capitalize()}")
            use_item_button.clicked.connect(lambda: self.Evolve_Fossil(item_name, fossil_id, fossil_pokemon_name))
        elif item_name in self.pokeball_chances:
            use_item_button = QPushButton("Try catching wild Pokemon")
            use_item_button.clicked.connect(lambda: self.Handle_Pokeball(item_name))
        elif item_type == "TM":
            use_item_button = QLabel()

            # FIX: Remove indentation by using textwrap.dedent() or reformatting the string
            import textwrap

            description_text = f"""Allows the user to teach {item_name.replace('-', ' ').title()} to Pokémon that can learn this move.\n\nTeaching the move doesn't consume the TM."""

            info_item_button = QLabel(textwrap.dedent(description_text).strip())

            # Enable word wrapping for TM description
            info_item_button.setWordWrap(True)
            # Optional: Set alignment for better appearance
            info_item_button.setAlignment(Qt.AlignmentFlag.AlignTop)

        elif item_name in self.evolution_items:
            use_item_button = QPushButton("Evolve Pokemon")
            use_item_button.clicked.connect(
                lambda: self._prompt_and_check_evo_item(item_name)
            )
        elif item_name in self.ability_items:
            use_item_button = QPushButton("Change Ability")
            use_item_button.clicked.connect(
                lambda: self._prompt_and_change_ability(item_name)
            )
        elif item_name in GiveItemWindow.NOT_YET_IMPLEMENTED_ITEMS or item_name.endswith("-berry") or item_name.endswith("-gem"):
            use_item_button = QLabel("Not implemented yet")
            use_item_button.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            use_item_button = QPushButton("Give Item To Pokemon")
            use_item_button.clicked.connect(
                lambda: self._prompt_and_give_held_item(item_name)
            )

        item_frame.addWidget(use_item_button)
        item_frame.addWidget(info_item_button)
        item_frame_widget = QWidget()
        item_frame_widget.setLayout(item_frame)

        return item_frame_widget

    def dispatch_use(self, item_name: str, item_type: Optional[str] = None) -> dict:
        """Invoke the right use-action for an item without requiring the bag UI
        to be visible. Returns {ok, message} for callers that want to surface
        a result (e.g. the web Items window's toast).

        The branching mirrors ItemLabel above; keep them in sync if rules change.
        """
        name = (item_name or "").lower()
        if item_type == "TM":
            return {
                "ok": False,
                "message": "TMs are taught from the move-learning flow, not used directly.",
            }
        try:
            if name in self.hp_heal_items:
                if not self.main_pokemon:
                    return {"ok": False, "message": "No active Pokémon to heal."}
                hp_heal = self.hp_heal_items[name]
                self.Check_Heal_Item(
                    self.main_pokemon.name, hp_heal, name, self.achievements
                )
                return {
                    "ok": True,
                    "message": f"Healed {self.main_pokemon.name} with {name}.",
                }
            if name in self.fossil_pokemon:
                fossil_id = self.fossil_pokemon[name]
                fossil_pokemon_name = search_pokedex_by_id(fossil_id)
                # Evolve_Fossil returns False both on a real revival error AND when
                # it no-ops because another item action is already running. Check
                # the busy flag first so a rapid double-use reports "in progress"
                # instead of a false "Failed to revive".
                if self._item_action_in_progress:
                    return {
                        "ok": False,
                        "message": "Another item action is already in progress.",
                    }
                if self.Evolve_Fossil(name, fossil_id, fossil_pokemon_name):
                    return {"ok": True, "message": f"Revived {fossil_pokemon_name}."}
                return {
                    "ok": False,
                    "message": f"Failed to revive {fossil_pokemon_name}.",
                }
            if name in self.pokeball_chances:
                self.Handle_Pokeball(name)
                return {"ok": True, "message": f"Threw {name}."}
            if name in self.evolution_items:
                self._prompt_and_check_evo_item(name)
                return {"ok": True, "message": ""}
            if name in self.ability_items:
                self._prompt_and_change_ability(name)
                return {"ok": True, "message": ""}
            if (
                name in GiveItemWindow.NOT_YET_IMPLEMENTED_ITEMS
                or name.endswith("-berry")
                or name.endswith("-gem")
            ):
                return {"ok": False, "message": "This item isn't usable yet."}
            self._prompt_and_give_held_item(name)
            return {"ok": True, "message": ""}
        except Exception as e:
            return {"ok": False, "message": f"Use failed: {e}"}

    def PokemonList(self, comboBox):
        try:
            db = services.db
            pokemon_list = db.execute("SELECT name, individual_id, pokedex_id FROM captured_pokemon").fetchall()
            if pokemon_list:
                for pokemon in pokemon_list:
                    pokemon_name = pokemon[0]
                    individual_id = pokemon[1]
                    id_ = pokemon[2]
                    if individual_id and id_:  # Ensure the ID exists
                        # Add Pokémon name to comboBox
                        comboBox.addItem(pokemon_name)
                        # Store both individual_id and id as separate data using roles
                        comboBox.setItemData(comboBox.count() - 1, individual_id, role=UserRole)
                        comboBox.setItemData(comboBox.count() - 1, id_, role=UserRole + 1)
        except Exception as e:
            self.logger.log_and_showinfo("error", f"Error loading Pokémon list: {e}")

    def _load_pokemon_choices(self):
        if self._pokemon_choices_cache is not None:
            return self._pokemon_choices_cache
        choices = []
        try:
            db = services.db
            rows = db.execute("SELECT name, individual_id, pokedex_id FROM captured_pokemon").fetchall()
            for row in rows:
                name, individual_id, poke_id = row[0], row[1], row[2]
                if name and individual_id and poke_id:
                    choices.append((f"{name} ({individual_id[:8]})", individual_id, poke_id))
        except Exception as e:
            self.logger.log("error", f"Error loading Pokemon list: {e}")
        self._pokemon_choices_cache = choices
        return choices

    def _select_pokemon(self, title: str):
        choices = self._load_pokemon_choices()
        if not choices:
            self.logger.log_and_showinfo("error", "No Pokemon available.")
            return None
        labels = [c[0] for c in choices]
        selected_label, ok = QInputDialog.getItem(self, title, "Select Pokemon:", labels, 0, False)
        if not ok:
            return None
        for label, individual_id, poke_id in choices:
            if label == selected_label:
                return individual_id, poke_id
        return None

    def _prompt_and_give_held_item(self, item_name: str):
        selected = self._select_pokemon("Give Item")
        if not selected:
            return
        individual_id, _ = selected
        self._give_held_item_by_id(individual_id, item_name)

    def _prompt_and_check_evo_item(self, item_name: str):
        selected = self._select_pokemon("Use Evolution Item")
        if not selected:
            return
        individual_id, prevo_id = selected
        self.Check_Evo_Item(individual_id, prevo_id, item_name)

    def _prompt_and_change_ability(self, item_name: str):
        selected = self._select_pokemon("Use Ability Item")
        if not selected:
            return
        individual_id, poke_id = selected
        self.Change_Ability(individual_id, item_name)

    def Change_Ability(self, individual_id: str, item_name: str):
        db = services.db
        pokemon = db.get_pokemon(individual_id)
        if not pokemon:
            return

        poke_obj = PokemonObject.from_dict(pokemon)
        from ..functions.pokedex_functions import search_pokedex

        # Get abilities from pokedex
        abilities = search_pokedex(poke_obj.name, "abilities")
        if not abilities:
            self.logger.log_and_showinfo("error", f"Could not find abilities for {poke_obj.name}.")
            return

        current_ability = poke_obj.ability
        new_ability = None

        if item_name == "ability-patch":
            # Switch to Hidden Ability (usually 'H', sometimes 'S' for Special like Battle Bond)
            hidden_ability = abilities.get("H")
            special_ability = abilities.get("S")

            if hidden_ability and current_ability != hidden_ability:
                new_ability = hidden_ability
            elif special_ability and current_ability != special_ability:
                new_ability = special_ability
            else:
                self.logger.log_and_showinfo("info", f"{poke_obj.name} already has its Hidden Ability or doesn't have one.")
                return

        elif item_name == "ability-capsule":
            # Switch between standard abilities (usually '0' and '1')
            ability_0 = abilities.get("0")
            ability_1 = abilities.get("1")

            if ability_0 and ability_1:
                if current_ability == ability_0:
                    new_ability = ability_1
                elif current_ability == ability_1:
                    new_ability = ability_0
                else:
                    self.logger.log_and_showinfo("info", f"{poke_obj.name} has a Hidden Ability. Ability Capsule only works on standard abilities.")
                    return
            else:
                self.logger.log_and_showinfo("info", f"{poke_obj.name} only has one standard ability.")
                return

        if new_ability:
            poke_obj.ability = new_ability
            pokemon["ability"] = new_ability
            db.save_pokemon(pokemon)
            db.update_item_quantity(item_name, -1)
            self.logger.log_and_showinfo("info", f"{poke_obj.name}'s ability changed to {new_ability}!")

            # Refresh if it's the main pokemon
            if self.main_pokemon and self.main_pokemon.individual_id == individual_id:
                self.main_pokemon.ability = new_ability

            # Trigger GUI refresh
            self.update_bag_gui()


    def Evolve_Fossil(
        self, item_name: str, fossil_id: int, fossil_poke_name: str
    ) -> bool:
        """Revive a fossil into its corresponding Pokémon. Returns True on
        success, False if another item action is already running or revival
        threw. Existing PyQt button callers ignore the return value."""
        if self._item_action_in_progress:
            return False
        self._item_action_in_progress = True
        try:
            if not isinstance(fossil_id, int):
                raise ValueError(f"Invalid fossil id: {fossil_id}")
            save_fossil_pokemon(fossil_id)
            self._pokemon_choices_cache = None
            self.delete_item(item_name)
            # Re-fetch the starter window if its C++ side was deleted (mirrors the
            # evo_window guard in Check_Evo_Item); a cached-but-dead reference would
            # raise RuntimeError inside display_fossil_pokemon.
            if not is_alive(self.starter_window):
                from ..singletons import get_starter_window
                self.starter_window = get_starter_window()
            self.starter_window.display_fossil_pokemon(fossil_id, fossil_poke_name)
            # Read services.pokemon_pc directly rather than importing pokemon_pc from
            # singletons, which would force-construct a PC window the user never opened.
            if is_alive(services.pokemon_pc):
                services.pokemon_pc.refresh_pokemon_grid()
            return True
        except Exception as e:
            show_warning_with_traceback(parent=self, exception=e, message=f"Error using fossil item '{item_name}'")
            return False
        finally:
            self._item_action_in_progress = False

    def modified_pokeball_chances(self, item_name: str, catch_chance: int):
        # Adjust catch chance based on Pokémon type and Poké Ball
        if item_name == 'net-ball' and ('water' in self.enemy_pokemon.type or 'bug' in self.enemy_pokemon.type):
            catch_chance += 10  # Additional 10% for Water or Bug-type Pokémon
            self.logger.log("game", f"{item_name} gets a bonus for Water/Bug-type Pokémon!")

        elif item_name == 'iron-ball' and 'steel' in self.enemy_pokemon.type:
            catch_chance += 10  # Additional 10% for Steel-type Pokémon
            self.logger.log("game", f"{item_name} gets a bonus for Steel-type Pokémon!")

        elif item_name == 'dive-ball' and 'water' in self.enemy_pokemon.type:
            catch_chance += 10  # Additional 10% for Water-type Pokémon
            self.logger.log("game", f"{item_name} gets a bonus for Water-type Pokémon!")

        return catch_chance

    def Handle_Pokeball(self, item_name: str):
        # Check if the item exists in the pokeball chances
        if item_name in self.pokeball_chances:
            catch_chance = self.pokeball_chances[item_name]
            catch_chance = self.modified_pokeball_chances(item_name, catch_chance)

            # Simulate catching the Pokémon based on the catch chance
            if random.randint(1, 100) <= catch_chance:
                # Pokémon caught successfully
                self.logger.log_and_showinfo("info", f"{item_name} successfully caught the Pokémon!")
                self.delete_item(item_name)  # Delete the Poké Ball after use
            else:
                # Pokémon was not caught
                self.logger.log_and_showinfo("info", f"{item_name} failed to catch the Pokémon.")
                self.delete_item(item_name)  # Still delete the Poké Ball after use
        else:
            self.logger.log_and_showinfo("error", f"{item_name} is not a valid Poké Ball!")

    def delete_item(self, item_name: str):
        # Update database directly for performance
        services.db.update_item_quantity(item_name, -1)
        self.renewWidgets()

    def Check_Heal_Item(self, prevo_name: str, heal_points: int, item_name: str, achievements):
        check = check_for_badge(achievements, 20)
        if check is False:
            receive_badge(20, achievements)
        if item_name == "fullrestore" or item_name == "maxpotion":
            heal_points = self.main_pokemon.max_hp
        self.main_pokemon.hp += heal_points
        if self.main_pokemon.hp > (self.main_pokemon.max_hp):
            self.main_pokemon.hp = self.main_pokemon.max_hp
        self.delete_item(item_name)
        play_effect_sound(self.settings_obj, "HpHeal")
        self.logger.log_and_showinfo("info", f"{prevo_name} was healed for {heal_points}")

    def Check_Evo_Item(self, individual_id: str, prevo_id: str, item_name: str):
        try:
            item_id = return_id_for_item_name(item_name)
            evo_id = check_evolution_by_item(prevo_id, item_id)
            if evo_id:
                # Perform your action when the item matches the Pokémon's evolution item
                self.logger.log_and_showinfo("info", "Pokemon Evolution is fitting !")
                if not is_alive(self.evo_window):
                    from ..singletons import get_evo_window
                    self.evo_window = get_evo_window()
                self.evo_window.ask_pokemon_evo(individual_id, prevo_id, evo_id, item_name=item_name)
            else:
                self.logger.log_and_showinfo("info", "This Pokemon does not need this item.")
        except Exception as e:
            show_warning_with_traceback(parent=self, exception=e, message=f"{e}")

    def load_evolution_items(self):
        try:
            evolution_item_ids = set()
            with open(poke_evo_path, mode='r', newline='', encoding='utf-8') as evo_file:
                reader = csv.DictReader(evo_file)
                for row in reader:
                    # Use-item evolutions (trigger 3) consume trigger_item_id.
                    if row.get('evolution_trigger_id') == '3':
                        item_id = row.get('trigger_item_id')
                        if item_id:
                            evolution_item_ids.add(item_id)
                    # Trade-with-held-item evolutions (trigger 2) need held_item_id.
                    # Ankimon has no trading, so surface the held item as usable
                    # (e.g. Metal Coat -> Steelix/Scizor, Deep Sea Tooth/Scale ->
                    # Huntail/Gorebyss, Dragon Scale -> Kingdra, King's Rock ->
                    # Politoed/Slowking).
                    elif row.get('evolution_trigger_id') == '2':
                        item_id = row.get('held_item_id')
                        if item_id:
                            evolution_item_ids.add(item_id)

            with open(csv_file_items_cost, mode='r', newline='', encoding='utf-8') as items_file:
                reader = csv.DictReader(items_file)
                for row in reader:
                    if row['id'] in evolution_item_ids:
                        self.evolution_items.add(row['identifier'])
            
        except Exception as e:
            self.logger.log_and_showinfo("error", f"Error loading evolution items: {e}")

    def write_items_file(self, itembag_list: list[Any]):
        """Writes items to the database. Legacy method kept for compatibility."""
        db = services.db
        for item in itembag_list:
            item_id = item.get("id")
            item_name = item.get("item") or item.get("item_name", "")
            quantity = item.get("quantity", 1)
            # Pass cached metadata back to database
            db.save_item(
                item_id, 
                item_name, 
                quantity, 
                extra_data=item, 
                category_id=item.get("category_id"),
                cost=item.get("cost"),
                fling_power=item.get("fling_power"),
                fling_effect_id=item.get("fling_effect_id")
            )

    def read_items_file(self):
        """
        Reads the item list from the database.
        Returns items in the expected format for the UI, using SQL columns for efficiency.
        """
        try:
            db = services.db
            items = db.get_all_items()
            # Convert database format to UI format
            result = []
            for item in items:
                # Prioritize SQL columns over JSON blob
                cat_id = item.get("category_id")
                item_type = "TM" if cat_id == 37 else None
                
                # Combine database columns into the UI dictionary (preserving extra_data for legacy)
                item_dict = {
                    "id": item.get("id"),
                    "item": item.get("item_name"),
                    "quantity": item.get("quantity", 1),
                    "type": item_type,
                    "category_id": cat_id,
                    "cost": item.get("cost"),
                    "fling_power": item.get("fling_power"),
                    "fling_effect_id": item.get("fling_effect_id")
                }
                
                # Merge with any existing extra_data for backward compatibility
                if item.get("extra_data"):
                    for k, v in item["extra_data"].items():
                        if k not in item_dict:
                            item_dict[k] = v
                            
                result.append(item_dict)
            return result
        except Exception as e:
            self.logger.log("error", f"Error reading items from database: {e}")
            return []

    def _normalize_item_entries(self, items_data):
        if not isinstance(items_data, list):
            return []
        normalized = []
        for raw in items_data:
            if not isinstance(raw, dict):
                continue
            name = raw.get("item")
            if not isinstance(name, str) or not name.strip():
                continue
            quantity = raw.get("quantity", 1)
            try:
                quantity = int(quantity)
            except Exception:
                quantity = 1
            if quantity < 1:
                continue
            normalized.append({
                **raw,
                "item": name.strip(),
                "quantity": quantity,
            })
        return normalized

    def clear_layout(self, layout):
        if not is_alive(layout):
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def showEvent(self, event):
        self._pokemon_choices_cache = None
        self.renewWidgets()

    def show_window(self):
        # Get the geometry of the main screen
        main_screen_geometry = mw.geometry()

        # Calculate the position to center the ItemWindow on the main screen
        x = int(main_screen_geometry.center().x() - self.width() // 2)
        y = int(main_screen_geometry.center().y() - self.height() // 2)

        # Move the ItemWindow to the calculated position
        self.move(x, y)

        self.show()

    def more_info_button_act(self, item_name: str):
        description = get_id_and_description_by_item_name(item_name)
        self.logger.log_and_showinfo("info", f"{description}")
