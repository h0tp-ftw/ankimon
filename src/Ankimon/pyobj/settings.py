import json
import os
from aqt import mw
from aqt.utils import showInfo
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)
from PyQt6.QtWidgets import QRadioButton, QHBoxLayout, QMainWindow, QScrollArea
from pathlib import Path
from ..resources import user_path

DEFAULT_CONFIG = {
    "battle.automatic_battle": 0,
    "battle.cards_per_round": 2,
    "battle.daily_average": 100,
    "battle.card_max_time": 60,
    "battle.review_based_damage": True,
    "controls.pokemon_buttons": True,
    "controls.defeat_key": "5",
    "controls.catch_key": "6",
    "controls.key_for_opening_closing_ankimon": "Ctrl+Shift+P",
    "controls.allow_to_choose_moves": False,
    "gui.animate_time": True,
    "gui.gif_in_collection": True,
    "gui.styling_in_reviewer": True,
    "gui.hp_bar_config": True,
    "gui.pop_up_dialog_message_on_defeat": False,
    "gui.review_hp_bar_thickness": 2,
    "gui.reviewer_image_gif": False,
    "gui.reviewer_text_message_box": True,
    "gui.reviewer_text_message_box_time": 3,
    "gui.show_mainpkmn_in_reviewer": 1,
    "gui.team_deck_view": True,
    "gui.view_main_front": True,
    "gui.xp_bar_config": True,
    "gui.xp_bar_location": 2,
    "audio.sound_effects": False,
    "audio.sounds": True,
    "audio.battle_sounds": False,
    "audio.volume": 0.5,
    "misc.gen1": True,
    "misc.gen2": True,
    "misc.gen3": True,
    "misc.gen4": True,
    "misc.gen5": True,
    "misc.gen6": True,
    "misc.gen7": True,
    "misc.gen8": True,
    "misc.gen9": False,
    "misc.remove_level_cap": False,
    "misc.language": 9,
    "misc.ssh": True,
    "misc.leaderboard": False,
    "misc.ankiweb_sync": False,
    "misc.YouShallNotPass_Ankimon_News": False,
    "misc.show_tip_on_startup": True,  # Added default for Tip of the Day
    "misc.discord_rich_presence": False,
    "misc.discord_rich_presence_text": 1,
    "misc.developer_mode": False,
    "trainer.name": "Ash",
    "trainer.sprite": "ash",
    "trainer.id": 0,
    "trainer.cash": 0,
    "trainer.level": 0,
    "trainer.xp": 0,
}


