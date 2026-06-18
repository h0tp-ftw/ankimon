from ..functions.sprite_functions import get_sprite_path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton, QScrollArea, QGroupBox, QFrame, QGridLayout, QComboBox, QDialogButtonBox
from PyQt6.QtGui import QPixmap
import json
import os
from aqt import mw
from aqt.utils import showInfo, showWarning
from ..resources import mypokemon_path, frontdefault, team_pokemon_path

class PokemonTeamDialog(QDialog):
    def __init__(self, settings_obj, logger, trainer_card=None, parent=mw):
        super().__init__(parent)

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Choose Your Pokémon Team (Max 6 Pokémon)")
        self.settings = settings_obj
        self.logger = logger
        self.trainer_card = trainer_card

        # Set the minimum size of the dialog
        self.setMinimumSize(900, 500)  # Minimum size of 900x500 pixels

        # Load the Pokémon team data
        self.my_pokemon = self.load_my_pokemon()
        self.team_pokemon = [None] * 6  # Assuming a team can hold 6 Pokémon
        self.team_pokemon = self.load_pokemon_team()

        # Layout
        layout = QVBoxLayout()

        # Label
        label = QLabel("Choose your Pokémon team (up to 6 Pokémon):")
        layout.addWidget(label)

        # Team selection area (scrollable)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)

        team_widget = QGroupBox()
        team_layout = QGridLayout()  # Change this to QGridLayout for grid arrangement

        # Create a frame for each Pokémon in the team
        self.pokemon_frames = []
        for i in range(6):
            row = i // 3  # Determine the row (0 or 1)
            col = i % 3  # Determine the column (0, 1, or 2)

            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            frame.setFrameShadow(QFrame.Shadow.Raised)

            pokemon_layout = QVBoxLayout()

            # Label for Pokémon name and level
            pokemon_label = QLabel(f"Pokémon {i+1}: Not Selected")
            pokemon_layout.addWidget(pokemon_label)

            # Add Pokémon sprite preview
            sprite_label = QLabel()
            pokemon_layout.addWidget(sprite_label)

            # "Switch out Pokémon" button
            switch_button = QPushButton(f"Switch out Pokémon {i+1}")
            switch_button.clicked.connect(lambda _, i=i: self.switch_out_pokemon(i))
            pokemon_layout.addWidget(switch_button)

            # "Remove Pokémon" button
            remove_button = QPushButton(f"Remove Pokémon {i+1}")
            remove_button.clicked.connect(lambda _, i=i: self.remove_pokemon(i))
            pokemon_layout.addWidget(remove_button)

            frame.setLayout(pokemon_layout)
            team_layout.addWidget(frame, row, col)  # Add frame to grid layout at specific row and column
            self.pokemon_frames.append({'frame': frame, 'label': pokemon_label, 'sprite': sprite_label, 'switch_button': switch_button, 'remove_button': remove_button})

        team_widget.setLayout(team_layout)
        scroll_area.setWidget(team_widget)
        layout.addWidget(scroll_area)

        # XP Share selection
        self.xp_share_selected_individual_id = None
        self.xp_share_label = QLabel("XP Share: None")
        xp_share_button = QPushButton("Choose Pokémon with XP Share")
        xp_share_button.clicked.connect(self.choose_xp_share_pokemon)

        # Load initial XP Share Pokémon (based on settings)
        xp_share_pokemon_individual_id = self.settings.get("trainer.xp_share")
        if xp_share_pokemon_individual_id:
            self.xp_share_selected_individual_id = xp_share_pokemon_individual_id
            # Find the name for display
            for pokemon in self.my_pokemon:
                if pokemon['individual_id'] == xp_share_pokemon_individual_id:
                    self.xp_share_label.setText(f"XP Share: {pokemon['name']} (Level {pokemon['level']})")
                    break

        layout.addWidget(self.xp_share_label)
        layout.addWidget(xp_share_button)

        # OK Button
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.on_ok)
        layout.addWidget(ok_button)

        # Set layout
        self.setLayout(layout)

        # Initialize team with current Pokémon data
        self.update_team_display()

        self.exec()

    def load_my_pokemon(self):
        """Load the player's Pokémon data from database using lightweight stubs"""
        cursor = mw.ankimon_db.execute("""
            SELECT individual_id, 
                   name as name,
                   level as level,
                   pokedex_id as id,
                   shiny as shiny,
                   json_extract(data, '$.gender') as gender
            FROM captured_pokemon
            ORDER BY individual_id ASC
        """)
        
        my_pokemon = []
        for row in cursor.fetchall():
            my_pokemon.append({
                "individual_id": row[0],
                "name": row[1],
                "level": row[2],
                "id": row[3],
                "shiny": bool(row[4]),
                "gender": row[5]
            })
        return my_pokemon

    def _calculate_pokemon_cp(self, individual_id):
        """Calculate CP for a Pokémon by fetching full data"""
        from ..business import calculate_pokemon_go_cp, pokemon_go_raw_stats
        
        try:
            pokemon_data = mw.ankimon_db.get_pokemon(individual_id)
            if not pokemon_data:
                return 0
            
            base_stats = pokemon_data.get('base_stats', {})
            if not base_stats:
                base_stats = pokemon_data.get('detail_stats', {})
            
            iv = pokemon_data.get('iv', {})
            ev = pokemon_data.get('ev', {})
            level = pokemon_data.get('level', 1)
            
            if not iv:
                iv = {stat: 0 for stat in ['hp', 'atk', 'def', 'spa', 'spd', 'spe']}
            if not ev:
                ev = {stat: 0 for stat in ['hp', 'atk', 'def', 'spa', 'spd', 'spe']}
            
            attack, defense, stamina = pokemon_go_raw_stats(base_stats, iv, ev)
            cp = calculate_pokemon_go_cp(attack, defense, stamina, level)
            return cp
        except Exception as e:
            self.logger.log("error", f"Error calculating CP for {individual_id}: {e}")
            return 0

    def load_pokemon_team(self):
        """Load the player's Pokémon Team from the database"""
        team_data = mw.ankimon_db.get_team()
        matching_pokemon = []

        for pokemon_in_team in team_data:
            individual_id = pokemon_in_team.get('individual_id')
            if individual_id:
                pokemon = mw.ankimon_db.get_pokemon(individual_id)
                if pokemon:
                    matching_pokemon.append(pokemon)

        return matching_pokemon

    def update_team_display(self):
        """Update the display with the player's current team"""
        # Ensure team_pokemon has 6 slots (pad with None if less than 6)
        max_pokemon_slots = 6
        self.team_pokemon = self.team_pokemon[:max_pokemon_slots]  # Trim to a max of 6 Pokémon
        self.team_pokemon.extend([None] * (max_pokemon_slots - len(self.team_pokemon)))  # Pad with None if less than 6

        for i, frame_data in enumerate(self.pokemon_frames):
            # Check if a Pokémon is selected for this slot (i.e., it's not None)
            if self.team_pokemon[i] is not None:
                pokemon = self.team_pokemon[i]
                pokemon_name = pokemon['name']
                pokemon_level = pokemon['level']
                cp_value = self._calculate_pokemon_cp(pokemon['individual_id'])
                sprite_path = os.path.join(frontdefault, f"{pokemon['id']}.png")

                # Update label with name, level, and CP
                frame_data['label'].setText(f"{pokemon_name} (Level {pokemon_level}) - CP {cp_value}")

                # Display the sprite image
                if os.path.exists(sprite_path):
                    pixmap = QPixmap(sprite_path)
                    frame_data['sprite'].setPixmap(pixmap.scaled(50, 50))  # Resize sprite for preview
                    frame_data['sprite'].setAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    frame_data['sprite'].clear()
            else:
                frame_data['label'].setText("Pokémon Not Selected")
                frame_data['sprite'].clear()  # Clear the sprite if not selected

    def switch_out_pokemon(self, slot):
        """Allow the player to switch out a Pokémon for the selected slot"""

        # Create a dialog to choose a new Pokémon for the slot
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Pokémon to Switch In")
        dialog.setMinimumSize(500, 400)

        layout = QVBoxLayout()

        label = QLabel("Choose a Pokémon to switch in:")
        layout.addWidget(label)

        # Add a search box and sort dropdown
        search_layout = QHBoxLayout()

        # Sorting dropdown
        sort_label = QLabel("Sort by:")
        sort_combo = QComboBox()
        sort_combo.addItems(["Name (A-Z)", "Level (High-Low)", "Pokédex ID", "CP (High-Low)"])
        search_layout.addWidget(sort_label)
        search_layout.addWidget(sort_combo)

        # Search box
        search_label_box = QLabel("Search:")
        search_input = QLineEdit()
        search_input.setPlaceholderText("Type Pokémon name...")
        search_layout.addWidget(search_label_box)
        search_layout.addWidget(search_input)

        layout.addLayout(search_layout)

        # Create a dropdown to select a new Pokémon
        combo_box = QComboBox()

        # Add only those Pokémon to the combo box that are not already in the team
        used_pokemon_ids = []
        for i, pokemon in enumerate(self.team_pokemon):
            if pokemon is not None and i != slot:
                used_pokemon_ids.append(pokemon['individual_id'])
        
        available_pokemon = [pokemon for pokemon in self.my_pokemon if pokemon and pokemon['individual_id'] not in used_pokemon_ids]

        # Label and preview for image
        preview_label = QLabel("Preview:")
        layout.addWidget(QLabel("Select Pokémon:"))
        layout.addWidget(combo_box)
        layout.addWidget(preview_label)
        
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(image_label)

        # Function to update the combo box list based on search and sort
        def update_pokemon_list():
            search_text = search_input.text().lower()
            sort_option = sort_combo.currentText()
            
            # Filter by search text
            filtered = [p for p in available_pokemon if search_text in p['name'].lower()]
            
            # Sort based on selection
            if "Name" in sort_option:
                filtered.sort(key=lambda p: p['name'])
            elif "Level" in sort_option:
                filtered.sort(key=lambda p: p['level'], reverse=True)
            elif "Pokédex" in sort_option:
                filtered.sort(key=lambda p: p['id'])
            elif "CP" in sort_option:
                # Calculate CP for each and sort
                filtered.sort(key=lambda p: self._calculate_pokemon_cp(p['individual_id']), reverse=True)
            
            # Update combo_box
            combo_box.blockSignals(True)  # Prevent triggering update_preview while updating
            combo_box.clear()
            
            if filtered:
                for pokemon in filtered:
                    cp_value = self._calculate_pokemon_cp(pokemon['individual_id'])
                    display_text = f"{pokemon['name']} (Level {pokemon['level']}) - CP {cp_value}"
                    
                    combo_box.addItem(display_text, pokemon)
                    sprite_path = get_sprite_path("front", "png", pokemon['id'], pokemon["shiny"], pokemon["gender"])
                    pixmap = QPixmap(sprite_path)
                    combo_box.setItemData(combo_box.count() - 1, pixmap, Qt.ItemDataRole.DecorationRole)
            else:
                combo_box.addItem("No Pokémon found", None)
            
            combo_box.blockSignals(False)
            update_preview(0)

        # Function to update the image preview when a new item is selected
        def update_preview(index):
            pokemon = combo_box.itemData(index)
            if pokemon:
                sprite_path = get_sprite_path("front", "png", pokemon['id'], pokemon["shiny"], pokemon["gender"])
                pixmap = QPixmap(sprite_path)
                image_label.setPixmap(pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio))
                image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                image_label.clear()

        # Connect signals
        sort_combo.currentTextChanged.connect(update_pokemon_list)
        search_input.textChanged.connect(update_pokemon_list)
        combo_box.currentIndexChanged.connect(lambda: update_preview(combo_box.currentIndex()))

        # Initial population
        update_pokemon_list()

        # Button to confirm the selection
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(lambda: self.confirm_switch(combo_box, slot, dialog))
        button_box.rejected.connect(dialog.reject)

        layout.addWidget(button_box)

        dialog.setLayout(layout)
        dialog.exec()

    def confirm_switch(self, combo_box, slot, dialog):
        """Confirm the Pokémon switch and update the team"""
        selected_pokemon = combo_box.itemData(combo_box.currentIndex())

        if selected_pokemon:
            self.team_pokemon[slot] = selected_pokemon  # Replace the Pokémon in the team slot
            self.update_team_display()

        dialog.accept()

    def remove_pokemon(self, slot):
        """Remove the Pokémon from the team and handle XP Share if necessary"""
        # Check if there's a Pokémon in the selected slot
        if self.team_pokemon[slot] is not None:
            # Check if the Pokémon in this slot is the one with XP Share
            pokemon_individual_id = self.team_pokemon[slot]['individual_id']
            xp_share_pokemon_individual_id = self.settings.get("trainer.xp_share")

            if xp_share_pokemon_individual_id == pokemon_individual_id:
                # Remove XP Share from the Pokémon if it exists
                self.settings.set("trainer.xp_share", None)

            # Remove the Pokémon from the team slot
            self.team_pokemon[slot] = None

            # Update the display after removal
            self.update_team_display()

    def on_ok(self):
        """Store the selected Pokémon team and XP Share setting, then close the dialog"""
        #team = [frame_data['label'].text() for frame_data in self.pokemon_frames if frame_data['label'].text() != "Pokémon Not Selected"]
        team_data = []  # Initialize the list to store selected Pokémon

        # Process each Pokémon frame to construct the team
        for frame_data in self.team_pokemon:
            if frame_data:  # Ensure the Pokémon has a name
                # Restructure Pokémon data to the desired format
                pokemon_data = {
                    "individual_id": frame_data['individual_id']
                }
                team_data.append(pokemon_data)

        pokemon_names = []

        for frame_data in self.team_pokemon:
            if frame_data:
                # Restructure Pokémon data to the desired format
                pokemon_name = {
                    "name": frame_data['name']
                }
                pokemon_names.append(pokemon_name)

        # Get the selected Pokémon for XP Share
        xp_share_individual_id = self.xp_share_selected_individual_id
        if xp_share_individual_id:
            xp_share_pokemon = next((p['name'] for p in self.my_pokemon if p['individual_id'] == xp_share_individual_id), "Unknown")
        else:
            xp_share_pokemon = "No XP Share"

        # Update settings with the selected team and XP Share setting
        self.settings.set("trainer.team", team_data)
        self.settings.set("trainer.xp_share", xp_share_individual_id)  # Save XP Share Pokémon

        try:
            mw.ankimon_db.save_team(team_data)

            self.logger.log_and_showinfo("info", "Trainer settings saved to database.")
            self.logger.log_and_showinfo("info", f"You chose the following team: [{', '.join([pokemon['name'] for pokemon in pokemon_names])}]\nXP Share: {xp_share_pokemon}")
        except Exception as e:
            self.logger.log_and_showinfo("error", f"Failed to save trainer settings: {e}")

        # Reload trainer card team data when confirmed new team
        if self.trainer_card is not None:
            self.trainer_card.reload_team()

        self.accept()  # Close the dialog

    def choose_xp_share_pokemon(self):
        """Open dialog to select XP Share Pokémon"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Pokémon for XP Share")
        dialog.setMinimumSize(500, 400)

        layout = QVBoxLayout()

        label = QLabel("Choose a Pokémon to receive XP Share:")
        layout.addWidget(label)

        # Add a search box and sort dropdown
        search_layout = QHBoxLayout()

        # Sorting dropdown
        sort_label = QLabel("Sort by:")
        sort_combo = QComboBox()
        sort_combo.addItems(["Name (A-Z)", "Level (High-Low)", "Pokédex ID", "CP (High-Low)"])
        search_layout.addWidget(sort_label)
        search_layout.addWidget(sort_combo)

        # Search box
        search_label_box = QLabel("Search:")
        search_input = QLineEdit()
        search_input.setPlaceholderText("Type Pokémon name...")
        search_layout.addWidget(search_label_box)
        search_layout.addWidget(search_input)

        layout.addLayout(search_layout)

        # Create a dropdown to select XP Share Pokémon
        combo_box = QComboBox()
        combo_box.addItem("No XP Share", None)

        available_pokemon = self.my_pokemon

        # Label and preview for image
        preview_label = QLabel("Preview:")
        layout.addWidget(QLabel("Select Pokémon:"))
        layout.addWidget(combo_box)
        layout.addWidget(preview_label)
        
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(image_label)

        # Function to update the combo box list based on search and sort
        def update_pokemon_list():
            search_text = search_input.text().lower()
            sort_option = sort_combo.currentText()
            
            # Filter by search text
            filtered = [p for p in available_pokemon if search_text in p['name'].lower()]
            
            # Sort based on selection
            if "Name" in sort_option:
                filtered.sort(key=lambda p: p['name'])
            elif "Level" in sort_option:
                filtered.sort(key=lambda p: p['level'], reverse=True)
            elif "Pokédex" in sort_option:
                filtered.sort(key=lambda p: p['id'])
            elif "CP" in sort_option:
                filtered.sort(key=lambda p: self._calculate_pokemon_cp(p['individual_id']), reverse=True)
            
            # Update combo_box
            combo_box.blockSignals(True)
            combo_box.clear()
            combo_box.addItem("No XP Share", None)
            
            if filtered:
                for pokemon in filtered:
                    cp_value = self._calculate_pokemon_cp(pokemon['individual_id'])
                    display_text = f"{pokemon['name']} (Level {pokemon['level']}) - CP {cp_value}"
                    
                    combo_box.addItem(display_text, pokemon['individual_id'])
                    sprite_path = get_sprite_path("front", "png", pokemon['id'], pokemon["shiny"], pokemon["gender"])
                    pixmap = QPixmap(sprite_path)
                    combo_box.setItemData(combo_box.count() - 1, pixmap, Qt.ItemDataRole.DecorationRole)
            
            combo_box.blockSignals(False)
            update_preview(0)

        # Function to update the image preview when a new item is selected
        def update_preview(index):
            individual_id = combo_box.itemData(index)
            if individual_id:
                pokemon = next((p for p in self.my_pokemon if p['individual_id'] == individual_id), None)
                if pokemon:
                    sprite_path = get_sprite_path("front", "png", pokemon['id'], pokemon["shiny"], pokemon["gender"])
                    pixmap = QPixmap(sprite_path)
                    image_label.setPixmap(pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio))
                    image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                image_label.clear()

        # Connect signals
        sort_combo.currentTextChanged.connect(update_pokemon_list)
        search_input.textChanged.connect(update_pokemon_list)
        combo_box.currentIndexChanged.connect(lambda: update_preview(combo_box.currentIndex()))

        # Initial population
        update_pokemon_list()

        # Button to confirm the selection
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(lambda: self.confirm_xp_share(combo_box.itemData(combo_box.currentIndex()), dialog))
        button_box.rejected.connect(dialog.reject)

        layout.addWidget(button_box)

        dialog.setLayout(layout)
        dialog.exec()

    def confirm_xp_share(self, selected_individual_id, dialog):
        """Confirm the XP Share Pokémon selection"""
        self.xp_share_selected_individual_id = selected_individual_id

        if selected_individual_id:
            pokemon = next((p for p in self.my_pokemon if p['individual_id'] == selected_individual_id), None)
            if pokemon:
                self.xp_share_label.setText(f"XP Share: {pokemon['name']} (Level {pokemon['level']})")
        else:
            self.xp_share_label.setText("XP Share: None")
    
        dialog.accept()
