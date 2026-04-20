import json
import random
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
from ..functions.pokemon_functions import (
    find_experience_for_level,
    get_levelup_move_for_pokemon,
    pick_random_gender,
    shiny_chance,
)
from ..functions.pokedex_functions import (
    check_evolution_for_pokemon,
    get_all_pokemon_moves,
    get_base_experience,
    get_effort_values,
    get_growth_rate,
    return_name_for_id,
    search_pokedex,
    search_pokedex_by_id,
)
from ..pyobj.error_handler import show_warning_with_traceback
from ..functions.trainer_functions import xp_share_gain_exp
from ..functions.badges_functions import check_for_badge, receive_badge
from ..functions.drawing_utils import tooltipWithColour
from ..utils import limit_ev_yield, play_effect_sound, get_ev_spread
from ..business import calc_experience, calculate_cp_from_dict
from ..const import gen_ids
from ..singletons import (
    main_pokemon,
    ankimon_tracker_obj,
    trainer_card,
    settings_obj,
    translator,
    pokemon_pc,
)
from ..resources import (
    mypokemon_path,
    mainpokemon_path,
)


def modify_percentages(total_reviews, daily_average, trainer_level):
    """
    Modify Pokémon encounter percentages based on total reviews, trainer level, and main Pokémon level.
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

    # Example modification based on trainer level
    if trainer_level:
        adjustment = 5  # Adjustment value for the example
        if trainer_level > 10:
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


def get_random_pokemon_in_tier(tier):
    from . import encounter_data

    if tier == "Normal":
        id_data = encounter_data.NORMAL
    elif tier == "Baby":
        id_data = encounter_data.BABY
    elif tier == "Ultra":
        id_data = encounter_data.ULTRA
    elif tier == "Legendary":
        id_data = encounter_data.LEGENDARY
    elif tier == "Mythical":
        id_data = encounter_data.MYTHICAL
    else:
        raise ValueError()

    # Select a random Pokemon ID from those in the tier
    random_pokemon_id = random.choice(id_data)
    return random_pokemon_id


def get_tier(total_reviews, trainer_level=1, event_modifier=None):
    """_summary_
    Randomly picks the tier for a new enemy Pokemon to be generated from, based on weighted probabilities based on number of reviews and trainer level.

    Args:
        total_reviews (int): Number of reviews done in that Anki session.
        trainer_level (int, optional): Trainer XP level. Defaults to 1.
        event_modifier (?, optional): Unused argument. Defaults to None.

    Returns:
        choice[0]: The first choice of TIER picked randomly (by a random.choices function)
    """
    daily_average = int(settings_obj.get("battle.daily_average"))
    percentages = modify_percentages(total_reviews, daily_average, trainer_level)

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
        tier (str): Rarity tier for generated Pokemon (normal/ultra/legendary etc.)
    """
    total_reviews = ankimon_tracker_obj.get_total_reviews()
    trainer_level = trainer_card.level
    try:
        tier = get_tier(total_reviews, trainer_level)
        id = get_random_pokemon_in_tier(tier)
        mw.logger.game_log(f"Selected tier: {tier}, Resulting Pokemon ID: {id}")
        return id, tier
    except Exception as e:
        mw.logger.log("error", f"Error in choose_random_pkmn_from_tier: {str(e)}")
        show_warning_with_traceback(parent=mw, exception=e, message="Error occurred")


def check_min_generate_level(name):
    evoType = search_pokedex(name.lower(), "evoType")
    evoLevel = search_pokedex(name.lower(), "evoLevel")
    if evoLevel:
        return int(evoLevel)
    elif evoType != []:
        min_level = 100
        return min_level
    else:
        min_level = 1
        return min_level


def check_id_ok(id_num: Union[int, list[int]]):
    if isinstance(id_num, list):
        if len(id_num) > 0:
            id_num = id_num[0]
        else:
            return False

    if not isinstance(id_num, int):
        return False

    generation = 0
    for gen, max_id in gen_ids.items():
        if id_num <= max_id:
            generation = int(gen.split("_")[1])

            gen_config = [settings_obj.get(f"misc.gen{i}") for i in range(1, 10)]
            return gen_config[generation - 1]

    return False


