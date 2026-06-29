from __future__ import annotations

import json
import random
import math
from typing import TYPE_CHECKING, Union
from datetime import datetime
import uuid

from ..services import services
from ..events import events
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
    safe_int,
    get_pretty_name_for_name
)
from ..functions.friendship_evolution import (
    check_friendship_evolution_for_pokemon,
)
from ..pyobj.error_handler import show_warning_with_traceback
from ..functions.trainer_functions import xp_share_gain_exp
from ..functions.badges_functions import check_for_badge, receive_badge
from ..functions.drawing_utils import tooltipWithColour
from ..utils import limit_ev_yield, play_effect_sound, get_ev_spread, is_alive
from ..business import calc_experience, calculate_cp_from_dict
from ..const import gen_ids
from . import encounter_data

if TYPE_CHECKING:
    # Type-hint-only imports (several pull in Qt). `from __future__ import
    # annotations` keeps the signatures below as strings so these never run.
    from ..pyobj.ankimon_tracker import AnkimonTracker
    from ..pyobj.pokemon_obj import PokemonObject
    from ..pyobj.reviewer_obj import Reviewer_Manager
    from ..pyobj.test_window import TestWindow
    from ..pyobj.trainer_card import TrainerCard
    from ..pyobj.InfoLogger import ShowInfoLogger
    from ..pyobj.evolution_window import EvoWindow
    from ..pyobj.translator import Translator

# Shared singletons used as bare module globals below. They are bound to the live
# registry objects by core.bind_runtime_globals() after composition.
main_pokemon = None
ankimon_tracker_obj = None
trainer_card = None
settings_obj = None
translator = None
ankimon_db = None
pokemon_pc = None

def _build_regional_lookup() -> None:
    """Populates encounter_data.REGIONAL_FORM_LOOKUP from pokedex.json."""
    import json
    try:
        from ..resources import pokedex_path
        with open(pokedex_path, "r", encoding="utf-8") as f:
            pokedex = json.load(f)
        aid_to_sid: dict[int, int] = {
            v["actual_id"]: v["species_id"]
            for v in pokedex.values()
            if "actual_id" in v and "species_id" in v
        }
        for region, aids in encounter_data.REGIONAL_FORMS.items():
            for aid in aids:
                sid = aid_to_sid.get(aid)
                if sid is None:
                    continue
                region_map = encounter_data.REGIONAL_FORM_LOOKUP.setdefault(sid, {})
                region_map.setdefault(region, []).append(aid)
    except Exception as e:
        print(f"[Ankimon] Warning: Could not build regional form lookup: {e}")

_build_regional_lookup()

ALL_NATURES = [
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty",
    "Bold", "Docile", "Relaxed", "Impish", "Lax",
    "Timid", "Hasty", "Serious", "Jolly", "Naive",
    "Modest", "Mild", "Quiet", "Bashful", "Rash",
    "Calm", "Gentle", "Sassy", "Careful", "Quirky"
]

# === PERFORMANCE FIX: Cache percentage calculations ===

_percentages_cache = {
    'percentages': None,
    'total_reviews': None,
    'trainer_level': None,
    'main_pokemon_level': None,
}

