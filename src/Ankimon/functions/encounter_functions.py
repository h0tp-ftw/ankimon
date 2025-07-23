import json
import random
import logging
import math
from typing import Union
from datetime import datetime
import uuid

from aqt import mw
from aqt.qt import QDialog
from aqt.utils import showWarning

from ..pyobj.ankimon_tracker import AnkimonTracker
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.reviewer_obj import Reviewer_Manager
from ..pyobj.test_window import TestWindow
from ..pyobj.trainer_card import TrainerCard
from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.evolution_window import EvoWindow
from ..pyobj.attack_dialog import AttackDialog
from ..pyobj.translator import Translator
from ..functions.pokemon_functions import find_experience_for_level, get_levelup_move_for_pokemon, pick_random_gender, shiny_chance
from ..functions.pokedex_functions import (
    check_evolution_for_pokemon,
    get_all_pokemon_moves,
    return_name_for_id,
    search_pokeapi_db_by_id,
    search_pokedex,
    search_pokedex_by_id
)
from ..pyobj.error_handler import show_warning_with_traceback
from ..functions.trainer_functions import xp_share_gain_exp
from ..functions.badges_functions import check_for_badge, receive_badge
from ..functions.drawing_utils import tooltipWithColour
from ..utils import limit_ev_yield, play_effect_sound, iv_rand_gauss, get_ev_spread
from ..business import calc_experience
from ..const import gen_ids
from ..singletons import (
    main_pokemon,
    ankimon_tracker_obj,
    trainer_card,
    settings_obj,
    translator,
)
from ..resources import (
    POKEMON_TIERS,
    mypokemon_path,
    mainpokemon_path,
)
from ..config_var import remove_levelcap

# form selection
ALLOWED_FORMES = [
    "alola", "hisui", "galar", "paldea", "origin", "therian", "incarnate",
    "paldea-aqua", "paldea-blaze", "paldea-combat", "origin", "altered", "incarnate", "therian", "average", "large", "small", "super",
    "red-striped", "blue-striped", "white-striped", "autumn", "spring", "summer", "winter", "blue", "orange", "red", "white", "yellow", "eternal",
    "natural", "dandy", "debutante", "diamond", "heart", "kabuki", "la-reine", "matron", "pharaoh", "star", "male", "female", "plant",
    "sandy", "trash", "east", "west", "midday", "midnight", "dusk", "disguised", "bloodmoon", "cornerstone", "hearthflame", "wellspring",
    "droopy", "stretchy", "curly", "four", "threesegment", "roaming"
]
def select_pokemon_form(pokemon_id):
    """
    Given a Pokémon ID, checks for alternate forms and randomly selects one
    that has a valid sprite, giving equal chance to all valid forms including base.
    """
    try:
        base_name = search_pokedex_by_id(pokemon_id)
        if not base_name:
            return None, None

        # Start with the base form as a candidate.
        valid_forms = [(base_name, None)] 

        forme_order_list = search_pokedex(base_name, "formeOrder")
        
        if forme_order_list:
            for full_forme_name in forme_order_list:
                if full_forme_name.lower() == base_name.lower():
                    continue

                pokedex_key = full_forme_name.replace('-', '').lower()
                form_key = search_pokedex(pokedex_key, "forme")
                
                if form_key and any(allowed in form_key.lower() for allowed in ALLOWED_FORMES):
                    # You'll need a function to check if sprites exist. 
                    # This is a placeholder for your actual sprite checking logic.
                    # For now, we'll assume sprites exist if the form is allowed.
                    valid_forms.append((full_forme_name, form_key))

        # Choose randomly from all valid forms (base + alternates)
        return random.choice(valid_forms)

    except Exception as e:
        # Fallback to base form in case of an error
        base_name = search_pokedex_by_id(pokemon_id)
        if base_name:
            return base_name, None
        return None, None