def generate_random_pokemon(
    main_pokemon_level: int, ankimon_tracker_obj: AnkimonTracker
):
    """
    Generates a random wild Pokémon with attributes scaled to the level of the player's main Pokémon.

    This function resets the encounter and battle round state in the provided `AnkimonTracker` object.
    It then selects a valid Pokémon that can appear at the current level range, computes its stats,
    determines its moves, ability, and other combat-relevant characteristics, and returns all necessary
    data required for a battle.

    Args:
        main_pokemon_level (int): The level of the player's main Pokémon. Determines the level range of
            the generated wild Pokémon.
        ankimon_tracker_obj (AnkimonTracker): An object used to track battle state, such as the number
            of Pokémon encountered and cards used in the battle.

    Returns:
        tuple: A tuple containing the following elements:
            - name (str): Name of the wild Pokémon.
            - pokemon_id (int): Unique ID of the Pokémon.
            - wild_pokemon_lvl (int): The level of the generated Pokémon.
            - ability (str): The selected ability of the Pokémon.
            - pokemon_type (list[str]): List of type(s) the Pokémon belongs to.
            - base_stats (dict): Dictionary of the Pokémon's base stats.
            - moves (list[str]): List of up to 4 moves the Pokémon can use in battle.
            - base_experience (int): Experience points awarded for defeating the Pokémon.
            - growth_rate (str): Growth rate category of the Pokémon (e.g., "slow", "fast").
            - ev (dict): Effort values (EVs) for each stat, initialized to 0.
            - iv (dict): Randomly generated individual values (IVs) for each stat.
            - gender (str): Randomly assigned gender.
            - battle_status (str): Current status of the Pokémon in battle, defaulted to "fighting".
            - final_stats (dict): Final computed stats of the Pokémon.
            - tier (str): Tier from which the Pokémon was selected (e.g., common, rare).
            - ev_yield (dict): Effort values (EVs) awarded upon defeating the Pokémon.
            - is_shiny (bool): Indicates whether the Pokémon is shiny.

    Raises:
        ValueError: If no valid Pokémon can be generated (highly unlikely under normal conditions).
    """
    lvl_variation = 3
    lvl_range = (
        max(1, main_pokemon_level - lvl_variation),
        max(1, main_pokemon_level + lvl_variation),
    )
    wild_pokemon_lvl = random.randint(*lvl_range)
    wild_pokemon_lvl = max(
        1, wild_pokemon_lvl
    )  # Ensures that the wild pokemon's level is at least 1
    if main_pokemon_level == 100:
        wild_pokemon_lvl = 100

    # First, we draw a random, valid pokemon id.
    pokemon_id, tier = choose_random_pkmn_from_tier()
    name = search_pokedex_by_id(pokemon_id)
    min_allowed_pokemon_lvl = check_min_generate_level(
        str(name.lower())
    )  # Gets the minimum allowed level for that pokemon given its stage of evolution
    while (not check_id_ok(pokemon_id)) or (
        wild_pokemon_lvl < min_allowed_pokemon_lvl
    ):  # We keep drawing a random pokemon until we find a valid one
        pokemon_id, tier = choose_random_pkmn_from_tier()
        name = search_pokedex_by_id(pokemon_id)
        min_allowed_pokemon_lvl = check_min_generate_level(
            str(name.lower())
        )  # Gets the minimum allowed level for that pokemon given its stage of evolution

    # Now we get all necessary information about the chosen pokemon.
    pokemon_type = search_pokedex(name, "types")
    base_experience = get_base_experience(
        search_pokedex(name, "actual_id")
    )  # Experience that the wild pokemon will give once beaten
    growth_rate = get_growth_rate(pokemon_id)
    ev_yield = get_effort_values(search_pokedex(name, "actual_id"))
    gender = pick_random_gender(name)
    is_shiny = shiny_chance()
    battle_status = "fighting"
    base_stats = search_pokedex(name, "baseStats")

    all_possible_moves = get_all_pokemon_moves(name, wild_pokemon_lvl)
    if len(all_possible_moves) <= 4:
        moves = all_possible_moves
    else:
        moves = random.sample(all_possible_moves, 4)

    ability = "no_ability"  # Default value for ability
    possible_abilities = search_pokedex(name, "abilities")
    if possible_abilities:
        numeric_abilities = {k: v for k, v in possible_abilities.items() if k.isdigit()}
        if numeric_abilities:
            ability = random.choice(list(numeric_abilities.values()))

    stat_names = ["hp", "atk", "def", "spa", "spd", "spe"]
    # ev = {stat: 0 for stat in stat_names}
    ev = get_ev_spread(random.choice(["random", "pair", "defense", "uniform"]))
    # tau = 200
    # mu = 31 * (1 - math.exp(-ankimon_tracker_obj.total_reviews / tau))  # At total reviews > 3 * tau, we get mu ~= 31
    # iv = {stat: iv_rand_gauss(mu=mu, sigma=5) for stat in stat_names}  # The higher the number of reviews, the higher the IVs
    iv = {stat: random.randint(0, 31) for stat in stat_names}
    final_stats = base_stats

    ankimon_tracker_obj.pokemon_encounter = 0  # 0: Start of Battle: 1: Current Battle
    ankimon_tracker_obj.cards_battle_round = 0  # Amount of cards in this current battle

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
    )


