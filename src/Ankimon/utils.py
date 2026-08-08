import os
from pathlib import Path
import requests
import json
import random
import csv
import base64
from typing import Any, Optional

from .services import services
from .events import events

from .pyobj.settings import Settings
from .pyobj.InfoLogger import ShowInfoLogger

from .functions.battle_functions import calculate_hp
from .functions.pokedex_functions import find_details_move, search_pokedex

from .pyobj.error_handler import show_warning_with_traceback
from .resources import (
    battlescene_path,
    berries_path,
    items_path,
    csv_file_items_cost,
    csv_file_descriptions,
    font_path,
    hurt_normal_sound_path,
    hurt_noteff_sound_path,
    hurt_supereff_sound_path,
    hpheal_sound_path,
    ownhplow_sound_path,
    fainted_sound_path,
    addon_dir,
    POKEMON_TIERS,
    pokedex_path,
)
from .move_names import format_move_name


# Audio is optional. Under Anki these construct real Qt players; headless (the
# agent harness / tests) there is no QtMultimedia, so we degrade to "no audio"
# and play_sound/play_effect_sound become event-only.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

    audio_output = QAudioOutput()
    media_player = QMediaPlayer()
    media_player.setAudioOutput(audio_output)
    _HAVE_AUDIO = True
except Exception:
    audio_output = None
    media_player = None
    _HAVE_AUDIO = False


def showInfo(message, *args, **kwargs):
    """Headless-safe stand-in for ``aqt.utils.showInfo``.

    Routed through the UI presenter: a real popup under Anki (the QtPresenter),
    a recorded ``notify`` event headless. Keeps utils free of an aqt import.
    """
    services.ui.notify("info", str(message))


def showWarning(message, *args, **kwargs):
    """Headless-safe stand-in for ``aqt.utils.showWarning``."""
    services.ui.notify("warning", str(message))

with open(pokedex_path, "r", encoding="utf-8") as f:
    data = json.load(f)
    POKEMON_NAME_LOOKUP = {x: data[x]["name"] for x in data}


def format_pokemon_name(name: str) -> str:
    """
    Look up the official Pokémon name using the normalized key.
    Falls back to capitalizing if not found.
    """
    key = name.replace(" ", "").replace("-", "").replace("_", "").lower()
    return POKEMON_NAME_LOOKUP.get(key, name.capitalize())


def check_folders_exist(parent_directory, folder):
    folder_path = os.path.join(parent_directory, folder)
    return os.path.isdir(folder_path)


def check_file_exists(folder, filename):
    file_path = os.path.join(folder, filename)
    return os.path.isfile(file_path)


def test_online_connectivity(
    url="https://raw.githubusercontent.com/Unlucky-Life/ankimon/main/update_txt.md",
    timeout=1,
):
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
    except ValueError:
        return False

    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        # Direct sockets can be blocked on proxy-only networks. Fall back to the
        # configured requests transport without downloading the response body.
        response = None
        try:
            response = requests.get(url, timeout=timeout, stream=True)
            return response.status_code == 200
        except requests.RequestException:
            return False
        finally:
            if response is not None:
                response.close()


# Define the hook function
def addon_config_editor_will_display_json(text: str) -> str:
    """
    This function modifies the JSON configuration text before displaying it to the user.
    It replaces the values for the keys "pokemon_collection" and "mainpokemon".

    Args:
        text (str): The JSON configuration text.

    Returns:
        str: The modified JSON configuration text.
    """
    try:
        # Parse the JSON text
        config = json.loads(text)
        if "mainpokemon" in config:
            # showInfo(f"{config}")
            showInfo(
                "This Configuration is old and wont be used anymore. \n Please use the Settings Window in the Ankimon Menu => Settings"
            )
            # mw.settings_ankimon.show_window()
            # dont show all mainpokemon and mypokemon information in config
            if "pokemon_collection" in config:
                del config["pokemon_collection"]
            if "mainpokemon" in config:
                del config["mainpokemon"]
            if "trainer.cash" in config:
                del config["trainer.cash"]

            # Convert back to JSON string
            modified_text = json.dumps(config, indent=4)
            return modified_text
        return text
    except (requests.RequestException, json.JSONDecodeError):
        # Handle JSON parsing or network errors
        return text