def modify_percentages(total_reviews, daily_average, player_level):
    """
    Modify Pokémon encounter percentages based on total reviews, player level, event modifiers, and main Pokémon level.
    """
    # Start with the base percentages
    percentages = {"Baby": 2, "Legendary": 0.5, "Mythical": 0.2, "Normal": 92.3, "Ultra": 5}

    # Adjust percentages based on total reviews relative to the daily average
    review_ratio = total_reviews / daily_average if daily_average > 0 else 0

    # Adjust for review progress
    if review_ratio < 0.4:
        percentages["Normal"] += percentages.pop("Baby", 0) + percentages.pop("Legendary", 0) + \
                                 percentages.pop("Mythical", 0) + percentages.pop("Ultra", 0)
    elif review_ratio < 0.6:
        percentages["Baby"] += 2
        percentages["Normal"] -= 2
    elif review_ratio < 0.8:
        percentages["Ultra"] += 3
        percentages["Normal"] -= 3
    else:
        percentages["Legendary"] += 2
        percentages["Ultra"] += 3
        percentages["Normal"] -= 5
    
    # Restrict access to certain tiers based on main Pokémon level
    if main_pokemon.level:
        # Define level thresholds for each tier
        level_thresholds = {
            "Ultra": 30,  # Example threshold for Ultra Pokémon
            "Legendary": 50,  # Example threshold for Legendary Pokémon
            "Mythical": 75  # Example threshold for Mythical Pokémon
        }

        for tier in ["Ultra", "Legendary", "Mythical"]:
            if main_pokemon.level < level_thresholds.get(tier, float("inf")):
                percentages[tier] = 0  # Set percentage to 0 if the level requirement isn't met

    # Example modification based on player level
    if player_level:
        adjustment = 5  # Adjustment value for the example
        if player_level > 10:
            for tier in percentages:
                if tier == "Normal":
                    percentages[tier] = max(percentages[tier] - adjustment, 0)
                else:
                    percentages[tier] = percentages.get(tier, 0) + adjustment
                    
    # Normalize percentages to ensure they sum to 100
    total = sum(percentages.values())
    for tier in percentages:
        percentages[tier] = (percentages[tier] / total) * 100 if total > 0 else 0
    # this function gets called maybe 10 times per battle round, which is concerning. 
    # it could be rewritten to run ONLY when the change in review ratio is detected.
    return percentages

import logging
logger = logging.getLogger(__name__)

def get_pokemon_id_by_tier(tier):
    if tier not in POKEMON_TIERS:
        logger.warning(f"Tier '{tier}' not found in POKEMON_TIERS dictionary.")
        return None
    tier_ids = POKEMON_TIERS[tier]
    if not tier_ids:
        logger.warning(f"No Pokémon IDs found in tier '{tier}'.")
        return None
    return random.choice(tier_ids)

def get_tier(total_reviews, player_level=1, event_modifier=None):
    """_summary_
    Randomly picks the tier for a new enemy Pokemon to be generated from, based on weighted probabilities based on number of reviews and player level.

    Args:
        total_reviews (int): Number of reviews done in that Anki session.
        player_level (int, optional): Trainer XP level. Defaults to 1.
        event_modifier (?, optional): Unused argument. Defaults to None.

    Returns:
        choice[0]: The first choice of TIER picked randomly (by a random.choices function) 
    """
    daily_average = int(settings_obj.get('battle.daily_average', 100))
    percentages = modify_percentages(total_reviews, daily_average, player_level)
    
    tiers = list(percentages.keys())
    probabilities = list(percentages.values())
    
    choice = random.choices(tiers, probabilities, k=1)
    return choice[0]

def choose_random_pkmn_from_tier():
    """
    Runs a tier-selection and a subsequent ID-selection function to pick a random Pokemon from a given randomly picked Tier.
    The tier is a weighted probability selection, based on total_reviews and trainer_level.
    Pokemon ID is picked randomly from within that tier. 

    Returns:
        id (int): Pokedex ID for generated Pokemon
        tier (string): Rarity tier for generated Pokemon (normal/ultra/legendary etc.)
    """
    total_reviews = ankimon_tracker_obj.total_reviews
    trainer_level = trainer_card.level
    try:
        tier = get_tier(total_reviews, trainer_level)
        id = get_pokemon_id_by_tier(tier)
        return id, tier
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message="Error occurred")