def new_pokemon(
    pokemon: PokemonObject,
    test_window: TestWindow,
    ankimon_tracker: AnkimonTracker,
    reviewer_obj: Reviewer_Manager,
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
    ) = generate_random_pokemon(main_pokemon.level, ankimon_tracker_obj)
    pokemon_data = {
        "name": name,
        "id": pkmn_id,
        "level": level,
        "ability": ability,
        "type": pkmn_type,
        "base_stats": base_stats,
        "attacks": enemy_attacks,
        "base_experience": base_experience,
        "growth_rate": growth_rate,
        "ev": ev,
        "iv": iv,
        "gender": gender,
        "battle_status": battle_status,
        "battle_stats": battle_stats,
        "stat_stages": {
            "atk": 0,
            "def": 0,
            "spa": 0,
            "spd": 0,
            "spe": 0,
            "accuracy": 0,
            "evasion": 0,
        },
        "tier": tier,
        "ev_yield": ev_yield,
        "shiny": is_shiny,
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
    experience = int(
        find_experience_for_level(
            main_pokemon.growth_rate,
            main_pokemon.level,
            settings_obj.get("misc.remove_level_cap"),
        )
    )
    if settings_obj.get("misc.remove_level_cap") is True:
        main_pokemon.xp += exp
        level_cap = None
    elif main_pokemon.level != 100:
        main_pokemon.xp += exp
        level_cap = 100
    try:
        if mainpokemon_path.is_file():
            with open(mainpokemon_path, "r", encoding="utf-8") as json_file:
                main_pokemon_data = json.load(json_file)
        else:
            mw.logger.log(
                "warning", f"Main pokemon data file not found at {mainpokemon_path}"
            )
            showWarning(translator.translate("missing_mainpokemon_data"))
    except Exception as e:
        mw.logger.log("error", f"Error loading main pokemon data: {str(e)}")
        show_warning_with_traceback(
            parent=mw, exception=e, message="Error loading main pokemon data."
        )
        return
    while int(
        find_experience_for_level(
            main_pokemon.growth_rate,
            main_pokemon.level,
            settings_obj.get("misc.remove_level_cap"),
        )
    ) < int(main_pokemon.xp) and (level_cap is None or main_pokemon.level < level_cap):
        main_pokemon.level += 1
        msg = ""
        msg += f"Your {main_pokemon.name} is now level {main_pokemon.level} !"
        color = "#6A4DAC"  # pokemon leveling info color for tooltip
        check = check_for_badge(achievements, 5)
        if check is False:
            achievements = receive_badge(5, achievements)
        try:
            mw.logger.game_log(f"Level Up: {msg}")
            tooltipWithColour(msg, color)
        except:
            pass
        if settings_obj.get("gui.pop_up_dialog_message_on_defeat") is True:
            logger.log_and_showinfo("info", f"{msg}")
        main_pokemon.xp = int(max(0, int(main_pokemon.xp) - int(experience)))

        # Request to open the pokemon evo window
        evo_id = check_evolution_for_pokemon(
            main_pokemon.individual_id,
            main_pokemon.id,
            main_pokemon.level,
            evo_window,
            main_pokemon.everstone,
        )
        if evo_id is not None:
            logger.log_and_showinfo(
                "info",
                translator.translate(
                    "pokemon_about_to_evolve",
                    main_pokemon_name=main_pokemon.name,
                    evo_pokemon_name=return_name_for_id(evo_id).capitalize(),
                    main_pokemon_level=main_pokemon.level,
                ),
            )

        for mainpkmndata in main_pokemon_data:
            if mainpkmndata["name"] == main_pokemon.name.capitalize():
                attacks = mainpkmndata["attacks"]
                new_attacks = get_levelup_move_for_pokemon(
                    main_pokemon.name.lower(), int(main_pokemon.level)
                )
                if new_attacks:
                    msg = ""
                    msg += translator.translate(
                        "mainpokemon_can_learn_new_attack",
                        main_pokemon_name=main_pokemon.name.capitalize(),
                    )
                for new_attack in new_attacks:
                    if len(attacks) < 4 and new_attack not in attacks:
                        attacks.append(new_attack)
                        msg += translator.translate(
                            "mainpokemon_learned_new_attack",
                            new_attack_name=new_attack,
                            main_pokemon_name=main_pokemon.name.capitalize(),
                        )
                        color = "#6A4DAC"
                        tooltipWithColour(msg, color)
                        if (
                            settings_obj.get("gui.pop_up_dialog_message_on_defeat")
                            is True
                        ):
                            logger.log_and_showinfo("info", f"{msg}")
                    else:
                        dialog = AttackDialog(attacks, new_attack)
                        if dialog.exec() == QDialog.DialogCode.Accepted:
                            selected_attack = dialog.selected_attack
                            index_to_replace = None
                            for index, attack in enumerate(attacks):
                                if attack == selected_attack:
                                    index_to_replace = index
                            # If the attack is found, replace it with 'new_attack'
                            if index_to_replace is not None:
                                attacks[index_to_replace] = new_attack
                                logger.log_and_showinfo(
                                    "info",
                                    f"Replaced '{selected_attack}' with '{new_attack}'",
                                )
                            else:
                                logger.log_and_showinfo(
                                    "info", f"'{selected_attack}' not found in the list"
                                )
                        else:
                            # Handle the case where the user cancels the dialog
                            logger.log_and_showinfo(
                                "info", f"{new_attack} will be discarded."
                            )
                mainpkmndata["attacks"] = attacks
                break
    msg = ""
    msg += translator.translate(
        "mainpokemon_gained_xp",
        main_pokemon_name=main_pokemon.name,
        exp=exp,
        experience_till_next_level=experience,
        main_pokemon_xp=main_pokemon.xp,
    )
    color = "#a17cf7"  # pokemon leveling info color for tooltip
    tooltipWithColour(msg, color)
    if settings_obj.get("gui.pop_up_dialog_message_on_defeat") is True:
        logger.log_and_showinfo("info", f"{msg}")

    # Update in-place, then persist both main and mypokemon files. Base
    # stats intentionally not overwritten here — "stats" in the legacy main
    # record was level-scaled and confused downstream readers that expect
    # "stats" to mean base stats.
    for mainpkmndata in main_pokemon_data:
        mainpkmndata["xp"] = int(main_pokemon.xp)
        mainpkmndata["level"] = int(main_pokemon.level)
        ev_yield = limit_ev_yield(mainpkmndata["ev"], enemy_pokemon.ev_yield)
        mainpkmndata["ev"]["hp"] += ev_yield["hp"]
        mainpkmndata["ev"]["atk"] += ev_yield["attack"]
        mainpkmndata["ev"]["def"] += ev_yield["defense"]
        mainpkmndata["ev"]["spa"] += ev_yield["special-attack"]
        mainpkmndata["ev"]["spd"] += ev_yield["special-defense"]
        mainpkmndata["ev"]["spe"] += ev_yield["speed"]
        # Mirror EV gain onto the in-memory object so CP/stats reads
        # stay consistent with the persisted dict until next restart.
        # Dict-item mutation doesn't fire __setattr__, so invalidate
        # the CP cache explicitly.
        main_pokemon.ev["hp"] += ev_yield["hp"]
        main_pokemon.ev["atk"] += ev_yield["attack"]
        main_pokemon.ev["def"] += ev_yield["defense"]
        main_pokemon.ev["spa"] += ev_yield["special-attack"]
        main_pokemon.ev["spd"] += ev_yield["special-defense"]
        main_pokemon.ev["spe"] += ev_yield["speed"]
        main_pokemon.invalidate_cp_cache()
        mainpkmndata["current_hp"] = int(main_pokemon.hp)
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
    mainpkmndata = [mainpkmndata]
    # Refresh cached CP so Collection Dialog / PC Box readers don't
    # short-circuit to a stale pre-level-up value on the next render.
    mainpkmndata[0]["cp"] = calculate_cp_from_dict(mainpkmndata[0])
    # Save the caught Pokémon's data to a JSON file
    try:
        with open(str(mainpokemon_path), "w") as json_file:
            json.dump(mainpkmndata, json_file, indent=2)

        # Load data from the output JSON file
        with open(str(mypokemon_path), "r", encoding="utf-8") as output_file:
            mypokemondata = json.load(output_file)

            # Find and replace the specified Pokémon's data in mypokemondata
            for index, pokemon_data in enumerate(mypokemondata):
                if (
                    pokemon_data.get("individual_id") == main_pokemon.individual_id
                ):  # Match by individual_id
                    mypokemondata[index] = mainpkmndata[0]
                    break

            # Save the modified data to the output JSON file
            with open(str(mypokemon_path), "w") as output_file:
                json.dump(mypokemondata, output_file, indent=2)

        mw.logger.log("info", f"Successfully saved progress for {main_pokemon.name}")
    except Exception as e:
        mw.logger.log("error", f"Failed to save pokemon progress: {str(e)}")

    return main_pokemon.level


def kill_pokemon(
    main_pokemon: PokemonObject,
    enemy_pokemon: PokemonObject,
    evo_window: EvoWindow,
    logger: ShowInfoLogger,
    achievements: dict,
    trainer_card: Union[TrainerCard, None] = None,
):
    if trainer_card is not None:
        trainer_card.gain_xp(
            enemy_pokemon.tier, settings_obj.get("controls.allow_to_choose_moves")
        )

    # Calculate experience based on whether moves are chosen manually
    exp = calc_experience(enemy_pokemon.base_experience, enemy_pokemon.level)
    if settings_obj.get("controls.allow_to_choose_moves"):
        exp *= 0.5

    # Ensure exp is at least 1 and round up if it's a decimal
    exp = max(1, math.ceil(exp))

    # Handle XP share logic
    xp_share_individual_id = settings_obj.get("trainer.xp_share")
    if xp_share_individual_id:
        exp = xp_share_gain_exp(logger, settings_obj, evo_window, main_pokemon.id, exp, xp_share_individual_id)
    
    msg = ""

    if main_pokemon.held_item == "lucky-egg":
        exp = int(exp * 1.5)
        msg += f"{main_pokemon.name}'s Lucky Egg boosts its XP gained!\n"

    logger.log("info", msg)

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
    nickname: Union[str, None] = None,
    achievements: Union[dict, None] = None,
):
    # Create a dictionary to store the Pokémon's data
    # add all new values like hp as max_hp, evolution_data, description and growth rate
    if enemy_pokemon.tier is not None and achievements is not None:
        if enemy_pokemon.tier == "Normal":
            check = check_for_badge(achievements, 17)
            if check is False:
                achievements = receive_badge(17, achievements)
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

    # enemy_pokemon.stats["xp"] = 0
    enemy_pokemon.xp = 0
    # Use to_dict() so the caught record shares the canonical shape with
    # saved main Pokemon (includes base_stats, level-scaled stats, cp,
    # nature). Then override caught-only fields.
    _max_hp = enemy_pokemon.calculate_max_hp()
    caught_pokemon = enemy_pokemon.to_dict()
    caught_pokemon.update({
        "name": enemy_pokemon.name.capitalize(),
        "nickname": "",
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "friendship": 0,
        "pokemon_defeated": 0,
        "xp": 0,
        "everstone": False,
        "captured_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "individual_id": str(uuid.uuid4()),
        "mega": False,
        "special_form": None,
        "is_favorite": False,
        "held_item": None,
        "hp": _max_hp,
        "current_hp": _max_hp,
    })
    # Recompute CP against the overridden (zeroed) EVs.
    caught_pokemon["cp"] = calculate_cp_from_dict(caught_pokemon)

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
    logger: Union[ShowInfoLogger, None] = None,
    nickname: Union[str, None] = None,
    collected_pokemon_ids: Union[set, None] = None,
    achievements: Union[dict, None] = None,
):
    ankimon_tracker_obj.caught += 1
    if ankimon_tracker_obj.caught > 1:
        if settings_obj.get("gui.pop_up_dialog_message_on_defeat") is True:
            logger.log_and_showinfo(
                "info", translator.translate("already_caught_pokemon")
            )  # Display a message when the Pokémon is caught
            return

    # If we arrive here, this means that ankimon_tracker_obj.caught == 1
    if nickname is not None or not nickname:
        nickname = enemy_pokemon.name
    if collected_pokemon_ids is not None:
        collected_pokemon_ids.add(enemy_pokemon.id)  # Update cache
    save_caught_pokemon(enemy_pokemon, nickname, achievements)

    ankimon_tracker_obj.general_card_count_for_battle = 0

    msg = translator.translate(
        "caught_wild_pokemon", enemy_pokemon_name=enemy_pokemon.name.capitalize()
    )

    if settings_obj.get("gui.pop_up_dialog_message_on_defeat") is True:
        if logger is not None:
            logger.log_and_showinfo(
                "info", f"{msg}"
            )  # Display a message when the Pokémon is caught

    color = "#a17cf7"  # 6A4DAC" #pokemon leveling info color for tooltip
    try:
        tooltipWithColour(msg, color)
    except Exception as e:
        if logger is not None:
            show_warning_with_traceback(
                parent=mw, exception=e, message="Error while catching Pokemon:"
            )  # Display a message when the Pokémon is caught

    pokemon_pc.refresh_pokemon_grid()


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
        auto_battle_setting = int(settings_obj.get("battle.automatic_battle"))
        if not (0 <= auto_battle_setting <= 3):
            auto_battle_setting = 0  # fallback
    except ValueError:
        auto_battle_setting = 0  # fallback

    if auto_battle_setting == 3:  # Catch if uncollected
        enemy_id = enemy_pokemon.id
        # Check cache instead of file
        if enemy_id not in collected_pokemon_ids or enemy_pokemon.shiny:
            catch_pokemon(
                enemy_pokemon,
                ankimon_tracker_obj,
                logger,
                "",
                collected_pokemon_ids,
                achievements,
            )
        else:
            kill_pokemon(
                main_pokemon,
                enemy_pokemon,
                evo_window,
                logger,
                achievements,
                trainer_card,
            )
        new_pokemon(
            enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
        )  # Show a new random Pokémon
    elif auto_battle_setting == 1:  # Existing auto-catch
        catch_pokemon(
            enemy_pokemon,
            ankimon_tracker_obj,
            logger,
            "",
            collected_pokemon_ids,
            achievements,
        )
        new_pokemon(
            enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
        )  # Show a new random Pokémon
    elif auto_battle_setting == 2:  # Existing auto-defeat
        kill_pokemon(
            main_pokemon, enemy_pokemon, evo_window, logger, achievements, trainer_card
        )
        new_pokemon(
            enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
        )  # Show a new random Pokémon

    # For Manual mode (auto_battle_setting == 0): no need to show window or do actions automatically
    test_window.display_pokemon_death()
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
    msg = translator.translate(
        "pokemon_fainted", enemy_pokemon_name=main_pokemon.name.capitalize()
    )
    tooltipWithColour(msg, "#E12939")
    play_effect_sound(settings_obj, "Fainted")

    main_pokemon.hp = main_pokemon.max_hp
    main_pokemon.current_hp = main_pokemon.max_hp
    main_pokemon.reset_bonuses()

    new_pokemon(
        enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
    )  # Show a new random Pokémon
