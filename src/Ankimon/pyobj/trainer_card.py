from ..resources import trainer_sprites_path, mypokemon_path, team_pokemon_path
from ..functions.trainer_functions import find_trainer_rank
from ..functions.badges_functions import get_achieved_badges
from aqt import mw
from aqt.utils import showWarning, showInfo
import math
import json
from .ankimon_leaderboard import (
    sync_data_to_leaderboard,
    show_api_key_dialog
)


# Constants for leveling
BASE_XP = 50  # Base XP required for level 1
EXPONENTIAL_FACTOR = 1.5  # Scaling factor for exponential XP curve

# Tier-based XP rewards (can be extended)
POKEMON_TIERS = {
    "normal": 10,
    "baby": 16,
    "ultra": 30,
    "legendary": 120,
    "mythical": 160,
}


class TrainerCard:
    def __init__(
        self,
        logger,
        main_pokemon,
        settings_obj,
        trainer_name,
        trainer_id,
        level=1,
        achievements=None,
        team=None,
        image_path=trainer_sprites_path,
        league="unranked",
    ):
        self.logger = logger
        self.main_pokemon = main_pokemon
        self.settings_obj = settings_obj
        self.trainer_name = trainer_name  # Name of the trainer
        self.favorite_pokemon = main_pokemon.name  # Trainer's favorite Pokémon
        self.trainer_id = trainer_id  # Unique ID for the trainer
        self.level = int(settings_obj.get("trainer.level"))  # Trainer's level
        self.xp = int(settings_obj.get("trainer.xp"))  # Experience points
        self.total_xp = int(settings_obj.get("trainer.total_xp", 0)) # Total Experience points
        self.achievements = (
            achievements if achievements else []
        )  # List of achievements (if any)
        self.team = team if team is not None else self.get_team()  # Team as a simple string
        highest_level = self.get_highest_level_pokemon()
        self.highest_level = highest_level  # Highest level Pokémon
        highest_pokemon_level = int(self.highest_pokemon_level())
        self.image_path = (
            f"{trainer_sprites_path}"
            + "/"
            + settings_obj.get("trainer.sprite")
            + ".png"
        )
        league = find_trainer_rank(
            int(self.highest_pokemon_level()), int(self.level)
        )  # Trainer's rank in the Pokémon world
        self.league = league
        cash = int(settings_obj.get("trainer.cash"))
        self.cash = cash

        # Sync Data to ankimon leaderboard
        data = {
            "trainerRank": f"{league}",  # Example rank
            "trainerName": trainer_name,  # Example trainer name
            "level": max(1, int(settings_obj.get("trainer.level"))),
            "pokedex": mw.ankimon_db.execute("SELECT COUNT(DISTINCT pokedex_id) FROM captured_pokemon WHERE pokedex_id IS NOT NULL").fetchone()[0],
            "caughtPokemon": mw.ankimon_db.get_pokemon_count(),
            "trainerLevel": self.level,  # Add a logic for trainer's level if applicable
            "highestLevel": highest_pokemon_level,  # Example highest level
            "shinies": f"{mw.ankimon_db.get_shiny_count()}",  # Example shinies
            "cash": cash,  # Example cash,
            "trainerSprite": f"{settings_obj.get('trainer.sprite') + '.png'}",
        }
        try:
            sync_data_to_leaderboard(data)
        except Exception as e:
            self.logger.log_and_showinfo(
                "error", f"Error in syncing data to leaderboard {e}"
            )

    # Number of badges the trainer has earned
    def badge_count(self):
        return len(get_achieved_badges())

    def get_highest_level_pokemon(self):
        """Method to find the name of the highest-level Pokémon from the database."""
        try:
            db = mw.ankimon_db
            cursor = db.execute("SELECT name, level FROM captured_pokemon WHERE level IS NOT NULL ORDER BY level DESC LIMIT 1")
            row = cursor.fetchone()

            if not row:
                return None  # Return None if the data is empty

            return f"{row['name']} (Level {row['level']})"
        except Exception as e:
            showInfo(f"Error getting highest level pokemon: {e}")
            return "None"

    def highest_pokemon_level(self):
        """Method to find the highest level from all Pokémon in the database."""
        try:
            db = mw.ankimon_db
            cursor = db.execute("SELECT level FROM captured_pokemon WHERE level IS NOT NULL ORDER BY level DESC LIMIT 1")
            row = cursor.fetchone()

            if not row:
                return int(0)  # Return 0 if the data is empty

            return int(row["level"])
        except Exception as e:
            showInfo(f"Error getting highest level: {e}")
            return int(0)

    def add_achievement(self, achievement):
        """Method to add a new achievement"""
        self.achievements.append(achievement)

    def get_team(self):
        """Method to get the trainer's active team (team as a string)"""
        try:
            team_data = mw.ankimon_db.get_team()
            
            if not team_data:
                return "No Team Set"

            # Use new DB method for targeted fetch
            ids_to_fetch = [str(t.get("individual_id")) for t in team_data if t.get("individual_id")]
            my_pokemon_data = mw.ankimon_db.get_pokemons_by_individual_ids(ids_to_fetch)

            # Create lookup dict
            pokemon_map = {str(p.get("individual_id")): p for p in my_pokemon_data}

            pokemon_strings = []
            for pokemon in team_data:
                ind_id = str(pokemon.get("individual_id"))
                if ind_id in pokemon_map:
                    p = pokemon_map[ind_id]
                    pokemon_strings.append(f"{p.get('name')} (Level {p.get('level')})")
                else:
                    pokemon_strings.append("Unknown Pokemon")

            return ", ".join(pokemon_strings)

        except FileNotFoundError:
            return "No Team Set"
        except Exception as e:
            self.logger.log_and_showinfo("error", f"Error ; team.json: {e}")
            return "Error Loading Team"

    def set_team(self, team_pokemons):
        """Method to set the trainer's active team (team as a string)"""
        self.team = ", ".join(team_pokemons)

    def reload_team(self):
        """Reload the team data from the file"""
        self.team = self.get_team()

    def display_card_data(self):
        """Method to return trainer card data as a dictionary"""
        return {
            "trainer_name": self.trainer_name,
            "trainer_id": self.trainer_id,
            "level": self.level,
            "xp": self.xp,
            "total_xp": self.total_xp,
            "badges": self.badge_count(),
            "favorite_pokemon": self.main_pokemon.name,
            "highest_level_pokemon": self.get_highest_level_pokemon(),
            "team": self.team,
            "achievements": self.achievements,
            "xp_for_next_level": self.xp_for_next_level,
            "league": self.league,
        }

    def xp_for_next_level(self):
        """Calculate XP required for the next level."""
        return int(BASE_XP * math.pow(self.level, EXPONENTIAL_FACTOR))

    def on_level_up(self):
        """Triggered when leveling up."""
        self.logger.log_and_showinfo(
            "game", f"Congratulations! You reached Level {self.level}!"
        )

    def gain_xp(self, tier, allow_to_choose_move=False):
        """Add XP based on defeated Pokémon's tier."""
        xp_gained = POKEMON_TIERS.get(tier.lower(), 0)
        if allow_to_choose_move is True:
            xp_gained = xp_gained * 0.5
        self.settings_obj.set(
            "trainer.xp", int(self.settings_obj.get("trainer.xp") + xp_gained)
        )
        self.settings_obj.set(
            "trainer.total_xp", int(self.settings_obj.get("trainer.total_xp", 0) + xp_gained)
        )
        self.xp = self.settings_obj.get("trainer.xp")
        self.total_xp = self.settings_obj.get("trainer.total_xp")
        print(f"Gained {xp_gained} XP from defeating a {tier} Pokémon!")
        self.check_level_up()

    def check_level_up(self):
        """Update level based on XP."""
        xp_needed = self.xp_for_next_level()
        while self.xp >= xp_needed:
            self.xp -= xp_needed
            self.level += 1
            self.settings_obj.set("trainer.level", self.level)
            self.settings_obj.set("trainer.xp", self.xp)
            self.on_level_up()
            # Recalculate for next iteration (in case multiple levels gained)
            xp_needed = self.xp_for_next_level()
