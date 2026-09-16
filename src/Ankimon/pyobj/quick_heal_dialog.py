from aqt.qt import QDialog, QVBoxLayout, QPushButton, QLabel, QScrollArea, QWidget, QGridLayout, Qt, QPixmap
import os

from ..services import services
from ..singletons import main_pokemon, achievements, reviewer_obj, logger
from ..functions.badges_functions import check_for_badge, receive_badge
from ..utils import play_effect_sound, safe_int

class QuickHealDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Heal")
        self.setMinimumWidth(300)
        self.setMinimumHeight(400)
        self.db = services.db
        self.settings = services.settings

        # Borrow the healing list from the item window's definition
        self.hp_heal_items = {
            "potion": 20,
            "sweet-heart": 20,
            "berry-juice": 20,
            "fresh-water": 30,
            "soda-pop": 50,
            "super-potion": 60,
            "energy-powder": 60,
            "lemonade": 70,
            "moomoo-milk": 100,
            "hyper-potion": 120,
            "energy-root": 120,
            "full-restore": 1000,
            "max-potion": 1000,
        }

        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        title = QLabel("Select an item to heal your Pokémon:")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)

        self.grid = QGridLayout(content)

        self.refresh_items()

    def refresh_items(self):
        # Clear existing layout
        for i in reversed(range(self.grid.count())):
            self.grid.itemAt(i).widget().setParent(None)

        items = self.db.get_all_items()

        row = 0
        has_healing_items = False

        for item in items:
            item_name = item.get("item_name", "").lower()
            quantity = safe_int(item.get("quantity", 0))

            if quantity > 0 and item_name in self.hp_heal_items:
                has_healing_items = True
                heal_points = self.hp_heal_items[item_name]

                # Image
                img_label = QLabel()
                img_path = os.path.join(services.addon_dir, "user_files", "sprites", "items", f"{item_name}.png")
                if os.path.exists(img_path):
                    pixmap = QPixmap(img_path).scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio)
                    img_label.setPixmap(pixmap)
                self.grid.addWidget(img_label, row, 0)

                # Name and quantity
                formatted_name = item_name.replace('-', ' ').title()
                name_label = QLabel(f"{formatted_name} (x{quantity})")
                self.grid.addWidget(name_label, row, 1)

                # Heal Button
                btn = QPushButton(f"Heal {heal_points} HP")
                btn.clicked.connect(lambda checked, i_n=item_name, hp=heal_points: self.heal_pokemon(i_n, hp))
                self.grid.addWidget(btn, row, 2)

                row += 1

        if not has_healing_items:
            no_items = QLabel("You have no healing items.")
            no_items.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(no_items, 0, 0, 1, 3)

    def heal_pokemon(self, item_name, heal_points):
        if main_pokemon is None:
            logger.log_and_showinfo("error", "No active Pokemon to heal.")
            return

        if main_pokemon.hp >= main_pokemon.max_hp:
            logger.log_and_showinfo("info", f"{main_pokemon.name} is already at full health.")
            return

        # Double check quantity
        quantity = self.db.get_item_quantity(item_name)
        if quantity <= 0:
            logger.log_and_showinfo("info", f"You have no {item_name} left.")
            self.refresh_items()
            return

        if item_name == "full-restore" or item_name == "max-potion":
            heal_points = main_pokemon.max_hp

        # Consume item
        self.db.update_item_quantity(item_name, quantity - 1)

        # Heal
        prevo_name = main_pokemon.name
        main_pokemon.hp += heal_points
        if main_pokemon.hp > main_pokemon.max_hp:
            main_pokemon.hp = main_pokemon.max_hp

        main_pokemon.current_hp = main_pokemon.hp

        # Save to DB
        from ..functions.update_main_pokemon import save_main_pokemon
        save_main_pokemon(main_pokemon)

        check = check_for_badge(achievements, 20)
        if check is False:
            receive_badge(20, achievements)

        play_effect_sound(self.settings, "HpHeal")

        # Trigger HUD update in reviewer
        if hasattr(reviewer_obj, 'refresh_hud'):
            reviewer_obj.refresh_hud()

        logger.log_and_showinfo("info", f"{prevo_name} was healed for {heal_points} HP")
        self.accept()
