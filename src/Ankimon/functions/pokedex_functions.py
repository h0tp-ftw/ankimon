from typing import Literal
from ..resources import (
    pokedex_path,
    pokedesc_lang_path,
    pokenames_lang_path,
    learnset_path,
    moves_file_path,
    poke_evo_path,
    poke_species_path,
    csv_file_items_cost,
    stats_csv,
    pokemon_csv,
)
from aqt.utils import showWarning
from aqt import mw
import json
import random
import csv
from ..pyobj.error_handler import show_warning_with_traceback

GROWTH_RATES = {
    1: "slow",
    2: "medium",
    3: "fast",
    4: "medium-slow",
    5: "slow-then-very-fast",
    6: "fast-then-very-slow"
}

STATS = {
    1: "hp",
    2: "attack",
    3: "defense",
    4: "special-attack",
    5: "special-defense",
    6: "speed",
}

# === PERFORMANCE FIX: Cache pokedex data ===
_pokedex_cache = None
_poke_species_cache = None

def _load_pokedex_cache():
    """Load pokedex JSON once and cache it in memory"""
    global _pokedex_cache
    if _pokedex_cache is None:
        try:
            with open(str(pokedex_path), "r", encoding="utf-8") as json_file:
                _pokedex_cache = json.load(json_file)
        except Exception as e:
            print(f"Error loading pokedex cache: {e}")
            _pokedex_cache = {}
    return _pokedex_cache

# === ID INDEX CACHE: Fast O(1) lookups by species_id ===
_pokedex_id_index = None

def _load_pokedex_id_index():
    """Build a reverse index: species_id -> pokemon_name for O(1) lookups"""
    global _pokedex_id_index
    if _pokedex_id_index is None:
        try:
            pokedex_data = _load_pokedex_cache()
            _pokedex_id_index = {}
            for entry_name, attributes in pokedex_data.items():
                species_id = attributes.get("species_id")
                if species_id is not None:
                    _pokedex_id_index[species_id] = entry_name
        except Exception as e:
            print(f"Error building pokedex ID index: {e}")
            _pokedex_id_index = {}
    return _pokedex_id_index

