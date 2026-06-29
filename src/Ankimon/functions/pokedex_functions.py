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
from ..services import services
try:
    # Only used as a parent for the error dialog; None when headless.
    from aqt import mw
except Exception:
    mw = None
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

def safe_int(value, default=0):
    """Safely convert a value to an integer, returning a default if conversion fails."""
    if value is None:
        return default
    try:
        # Strip whitespace if it's a string
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default
        # Using float first handles things like "123.0"
        return int(float(value))
    except (ValueError, TypeError):
        return default

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
                species_id = safe_int(attributes.get("species_id"))
                actual_id = safe_int(attributes.get("actual_id"))
                
                # Use internal names (keys) for logic consistency
                if actual_id is not None:
                    _pokedex_id_index[actual_id] = entry_name
                
                if species_id is not None:
                    if species_id not in _pokedex_id_index:
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
            _poke_species_cache = {}
            with open(poke_species_path, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    species_id = safe_int(row.get("id", 0))
                    _poke_species_cache[species_id] = row
        except Exception as e:
            print(f"Error loading poke_species cache: {e}")
            _poke_species_cache = {}
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
            _pokemon_csv_cache = {}
            with open(pokemon_csv, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    actual_id = safe_int(row.get("id"))
                    _pokemon_csv_cache[actual_id] = row
        except Exception as e:
            print(f"Error loading pokemon CSV cache: {e}")
            _pokemon_csv_cache = {}
    return _pokemon_csv_cache

def _load_stats_csv_cache():
    """Cache stats.csv to avoid repeated file I/O. Keyed by pokemon_id."""
    global _stats_csv_cache
    if _stats_csv_cache is None:
        try:
            _stats_csv_cache = {}
            with open(stats_csv, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    actual_id = safe_int(row.get("pokemon_id"))
                    stat_id = safe_int(row.get("stat_id"))
                    effort = safe_int(row.get("effort"))
                    if actual_id not in _stats_csv_cache:
                        _stats_csv_cache[actual_id] = {}
                    _stats_csv_cache[actual_id][stat_id] = effort
        except Exception as e:
            print(f"Error loading stats CSV cache: {e}")
            _stats_csv_cache = {}
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
                    species_id = safe_int(row.get("pokemon_species_id"))
                    lang_id = safe_int(row.get("local_language_id"))
                    name = row.get("name", "")
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
                    species_id = safe_int(row.get("species_id"))
                    lang_id = safe_int(row.get("language_id"))
                    flavor_text = row.get("flavor_text", "").replace("\x0c", " ")
                    
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
        if isinstance(pokemon_name, str):
            pokemon_name = pokemon_name.lower()
            
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
            
            # 2. Try normalized version (no spaces, hyphens, or apostrophes)
            # This handles cases like "Venusaur-Mega" matching "venusaurmega"
            normalized_name = current_name.replace(" ", "").replace("-", "").replace("'", "")
            if normalized_name in pokedex_data:
                pokemon_info = pokedex_data[normalized_name]
                var = pokemon_info.get(variable)
                if var is not None:
                    return var

            # 3. If no match, find the last hyphen to try the base form
            last_hyphen_index = current_name.rfind("-")

            # 4. If no hyphen is found, we can't shorten the name anymore.
            if last_hyphen_index == -1:
                break

            # 5. Remove the suffix and try again in the next iteration
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

    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"Error searching for pokemon '{pokemon_name}'",
        )
        return []

def search_pokedex_by_id(species_id):
    id_index = _load_pokedex_id_index()  # Use index for O(1) lookup instead of O(n)
    return id_index.get(safe_int(species_id), "Pokémon not found")

def format_lore_name(name: str) -> str:
    """Transform internal hyphenated names into lore-accurate ones (e.g. Venusaur-Mega -> Mega Venusaur)."""
    if not name or not isinstance(name, str):
        return name
        
    # Order matters: check more specific ones first
    if "-Mega-X" in name:
        return "Mega " + name.replace("-Mega-X", " X")
    if "-Mega-Y" in name:
        return "Mega " + name.replace("-Mega-Y", " Y")
    if "-Mega-Z" in name:
        return "Mega " + name.replace("-Mega-Z", " Z")
    
    replacements = {
        "-Mega": "Mega ",
        "-Gmax": "Gigantamax ",
        "-Alola": "Alolan ",
        "-Galar": "Galarian ",
        "-Paldea": "Paldean ",
        "-Hisui": "Hisuian ",
        "-Primal": "Primal ",
        "-Origin": "Origin ",
        "-Therian": "Therian ",
    }
    
    for suffix, prefix in replacements.items():
        if suffix in name:
            base = name.replace(suffix, "")
            return prefix + base
            
    return name

def get_pretty_name_for_id(species_id):
    """Get the official pretty name (e.g. Mega Venusaur) for an ID."""
    try:
        pokedex_data = _load_pokedex_cache()
        internal_name = search_pokedex_by_id(species_id)
        if internal_name in pokedex_data:
            raw_name = pokedex_data[internal_name].get("name", internal_name.capitalize())
            return format_lore_name(raw_name)
    except:
        pass
    return "Pokémon not found"

def get_pretty_name_for_name(pokemon_name):
    """Get the official pretty name (e.g. Mega Venusaur) from an internal name."""
    try:
        pokedex_data = _load_pokedex_cache()
        # Use aggressive normalization (isalnum) to match cache keys
        internal_name = "".join(c for c in str(pokemon_name).lower() if c.isalnum())
        
        if internal_name in pokedex_data:
            raw_name = pokedex_data[internal_name].get("name", pokemon_name.title())
            return format_lore_name(raw_name)
            
        # Fallback: try removing common suffixes if direct match fails
        for suffix in ["-mega", "-gmax", "-alola", "-galar", "-hisui", "-paldea"]:
            if suffix in pokemon_name.lower():
                base_name = pokemon_name.lower().replace(suffix, "").replace("-", "")
                if base_name in pokedex_data:
                    raw_base = pokedex_data[base_name].get("name", base_name.capitalize())
                    return format_lore_name(pokemon_name.title())
    except:
        pass
    return format_lore_name(str(pokemon_name).replace("-", " ").title())

def get_mainpokemon_evo(pokemon_name):
    pokedex_data = _load_pokedex_cache()  # Use cache instead of file I/O
    if pokemon_name not in pokedex_data:
        return []
    pokemon_info = pokedex_data[pokemon_name]
    evolutions = pokemon_info.get("evos", [])
    return evolutions
def get_growth_rate(species_id: int) -> str:
    # Coerce string callers to int so they match the integer CSV ids; a
    # non-numeric argument keeps the original "not found" behaviour.
    try:
        species_id = int(species_id)
    except (TypeError, ValueError):
        raise ValueError(species_id)
    cache = _load_poke_species_cache()
    row = cache.get(species_id)
    if row:
        return GROWTH_RATES[int(row["growth_rate_id"])]
    raise ValueError(species_id)

def get_base_experience(actual_id: int) -> int:
    # Coerce string callers to int so they match the integer CSV ids; a
    # non-numeric argument keeps the original "not found" behaviour.
    try:
        actual_id = int(actual_id)
    except (TypeError, ValueError):
        raise ValueError(actual_id)
    cache = _load_pokemon_csv_cache()
    row = cache.get(actual_id)
    if row:
        return int(row["base_experience"])
    raise ValueError(actual_id)

def get_effort_values(actual_id: int) -> dict[str, int]:
    evs = {}
    stats_data = _load_stats_csv_cache()  # Use cache instead of file I/O
    
    pokemon_stats = stats_data.get(actual_id, {})
    for stat_id, effort in pokemon_stats.items():
        if stat_id in STATS:
            evs[STATS[stat_id]] = effort

    return {
        "hp": evs.get("hp", 0),
        "attack": evs.get("attack", 0),
        "defense": evs.get("defense", 0),
        "special-attack": evs.get("special-attack", 0),
        "special-defense": evs.get("special-defense", 0),
        "speed": evs.get("speed", 0),
    }

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
        return format_lore_name(name)
        
    # If not found and it's a form ID (>= 10000), fall back to species ID
    if pokemon_id >= 10000:
        internal_name = search_pokedex_by_id(pokemon_id)
        # Load pokedex data to get the raw name with suffix (e.g. Meowth-Alola)
        pokedex_data = _load_pokedex_cache()
        info = pokedex_data.get(internal_name, {})
        raw_pokedex_name = info.get("name", "")
        
        species_id = safe_int(info.get("species_id"))
        if species_id:
            base_lang_name = names_cache.get((species_id, language))
            if base_lang_name:
                # If we have a hyphenated name, reconstruct with translated base
                if "-" in raw_pokedex_name:
                    suffix = raw_pokedex_name[raw_pokedex_name.find("-"):]
                    return format_lore_name(base_lang_name + suffix)
                return format_lore_name(base_lang_name)

    return "No Translation in this language"

def extract_ids_from_file():
    try:
        # get_all_pokemon_ids returns a set of integer IDs natively from SQLite virtual columns
        ids = services.db.get_all_pokemon_ids()
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
            services.ui.warn(f"Move '{move_name}' not found. Returning default move 'tackle'.")
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

    Two evolution methods consume an item:

    * ``evolution_trigger_id == 3`` — *use-item* evolutions, where the item is
      applied directly (e.g. Eevee + Water Stone -> Vaporeon). The item id lives
      in ``trigger_item_id``.
    * ``evolution_trigger_id == 2`` — *trade* evolutions that additionally
      require a *held item* (e.g. Clamperl -> Huntail/Gorebyss via Deep Sea
      Tooth/Scale, Onix -> Steelix via Metal Coat, Seadra -> Kingdra via Dragon
      Scale). Since Ankimon has no trading, we treat the held item as the trigger
      so these species are still reachable. The item id lives in ``held_item_id``.

    Comparisons are done as strings via ``.get(...)`` so blank/missing CSV fields
    (most trade rows leave ``trigger_item_id`` empty, and most use-item rows leave
    ``held_item_id`` empty) never raise on ``int("")``.

    Args:
        pokemon_id (int): The ID of the (pre-evolution) Pokémon.
        item_id (int): The ID of the item.

    Returns:
        int | None: The evolved species id if the Pokémon evolves with the given
        item, otherwise ``None``.
    """
    # Get the evolution data for the given Pokémon ID
    possible_evos = pokemon_evolves_from_id(
        pokemon_id
    )  # Ensure this returns a list of possible evolutions
    if not possible_evos:
        services.ui.warn("No possible evos found")
        return None

    target_item = str(item_id)

    # Iterate through the possible evolutions
    for evos in possible_evos:
        try:
            evo_data = get_pokemon_evolution_data(int(evos))
        except (TypeError, ValueError):
            continue
        if not evo_data:
            continue

        try:
            trigger_id = int(evo_data.get("evolution_trigger_id", 0) or 0)
        except (TypeError, ValueError):
            continue

        # Use-item evolutions match on trigger_item_id; trade-with-held-item
        # evolutions match on held_item_id. Compare as strings so blank fields
        # ("") simply don't match instead of raising on int("").
        if trigger_id == 3 and str(evo_data.get("trigger_item_id", "")) == target_item:
            return int(evo_data["evolved_species_id"])
        if trigger_id == 2 and str(evo_data.get("held_item_id", "")) == target_item:
            return int(evo_data["evolved_species_id"])

    # If no evolution matches the criteria, return None
    return None


def get_time_of_day():
    """Return the current time of day as 'day' or 'night' self-contained without friendship_evolution dependency."""
    try:
        from datetime import datetime, timedelta, timezone
        settings_obj = services.settings  # registry-backed; no singletons/aqt import

        # Check if settings_obj exists and is fully initialized
        if settings_obj is None:
            return "day"
            
        offset_str = settings_obj.get("evolution.timezone_offset", "auto")
        if offset_str == "auto":
            moment = datetime.now()
        else:
            try:
                offset_hours = float(offset_str)
                tz = timezone(timedelta(hours=offset_hours))
                moment = datetime.now(tz)
            except Exception:
                moment = datetime.now()
                
        hour = moment.hour
        
        def coerce_hour(val, default):
            try:
                return max(0, min(23, int(float(val))))
            except Exception:
                return default
                
        day_start = coerce_hour(settings_obj.get("evolution.day_start_hour", 6), 6)
        night_start = coerce_hour(settings_obj.get("evolution.night_start_hour", 18), 18)
        
        return "day" if day_start <= hour < night_start else "night"
    except Exception:
        # Fallback to local system time in case of any exception/import error
        try:
            from datetime import datetime
            hour = datetime.now().hour
            return "day" if 6 <= hour < 18 else "night"
        except Exception:
            return "day"


# get pokemon name for next evolution from csv species
# get pokemon id from name
# get from pokemon_evolutions.csv with pokemon evo id the evo trigger id and evolution min_level or item_id


def check_evolution_for_pokemon(
    individual_id, pokemon_id, level, evo_window, everstone=False, evolution_rejected=False
):
    """
    Check if a Pokémon evolves using a specific item or level condition.
    Prioritizes pokedex.json for regional forms, falling back to species CSVs.

    Args:
        individual_id (str): The ID of the individual Pokémon.
        pokemon_id (int): The ID of the Pokémon species/form.
        level (int): The current level of the Pokémon.
        evo_window (object): The evolution window object for displaying evolution information.
        everstone (bool): Whether the Pokémon is holding an Everstone. Defaults to False.
        evolution_rejected (bool): Whether the user previously rejected this
            evolution. When True the automatic prompt is suppressed. Defaults to False.

    Returns:
        int | None: The evolution ID if an evolution is found, or None otherwise.
    """
    if not everstone and not evolution_rejected:
        # Get the evolution data for the given Pokémon ID
        possible_evos = pokemon_evolves_from_id(
            pokemon_id
        )  # Ensure this returns a list of possible evolutions
        if not possible_evos:
            # services.ui.warn("No possible evolutions found")
            return None

    try:
        current_time = get_time_of_day()

        # 1. PRIORITY CHECK: Form-aware evolution from pokedex.json
        # This handles Alolan/Galarian etc. forms that aren't in the base species CSV
        pokedex_data = _load_pokedex_cache()
        internal_name = search_pokedex_by_id(pokemon_id)
        
        if internal_name in pokedex_data:
            details = pokedex_data[internal_name]
            evo_list = details.get("evos")
            
            if evo_list:
                for target_evo_name in evo_list:
                    normalized_target = target_evo_name.lower().replace(" ", "").replace("-", "").replace("'", "")
                    target_data = pokedex_data.get(normalized_target) or pokedex_data.get(target_evo_name.lower())
                    
                    if target_data:
                        # In Smogon-style pokedex.json, evoLevel is stored on the evolved species
                        condition = (target_data.get("evoCondition") or "").lower()

                        if condition == "minimumdefeated":
                            evo_defeated = safe_int(target_data.get("evoDefeated"))
                            if evo_defeated > 0:
                                pokemon_data = services.db.get_pokemon(individual_id)
                                if pokemon_data:
                                    from ..pyobj.pokemon_obj import PokemonObject
                                    pokemon_obj = PokemonObject.from_dict(pokemon_data)
                                    if (pokemon_obj.pokemon_defeated or 0) >= evo_defeated:
                                        evo_id = safe_int(target_data.get("actual_id") or target_data.get("species_id"))
                                        if evo_id > 0:
                                            evo_window.ask_pokemon_evo(individual_id, pokemon_id, evo_id)
                                            return evo_id
                            continue

                        min_level = safe_int(target_data.get("evoLevel"))
                        
                        if min_level > 0 and level >= min_level:

                            time_of_day = None
                            if "day" in condition:
                                time_of_day = "day"
                            elif "night" in condition:
                                time_of_day = "night"

                            if time_of_day is None or time_of_day == current_time:
                                evo_id = safe_int(target_data.get("actual_id") or target_data.get("species_id"))
                                if evo_id > 0:
                                    evo_window.ask_pokemon_evo(individual_id, pokemon_id, evo_id)
                                    return evo_id

        # 2. LEGACY FALLBACK: Species CSV lookup
        # This covers base forms and legacy data mapping
        possible_evos = pokemon_evolves_from_id(pokemon_id)
        if possible_evos:
            for evos in possible_evos:
                evo_data = get_pokemon_evolution_data(safe_int(evos))
                if evo_data and safe_int(evo_data.get("evolution_trigger_id", 0)) == 1:
                    min_level = safe_int(evo_data.get("minimum_level"))
                    if min_level > 0 and level >= min_level:
                        time_raw = (evo_data.get("time_of_day") or "").strip().lower()
                        time_of_day = time_raw if time_raw in ("day", "night") else None

                        if time_of_day is None or time_of_day == current_time:
                            evo_window.ask_pokemon_evo(individual_id, pokemon_id, safe_int(evos))
                            return safe_int(evos)

        return None

        
    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"Error checking evolution for Pokémon ID {pokemon_id}",
        )
        return None


def check_if_evolution_exists(pokemon_id):
    possible_evos = pokemon_evolves_from_id(
        pokemon_id
    )  # Ensure this returns a list of possible evolutions
    if not possible_evos:
        services.ui.warn("No possible evos found")
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
        for row in poke_species_data.values():
            evolves_from_species_id = row.get("evolves_from_species_id", None)
            if evolves_from_species_id:
                try:
                    if safe_int(evolves_from_species_id) == safe_int(pokemon_id):
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
                if safe_int(row.get("evolved_species_id")) == safe_int(pokemon_id):
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


def rows_for_key_in_table(column_name, value, file_path):
    """Return *all* rows where ``column_name`` equals ``value`` (as a list).

    Unlike :func:`check_key_in_table`, which stops at the first hit, this returns
    every matching row. The bundled ``pokemon_evolution.csv`` stores one row per
    evolution *method*, so a single evolved species can appear on several rows —
    e.g. Sylveon has a blank row and a separate ``minimum_happiness`` row, and
    Persian has both a level-up row and a friendship row. Callers that need to
    pick the row matching a specific method (level vs. friendship) must see them
    all rather than just whichever comes first in the file.

    Args:
        column_name: The column to match on.
        value: The value to match (compared as a string, like
            :func:`check_key_in_table`).
        file_path: Path to the CSV file to scan.

    Returns:
        A list of matching rows (each a ``dict``); empty on no match or error.
    """
    matching_rows = []
    try:
        with open(file_path, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                # Use .get() to prevent KeyError if the column doesn't exist.
                if row.get(column_name) and str(row[column_name]) == str(value):
                    matching_rows.append(row)
    except FileNotFoundError:
        print(f"Error: The file {file_path} does not exist.")
    except Exception as e:
        print(f"Error: {e}")

    return matching_rows


def rows_for_key_in_table(column_name, value, file_path):
    """Return *all* rows where ``column_name`` equals ``value`` (as a list).

    Unlike :func:`check_key_in_table`, which stops at the first hit, this returns
    every matching row. The bundled ``pokemon_evolution.csv`` stores one row per
    evolution *method*, so a single evolved species can appear on several rows —
    e.g. Sylveon has a blank row and a separate ``minimum_happiness`` row, and
    Persian has both a level-up row and a friendship row. Callers that need to
    pick the row matching a specific method (level vs. friendship) must see them
    all rather than just whichever comes first in the file.

    Args:
        column_name: The column to match on.
        value: The value to match (compared as a string, like
            :func:`check_key_in_table`).
        file_path: Path to the CSV file to scan.

    Returns:
        A list of matching rows (each a ``dict``); empty on no match or error.
    """
    matching_rows = []
    try:
        with open(file_path, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                # Use .get() to prevent KeyError if the column doesn't exist.
                if column_name in row and str(row[column_name]) == str(value):
                    matching_rows.append(row)
    except FileNotFoundError:
        print(f"Error: The file {file_path} does not exist.")
    except Exception as e:
        print(f"Error: {e}")

    return matching_rows


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
        cache = _load_pokemon_csv_cache()
        row = cache.get(int(pokemon_id))
        if row:
            return row["identifier"]

        # Log a message if the item is not found
        services.ui.warn(f"Name for Pokemon with ID '{pokemon_id}' not found in the CSV.")
        return None
    except Exception as e:
        # Log any unexpected errors
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"Error retrieving name for Pokémon ID '{pokemon_id}'",
        )
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
        cache = _load_items_cost_cache()
        for row in cache:
            if row["identifier"] == item_name:
                return row["id"]

        # Log a message if the item is not found
        services.ui.warn(f"Item '{item_name}' not found in the CSV.")
        return None
    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"Error retrieving ID for item '{item_name}'",
        )
        return None