def check_min_generate_level(name):
    evo_type = search_pokedex(name.lower(), "evoType")
    evo_level = search_pokedex(name.lower(), "evoLevel")

    if evo_type is None and evo_level is None:
        # This is likely a base form with no evolution — allow level 1
        return 1
    elif evo_type is not None and evo_level is None:
        # This evolves by friendship, item, trade, or unknown — assume level 20 minimum
        return 20
    elif evo_level is not None:
        # This evolves by level — we return its evolution level as minimum encounter level
        return int(evo_level)
    else:
        # Fallback
        return 1

def check_id_ok(id_num: Union[int, list[int]]):
    if isinstance(id_num, list):
        if len(id_num) > 0:
            id_num = id_num[0]
        else:
            return False
    
    if not isinstance(id_num, int):
        return False

    if id_num >= 898:
        return False

    generation = 0
    for gen, max_id in gen_ids.items():
        if id_num <= max_id:
            generation = int(gen.split('_')[1])
            config = mw.addonManager.getConfig(__name__)
            gen_config = [config[f"misc.gen{i}"] for i in range(1, 10)]
            return gen_config[generation - 1]

    return False

def is_pokemon_level_valid(pokemon_name: str, wild_level: int) -> bool:
    """
    Validate Pokémon level according to its evolution stage.

    - If Pokémon has a pre-evolution, it must be at least its evolution level.
    - If Pokémon has evolutions, it cannot appear at or above the next evolution's level.
    """
    key = pokemon_name.lower()
    prevo = search_pokedex(key, "prevo")
    evo_level = search_pokedex(key, "evoLevel")
    evos = search_pokedex(key, "evos")

    # Evolved Pokémon: must be at or above their evolution level
    if prevo is not None:
        if evo_level is None or wild_level < int(evo_level):
            return False

    # Unevolved Pokémon: cannot appear if level >= next evolution's level
    if evos:
        next_evo_key = evos[0].lower()
        next_evo_level = search_pokedex(next_evo_key, "evoLevel")
        if next_evo_level is not None and wild_level >= int(next_evo_level):
            return False

    return True
    
def generate_random_pokemon(main_pokemon_level: int, ankimon_tracker_obj: AnkimonTracker):
    """
    Generates a wild Pokémon with level-appropriate stats and legality,
    including proper evolution-level filtering.
    """
    lvl_variation = 3
    lvl_range = (
        max(1, main_pokemon_level - lvl_variation),
        min(100, main_pokemon_level + lvl_variation)
    )

    attempts = 0
    while attempts < 100:
        wild_pokemon_lvl = random.randint(*lvl_range)
        pokemon_id, tier = choose_random_pkmn_from_tier()
        name, form_name = select_pokemon_form(pokemon_id)

        if not name:
            attempts += 1
            continue

        min_allowed_level = check_min_generate_level(name.lower())
        # Check all conditions: ID validity, level >= min allowed, and evolution-level validity
        if (check_id_ok(pokemon_id) and
            wild_pokemon_lvl >= min_allowed_level and
            is_pokemon_level_valid(name, wild_pokemon_lvl)):

            # Gather Pokémon data
            pokemon_type = search_pokedex(name, "types")
            base_experience = search_pokeapi_db_by_id(pokemon_id, "base_experience")
            growth_rate = search_pokeapi_db_by_id(pokemon_id, "growth_rate")
            ev_yield = search_pokeapi_db_by_id(pokemon_id, "effort_values")
            gender = pick_random_gender(name)
            is_shiny = shiny_chance()
            battle_status = "fighting"
            base_stats = search_pokedex(name, "baseStats")

            all_possible_moves = get_all_pokemon_moves(name, wild_pokemon_lvl)
            moves = all_possible_moves if len(all_possible_moves) <= 4 else random.sample(all_possible_moves, 4)

            ability = "no_ability"
            possible_abilities = search_pokedex(name, "abilities")
            if possible_abilities:
                numeric_abilities = {k: v for k, v in possible_abilities.items() if k.isdigit()}
                if numeric_abilities:
                    ability = random.choice(list(numeric_abilities.values()))

            stat_names = ["hp", "atk", "def", "spa", "spd", "spe"]
            ev = get_ev_spread(random.choice(["random", "pair", "defense", "uniform"]))
            iv = {stat: random.randint(0, 31) for stat in stat_names}
            final_stats = base_stats

            # Reset encounter tracker
            ankimon_tracker_obj.pokemon_encounter = 0
            ankimon_tracker_obj.cards_battle_round = 0

            return (
                name,
                pokemon_id,
                wild_pokemon_lvl,
                ability,
                pokemon_type,
                base_stats,
                moves,
                base_experience,
                growth_rate,
                ev,
                iv,
                gender,
                battle_status,
                final_stats,
                tier,
                ev_yield,
                is_shiny,
                form_name
            )

        attempts += 1

    raise ValueError("Failed to generate a valid Pokémon after 100 attempts.")