# Function to read the content of the local file
def read_local_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        return None


# Function to check if the file exists on GitHub and read its content
def read_github_file(url):
    response = requests.get(url)

    if response.status_code == 200:
        # File exists, parse the Markdown content
        content = response.text
        return content
    else:
        return None


# Function to check if the content of the two files is the same
def compare_files(local_content, github_content):
    return local_content == github_content


# Function to write content to a local file
def write_local_file(file_path, content):
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(content)


def read_html_file(file_path):
    """Reads an HTML file and returns its content as a string."""
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()


def random_battle_scene():
    # TODO: choice?
    # TODO: merge with random_berries and
    battle_scenes = {}
    for index, filename in enumerate(os.listdir(battlescene_path)):
        if filename.endswith(".png"):
            battle_scenes[index + 1] = filename
    # Get the corresponding file name
    battlescene_file = battle_scenes.get(random.randint(1, len(battle_scenes)))
    return battlescene_file


def random_berries():
    berries = {}
    for index, filename in enumerate(os.listdir(berries_path)):
        if filename.endswith(".png"):
            berries[index + 1] = filename
    # Get the corresponding file name
    berries_file = berries.get(random.randint(1, len(berries)))
    return berries_file


def filter_item_sprites(string):
    # Initialize an empty list to store the file names
    item_names = []
    # Iterate over each file in the directory
    for file in os.listdir(items_path):
        # Check if the file is a .png file
        if file.endswith(".png"):
            # Append the file name without the .png extension to the list
            item_names.append(file[:-4])
    # filter by -ball, -repel..etc
    item_names = [name for name in item_names if name.endswith(f"{string}")]
    showInfo(f"{item_names}")
    return item_names


USELESS_ITEMS = {
    # not real items
    # NOTE: maybe these should be in a separate folder?
    "Bag_TM_normal_SV_Sprite",
    "Bag_TM_bug_SV_Sprite",
    "Bag_TM_dark_SV_Sprite",
    "Bag_TM_dragon_SV_Sprite",
    "Bag_TM_electric_SV_Sprite",
    "Bag_TM_fairy_SV_Sprite",
    "Bag_TM_fighting_SV_Sprite",
    "Bag_TM_fire_SV_Sprite",
    "Bag_TM_flying_SV_Sprite",
    "Bag_TM_ghost_SV_Sprite",
    "Bag_TM_grass_SV_Sprite",
    "Bag_TM_ground_SV_Sprite",
    "Bag_TM_ice_SV_Sprite",
    "Bag_TM_poison_SV_Sprite",
    "Bag_TM_psychic_SV_Sprite",
    "Bag_TM_rock_SV_Sprite",
    "Bag_TM_steel_SV_Sprite",
    "Bag_TM_water_SV_Sprite",
    # items that are sold for cash
    "balm-mushroom",
    "big-mushroom",
    "big-pearl",
    "comet-shard",
    "nugget",
    "pearl",
    "pearl-string",
    "pretty-wing",
    "rare-bone",
    "relic-gold",
    "tiny-mushroom",
    # catching / escape / encounter rate items
    "dive-ball",
    "dusk-ball",
    "great-ball",
    "heal-ball",
    "luxury-ball",
    "master-ball",
    "nest-ball",
    "net-ball",
    "poke-ball",
    "premier-ball",
    "quick-ball",
    "repeat-ball",
    "safari-ball",
    "timer-ball",
    "ultra-ball",
    "smoke-ball",  # escape from wild battles
    "fluffy-tail",  # escape from wild battles
    "repel",
    "max-repel",
    "super-repel",
    # Flutes and scarves, that work outside battle
    "black-flute",
    "blue-flute",
    "red-flute",
    "white-flute",
    "yellow-flute",
    "blue-scarf",
    "green-scarf",
    "pink-scarf",
    "red-scarf",
    "yellow-scarf",
    # Collectible shards
    "blue-shard",
    "green-shard",
    "red-shard",
    "yellow-shard",
    # Contest / Grooming / Friendship items outside battle
    "soothe-bell",
    "luxury-ball",
    "pretty-wing",
    # Miscellaneous items for info
    "heart-scale",
    "honey",
    "heart-scale",
    "shoal-salt",
    "shoal-shell",
    # Non-heal status items that only work out of battle
    "antidote",
    "awakening",
    "burn-heal",
    "full-heal",
    "ice-heal",
    "lava-cookie",
    "old-gateau",
    "heal-powder",
    "paralyze-heal",
    # Non-battle stat=ups or contests
    "calcium",
    "carbos",
    "clever-wing",
    "genius-wing",
    "health-wing",
    "hp-up",
    "iron",
    "muscle-wing",
    "protein",
    "resist-wing",
    "swift-wing",
    "zinc",
    # Rare candy and PP / elixirs
    "rare-candy",
    "pp-max",
    "pp-up",
    "max-elixir",
    "max-ether",
    "elixir",
    "ether",
}