class Settings:
    def __init__(self):
        self.config = self.load_config()
        self.compute_gui_config()

    def get_description(self, key):
        return self.descriptions.get(key, "No description available.")

    def load_config(self):
        from aqt import mw
        
        config = {}
        
        # First, try to load from database
        if hasattr(mw, 'ankimon_db') and mw.ankimon_db is not None:
            try:
                if mw.ankimon_db.has_config():
                    config = mw.ankimon_db.get_all_config()
                    self._apply_type_coercion(config)
            except Exception as e:
                print(f"Ankimon: Error loading config from database: {e}")
        
        # If no config in database, fall back to config.obf for migration
        if not config:
            obfuscated_config_path = user_path / "config.obf"
            if obfuscated_config_path.is_file():
                try:
                    from ..pyobj.ankimon_sync import AnkimonDataSync
                    sync_handler = AnkimonDataSync()
                    
                    with open(obfuscated_config_path, "r", encoding="utf-8") as f:
                        obfuscated_str = f.read()
                    config = sync_handler._deobfuscate_data(obfuscated_str)
                    
                    # Migration: remove legacy keys
                    if "items" in config and isinstance(config["items"], list):
                        del config["items"]
                    if "trainer.team" in config:
                        del config["trainer.team"]
                    
                    self._apply_type_coercion(config)
                    
                    # Migrate config to database
                    if hasattr(mw, 'ankimon_db') and mw.ankimon_db is not None:
                        try:
                            mw.ankimon_db.save_all_config(config)
                            print("Ankimon: Migrated config from config.obf to database")
                        except Exception as e:
                            print(f"Ankimon: Failed to migrate config to database: {e}")
                            
                except Exception as e:
                    print(f"Ankimon: Error loading config from config.obf: {e}. Falling back to default config.")
                    config = {}

        # Ensure all default settings are present
        modified = False
        for key in DEFAULT_CONFIG:
            if key not in config:
                modified = True
                config[key] = DEFAULT_CONFIG[key]

        if modified:
            self.save_config(config)

        return config
    
    def _apply_type_coercion(self, config):
        """Apply type coercion to config values that need to be integers."""
        keys_to_coerce_to_int = [
            "battle.automatic_battle",
            "battle.daily_average",
            "gui.reviewer_text_message_box_time",
            "gui.xp_bar_location",
            "misc.discord_rich_presence_text",
        ]
        for key in keys_to_coerce_to_int:
            if key in config and isinstance(config[key], str):
                try:
                    config[key] = int(config[key])
                except ValueError:
                    print(f"Ankimon: Warning: Could not convert '{config[key]}' for key '{key}' to int.")

    def save_config(self, config):
        from ..pyobj.ankimon_sync import AnkimonDataSync  # To reuse obfuscation logic

        obfuscated_config_path = user_path / "config.obf"
        sync_handler = AnkimonDataSync()  # Re-use the obfuscation logic
        try:
            obfuscated_str = sync_handler._obfuscate_data(config)
            warning_message = "WARNING: This file contains important user data. Do not delete or modify this file. Deleting or modifying this file can lead to data loss in the Ankimon addon.\n---"
            file_content = warning_message + obfuscated_str
            with open(obfuscated_config_path, "w", encoding="utf-8") as f:
                f.write(file_content)
        except Exception as e:
            print(f"Ankimon: Could not save obfuscated config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save_config(self.config)
        self.load_config()

    def compute_gui_config(self):
        # Manage conditional GUI settings
        config = self.config
        sound_effects = config.get("audio.sound_effects", False)

        view_main_front = config.get("gui.view_main_front", True)
        reviewer_image_gif = config.get("gui.reviewer_image_gif", False)
        self.view_main_front = -1 if view_main_front and reviewer_image_gif else 1

        animate_time = config.get("gui.animate_time", False)
        self.animate_time = 0.8 if animate_time else 0

        xp_bar_location = config.get("gui.xp_bar_location", 0)
        xp_bar_config = config.get("gui.xp_bar_config", False)
        if xp_bar_config:
            if xp_bar_location == 1:
                self.xp_bar_location = "top"
                self.xp_bar_spacer = 0
            elif xp_bar_location == 2:
                self.xp_bar_location = "bottom"
                self.xp_bar_spacer = 20
        else:
            self.xp_bar_spacer = 0

        hp_bar_config = config.get("gui.hp_bar_config", True)
        if not hp_bar_config:
            self.hp_only_spacer = 15
            self.wild_hp_spacer = 65
        else:
            self.hp_only_spacer = 0
            self.wild_hp_spacer = 0

    def compute_special_variable(self, key):
        # Dynamically compute and return the requested GUI variable
        if key == "view_main_front":
            view_main_front = self.config.get("gui.view_main_front", True)
            reviewer_image_gif = self.config.get("gui.reviewer_image_gif", False)
            return -1 if view_main_front and reviewer_image_gif else 1

        elif key == "animate_time":
            animate_time = self.config.get("gui.animate_time", False)
            return 0.8 if animate_time else 0

        elif key == "xp_bar_location":
            xp_bar_config = self.config.get("gui.xp_bar_config", True)
            xp_bar_location = int(self.config.get("gui.xp_bar_location", 2))

            if xp_bar_config:
                if xp_bar_location == 1:
                    return "top"
                elif xp_bar_location == 2:
                    return "bottom"
            return None  # Default when XP bar is disabled

        elif key == "xp_bar_spacer":
            xp_bar_config = self.config.get("gui.xp_bar_config", False)
            xp_bar_location = self.config.get("gui.xp_bar_location", 0)

            if xp_bar_config:
                if xp_bar_location == 2:  # Bottom
                    return 20
                elif xp_bar_location == 1:  # Top
                    return 0
            return 0  # Default spacer

        elif key == "hp_only_spacer":
            hp_bar_config = self.config.get("gui.hp_bar_config", True)
            return 15 if not hp_bar_config else 0

        elif key == "wild_hp_spacer":
            hp_bar_config = self.config.get("gui.hp_bar_config", True)
            return 65 if not hp_bar_config else 0

        else:
            raise ValueError(f"Unknown key: {key}")