def new_pokemon(
        pokemon: PokemonObject,
        test_window: TestWindow,
        ankimon_tracker: AnkimonTracker,
        reviewer_obj: Reviewer_Manager
        ) -> PokemonObject:
    """
    Initializes a new wild Pokémon encounter by generating a random Pokémon,
    updating its stats, setting its HP, and preparing the battle scene.

    This function uses the player's main Pokémon level to generate an appropriately
    leveled wild Pokémon with randomized attributes. It updates the provided `pokemon`
    object with generated data, resets HP, triggers any battle scene randomization,
    and updates the reviewer interface if applicable.

    Args:
        pokemon (PokemonObject): The Pokémon object to be updated with the new wild Pokémon's data.
        test_window (TestWindow): Optional UI window to display the first encounter scene.
        ankimon_tracker (AnkimonTracker): Object tracking battle-related state and handling battle scene randomization.
        reviewer_obj (Reviewer_Manager): Manager object responsible for updating battle review elements like life bars.

    Returns:
        PokemonObject: The updated `pokemon` object representing the newly generated wild Pokémon ready for battle.
    """
    (
        name,
        pkmn_id,
        level,
        ability,
        pkmn_type,
        base_stats,
        enemy_attacks,
        base_experience,
        growth_rate,
        ev,
        iv,
        gender,
        battle_status,
        battle_stats,
        tier,
        ev_yield,
        is_shiny,
        form_name
        ) = generate_random_pokemon(main_pokemon.level, ankimon_tracker_obj)
    pokemon_data = {
        'name': name,
        'id': pkmn_id,
        'level': level,
        'ability': ability,
        'type': pkmn_type,
        'base_stats': base_stats,
        'attacks': enemy_attacks,
        'base_experience': base_experience,
        'growth_rate': growth_rate,
        'ev': ev,
        'iv': iv,
        'gender': gender,
        'battle_status': battle_status,
        'battle_stats': battle_stats,
        'stat_stages': {'atk': 0, 'def': 0, 'spa': 0, 'spd': 0, 'spe': 0, 'accuracy': 0, 'evasion': 0},
        'tier': tier,
        'ev_yield': ev_yield,
        'shiny': is_shiny,
        'form_name': form_name
    }
    pokemon.update_stats(**pokemon_data)
    max_hp = pokemon.calculate_max_hp()
    pokemon.current_hp = max_hp
    pokemon.hp = max_hp
    pokemon.max_hp = max_hp
    
    ankimon_tracker.randomize_battle_scene()
    if test_window is not None:
        test_window.display_first_encounter()
    class Container(object):
        pass
    reviewer = Container()
    reviewer.web = mw.reviewer.web
    reviewer_obj.update_life_bar(reviewer, 0, 0)

    return pokemon

