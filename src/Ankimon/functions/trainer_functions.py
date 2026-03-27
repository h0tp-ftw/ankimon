import json

from .badges_functions import get_achieved_badges
from .pokedex_functions import extract_ids_from_file
from ..resources import mypokemon_path
from .pokemon_functions import find_experience_for_level
from .pokedex_functions import check_evolution_for_pokemon, return_name_for_id
from aqt.utils import showInfo, showWarning
from aqt import mw

def find_trainer_rank(highest_level, trainer_level):
    """
    Determines the Pokémon rank based on the player's achievements like Pokémon caught (from Pokedex),
    highest level Pokémon, trainer XP, trainer level, shiny Pokémon count, and badges.

    Args:
    highest_level (int): The highest level Pokémon the player owns.
    trainer_level (int): The level of the trainer.

    Returns:
    str: The Pokémon rank (Grand Champion, Champion, Elite, Veteran, Rookie, etc.).
    """
    try:
        # Count the amount of Pokémon caught based on the Pokedex
        caught_pokemon = mw.ankimon_db.execute("SELECT COUNT(DISTINCT pokedex_id) FROM captured_pokemon").fetchone()[0]

        # Count the number of shiny Pokémon
        shiny_pokemon_count = mw.ankimon_db.get_shiny_count()

        # Count badges
        badge_count = len(get_achieved_badges())

        # Determine rank based on achievements
        if caught_pokemon >= 900 and highest_level >= 99 and trainer_level >= 100 and shiny_pokemon_count >= 50:
            rank = "Legendary Trainer"
        elif caught_pokemon >= 800 and highest_level >= 95 and trainer_level >= 80 and shiny_pokemon_count >= 25:
            rank = "Grand Champion"
        elif caught_pokemon >= 700 and highest_level >= 90 and trainer_level >= 70 and shiny_pokemon_count >= 20:
            rank = "Champion"
        elif caught_pokemon >= 600 and highest_level >= 80 and trainer_level >= 60 and shiny_pokemon_count >= 10 and badge_count >= 8:
            rank = "Master Trainer"
        elif caught_pokemon >= 500 and highest_level >= 75 and trainer_level >= 50 and shiny_pokemon_count >= 5 and badge_count > 6:
            rank = "Elite"
        elif caught_pokemon >= 400 and highest_level >= 70 and trainer_level >= 45 and shiny_pokemon_count >= 3 and badge_count > 5:
            rank = "Elite Trainer"
        elif caught_pokemon >= 350 and highest_level >= 60 and trainer_level >= 40 and shiny_pokemon_count >= 2 and badge_count > 4:
            rank = "Advanced Trainer"
        elif caught_pokemon >= 300 and highest_level >= 50 and trainer_level >= 30 and shiny_pokemon_count > 0 and badge_count > 3:
            rank = "Veteran"
        elif caught_pokemon >= 250 and highest_level >= 40 and trainer_level >= 20 and shiny_pokemon_count > 0:
            rank = "Skilled Trainer"
        elif caught_pokemon >= 150 and highest_level >= 30 and trainer_level >= 10:
            rank = "Rookie"
        else:
            rank = "Novice Trainer"  # Default rank for beginners

        return rank

    except FileNotFoundError:
        print("Error: One of the files (Pokedex or MyPokemon) could not be found.")
        return "Unknown Rank"

def xp_share_gain_exp(logger, settings_obj, evo_window, main_pokemon_id, exp, xp_share_individual_id):
    # Ensure that the XP Share Pokémon is set and different from the main Pokémon
    if not xp_share_individual_id:
        return exp

    if xp_share_individual_id == main_pokemon_id:
        return exp

    original_exp = int(exp * 0.5)
    remove_level_cap = settings_obj.get("misc.remove_level_cap")
    exp = int(exp * 0.5)  # Convert the experience to an integer

    # Load pokemon from database
    db = mw.ankimon_db

    msg = ""
    evolution_triggered = False

    pokemon = db.get_pokemon(xp_share_individual_id)
    # Increase the xp of the matched Pokémon
    current_level = int(pokemon['level'])  # MODIFIED: Use local variable for level
    current_xp = pokemon.get("xp") or pokemon.get("stats", {}).get("xp", 0)
    growth_rate = pokemon['growth_rate']  # MODIFIED: Use local variable for growth rate
    experience_needed = int(find_experience_for_level(growth_rate, current_level, remove_level_cap))  # MODIFIED: Pre-calculate needed XP
    evo_id = None # Initialize variable

    logger.log("info", "Running XP share function")
    if experience_needed > exp + current_xp:
        pokemon["xp"] = current_xp + exp
    else:
        while exp + current_xp > experience_needed:
            if (remove_level_cap or current_level < 100):
                current_level += 1
                exp = exp + current_xp - experience_needed
                current_xp = 0
                experience_needed = int(find_experience_for_level(growth_rate, current_level, remove_level_cap))  # MODIFIED: Recalculate needed XP
                msg += f"XP increased for {pokemon['name']} with level {current_level} and XP {exp}\n"
            else:
                break
        pokemon['level'] = current_level
        pokemon['xp'] = 0 if exp < 0 else exp

    # Check for evolution
    evo_id = check_evolution_for_pokemon(
        pokemon['individual_id'],
        pokemon['id'],
        pokemon['level'],
        evo_window,
        pokemon['everstone']
    )

    if evo_id is not None:
        msg += f"{pokemon['name']} is about to evolve to {return_name_for_id(evo_id).capitalize()} at level {pokemon['level']}"
        evolution_triggered = True

        # Write the XP/level changes to database BEFORE calling evolution
        db.save_pokemon(pokemon)

        # Now call evolution (which will read the updated file and handle the evolution)

    # Only save to database if no evolution was triggered (since evolution already saved)
    if not evolution_triggered:
        db.save_pokemon(pokemon)

    logger.log("info", f"{msg}")
    return original_exp  # Return the amount of experience added