def _load_poke_species_cache():
    """Load poke_species CSV once and cache it in memory"""
    global _poke_species_cache
    if _poke_species_cache is None:
        try:
            _poke_species_cache = []
            with open(poke_species_path, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                _poke_species_cache = list(reader)
        except Exception as e:
            print(f"Error loading poke_species cache: {e}")
            _poke_species_cache = []
    return _poke_species_cache

# === ADDITIONAL CACHES ===
_pokemon_csv_cache = None
_stats_csv_cache = None
_poke_evo_cache = None
_moves_cache = None

def _load_pokemon_csv_cache():
    """Cache pokemon.csv to avoid repeated file I/O"""
    global _pokemon_csv_cache
    if _pokemon_csv_cache is None:
        try:
            _pokemon_csv_cache = []
            with open(pokemon_csv, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                _pokemon_csv_cache = list(reader)
        except Exception as e:
            print(f"Error loading pokemon CSV cache: {e}")
            _pokemon_csv_cache = []
    return _pokemon_csv_cache

def _load_stats_csv_cache():
    """Cache stats.csv to avoid repeated file I/O"""
    global _stats_csv_cache
    if _stats_csv_cache is None:
        try:
            _stats_csv_cache = []
            with open(stats_csv, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                _stats_csv_cache = list(reader)
        except Exception as e:
            print(f"Error loading stats CSV cache: {e}")
            _stats_csv_cache = []
    return _stats_csv_cache

def _load_poke_evo_cache():
    """Cache pokemon evolution data to avoid repeated file I/O"""
    global _poke_evo_cache
    if _poke_evo_cache is None:
        try:
            _poke_evo_cache = []
            with open(poke_evo_path, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                _poke_evo_cache = list(reader)
        except Exception as e:
            print(f"Error loading poke evo cache: {e}")
            _poke_evo_cache = []
    return _poke_evo_cache

def _load_moves_cache():
    """Cache moves.json to avoid repeated file I/O"""
    global _moves_cache
    if _moves_cache is None:
        try:
            with open(moves_file_path, "r", encoding="utf-8") as json_file:
                _moves_cache = json.load(json_file)
        except Exception as e:
            print(f"Error loading moves cache: {e}")
            _moves_cache = {}
    return _moves_cache

# === POKEMON NAME & DESCRIPTION CACHES ===
_pokemon_names_cache = {}  # {(pokemon_id, language): name}
_pokemon_descriptions_cache = {}  # {(species_id, language): description}

def _load_pokemon_names_csv():
    """Load all pokemon names into cache on first access"""
    global _pokemon_names_cache
    if not _pokemon_names_cache:
        try:
            with open(pokenames_lang_path, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    species_id = int(row["pokemon_species_id"])
                    lang_id = int(row["local_language_id"])
                    name = row["name"]
                    _pokemon_names_cache[(species_id, lang_id)] = name
        except Exception as e:
            print(f"Error loading pokemon names cache: {e}")
    return _pokemon_names_cache

def _load_pokemon_descriptions_csv():
    """Load all pokemon descriptions into cache on first access"""
    global _pokemon_descriptions_cache
    if not _pokemon_descriptions_cache:
        try:
            with open(pokedesc_lang_path, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    species_id = int(row["species_id"])
                    lang_id = int(row["language_id"])
                    flavor_text = row["flavor_text"].replace("\x0c", " ")
                    
                    # Store all descriptions for this (species_id, lang_id) pair
                    key = (species_id, lang_id)
                    if key not in _pokemon_descriptions_cache:
                        _pokemon_descriptions_cache[key] = []
                    _pokemon_descriptions_cache[key].append(flavor_text)
        except Exception as e:
            print(f"Error loading pokemon descriptions cache: {e}")
    return _pokemon_descriptions_cache

def clear_pokedex_caches():
    """Call this when pokedex data is updated or session ends"""
    global _pokedex_cache, _poke_species_cache, _pokemon_csv_cache, _stats_csv_cache, _poke_evo_cache, _moves_cache, _pokedex_id_index, _pokemon_names_cache, _pokemon_descriptions_cache
    _pokedex_cache = None
    _poke_species_cache = None
    _pokemon_csv_cache = None
    _stats_csv_cache = None
    _poke_evo_cache = None
    _moves_cache = None
    _pokedex_id_index = None
    _pokemon_names_cache = {}
    _pokemon_descriptions_cache = {}

def _normalize_language_id(language):
    """Map unsupported language IDs to a fallback that exists in data files."""
    try:
        lang = int(language)
    except Exception:
        return 9  # default to English on any parsing issue
    if lang == 14:  # Spanish (LatAm) falls back to Spanish data
        return 7
    return lang


def special_pokemon_names_for_min_level(name):
    if name == "flabébé":
        return "flabebe"
    elif name == "sirfetch'd":
        return "sirfetchd"
    elif name == "farfetch'd":
        return "farfetchd"
    elif name == "porygon-z":
        return "porygonz"
    elif name == "kommo-o":
        return "kommoo"
    elif name == "hakamo-o":
        return "hakamoo"
    elif name == "jangmo-o":
        return "jangmoo"
    elif name == "mr. rime":
        return "mrrime"
    elif name == "mr. mime":
        return "mrmime"
    elif name == "mime jr.":
        return "mimejr"
    elif name == "nidoran♂":
        return "nidoranm"
    elif name == "nidoran":
        return "nidoranf"
    elif name == "keldeo[e]":
        return "keldeo"
    elif name == "mew[e]":
        return "mew"
    elif name == "deoxys[e]":
        return "deoxys"
    elif name == "jirachi[e]":
        return "jirachi"
    elif name == "arceus[e]":
        return "arceus"
    elif name == "shaymin[e]":
        return "shaymin-land"
    elif name == "darkrai [e]":
        return "darkrai"
    elif name == "manaphy[e]":
        return "manaphy"
    elif name == "phione[e]":
        return "phione"
    elif name == "celebi[e]":
        return "celebi"
    elif name == "magearna[e]":
        return "magearna"
    elif name == "type: null" or name == "type-null":
        return "typenull"
    elif name == "ho-oh":
        return "hooh"
    elif name == "tapu-koko":
        return "tapukoko"
    elif name == "tapu-lele":
        return "tapulele"
    elif name == "tapu-bulu":
        return "tapubulu"
    elif name == "tapu-fini":
        return "tapufini"
    elif name == "ting-lu":
        return "tinglu"
    elif name == "chien-pao":
        return "chienpao"
    elif name == "wo-chien":
        return "wochien"
    elif name == "chi-yu":
        return "chiyu"
    else:
        return name


def search_pokedex(pokemon_name, variable):
    try:
        pokemon_name = special_pokemon_names_for_min_level(pokemon_name)
        pokedex_data = _load_pokedex_cache()  # Use cache instead of file I/O

        # Create a copy of the name to modify
        current_name = pokemon_name

        while True:
            # 1. Try to find a match with the current name
            if current_name in pokedex_data:
                pokemon_info = pokedex_data[current_name]
                var = pokemon_info.get(variable)
                if var is not None:
                    return var

            # 2. If no match, find the last hyphen
            last_hyphen_index = current_name.rfind("-")

            # 3. If no hyphen is found, we can't shorten the name anymore.
            if last_hyphen_index == -1:
                break

            # 4. Remove the suffix and try again in the next iteration
            current_name = current_name[:last_hyphen_index]

        # 5. If no match was ever found, return an empty list
        return []

    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"Error searching for pokemon '{pokemon_name}'",
        )
        return []

def search_pokedex_by_id(species_id):
    id_index = _load_pokedex_id_index()  # Use index for O(1) lookup instead of O(n)
    return id_index.get(species_id, "Pokémon not found")

def get_mainpokemon_evo(pokemon_name):
    pokedex_data = _load_pokedex_cache()  # Use cache instead of file I/O
    if pokemon_name not in pokedex_data:
        return []
    pokemon_info = pokedex_data[pokemon_name]
    evolutions = pokemon_info.get("evos", [])
    return evolutions

def get_base_experience(actual_id: int) -> int:
    pokemon_data = _load_pokemon_csv_cache()  # Use cache instead of file I/O
    for row in pokemon_data:
        if int(row["id"]) == actual_id:
            return int(row["base_experience"])
    raise ValueError(actual_id)

def get_effort_values(actual_id: int) -> dict[str, int]:
    evs = {}
    stats_data = _load_stats_csv_cache()  # Use cache instead of file I/O
    for row in stats_data:
        if int(row["pokemon_id"]) == actual_id:
            evs[STATS[int(row["stat_id"])]] = int(row["effort"])

    return {
        "hp": evs.get("hp", 0),
        "attack": evs.get("attack", 0),
        "defense": evs.get("defense", 0),
        "special-attack": evs.get("special-attack", 0),
        "special-defense": evs.get("special-defense", 0),
        "speed": evs.get("speed", 0),
    }

def get_growth_rate(species_id: int) -> str:
    """Get the growth rate for a pokemon species"""
    poke_species_data = _load_poke_species_cache()  # Use cache instead of file I/O
    for row in poke_species_data:
        if int(row.get("id", 0)) == species_id:
            growth_rate_id = int(row.get("growth_rate_id", 2))
            return GROWTH_RATES.get(growth_rate_id, "medium")
    return "medium"  # Default fallback

def get_pokemon_descriptions(species_id, language):
    """Get pokemon descriptions from cache. Returns a random description if multiple exist."""
    language = _normalize_language_id(language)
    
    # Load all descriptions into cache
    all_descriptions = _load_pokemon_descriptions_csv()
    
    # Get descriptions for this species and language
    descriptions = all_descriptions.get((species_id, language), [])
    
    if descriptions:
        if len(descriptions) > 1:
            return random.choice(descriptions)
        else:
            return descriptions[0]
    else:
        return "Description not found."


def get_pokemon_diff_lang_name(pokemon_id: int, language: int):
    """Get pokemon name in specified language from cache."""
    language = _normalize_language_id(language)
    
    # Load all names into cache
    names_cache = _load_pokemon_names_csv()
    
    # Look up the name
    name = names_cache.get((pokemon_id, language))
    if name:
        return name
    return "No Translation in this language"

def extract_ids_from_file():
    try:
        # get_all_pokemon_ids returns a set of integer IDs natively from SQLite virtual columns
        ids = mw.ankimon_db.get_all_pokemon_ids()
        return sorted(list(ids))
    except Exception as e:
        show_warning_with_traceback(
            parent=mw, exception=e, message="Error extracting IDs from file"
        )
        return []


from .learnset_retrieval import get_all_pokemon_moves  # noqa: F401 — re-export for backwards compat


def find_details_move(move_name: str) -> dict:
    """
    Retrieve the move details for the given move.
    """
    try:
        moves_data = _load_moves_cache()  # Use cache instead of file I/O
        move = moves_data.get(move_name.lower())
        if move:
            return move
        move_name = move_name.replace(" ", "")
        move = moves_data.get(move_name.lower())
        if move:
            return move
        move_name = move_name.replace("-", "")
        move = moves_data.get(move_name.lower())
        if move:
            return move
        else:
            move = moves_data.get("tackle")
            showWarning(f"Move '{move_name}' not found. Returning default move 'tackle'.")
            return move
                
    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"There is an issue in find_details_move for move: {move_name}. Returning to default move 'tackle'."
        )
        return moves_data.get("tackle") if moves_data else None

def check_evolution_by_item(pokemon_id, item_id, file_path=poke_evo_path):
    """
    Check if a Pokémon evolves using a specific item.

    Args:
        pokemon_id (int): The ID of the Pokémon.
        item_id (int): The ID of the item.

    Returns:
        bool: True if the Pokémon evolves with the given item, False otherwise.
    """
    # Get the evolution data for the given Pokémon ID
    possible_evos = pokemon_evolves_from_id(
        pokemon_id
    )  # Ensure this returns a list of possible evolutions
    if not possible_evos:
        showWarning("No possible evos found")
        return False

    # Iterate through the possible evolutions
    for evos in possible_evos:
        evo_data = get_pokemon_evolution_data(int(evos))
        if evo_data:
            if int(evo_data["evolution_trigger_id"]) == 3 and int(
                evo_data["trigger_item_id"]
            ) == int(item_id):
                return int(
                    evo_data["evolved_species_id"]
                )  # Return True as soon as a matching evolution is found

    # If no evolution matches the criteria, return False
    return None


# get pokemon name for next evolution from csv species
# get pokemon id from name
# get from pokemon_evolutions.csv with pokemon evo id the evo trigger id and evolution min_level or item_id


def check_evolution_for_pokemon(
    individual_id, pokemon_id, level, evo_window, everstone=False
):
    """
    Check if a Pokémon evolves using a specific item or level condition.

    Args:
        individual_id (int): The ID of the individual Pokémon.
        id (int): A unique identifier for the Pokémon instance.
        pokemon_id (int): The ID of the Pokémon species.
        level (int): The current level of the Pokémon.
        evo_window (object): The evolution window object for displaying evolution information.
        everstone (bool): Whether the Pokémon is holding an Everstone. Defaults to False.

    Returns:
        int | None: The evolution ID if an evolution is found, or None otherwise.
    """
    if not everstone:
        try:
            # Get the evolution data for the given Pokémon ID
            possible_evos = pokemon_evolves_from_id(
                pokemon_id
            )  # Ensure this returns a list of possible evolutions
            if not possible_evos:
                # showWarning("No possible evolutions found")
                return None

            # Check each possible evolution
            for evos in possible_evos:
                evo_data = get_pokemon_evolution_data(int(evos))
                # Only handle level-up evolutions (trigger_id == 1)
                if evo_data and int(evo_data.get("evolution_trigger_id", 0)) == 1:
                    min_level_str = evo_data.get("minimum_level", "")
                    # Only proceed if min_level_str represents a valid integer
                    if not min_level_str or not str(min_level_str).isdigit():
                        continue  # Skip this evolution if minimum_level is missing or not a number
                    min_level = int(min_level_str)
                    if min_level <= level:
                        evo_window.ask_pokemon_evo(
                            individual_id, pokemon_id, int(evos)
                        )
                        return int(evos)  # Return the evolution ID

            # If no evolutions fit the criteria
            # showWarning("No fitting evolution found for the given level")
            return None
        except Exception as e:
            show_warning_with_traceback(
                parent=mw,
                exception=e,
                message=f"Error checking evolution for Pokémon ID {pokemon_id}",
            )
            return None
    else:
        return None


def check_if_evolution_exists(pokemon_id):
    possible_evos = pokemon_evolves_from_id(
        pokemon_id
    )  # Ensure this returns a list of possible evolutions
    if not possible_evos:
        showWarning("No possible evos found")
        return False
    else:
        return possible_evos


def pokemon_evolves_from_id(pokemon_id):
    """Get the list of Pokémon IDs that evolve into the given Pokémon ID
    from the pokemon_species.csv file.
    """
    evolves_from_ids = []
    try:
        poke_species_data = _load_poke_species_cache()  # Use cache instead of file I/O
        for row in poke_species_data:
            evolves_from_species_id = row.get("evolves_from_species_id", None)
            if evolves_from_species_id:
                try:
                    if int(evolves_from_species_id) == int(pokemon_id):
                        evolves_from_ids.append(row["id"])
                except ValueError:
                    continue
        return evolves_from_ids
    except Exception as e:
        show_warning_with_traceback(
            exception=e,
            message=f"Error in pokemon_evolves_from_id function: {e} with pokemon_id {pokemon_id}",
        )
        return []


def get_pokemon_evolution_data(pokemon_id):
    """Returns the evolution data for a given Pokémon ID by matching evolved_species_id."""
    try:
        poke_evo_data = _load_poke_evo_cache()  # Use cache instead of file I/O
        for row in poke_evo_data:
            try:
                if int(row["evolved_species_id"]) == int(pokemon_id):
                    return row
            except ValueError:
                continue
        return None
    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"Error retrieving evolution data for Pokémon ID {pokemon_id}",
        )
        return None


def check_key_in_table(column_name, value, file_path):
    """Checks if a given value exists in the specified column and returns the matching row."""
    matching_row = None  # Initialize variable to hold matching row

    try:
        # Open the CSV file
        with open(file_path, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)

            # Search for the value in the specified column
            for row in reader:
                # Use .get() to prevent KeyError if the column doesn't exist
                if row.get(column_name) and str(row[column_name]) == str(
                    value
                ):  # Compare as string for consistency
                    matching_row = row
                    break  # Exit the loop once the matching row is found

    except FileNotFoundError:
        print(f"Error: The file {file_path} does not exist.")
    except Exception as e:
        print(f"Error: {e}")

    # Return the matching row or None if no match is found
    return matching_row


def return_name_for_id(pokemon_id):
    """
    For National Pokedex Pokémon ID, return the name (identifier).

    Parameters:
        pokemon_id (int): The ID of the Pokémon to search for.

    Returns:
        str: The name (identifier) of the Pokémon if found.
        None: If no matching Pokémon is found or an error occurs.
    """
    try:
        # Open the CSV file
        with open(pokemon_csv, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)  # Read the file as a dictionary

            # Search for the value in the "id" column
            for row in reader:
                if int(row["id"]) == int(
                    pokemon_id
                ):  # Convert CSV id to integer for comparison
                    return row["identifier"]  # Return the identifier from the CSV row

        # Log a message if the item is not found
        showWarning(f"Name for Pokemon with ID '{pokemon_id}' not found in the CSV.")
        return None
    except Exception as e:
        # Log any unexpected errors
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"No evolution data found for Pokémon ID '{pokemon_id}'",
        )(f"Error retrieving name for Pokémon ID '{pokemon_id}': {e}")
        return None


def return_id_for_item_name(item_name):
    """
    Returns the ID of an item based on its name (identifier) from a CSV file.

    Parameters:
        item_name (str): The name of the item to search for.

    Returns:
        str: The ID of the item if found.
        None: If no matching item is found or an error occurs.
    """
    try:
        # Open the CSV file
        with open(csv_file_items_cost, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)  # Read the file as a dictionary

            # Search for the value in the "identifier" column
            for row in reader:
                if (
                    row["identifier"] == item_name
                ):  # Check if the identifier matches the item name
                    return row["id"]  # Return the id from the CSV row

        # Log a message if the item is not found
        showWarning("warning", f"Item '{item_name}' not found in the CSV.")
        return None
    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"Error retrieving ID for item '{item_name}'",
        )
        return None