def save_main_pokemon_progress(
        main_pokemon: PokemonObject,
        enemy_pokemon: PokemonObject,
        exp: int,
        achievements: dict,
        logger: ShowInfoLogger,
        evo_window: EvoWindow,
        ):
    experience = int(find_experience_for_level(main_pokemon.growth_rate, main_pokemon.level, settings_obj.get("misc.remove_level_cap", False)))
    if remove_levelcap is True:
        main_pokemon.xp += exp
        level_cap = None
    elif main_pokemon.level != 100:
        main_pokemon.xp += exp
        level_cap = 100
    if mainpokemon_path.is_file():
        with open(mainpokemon_path, "r", encoding="utf-8") as json_file:
            main_pokemon_data = json.load(json_file)
    else:
        showWarning(translator.translate("missing_mainpokemon_data"))
    while int(find_experience_for_level(main_pokemon.growth_rate, main_pokemon.level, settings_obj.get("misc.remove_level_cap", False))) < int(main_pokemon.xp) and (level_cap is None or main_pokemon.level < level_cap):
        main_pokemon.level += 1
        msg = ""
        msg += f"Your {main_pokemon.name} is now level {main_pokemon.level} !"
        color = "#6A4DAC" #pokemon leveling info color for tooltip
        check = check_for_badge(achievements, 5)
        if check is False:
            achievements = receive_badge(5,achievements)
        try:
            tooltipWithColour(msg, color)
        except:
            pass
        if settings_obj.get('gui.pop_up_dialog_message_on_defeat', True) is True:
            logger.log_and_showinfo("info",f"{msg}")
        main_pokemon.xp = int(max(0, int(main_pokemon.xp) - int(experience)))
        evo_id = check_evolution_for_pokemon(main_pokemon.individual_id, main_pokemon.id, main_pokemon.level, evo_window, main_pokemon.everstone)
        if evo_id is not None:
            msg += translator.translate("pokemon_about_to_evolve", main_pokemon_name=main_pokemon.name, evo_pokemon_name=return_name_for_id(evo_id).capitalize(), main_pokemon_level=main_pokemon.level)
            logger.log_and_showinfo("info",f"{msg}")
            color = "#6A4DAC"
            try:
                tooltipWithColour(msg, color)
            except:
                pass
                    #evo_window.display_pokemon_evo(main_pokemon.name.lower())
        for mainpkmndata in main_pokemon_data:
            if mainpkmndata["name"] == main_pokemon.name.capitalize():
                attacks = mainpkmndata["attacks"]
                new_attacks = get_levelup_move_for_pokemon(main_pokemon.name.lower(),int(main_pokemon.level))
                if new_attacks:
                    msg = ""
                    msg += translator.translate("mainpokemon_can_learn_new_attack", main_pokemon_name=main_pokemon.name.capitalize())
                for new_attack in new_attacks:
                    if len(attacks) < 4 and new_attack not in attacks:
                        attacks.append(new_attack)
                        msg += translator.translate("mainpokemon_learned_new_attack", new_attack_name=new_attack, main_pokemon_name=main_pokemon.name.capitalize())
                        color = "#6A4DAC"
                        tooltipWithColour(msg, color)
                        if settings_obj.get('gui.pop_up_dialog_message_on_defeat', True) is True:
                            logger.log_and_showinfo("info",f"{msg}")
                    else:
                        dialog = AttackDialog(attacks, new_attack)
                        if dialog.exec() == QDialog.DialogCode.Accepted:
                            selected_attack = dialog.selected_attack
                            index_to_replace = None
                            for index, attack in enumerate(attacks):
                                if attack == selected_attack:
                                    index_to_replace = index
                                    pass
                                else:
                                    pass
                            # If the attack is found, replace it with 'new_attack'
                            if index_to_replace is not None:
                                attacks[index_to_replace] = new_attack
                                logger.log_and_showinfo("info",
                                    f"Replaced '{selected_attack}' with '{new_attack}'")
                            else:
                                logger.log_and_showinfo("info",f"'{selected_attack}' not found in the list")
                        else:
                            # Handle the case where the user cancels the dialog
                            logger.log_and_showinfo("info",f"{new_attack} will be discarded.")
                mainpkmndata["attacks"] = attacks
                break
    msg = ""
    msg += translator.translate("mainpokemon_gained_xp", main_pokemon_name=main_pokemon.name, exp=exp, experience_till_next_level=experience, main_pokemon_xp=main_pokemon.xp)
    color = "#a17cf7" #pokemon leveling info color for tooltip
    try:
        tooltipWithColour(msg, color)
    except:
        pass
    if settings_obj.get('gui.pop_up_dialog_message_on_defeat', True) is True:
        logger.log_and_showinfo("info",f"{msg}")
    # Load existing Pokémon data if it exists

    for mainpkmndata in main_pokemon_data:
        mainpkmndata["stats"] = main_pokemon.stats
        mainpkmndata["xp"] = int(main_pokemon.xp)
        #mainpkmndata["stats"]["xp"] = int(main_pokemon.xp)
        mainpkmndata["level"] = int(main_pokemon.level)
        ev_yield = limit_ev_yield(mainpkmndata["ev"], enemy_pokemon.ev_yield)
        mainpkmndata["ev"]["hp"] += ev_yield["hp"]
        mainpkmndata["ev"]["atk"] += ev_yield["attack"]
        mainpkmndata["ev"]["def"] += ev_yield["defense"]
        mainpkmndata["ev"]["spa"] += ev_yield["special-attack"]
        mainpkmndata["ev"]["spd"] += ev_yield["special-defense"]
        mainpkmndata["ev"]["spe"] += ev_yield["speed"]
        mainpkmndata["current_hp"] = int(main_pokemon.current_hp)
        main_pokemon.friendship += random.randint(5, 9)
        if main_pokemon.friendship > 255:
            main_pokemon.friendship = 255
        mainpkmndata["friendship"] = main_pokemon.friendship
        main_pokemon.pokemon_defeated += 1
        mainpkmndata["pokemon_defeated"] = main_pokemon.pokemon_defeated
        if hasattr(main_pokemon, "tier"):
            mainpkmndata["tier"] = main_pokemon.tier
        if hasattr(main_pokemon, "is_favorite"):
            mainpkmndata["is_favorite"] = main_pokemon.is_favorite   
    mypkmndata = mainpkmndata
    mainpkmndata = [mainpkmndata]
    # Save the caught Pokémon's data to a JSON file
    with open(str(mainpokemon_path), "w") as json_file:
        json.dump(mainpkmndata, json_file, indent=2)

    # Load data from the output JSON file
    with open(str(mypokemon_path), "r", encoding="utf-8") as output_file:
        mypokemondata = json.load(output_file)

        # Find and replace the specified Pokémon's data in mypokemondata
        for index, pokemon_data in enumerate(mypokemondata):
            if pokemon_data.get("individual_id") == main_pokemon.individual_id:  # Match by individual_id
                mypokemondata[index] = mypkmndata  # Replace with new data
                break

        # Save the modified data to the output JSON file
        with open(str(mypokemon_path), "w") as output_file:
            json.dump(mypokemondata, output_file, indent=2)

    return main_pokemon.level