_random_item_cache = None

def clear_utils_caches():
    """Clear performance caches in utils.py when profile closes."""
    global _random_item_cache
    _random_item_cache = None

def random_item():
    global _random_item_cache
    if _random_item_cache is None:
        item_names: list[str] = []

        # Iterate over each file in the directory
        for file in os.listdir(items_path):
            # Check if the file is a .png file
            if not file.endswith(".png"):
                continue

            # File name without the .png extension to the list
            name = file[:-4]

            if name in USELESS_ITEMS:
                continue
            if name.endswith("-ball"):
                continue
            if name.endswith("-repel"):
                continue
            if name.endswith("-incense"):
                continue
            if name.endswith("-fang"):
                continue
            if name.endswith("dust"):
                continue
            if name.endswith("-piece"):
                continue
            if name.endswith("-nugget"):
                continue

            item_names.append(name)
        _random_item_cache = item_names
    else:
        item_names = _random_item_cache

    item_name = random.choice(item_names)
    # add item to item list
    give_item(item_name)
    return item_name


# Function to get the list of daily items
def daily_item_list():
    """
    Generates a list of items available for the daily shop, filtering out certain categories.
    """
    # Check if the sprites directory exists. If not, trigger the download dialog.
    if not Path(items_path).exists():
        from .pyobj.download_sprites import show_agreement_and_download_dialog

        show_agreement_and_download_dialog(force_download=True)
        # Return an empty list to prevent the crash and allow the addon to load.
        return []

    # Items with these suffixes will be excluded from the daily shop
    excluded_suffixes = ["dust", "-piece", "-nugget", "-berry"]
    # Add full item names here to exclude them from the daily shop, e.g., ["master-ball"]

    item_names = []
    for file in os.listdir(items_path):
        if not file.endswith(".png"):
            continue

        item_name = file[:-4]

        # Filter out excluded items
        if (
            get_item_price(item_name) == 0
            or item_name in USELESS_ITEMS
            or any(item_name.endswith(suffix) for suffix in excluded_suffixes)
        ):
            continue

        item_names.append(
            {
                "name": item_name,
                "description": f"Item: {item_name}",
                "price": get_item_price(item_name),
            }
        )

    return item_names


# Function to give an item to the player
def give_item(item_name: str, item_type: Optional[str] = None):
    """Gives an item to the user."""
    db = services.db
    
    # Get current item or create new
    existing = db.get_item(item_name)
    if existing:
        db.update_item_quantity(item_name, 1)
        return
    
    extra_data = {"type": item_type} if item_type else None
    db.add_item(item_name, 1, extra_data)