def modify_percentages(total_reviews, daily_average, trainer_level):
    """
    Modify Pokémon encounter percentages based on total reviews, trainer level, and main Pokémon level.
    CACHED: Only recalculates when inputs change.
    """
    # Performance Guard: Skip recalculation if inputs haven't changed
    if (_percentages_cache['percentages'] is not None and
        _percentages_cache['total_reviews'] == total_reviews and
        _percentages_cache['trainer_level'] == trainer_level and
        _percentages_cache['main_pokemon_level'] == main_pokemon.level):
        return _percentages_cache['percentages']


    # Start with the base percentages
    percentages = {
        "Baby": 2,
        "Legendary": 0.5,
        "Mythical": 0.2,
        "Normal": 88.6,
        "Starter": 2.5,
        "Ultra": 5,
        "Mega": 0.7,
        "Gmax": 0.5,
    }
    # Adjust percentages based on total reviews relative to the daily average
    review_ratio = total_reviews / daily_average if daily_average > 0 else 0
    # Adjust for review progress
    if review_ratio < 0.4:
        percentages["Normal"] += (
            percentages.pop("Baby", 0)
            + percentages.pop("Legendary", 0)
            + percentages.pop("Mythical", 0)
            + percentages.pop("Ultra", 0)
            + percentages.pop("Mega", 0)
            + percentages.pop("Gmax", 0)
        )
    elif review_ratio < 0.6:
        percentages["Baby"] += 2
        percentages["Normal"] -= 2
    elif review_ratio < 0.8:
        percentages["Ultra"] += 3
        percentages["Normal"] -= 3
    else:
        percentages["Legendary"] += 2
        percentages["Ultra"] += 3
        percentages["Mega"] += 2
        percentages["Gmax"] += 1.5
        percentages["Normal"] -= 8.5
    # Restrict access to certain tiers based on main Pokémon level
    if main_pokemon.level:
        # Define level thresholds for each tier
        level_thresholds = {
            "Starter": 30,
            "Ultra": 30,
            "Legendary": 50,
            "Mega": 60,
            "Gmax": 65,
            "Mythical": 75,
        }
        
        # Example modification based on trainer level
        if trainer_level:
            adjustment = 5
            if trainer_level > 10:
                for tier in percentages:
                    if tier == "Normal":
                        percentages[tier] = max(percentages[tier] - adjustment, 0)
                    else:
                        percentages[tier] = percentages.get(tier, 0) + adjustment

        for tier in ["Starter", "Ultra", "Legendary", "Mythical", "Mega", "Gmax"]:
                if main_pokemon.level < level_thresholds.get(tier, float("inf")):
                    percentages[tier] = 0

        # Normalize percentages to ensure they sum to 100
        total = sum(percentages.values())
        for tier in percentages:
            percentages[tier] = (percentages[tier] / total) * 100 if total > 0 else 0

    # Cache and return
    _percentages_cache['percentages'] = percentages
    _percentages_cache['total_reviews'] = total_reviews
    _percentages_cache['trainer_level'] = trainer_level
    _percentages_cache['main_pokemon_level'] = main_pokemon.level
    

    return percentages

def clear_encounter_cache():
    """Clear cache when needed"""
    global _percentages_cache
    _percentages_cache = {
        'percentages': None,
        'total_reviews': None,
        'trainer_level': None,
        'main_pokemon_level': None,
    }

def get_random_pokemon_in_tier(tier):
    

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
    elif tier == "Mega":
        id_data = encounter_data.MEGA
    elif tier == "Gmax":
        id_data = encounter_data.GMAX
    elif tier == "Starter":
        id_data = encounter_data.STARTERS
    else:
        return 1

    return random.choice(id_data) if id_data else 1

def _player_owns_base_form(actual_id: int, collected_ids: set) -> bool:
    """Return True if the player owns the base species of this Mega/Gmax form."""
    name = search_pokedex_by_id(actual_id)
    if not name or name == "Pokémon not found":
        return True  # can't determine — allow through
    species_id = safe_int(search_pokedex(name, "species_id"))
    if not species_id:
        return True
    return species_id in collected_ids