def kill_pokemon(
        main_pokemon: PokemonObject,
        enemy_pokemon: PokemonObject,
        evo_window: EvoWindow,
        logger: ShowInfoLogger,
        achievements: dict,
        trainer_card: Union[TrainerCard, None]=None
        ):
    if trainer_card is not None:
        trainer_card.gain_xp(enemy_pokemon.tier, settings_obj.get("controls.allow_to_choose_moves", False))
    
    # Calculate experience based on whether moves are chosen manually
    exp = calc_experience(main_pokemon.base_experience, enemy_pokemon.level)
    if settings_obj.get("controls.allow_to_choose_moves", False):
        exp *= 0.5
    
    # Ensure exp is at least 1 and round up if it's a decimal
    exp = max(1, math.ceil(exp))
    
    # Handle XP share logic
    xp_share_individual_id = settings_obj.get("trainer.xp_share", None)
    if xp_share_individual_id:
        exp = xp_share_gain_exp(logger, settings_obj, evo_window, main_pokemon.id, exp, xp_share_individual_id)
    
    # Save main Pokémon's progress
    main_pokemon.level = save_main_pokemon_progress(
        main_pokemon,
        enemy_pokemon,
        exp,
        achievements,
        logger,
        evo_window,
    )
    
    ankimon_tracker_obj.general_card_count_for_battle = 0