# Function to return a cost of an item
def get_item_price(item_name, file_path=csv_file_items_cost):
    """
    Returns the cost of an item from a CSV file based on its identifier (name).

    Parameters:
        file_path (str): Path to the CSV file.
        item_name (str): The identifier (name) of the item.

    Returns:
        int: The cost of the item, or None if the item is not found or has no id.
    """
    try:
        with open(file_path, mode="r", newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row["identifier"] == item_name:
                    cost = row["cost"]
                    return int(cost)
    except FileNotFoundError:
        showWarning(f"Error: File {file_path} not found.")
        return 1000
    except KeyError:
        showWarning("Error: CSV file does not contain the expected headers.")
        return 1000
    except Exception as e:
        showWarning(f"Unexpected error: {e}")
        return 1000

    return None


# Function to return a cost of an item
def get_item_id(item_name, file_path=csv_file_items_cost):
    """
    Returns the cost of an item from a CSV file based on its identifier (name).

    Parameters:
        file_path (str): Path to the CSV file.
        item_name (str): The identifier (name) of the item.

    Returns:
        int: The id of the item, or None if the item is not found or has no id.
    """
    try:
        with open(file_path, mode="r", newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row["identifier"] == item_name:
                    id = row["id"]
                    return int(id)
    except (OSError, KeyError) as e:
        show_warning_with_traceback(
            exception=e, message="Error reading item data:"
        )
        return 4
    except Exception as e:
        show_warning_with_traceback(
            exception=e, message=f"Unexpected error: {e}"
        )
        return 4


# Function to return a random fossil
def random_fossil():
    fossil_names = []
    # Iterate over each file in the directory
    for file in os.listdir(items_path):
        # Check if the file is a .png file
        if file.endswith("-fossil.png"):
            # Append the file name without the .png extension to the list
            fossil_names.append(file[:-4])
    fossil_name = random.choice(fossil_names)
    give_item(fossil_name)
    return fossil_name


def count_items_and_rewrite():
    """
    Consolidates item quantities in the database.
    Legacy: Previously read from items.json, now uses database.
    """
    try:
        db = services.db

        # Legacy consolidation is now handled by the database (items are already
        # unique by item_name); this remains a safe read so a broken items table
        # surfaces via the except below at startup.
        db.get_all_items()

    except Exception as e:
        show_warning_with_traceback(
            exception=e, message=f"An unexpected error occurred: {e}"
        )


# Assuming the data is stored in a CSV file named 'item_flavor_texts.csv'
def get_item_description(item_name, language_id):
    """
    Fetch the flavor text for an item based on its item_id, version_group_id, and language_id.
    => get item_id from item_name via items.csv
    :param item_id: The ID of the item.
    :param language_id: The language ID for the flavor text.
    :param file_path: The path to the CSV file containing the flavor texts.
    :return: The flavor text if found, otherwise None.
    """
    try:
        item_id = get_item_id(item_name)
        file_path = csv_file_descriptions
        # Normalize language: fall back to Spanish data for es_latam (14), English on errors.
        try:
            normalized_lang = int(language_id)
        except Exception:
            normalized_lang = 9
        if normalized_lang == 14:
            normalized_lang = 7

        # Open the CSV file and read the contents
        with open(file_path, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)

            # Iterate through each row in the CSV
            for row in reader:
                # Check if the current row matches the item_id, version_group_id, and language_id
                if (
                    int(row["item_id"]) == item_id
                    and int(row["language_id"]) == normalized_lang
                ):
                    return row["flavor_text"]  # Return the matching flavor text

        # If no match is found, return None
        return None

    except Exception as e:
        show_warning_with_traceback(exception=e, message="An error occurred:")
        return None


def load_custom_font(font_size, language):
    if language == 1:
        font_file = "pkmn_w.ttf"
        font_file_path = font_path / font_file
        font_size = int((font_size * 1) / 2)
        if font_file_path.exists():
            font_name = "PKMN Western"
        else:
            font_name = "Early GameBoy"
            font_file = "Early GameBoy.ttf"
            font_size = int((font_size * 5) / 7)
    else:
        font_name = "Early GameBoy"
        font_file = "Early GameBoy.ttf"
        font_size = int((font_size * 2) / 5)

    # Register the custom font with its file path. Qt imported lazily so utils
    # stays importable headless (custom fonts are a GUI-only concern).
    from PyQt6.QtGui import QFontDatabase, QFont
    QFontDatabase.addApplicationFont(str(font_path / font_file))
    custom_font = QFont(
        font_name
    )  # Use the font family name you specified in the font file
    custom_font.setPointSize(int(font_size))  # Adjust the font size as needed

    return custom_font


def get_all_sprites(directory):
    """
    Returns a list of trainer sprite names without the '.png' extension
    from the specified directory.

    :param directory: Path to the directory containing trainer sprite images.
    :return: List of sprite names without '.png'.
    """
    try:
        sprite_names = [
            os.path.splitext(file)[0]  # Remove the file extension
            for file in os.listdir(directory)
            if file.endswith(".png")  # Filter for .png files
        ]
        return sprite_names
    except FileNotFoundError:
        print(f"Error: The directory '{directory}' does not exist.")
        return []


def play_effect_sound(settings_obj, sound_type):
    sound_effects = settings_obj.get("audio.sound_effects")
    if sound_effects is True:
        audio_path = None
        if sound_type == "HurtNotEffective":
            audio_path = hurt_noteff_sound_path
        elif sound_type == "HurtNormal":
            audio_path = hurt_normal_sound_path
        elif sound_type == "HurtSuper":
            audio_path = hurt_supereff_sound_path
        elif sound_type == "OwnHpLow":
            audio_path = ownhplow_sound_path
        elif sound_type == "HpHeal":
            audio_path = hpheal_sound_path
        elif sound_type == "Fainted":
            audio_path = fainted_sound_path

        if audio_path is None or not audio_path.is_file():
            return
        # Structured event so an agent "hears" the effect even with no audio device.
        events.emit("sound", kind="effect", sound=sound_type)
        if not _HAVE_AUDIO:
            return
        from PyQt6.QtCore import QUrl
        audio_output.setVolume(settings_obj.get("audio.volume"))
        media_player.setSource(QUrl.fromLocalFile(str(audio_path)))
        media_player.play()
    else:
        pass


def save_error_code(error_code, logger=None):
    error_fix_msg = ""
    try:
        # Find the position of the phrase "can't be transferred from Gen"
        index = error_code.find("can't be transferred from Gen")

        # Extract the substring starting from this position
        relevant_text = error_code[index:]

        # Find the first number in the extracted text (assuming it's the generation number)
        generation_number = int("".join(filter(str.isdigit, relevant_text)))

        # Show the generation number
        error_fix_msg += f"\n Please use Gen {str(generation_number)[0]} or lower"

        index = error_code.find("can't be transferred from Gen")

        # Extract the substring starting from this position
        relevant_text = error_code[index:]

        # Find the first number in the extracted text (assuming it's the generation number)
        generation_number = int("".join(filter(str.isdigit, relevant_text)))

        error_fix_msg += f"\n Please use Gen {str(generation_number)[0]} or lower"

    except Exception as e:
        if logger is not None:
            show_warning_with_traceback(exception=e, message="An error occurred:")

    if logger is not None:
        logger.log_and_showinfo("info", f"{error_fix_msg}")


def get_main_pokemon_data():
    main_pokemon_data = services.db.get_main_pokemon()
    
    if not main_pokemon_data:
        return None

    _name = main_pokemon_data["name"]
    if not main_pokemon_data.get('nickname') or main_pokemon_data.get('nickname') is None:
        _nickname = None
    else:
        _nickname = main_pokemon_data['nickname']
    _id = main_pokemon_data["id"]
    _ability = main_pokemon_data["ability"]
    _type = main_pokemon_data["type"]
    _stats = main_pokemon_data.get("stats") or main_pokemon_data.get("base_stats", {})
    _attacks = main_pokemon_data["attacks"]
    _level = main_pokemon_data["level"]
    _hp_base_stat = _stats.get("hp", 1)
    _growth_rate = main_pokemon_data["growth_rate"]
    _base_experience = main_pokemon_data["base_experience"]
    _ev = main_pokemon_data["ev"]
    _iv = main_pokemon_data["iv"]
    _gender = main_pokemon_data["gender"]
    _shiny = main_pokemon_data.get("shiny", False)
    _individual_id = main_pokemon_data.get("individual_id")
    _pokemon_defeated = main_pokemon_data.get("pokemon_defeated", 0)
    _current_hp = main_pokemon_data.get("current_hp")
    _xp = main_pokemon_data.get("xp", 0)
    _max_moves = main_pokemon_data.get("max_moves", [])
    _mega = main_pokemon_data.get("mega", False)
    _everstone = main_pokemon_data.get("everstone", False)
    _friendship = main_pokemon_data.get("friendship", 0)
    _held_item = main_pokemon_data.get("held_item")
    _status = main_pokemon_data.get("status")

    return {
        "name": _name, "nickname": _nickname, "id": _id, "ability": _ability,
        "type": _type, "stats": _stats, "attacks": _attacks,
        "level": _level, "hp": _hp_base_stat, "growth_rate": _growth_rate,
        "base_experience": _base_experience, "ev": _ev, "iv": _iv,
        "gender": _gender, "shiny": _shiny, "individual_id": _individual_id,
        "pokemon_defeated": _pokemon_defeated, "current_hp": _current_hp, "xp": _xp,
        "max_moves": _max_moves, "mega": _mega, "everstone": _everstone,
        "friendship": _friendship, "held_item": _held_item, "status": _status
    }


def play_sound(enemy_pokemon_id: int, settings_obj: Settings):
    if settings_obj.get("audio.sounds"):
        file_name = f"{enemy_pokemon_id}.ogg"
        audio_path = addon_dir / "user_files" / "sprites" / "sounds" / file_name
        if audio_path.is_file():
            events.emit("sound", kind="cry", pokemon_id=enemy_pokemon_id)
            if not _HAVE_AUDIO:
                return
            from PyQt6.QtCore import QUrl
            audio_output.setVolume(settings_obj.get("audio.volume"))
            media_player.setSource(QUrl.fromLocalFile(str(audio_path)))
            media_player.play()


def load_collected_pokemon_ids() -> set:
    """Loads all captured pokemon IDs from the database."""
    return services.db.get_all_pokemon_ids()


def limit_ev_yield(
    current_pokemon_ev: dict[str, int], ev_yield: dict[str, int]
) -> dict[str, int]:
    """
    Limits the EV (Effort Value) yield for a Pokémon based on current EVs and Pokémon game rules.

    Ensures that the total EVs after applying the yield do not exceed 510, and that no single
    stat exceeds 252 EVs. Adjusts the EV yield to comply with these constraints by capping individual
    stats and reducing EVs randomly if the total would exceed the maximum allowed.

    Args:
        current_pokemon_ev (dict[str, int]): Current EVs of the Pokémon, with keys as stat abbreviations
            ("hp", "atk", "def", "spa", "spd", "spe") and values as their EV amounts.
        ev_yield (dict[str, int]): Proposed EV yields from a defeated Pokémon, with keys as full stat names
            ("hp", "attack", "defense", "special-attack", "special-defense", "speed") and values as EV amounts.

    Raises:
        ValueError: If any key in `current_pokemon_ev` or `ev_yield` is not a recognized stat.

    Returns:
        dict[str, int]: Adjusted EV yields that do not cause the Pokémon's total EVs to exceed 510 or any
        single stat to exceed 252. The keys correspond to full stat names.
    """
    # The sum of EVs of a Pokemon can only add up to 510. With a limit of 252 EVs in a single stat.
    for stat in current_pokemon_ev.keys():
        if stat not in ("hp", "atk", "def", "spa", "spd", "spe"):
            raise ValueError(f"Unknown EV : {stat}")

    for stat in ev_yield.keys():
        if stat not in (
            "hp",
            "attack",
            "defense",
            "special-attack",
            "special-defense",
            "speed",
        ):
            raise ValueError(f"Unknown EV : {stat}")

    zipped_keys = zip(
        ["hp", "atk", "def", "spa", "spd", "spe"],
        ["hp", "attack", "defense", "special-attack", "special-defense", "speed"],
    )

    new_ev_yield = {
        "hp": 0,
        "attack": 0,
        "defense": 0,
        "special-attack": 0,
        "special-defense": 0,
        "speed": 0,
    }

    for key_1, key_2 in zipped_keys:
        # For each stat, we yield an amount of EVs that will not exceed the value of 252
        new_ev_yield[key_2] = min(ev_yield[key_2], 252 - current_pokemon_ev[key_1])

    # To ensure that we won't go above 510 EVs after yielding the EVs, we randomly reduce the EV yield until we drop below the 510 limit
    while (sum(current_pokemon_ev.values()) + sum(new_ev_yield.values())) > 510:
        rand_key = [
            key for key, val in new_ev_yield.items() if val > 0
        ]  # We only reduce the positive EV yield values. In other words : We don't give out negative EV yields
        if len(rand_key) == 0:
            break
        rand_key = random.choice(rand_key)
        new_ev_yield[rand_key] -= 1

    # This final block here is specifically made to give out negative EV yields
    # This might be necessary if, for any reason, the user's pokemon has a total EV sum already above 510
    # In that case, we randomly give out negative EV yields to bring down the EVs of the user's pokemon below 510
    while (sum(current_pokemon_ev.values()) + sum(new_ev_yield.values())) > 510:
        rand_key = random.choice(
            list(new_ev_yield.keys())
        )  # This time, we choose any EV yields, including those that could already have a negative EV yield
        new_ev_yield[rand_key] -= 1

    return new_ev_yield


def iv_rand_gauss(mu: float = 15, sigma: float = 5) -> int:
    """
    Generates a random individual value (IV) using a Gaussian distribution,
    clamped to the range [0, 31].

    Args:
        mu (float, optional): The mean of the Gaussian distribution. Defaults to 15.
        sigma (float, optional): The standard deviation of the Gaussian distribution. Defaults to 5.

    Returns:
        int: An integer IV value between 0 and 31 inclusive.
    """
    rand = random.gauss(mu, sigma)
    rand = max(0, rand)  # ensures that rand >= 0
    rand = min(31, rand)  # ensures that rand <= 31
    return int(rand)


def get_ev_spread(mode: str = "random") -> dict[str, int]:
    """
    Generate an EV (Effort Value) spread for Pokémon stats based on the specified mode.

    Args:
        mode (str): The mode of EV distribution. Supported modes are:
            - "random": Randomly distributes up to 510 EVs across stats using a uniform distribution,
                        with each stat capped at 252 EVs.
            - "pair": Assigns 252 EVs to two random stats and 4 EVs to a third random stat.
            - "defense": Returns a predefined defensive spread with 252 EVs in Defense and Special Defense,
                         and 4 EVs in HP.
            - "uniform": Distributes EVs evenly (84 EVs) across all stats.

    Returns:
        dict[str, int]: A dictionary mapping each stat ("hp", "atk", "def", "spa", "spd", "spe")
                        to its corresponding EV value according to the selected mode.
    """
    stat_names = ["hp", "atk", "def", "spa", "spd", "spe"]
    if mode == "random":  # Draws each EV following a uniform probability distribution
        cuts = sorted(random.sample(range(510 + 1), 6 - 1))
        parts = [a - b for a, b in zip(cuts + [510], [0] + cuts)]
        parts = [min(252, part) for part in parts]
        evs = {stat: val for stat, val in zip(stat_names, parts)}
        return evs
    elif mode == "pair":  # Draws 2 stats at 252 EVs, and a 3rd at 4 EVs
        ev = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
        stats = random.sample(stat_names, 3)
        ev[stats[0]] = 252
        ev[stats[1]] = 252
        ev[stats[2]] = 4
        return ev
    elif mode == "defense":
        return {"hp": 4, "atk": 0, "def": 252, "spa": 0, "spd": 252, "spe": 0}
    elif mode == "uniform":
        return {"hp": 84, "atk": 84, "def": 84, "spa": 84, "spd": 84, "spe": 84}

    raise ValueError(f"Received unknown value for 'mode': {mode}")


def scale_ev_spread_to_level(ev: dict[str, int], level: int) -> dict[str, int]:
    """Scale an EV spread down to a budget appropriate for ``level``.

    ``get_ev_spread`` hands out full competitive budgets (up to 510 EVs),
    which made every wild Pokemon stat-inflated relative to the player's,
    whose Pokemon earns EVs a few points per defeat. A wild Pokemon has
    only "trained" as long as its level implies, so its budget ramps
    linearly with level — ``min(510, level × 5.1)`` — reaching the full
    510 exactly at level 100. The spread's *shape* (which stats it
    favours) is preserved; each stat stays within the per-stat 252 cap.
    """
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 1
    budget = min(510, max(0, round(level * 5.1)))
    total = sum(max(0, int(v)) for v in ev.values())
    if total <= budget:
        return {k: min(252, max(0, int(v))) for k, v in ev.items()}
    scale = budget / total
    return {k: min(252, int(max(0, int(v)) * scale)) for k, v in ev.items()}


def get_tier_by_id(pokemon_id: int) -> Optional[str]:
    """
    Determines the tier category of a Pokémon based on its ID.

    Searches through lists in resources.py representing different Pokémon tiers
    (Normal, Legendary, Mythical, Baby, Ultra, Fossil, Hisuian, Starter) to find the tier corresponding
    to the given Pokémon ID.

    Args:
        pokemon_id (int): The unique identifier of the Pokémon.

    Returns:
        str | None: The tier name as a string if the Pokémon ID is found
        in one of the tier lists; otherwise, None.
    """

    for tier, ids in POKEMON_TIERS.items():
        if pokemon_id in ids:
            return tier
    return None


def safe_get_random_move(
    pokemon_moves: list[str], logger: Optional[ShowInfoLogger] = None
) -> dict:
    """
    Attempts to retrieve details of a randomly selected move from a list of Pokémon moves.

    This function shuffles the provided list of move names and tries to find the first
    move for which details can be successfully retrieved using `find_details_move`. If no
    valid move is found, it logs a warning (if a logger is provided) and defaults to
    returning the details for the move "Splash".

    Args:
        pokemon_moves (list[str]): A list of move names to select from.
        logger (ShowInfoLogger | None, optional): An optional logger instance for
            logging warnings if no valid move is found. Defaults to None.

    Returns:
        dict: A dictionary containing the details of a valid move if found;
            otherwise, the details for the move "Splash".
    """
    rand_moves = pokemon_moves.copy()
    random.shuffle(rand_moves)
    # We go through the shuffled list to find the first move that gets successfully parsed
    for move in rand_moves:
        move_details = find_details_move(move) or find_details_move(
            format_move_name(move)
        )
        if move_details is not None:
            return move_details
        else:
            if logger is not None:
                logger.log(
                    "warning",
                    f"Could not parse the following move : {str(move)}",
                )

    # If we fail to successfully parse a single move, we just return Splash
    if logger is not None:
        logger.log(
            "warning",
            f"Could not parse a single move in the following moveset : {str(pokemon_moves)}",
        )
    return find_details_move(format_move_name("splash"))

def png_to_base64(path: str) -> str:
    """Convert a PNG file to a base64 data URI for embedding into HTML.

    Args:
        path (str): absolute or relative filesystem path to a PNG file.

    Returns:
        str: a data URI string like ``data:image/png;base64,...`` or empty
             string if the file does not exist.
    """
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("utf-8")


def close_anki():
    # Guarded: only meaningful inside Anki. No-op headless.
    try:
        from aqt import mw
        mw.close()
    except Exception:
        pass


# --- Thread / Qt-liveness helpers (Stage A scaffolding) ---------------------
#
# Shared plumbing depended on by the thread-safe DB layer, the async-boot path,
# and any deferred leaf that touches background threads or long-lived Qt widgets.
# Re-fit verbatim from BRRRR_Experimental (utils.is_main_thread / is_alive);
# both use function-local imports so importing utils never drags in PyQt6.

def is_main_thread() -> bool:
    """True when called on Qt's GUI thread (or when there is no QApplication yet).

    Background workers (QueryOp boot, mobile-review resolution) use this to guard
    UI-touching work. Headless / pre-app it defaults to True so callers behave as
    if on the main thread.
    """
    # Consult sys.modules rather than importing PyQt6: force-loading Qt in an
    # aqt-free context (Tier-1 harness) can crash. No Qt loaded → headless → main.
    import sys
    qtwidgets = sys.modules.get("PyQt6.QtWidgets")
    qtcore = sys.modules.get("PyQt6.QtCore")
    if qtwidgets is None or qtcore is None:
        return True
    app = qtwidgets.QApplication.instance()
    if not app:
        return True
    return qtcore.QThread.currentThread() == app.thread()


def is_alive(obj) -> bool:
    """True if a QObject/QWidget's underlying C++ object has not been deleted.

    Guards against 'wrapped C/C++ object of type X has been deleted' RuntimeErrors
    when a cached widget reference outlives the window (reload-safety, live-update
    push targets).
    """
    if obj is None:
        return False
    try:
        obj.objectName()
        return True
    except (RuntimeError, AttributeError):
        return False


def is_dev_mode() -> bool:
    """Check if the user is a developer based on profile name or trainer name."""
    try:
        # Check Anki profile name (case-insensitive check for 'dev' triggers)
        try:
            from aqt import mw
            if mw and mw.pm and mw.pm.name:
                profile_name = mw.pm.name.lower()
                if "dev_" in profile_name or "_dev" in profile_name:
                    return True
        except Exception:
            pass

        # Check Trainer name in config
        if services.settings is not None:
            trainer_name = services.settings.get("trainer.name", "").lower()
            if "dev_" in trainer_name or "_dev" in trainer_name:
                return True
    except Exception:
        pass
    return False