def _meets_prerequisites(pokemon_id: int, collected_ids: set) -> bool:
    """Return True if all prerequisite Pokémon for this ID are collected.

    Prerequisite chains are defined in encounter_data.PREREQUISITES.
    Handles forms by checking the species_id prerequisites.
    """
    
    check_id = pokemon_id
    if pokemon_id >= 10000:
        name = search_pokedex_by_id(pokemon_id)
        species_id = safe_int(search_pokedex(name, "species_id"))
        if species_id:
            check_id = species_id

    required = encounter_data.PREREQUISITES.get(check_id)
    if not required:
        return True

    if isinstance(required, tuple) and len(required) == 2 and required[0] == "OR":
        # Any of these must be present
        return any(rid in collected_ids for rid in required[1])

    # All must be present (default behavior for sets)
    return required.issubset(collected_ids)


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
    daily_average_val = settings_obj.get("battle.daily_average")
    daily_average = int(daily_average_val) if daily_average_val is not None else 100
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
        services.logger.game_log(f"Selected tier: {tier}, Resulting Pokemon ID: {id}")
        return id, tier
    except Exception as e:
        services.logger.log("error", f"Error in choose_random_pkmn_from_tier: {str(e)}")
        show_warning_with_traceback(exception=e, message="Error occurred")


def check_min_generate_level(name):
    

    evoType = search_pokedex(name.lower(), "evoType")
    evoLevel = search_pokedex(name.lower(), "evoLevel")
    if evoLevel:
        min_level = safe_int(evoLevel)
    elif evoType != []:
        min_level = 100
    else:
        min_level = 1

    # Ensure special forms (Mega/Gmax) and Legendaries inherit correct level caps.
    # We check both species_id and actual_id against the rarity lists.
    species_id = safe_int(search_pokedex(name.lower(), "species_id"))
    actual_id = safe_int(search_pokedex(name.lower(), "actual_id"))
    
    is_mythical = (species_id in encounter_data.MYTHICAL) or (actual_id in encounter_data.MYTHICAL)
    is_legendary = (species_id in encounter_data.LEGENDARY) or (actual_id in encounter_data.LEGENDARY)
    is_ultra = (species_id in encounter_data.ULTRA) or (actual_id in encounter_data.ULTRA)
    is_starter = (species_id in encounter_data.STARTERS) or (actual_id in encounter_data.STARTERS)
    is_mega = (species_id in encounter_data.MEGA) or (actual_id in encounter_data.MEGA)
    is_gmax = (species_id in encounter_data.GMAX) or (actual_id in encounter_data.GMAX)

    if is_mythical:
        min_level = max(min_level, 75)
    elif is_gmax:
        min_level = max(min_level, 65)
    elif is_mega:
        min_level = max(min_level, 60)
    elif is_legendary:
        min_level = max(min_level, 50)
    elif is_ultra:
        min_level = max(min_level, 30)
    elif is_starter:
        min_level = max(min_level, 30)

    return min_level


def check_id_ok(id_num: Union[int, list[int]]):
    if isinstance(id_num, list):
        if len(id_num) > 0:
            id_num = id_num[0]
        else:
            return False

    if not isinstance(id_num, int):
        return False

    # Mega/Gmax forms have actual_ids >= 10000, which fall outside
    # the normal gen ranges. Resolve to base species for generation check.
    if id_num >= 10000:
        name = search_pokedex_by_id(id_num)
        if not name or name == "Pokémon not found":
            return True  # fallback

        species_id = safe_int(search_pokedex(name, "species_id"))
        gen_config = [settings_obj.get(f"misc.gen{i}") for i in range(1, 10)]

        # Check base species generation
        base_gen = 0
        for gen, max_id in gen_ids.items():
            if species_id <= max_id:
                base_gen = int(gen.split("_")[1])
                break
        if base_gen == 0:
            return True  # fallback
        if not gen_config[base_gen - 1]:
            return False  # base gen disabled

        # For regional forms, also require the form's intro gen to be enabled
        
        if id_num in encounter_data.REGIONAL_FORM_REGION:
            forme = search_pokedex(name, "forme") or ""
            intro_gen = None
            for f_name, g in encounter_data.REGIONAL_FORME_GEN.items():
                if f_name in forme:
                    intro_gen = g
                    break
            
            if intro_gen and not gen_config[intro_gen - 1]:
                return False  # regional form's intro gen disabled

        return True

    generation = 0
    for gen, max_id in gen_ids.items():
        if id_num <= max_id:
            generation = int(gen.split("_")[1])

            gen_config = [settings_obj.get(f"misc.gen{i}") for i in range(1, 10)]
            return gen_config[generation - 1]

    return False