def save_caught_pokemon(
        enemy_pokemon: PokemonObject,
        nickname: Union[str, None]=None,
        achievements: Union[dict, None]=None
        ):
    # Create a dictionary to store the Pokémon's data
    # add all new values like hp as max_hp, evolution_data, description and growth rate
    if enemy_pokemon.tier is not None and achievements is not None:
        if enemy_pokemon.tier == "Normal":
            check = check_for_badge(achievements, 17)
            if check is False:
                achievements = receive_badge(17,achievements)
        elif enemy_pokemon.tier == "Baby":
            check = check_for_badge(achievements, 18)
            if check is False:
                achievements = receive_badge(18, achievements)
        elif enemy_pokemon.tier == "Ultra":
            check = check_for_badge(achievements, 8)
            if check is False:
                achievements = receive_badge(8, achievements)
        elif enemy_pokemon.tier == "Legendary":
            check = check_for_badge(achievements, 9)
            if check is False:
                achievements = receive_badge(9, achievements)
        elif enemy_pokemon.tier == "Mythical":
            check = check_for_badge(achievements, 10)
            if check is False:
                achievements = receive_badge(10, achievements)

    #enemy_pokemon.stats["xp"] = 0
    enemy_pokemon.xp = 0
    form_name = getattr(enemy_pokemon, 'form_name', None)
    caught_pokemon = {
        "name": enemy_pokemon.name.capitalize(),
        "nickname": "",
        "level": enemy_pokemon.level,
        "gender": enemy_pokemon.gender,
        "id": enemy_pokemon.id,
        "ability": enemy_pokemon.ability,
        "type": enemy_pokemon.type,
        "stats": enemy_pokemon.base_stats,
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": enemy_pokemon.iv,
        "attacks": enemy_pokemon.attacks,
        "base_experience": enemy_pokemon.base_experience,
        "current_hp": enemy_pokemon.calculate_max_hp(),
        "growth_rate": enemy_pokemon.growth_rate,
        "friendship": 0,
        "pokemon_defeated": 0,
        "xp": 0,
        "everstone": False,
        "shiny": enemy_pokemon.shiny,
        "captured_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "individual_id": str(uuid.uuid4()),
        "mega": False,
        "form_name": form_name,
        "tier": enemy_pokemon.tier,
    }

    # Load existing Pokémon data if it exists
    caught_pokemon_data = []
    if mypokemon_path.is_file():
        with open(mypokemon_path, "r", encoding="utf-8") as json_file:
            caught_pokemon_data = json.load(json_file)

    # Append the caught Pokémon's data to the list
    caught_pokemon_data.append(caught_pokemon)

    # Save the caught Pokémon's data to a JSON file
    with open(str(mypokemon_path), "w") as json_file:
        json.dump(caught_pokemon_data, json_file, indent=2)

