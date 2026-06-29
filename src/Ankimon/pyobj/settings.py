import json
import os
import shutil
from pathlib import Path
from ..resources import user_path
from ..services import services

DEFAULT_CONFIG = {
    "battle.automatic_battle": 0,
    "battle.automatic_catch_special": True,
    "battle.cards_per_round": 2,
    "battle.daily_average": 100,
    "battle.card_max_time": 60,
    "battle.review_based_damage": True,
    "evolution.friendship_time_enabled": True,
    "evolution.day_start_hour": 6,
    "evolution.night_start_hour": 18,
    "evolution.timezone_auto": True,
    "evolution.timezone_offset": 0.0,
    "controls.pokemon_buttons": True,
    "controls.defeat_key": "5",
    "controls.catch_key": "6",
    "controls.team_cycle_key": "9",
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
    "gui.hud_hidden_on_startup": False,    
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
    "trainer.cash_reward_amount": 100,
    "trainer.cash_reward_interval": 10,
    "trainer.cash_earned_today": 0,
    "trainer.last_cash_reward_date": "",
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
        config = {}

        # First, try to load from database
        if services.db is not None:
            try:
                if services.db.has_config():
                    config = services.db.get_all_config()
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
                    if services.db is not None:
                        try:
                            services.db.save_all_config(config)
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

        if not hasattr(self, 'config'):
            self.config = {}
        if self.config is not config:
            self.config.clear()
            self.config.update(config)
        self.compute_gui_config()
        return self.config
    
    def _apply_type_coercion(self, config):
        """Apply type coercion to config values that need to be integers."""
        keys_to_coerce_to_int = [
            "battle.automatic_battle",
            "battle.daily_average",
            "gui.reviewer_text_message_box_time",
            "gui.xp_bar_location",
            "misc.discord_rich_presence_text",
            "trainer.cash_reward_amount",
            "trainer.cash_reward_interval",
            "trainer.cash_earned_today",
        ]
        for key in keys_to_coerce_to_int:
            if key in config and isinstance(config[key], str):
                try:
                    config[key] = int(config[key])
                except ValueError:
                    print(f"Ankimon: Warning: Could not convert '{config[key]}' for key '{key}' to int.")

    def save_config(self, config):
        # 1. Always save to database if available
        if services.db is not None:
            try:
                services.db.save_all_config(config)
                print("Ankimon: Saved config to database")
            except Exception as e:
                print(f"Ankimon: Failed to save config to database: {e}")

        # 2. Also save to obfuscated file if it exists (legacy support)
        self.config = config
        self._save_legacy_obf_if_present()
        self.compute_gui_config()

    def _save_legacy_obf_if_present(self):
        """Mirror self.config into a legacy config.obf, only if one still exists
        (pre-migration profiles). Migrated profiles archived it, so this is a no-op.
        Note: once moved to the archive folder this file is no longer found here."""
        obfuscated_config_path = user_path / "config.obf"
        if not obfuscated_config_path.is_file():
            return
        try:
            # Imported lazily, and only when a legacy config.obf is present, so this
            # module never drags in ankimon_sync (and thus aqt) at import time.
            from ..pyobj.ankimon_sync import AnkimonDataSync
            sync_handler = AnkimonDataSync()  # Re-use the obfuscation logic
            obfuscated_str = sync_handler._obfuscate_data(self.config)
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
        # Persist ONLY the changed key. The previous implementation re-saved the
        # entire config (~60 rows + a commit) on every set; the battle loop awards
        # cash per review, so a single battle rewrote all of config dozens of times.
        if services.db is not None:
            try:
                services.db.set_config_value(key, value)
            except Exception as e:
                print(f"Ankimon: Failed to save config key '{key}': {e}")
        else:
            # No DB yet (very early boot / legacy) — fall back to the full save.
            self.save_config(self.config)
            return
        self._save_legacy_obf_if_present()
        self.compute_gui_config()

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