def get_regional_substitute(species_id: int, region: str = None) -> "int | None":
    """
    Returns a regional form actual_id for the given species and region, or None.
    If region is None, returns any valid regional variant from any region.
    """
    
    
    eligible = []
    lookup = encounter_data.REGIONAL_FORM_LOOKUP.get(species_id, {})
    
    if region:
        options = lookup.get(region, [])
        for v in options:
            if check_id_ok(v):
                eligible.append(v)
    else:
        for reg_variants in lookup.values():
            for v in reg_variants:
                if check_id_ok(v):
                    eligible.append(v)
                    
    if eligible:
        return random.choice(eligible)
    return None

def get_boosted_gens_for_region(region: str) -> list[int]:
    mapping = {
        "kanto": [1], "johto": [2], "hoenn": [3], "sinnoh": [4],
        "unova": [5], "kalos": [6], "alola": [7], "galar": [8],
        "paldea": [9], "hisui": [4, 8]
    }
    return mapping.get(region, [])

def get_boosted_pool_chance(region: str) -> float:
    return 0.40 if region == "hisui" else 0.30

def get_base_species_gen(actual_id: int) -> int:
    species_id = actual_id
    if actual_id >= 10000:
        name = search_pokedex_by_id(actual_id)
        if name and name != "Pokémon not found":
            species_id = safe_int(search_pokedex(name, "species_id")) or actual_id

    for gen, max_id in gen_ids.items():
        if species_id <= max_id:
            return int(gen.split("_")[1])
    return 0

def get_all_pokemon_in_tier(tier: str) -> list[int]:
    if tier == "Normal": return encounter_data.NORMAL
    if tier == "Baby": return encounter_data.BABY
    if tier == "Ultra": return encounter_data.ULTRA
    if tier == "Legendary": return encounter_data.LEGENDARY
    if tier == "Mythical": return encounter_data.MYTHICAL
    if tier == "Mega": return encounter_data.MEGA
    if tier == "Gmax": return encounter_data.GMAX
    if tier == "Starter": return encounter_data.STARTERS
    return []


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
    if main_pokemon.level == 100:
        wild_pokemon_lvl = 100

    from ..utils import load_collected_pokemon_ids
    collected_ids = load_collected_pokemon_ids()

    # FALLBACK HIERARCHY
    # If a rolled tier fails, try the next one in the list.
    TIER_ORDER = ["Mythical", "Mega", "Legendary", "Gmax", "Ultra", "Starter", "Normal", "Baby"]
    
    selected_pokemon_id = None
    selected_tier = None
    
    # 1. Select the initial tier based on probabilities
    initial_tier = get_tier(ankimon_tracker_obj.get_total_reviews(), trainer_card.level)
    
    # Find starting point in fallback order
    try:
        start_idx = TIER_ORDER.index(initial_tier)
    except ValueError:
        start_idx = TIER_ORDER.index("Normal")
        
    # Iterate through tiers starting from the rolled one
    for i in range(start_idx, len(TIER_ORDER)):
        current_tier = TIER_ORDER[i]
        
        tier_ids = get_all_pokemon_in_tier(current_tier)
        full_pool = []
        for pokemon_id in tier_ids:
            name = search_pokedex_by_id(pokemon_id)
            if not name or name == "Pokémon not found":
                continue
                
            # Guard 1: Generation check
            if not check_id_ok(pokemon_id):
                continue

            # Guard 2: Level check
            min_allowed_pokemon_lvl = check_min_generate_level(str(name.lower()))
            if wild_pokemon_lvl < min_allowed_pokemon_lvl:
                continue

            # Guard 3: Mega/Gmax base ownership
            if current_tier in ("Mega", "Gmax") and not _player_owns_base_form(pokemon_id, collected_ids):
                continue

            # Guard 4: Prerequisite check
            if not _meets_prerequisites(pokemon_id, collected_ids):
                continue
                
            full_pool.append(pokemon_id)
            
        if not full_pool:
            continue

        active_region = settings_obj.get("misc.active_region")
        boosted_pool = []

        if active_region:
            boosted_gens = get_boosted_gens_for_region(active_region)
            
            for pid in full_pool:
                if get_base_species_gen(pid) in boosted_gens:
                    if pid not in boosted_pool:
                        boosted_pool.append(pid)
                
                # Add eligible regional variants for the active region
                options = encounter_data.REGIONAL_FORM_LOOKUP.get(pid, {}).get(active_region, [])
                for opt in options:
                    if check_id_ok(opt) and opt not in boosted_pool:
                        boosted_pool.append(opt)

        if active_region and boosted_pool:
            chance = get_boosted_pool_chance(active_region)
            if random.random() < chance:
                selected_pokemon_id = random.choice(boosted_pool)
            else:
                selected_pokemon_id = random.choice(full_pool)
        else:
            selected_pokemon_id = random.choice(full_pool)

        selected_tier = current_tier
        break
            
    # Final fallback if somehow everything failed (e.g. settings restrict all IDs)
    if not selected_pokemon_id:
        selected_pokemon_id = 19 # Rattata
        selected_tier = "Normal"

    # --- Regional form resolution ---
    # Apply 7%-per-variant resolution for base species.
    if selected_pokemon_id < 10000:
        region_forms = encounter_data.REGIONAL_FORM_LOOKUP.get(selected_pokemon_id, {})
        num_eligible = 0
        for variants in region_forms.values():
            for v in variants:
                if check_id_ok(v):
                    num_eligible += 1
        
        if num_eligible > 0:
            if random.random() < 0.07 * num_eligible:
                sub = get_regional_substitute(selected_pokemon_id)
                if sub:
                    selected_pokemon_id = sub
    # --- End form resolution ---

    pokemon_id = selected_pokemon_id
    tier = selected_tier
    name = search_pokedex_by_id(pokemon_id)


    # Now we get all necessary information about the chosen pokemon.
    pokemon_type = search_pokedex(name, "types")
    base_experience = get_base_experience(
        search_pokedex(name, "actual_id")
    )  # Experience that the wild pokemon will give once beaten
    growth_rate = get_growth_rate(search_pokedex(name, "species_id") or pokemon_id)
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
    nature = random.choice(ALL_NATURES)
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
        nature,
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
    ankimon_tracker.faint_processed = False
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
        nature,
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
        "nature": nature,
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
        try:
            test_window.display_first_encounter()
        except RuntimeError:
            pass

    # Observable: a fresh wild Pokemon appeared.
    events.emit(
        "encounter",
        pokemon=pokemon.name,
        id=pokemon.id,
        level=pokemon.level,
        tier=pokemon.tier,
        shiny=pokemon.shiny,
        hp=pokemon.hp,
        max_hp=pokemon.max_hp,
    )

    # Re-render the reviewer HUD. refresh_hud() grabs the live webview under
    # Anki and is a recorded no-op headless.
    if reviewer_obj is not None:
        reviewer_obj.refresh_hud()

    # Track as seen in Pokedex
    if services.db is not None:
        if hasattr(services.db, 'mark_as_seen'):
            services.db.mark_as_seen(pkmn_id)
        else:
            # Fallback tracking if not restarted
            try:
                seen_ids = services.db.get_user_data("pokedex_seen", [])
                if not isinstance(seen_ids, list): seen_ids = []
                if pkmn_id not in seen_ids:
                    seen_ids.append(pkmn_id)
                    services.db.set_user_data("pokedex_seen", seen_ids)
            except: pass

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
    else:
        # Cap is on AND the main is already level 100: no XP is granted, but
        # level_cap must still be defined or the while-loop condition below
        # raises NameError and crashes every post-cap defeat (upstream #402).
        level_cap = 100
    try:
        db = services.db
        main_pokemon_data = db.get_main_pokemon()
        if not main_pokemon_data:
            services.ui.warn(translator.translate("missing_mainpokemon_data"))
    except Exception as e:
        services.logger.log("error", f"Error loading main pokemon data: {str(e)}")
        show_warning_with_traceback(
            exception=e, message="Error loading main pokemon data."
        )
        return
    evolution_prompted = False
    while int(
        find_experience_for_level(
            main_pokemon.growth_rate,
            main_pokemon.level,
            settings_obj.get("misc.remove_level_cap"),
        )
    ) < int(main_pokemon.xp) and (level_cap is None or main_pokemon.level < level_cap):
        main_pokemon.level += 1
        events.emit("levelup", pokemon=main_pokemon.name, level=main_pokemon.level)
        msg = ""
        msg += f"Your {main_pokemon.name} is now level {main_pokemon.level} !"
        color = "#6A4DAC"  # pokemon leveling info color for tooltip
        check = check_for_badge(achievements, 5)
        if check is False:
            achievements = receive_badge(5, achievements)
        try:
            services.logger.game_log(f"Level Up: {msg}")
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
            getattr(main_pokemon, "evolution_rejected", False),
        )
        if evo_id is not None:
            evolution_prompted = True
            events.emit("evolution_offered", pokemon=main_pokemon.name, trigger="level", evo_id=evo_id)
            # None-safe: return_name_for_id can return None for an unknown id, so
            # fall back to the numeric id instead of crashing on .capitalize()
            # (mirrors the friendship-evolution path below).
            evo_display_name = return_name_for_id(evo_id)
            evo_display_name = (
                evo_display_name.capitalize() if evo_display_name else str(evo_id)
            )
            logger.log_and_showinfo(
                "info",
                translator.translate(
                    "pokemon_about_to_evolve",
                    main_pokemon_name=main_pokemon.name,
                    evo_pokemon_name=evo_display_name,
                    main_pokemon_level=main_pokemon.level,
                ),
            )

        if main_pokemon_data:
            mainpkmndata = main_pokemon_data
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
                        # Ask via the UI port (real dialog under Anki; scripted
                        # choice headless). None => discard the new move.
                        selected_attack = services.ui.choose_attack_to_replace(
                            attacks, new_attack
                        )
                        if selected_attack is not None:
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
                            # User declined to replace a move; discard the new one.
                            logger.log_and_showinfo(
                                "info", f"{new_attack} will be discarded."
                            )
                mainpkmndata["attacks"] = attacks
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

    # Load existing Pokémon data if it exists
    if main_pokemon_data:
        mainpkmndata = main_pokemon_data
        mainpkmndata["stats"] = main_pokemon.stats
        mainpkmndata["xp"] = int(main_pokemon.xp)
        mainpkmndata["level"] = int(main_pokemon.level)
        # Clone raw EV yield to avoid mutating the in-memory enemy template
        raw_ev_yield = enemy_pokemon.ev_yield.copy()
        
        # Normalize keys to the standard long form expected by limit_ev_yield
        normalized_yield = {
            "hp": raw_ev_yield.get("hp", 0),
            "attack": raw_ev_yield.get("attack", 0) + raw_ev_yield.get("atk", 0),
            "defense": raw_ev_yield.get("defense", 0) + raw_ev_yield.get("def", 0),
            "special-attack": raw_ev_yield.get("special-attack", 0) + raw_ev_yield.get("spa", 0),
            "special-defense": raw_ev_yield.get("special-defense", 0) + raw_ev_yield.get("spd", 0),
            "speed": raw_ev_yield.get("speed", 0) + raw_ev_yield.get("spe", 0),
        }

        held_item = mainpkmndata.get("held_item", main_pokemon.held_item)

        # Apply EV-boosting held items
        if held_item == "macho-brace":
            for stat in normalized_yield:
                normalized_yield[stat] *= 2
        else:
            power_item_mapping = {
                "power-weight": "hp",
                "power-bracer": "attack",
                "power-belt": "defense",
                "power-lens": "special-attack",
                "power-band": "special-defense",
                "power-anklet": "speed",
            }
            if held_item in power_item_mapping:
                stat_to_boost = power_item_mapping[held_item]
                normalized_yield[stat_to_boost] += 8

        ev_yield = limit_ev_yield(mainpkmndata["ev"], normalized_yield)
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
        # Friendship is uncapped — it keeps climbing past MAX_FRIENDSHIP (400) so
        # players can flex a super-bonded Pokémon. The progress bar still fills at
        # MAX_FRIENDSHIP; the raw number above it is what keeps growing.
        main_pokemon.friendship += random.randint(5, 9)
        mainpkmndata["friendship"] = main_pokemon.friendship
        if not evolution_prompted:
            friendship_evo_id = check_friendship_evolution_for_pokemon(
                main_pokemon.individual_id,
                main_pokemon.id,
                evo_window,
                main_pokemon.everstone,
                main_pokemon.friendship,
                getattr(main_pokemon, "evolution_rejected", False),
            )
            if friendship_evo_id is not None:
                evolution_prompted = True
                events.emit("evolution_offered", pokemon=main_pokemon.name, trigger="friendship", evo_id=friendship_evo_id)
                # return_name_for_id can return None (and pop a spurious warning)
                # if the evolved id is missing from the name CSV; guard the
                # .capitalize() so a data gap can't crash the defeat flow.
                friendship_evo_name = return_name_for_id(friendship_evo_id)
                friendship_evo_name = friendship_evo_name.capitalize() if friendship_evo_name else str(friendship_evo_id)
                logger.log_and_showinfo(
                    "info",
                    translator.translate(
                        "pokemon_about_to_evolve_friendship",
                        main_pokemon_name=main_pokemon.name,
                        evo_pokemon_name=friendship_evo_name,
                    ),
                )
        main_pokemon.pokemon_defeated += 1
        mainpkmndata["pokemon_defeated"] = main_pokemon.pokemon_defeated
        if hasattr(main_pokemon, "tier"):
            mainpkmndata["tier"] = main_pokemon.tier
        if hasattr(main_pokemon, "is_favorite"):
            mainpkmndata["is_favorite"] = main_pokemon.is_favorite

        # Save to database (replaces JSON file I/O for performance)
        services.db.save_main_pokemon(mainpkmndata)
        services.db.save_pokemon(mainpkmndata)  # Also update the captured pokemon collection

    return main_pokemon.level

# --- Utility: Sync mainpokemon to mypokemon ---
def sync_mainpokemon_to_mypokemon(main_pokemon):
    """
    Update the relevant entry in mypokemon database with the latest values from mainpokemon.
    Uses database instead of JSON files.
    """
    db = services.db

    # Get main pokemon from database
    main_entry = db.get_main_pokemon()
    if not main_entry:
        return
    
    main_id = main_entry.get("individual_id", None)
    if not main_id:
        main_id = getattr(main_pokemon, "individual_id", None)
    if not main_id:
        return
    
    # Save/update in captured_pokemon table
    db.save_pokemon(main_entry)
    return

def kill_pokemon(
    main_pokemon: PokemonObject,
    enemy_pokemon: PokemonObject,
    evo_window: EvoWindow,
    logger: ShowInfoLogger,
    achievements: dict,
    trainer_card: Union[TrainerCard, None] = None,
):
    events.emit(
        "defeat",
        pokemon=enemy_pokemon.name,
        id=enemy_pokemon.id,
        level=enemy_pokemon.level,
        tier=enemy_pokemon.tier,
    )
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
        "name": enemy_pokemon.name,
        "nickname": nickname or "",
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

    # Save to database (replaces JSON file I/O for performance)
    services.db.save_pokemon(caught_pokemon)


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
    if not nickname:
        nickname = get_pretty_name_for_name(enemy_pokemon.name)
    if collected_pokemon_ids is not None:
        collected_pokemon_ids.add(enemy_pokemon.id)  # Update cache
    save_caught_pokemon(enemy_pokemon, nickname, achievements)
    events.emit(
        "catch",
        pokemon=enemy_pokemon.name,
        id=enemy_pokemon.id,
        shiny=enemy_pokemon.shiny,
        tier=enemy_pokemon.tier,
        nickname=nickname,
    )

    ankimon_tracker_obj.general_card_count_for_battle = 0

    msg = translator.translate(
        "caught_wild_pokemon", enemy_pokemon_name=get_pretty_name_for_name(enemy_pokemon.name)
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
                exception=e, message="Error while catching Pokemon:"
            )  # Display a message when the Pokémon is caught

        if is_alive(pokemon_pc):
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
    if ankimon_tracker_obj.faint_processed:
        return
    events.emit("faint", who="enemy", pokemon=enemy_pokemon.name, id=enemy_pokemon.id)
    try:
        auto_battle_setting = int(settings_obj.get("battle.automatic_battle"))
        if not (0 <= auto_battle_setting <= 3):
            auto_battle_setting = 0  # fallback
    except ValueError:
        auto_battle_setting = 0  # fallback

    name_lower = enemy_pokemon.name.lower()
    forme = search_pokedex(name_lower, "forme")
    
    is_mega = (enemy_pokemon.id in encounter_data.MEGA)
    is_gmax = (enemy_pokemon.id in encounter_data.GMAX)
    
    is_special = (
        enemy_pokemon.tier in ["Ultra", "Legendary", "Mythical", "Starter"] or
        is_mega or
        is_gmax 
    )
    
    should_catch_always = settings_obj.get("battle.automatic_catch_special", True) and is_special

    if auto_battle_setting == 3:  # Catch if uncollected
        enemy_id = enemy_pokemon.id
        
        if enemy_id not in collected_pokemon_ids or enemy_pokemon.shiny or should_catch_always:
            ankimon_tracker_obj.faint_processed = True
            catch_pokemon(
                enemy_pokemon,
                ankimon_tracker_obj,
                logger,
                "",
                collected_pokemon_ids,
                achievements,
            )
        else:
            ankimon_tracker_obj.faint_processed = True
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
        ankimon_tracker_obj.faint_processed = True
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
        if enemy_pokemon.shiny or should_catch_always:
            ankimon_tracker_obj.faint_processed = True
            catch_pokemon(
                enemy_pokemon,
                ankimon_tracker_obj,
                logger,
                "",
                collected_pokemon_ids,
                achievements,
            )
        else:
            ankimon_tracker_obj.faint_processed = True
            kill_pokemon(
                main_pokemon, enemy_pokemon, evo_window, logger, achievements, trainer_card
            )
        new_pokemon(
            enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
        )  # Show a new random Pokémon
    else:
        # For Manual mode (auto_battle_setting == 0): show the death/catch screen
        if test_window is not None:
            try:
                test_window.display_pokemon_death()
            except RuntimeError:
                pass

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
    events.emit("faint", who="main", pokemon=main_pokemon.name)
    play_effect_sound(settings_obj, "Fainted")

    main_pokemon.hp = main_pokemon.max_hp
    main_pokemon.current_hp = main_pokemon.max_hp
    main_pokemon.reset_bonuses()

    new_pokemon(
        enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
    )  # Show a new random Pokémon