def catch_pokemon(
        enemy_pokemon: PokemonObject,
        ankimon_tracker_obj: AnkimonTracker,
        logger: Union[ShowInfoLogger, None]=None,
        nickname: Union[str, None]=None,
        collected_pokemon_ids: Union[set, None]=None,
        achievements: Union[dict, None]=None,
        ):
    ankimon_tracker_obj.caught += 1
    if ankimon_tracker_obj.caught > 1:
        if settings_obj.get('gui.pop_up_dialog_message_on_defeat', True) is True:
            logger.log_and_showinfo("info",translator.translate("already_caught_pokemon")) # Display a message when the Pokémon is caught

    # If we arrive here, this means that ankimon_tracker_obj.caught == 1
    if nickname is not None or not nickname:
        nickname = enemy_pokemon.name
    if collected_pokemon_ids is not None:
        collected_pokemon_ids.add(enemy_pokemon.id)  # Update cache
    save_caught_pokemon(enemy_pokemon, nickname, achievements)
    
    ankimon_tracker_obj.general_card_count_for_battle = 0

    msg = translator.translate("caught_wild_pokemon", enemy_pokemon_name=enemy_pokemon.name.capitalize())

    if settings_obj.get('gui.pop_up_dialog_message_on_defeat', True) is True:
        if logger is not None:
            logger.log_and_showinfo("info",f"{msg}") # Display a message when the Pokémon is caught

    color = "#a17cf7"#6A4DAC" #pokemon leveling info color for tooltip
    try:
        tooltipWithColour(msg, color)
    except Exception as e:
        if logger is not None:
            show_warning_with_traceback(parent=mw, exception=e, message="Error while catching Pokemon:") # Display a message when the Pokémon is caught

def handle_enemy_faint(
        main_pokemon: PokemonObject,
        enemy_pokemon: PokemonObject,
        collected_pokemon_ids: set,
        test_window: TestWindow,
        evo_window: EvoWindow,
        reviewer_obj: Reviewer_Manager,
        logger: ShowInfoLogger,
        achievements: dict,
        ):
    """
    Handles what automatically happens when the enemy Pokémon faints, based on auto-battle settings.
    """
    try:
        auto_battle_setting = int(settings_obj.get("battle.automatic_battle", 0))
        if not (0 <= auto_battle_setting <= 3):
            auto_battle_setting = 0  # fallback
    except ValueError:
        auto_battle_setting = 0  # fallback

    if auto_battle_setting == 3:  # Catch if uncollected
        enemy_id = enemy_pokemon.id
        # Check cache instead of file
        if enemy_id not in collected_pokemon_ids or enemy_pokemon.shiny:
            catch_pokemon(enemy_pokemon, ankimon_tracker_obj, logger, "", collected_pokemon_ids, achievements)
        else:
            kill_pokemon(main_pokemon, enemy_pokemon, evo_window, logger , achievements, trainer_card)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon
    elif auto_battle_setting == 1:  # Existing auto-catch
        catch_pokemon(enemy_pokemon, ankimon_tracker_obj, logger, "", collected_pokemon_ids, achievements)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon
    elif auto_battle_setting == 2:  # Existing auto-defeat
        kill_pokemon(main_pokemon, enemy_pokemon, evo_window, logger , achievements, trainer_card)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon

    main_pokemon.reset_bonuses()
    ankimon_tracker_obj.general_card_count_for_battle = 0

def handle_main_pokemon_faint(
        main_pokemon: PokemonObject,
        enemy_pokemon: PokemonObject,
        test_window: TestWindow,
        reviewer_obj: Reviewer_Manager,
        translator: Translator,
        ):
    """
    Handles what happens when the main Pokémon faints.
    """
    msg = translator.translate("pokemon_fainted", enemy_pokemon_name=main_pokemon.name.capitalize())
    tooltipWithColour(msg, "#E12939")
    play_effect_sound("Fainted")

    main_pokemon.hp = main_pokemon.max_hp
    main_pokemon.current_hp = main_pokemon.max_hp
    main_pokemon.reset_bonuses()

    new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon