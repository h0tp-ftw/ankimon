import base64
import csv

from .resources import csv_file_items, csv_file_descriptions

# Data for all 25 natures and their stat modifications
POKEMON_NATURES = {
    # Neutral Natures (no effect)
    "Hardy": {}, "Docile": {}, "Serious": {}, "Bashful": {}, "Quirky": {},
    # +Attack
    "Lonely": {"increased": "atk", "decreased": "def"},
    "Brave": {"increased": "atk", "decreased": "spe"},
    "Adamant": {"increased": "atk", "decreased": "spa"},
    "Naughty": {"increased": "atk", "decreased": "spd"},
    # +Defense
    "Bold": {"increased": "def", "decreased": "atk"},
    "Relaxed": {"increased": "def", "decreased": "spe"},
    "Impish": {"increased": "def", "decreased": "spa"},
    "Lax": {"increased": "def", "decreased": "spd"},
    # +Speed
    "Timid": {"increased": "spe", "decreased": "atk"},
    "Hasty": {"increased": "spe", "decreased": "def"},
    "Jolly": {"increased": "spe", "decreased": "spa"},
    "Naive": {"increased": "spe", "decreased": "spd"},
    # +Special Attack
    "Modest": {"increased": "spa", "decreased": "atk"},
    "Mild": {"increased": "spa", "decreased": "def"},
    "Quiet": {"increased": "spa", "decreased": "spe"},
    "Rash": {"increased": "spa", "decreased": "spd"},
    # +Special Defense
    "Calm": {"increased": "spd", "decreased": "atk"},
    "Gentle": {"increased": "spd", "decreased": "def"},
    "Sassy": {"increased": "spd", "decreased": "spe"},
    "Careful": {"increased": "spd", "decreased": "spa"},
}

def calculate_hp(base: int, level: int, iv: int, ev: int) -> int:
    ev_bonus = math.floor(ev / 4)
    base_and_iv = (2 * base + iv + ev_bonus)
    level_modifier = math.floor(base_and_iv * level / 100)
    return level_modifier + level + 10

def get_nature_multiplier(nature_name: str, stat_key: str) -> float:
    """
    Returns the stat multiplier (1.1, 1.0, or 0.9) for a given nature and stat.
    """
    nature_effects = POKEMON_NATURES.get(nature_name, {})
    if stat_key == nature_effects.get("increased"):
        return 1.1
    elif stat_key == nature_effects.get("decreased"):
        return 0.9
    else:
        return 1.0
    
def get_image_as_base64(path):
    with open(path, 'rb') as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
    return encoded_string

def split_string_by_length(input_string, max_length):
    current_length = 0
    current_line = []

    for word in input_string.split():
        word_length = len(word)  # Change this to calculate length in pixels

        if current_length + len(current_line) + word_length <= max_length:
            current_line.append(word)
            current_length += word_length
        else:
            yield ' '.join(current_line)
            current_line = [word]
            current_length = word_length

    yield ' '.join(current_line)

def split_japanese_string_by_length(input_string, max_length):
    max_length = 30
    current_length = 0
    current_line = ""

    for char in input_string:
        if current_length + 1 <= max_length:
            current_line += char
            current_length += 1
        else:
            yield current_line
            current_line = char
            current_length = 1

    if current_line:  # Ensure the last line is also yielded
        yield current_line

def resize_pixmap_img(pixmap, max_width):
    original_width = pixmap.width()
    original_height = pixmap.height()
    new_width = max_width
    new_height = (original_height * max_width) // original_width
    pixmap2 = pixmap.scaled(new_width, new_height)
    return pixmap2

def calc_experience(base_experience, enemy_level):
    exp = base_experience * enemy_level / 7
    return exp

def get_multiplier_stats(stage):
    # Define the mapping of stage to factor
    stage_to_factor = {
        -6: 3/9, -5: 3/8, -4: 3/7, -3: 3/6, -2: 3/5, -1: 3/4,
        0: 3/3,
        1: 4/3, 2: 5/3, 3: 6/3, 4: 7/3, 5: 8/3, 6: 9/3
    }

    # Return the corresponding factor or a default value if the stage is out of range
    return stage_to_factor.get(stage, "Invalid stage")

def get_multiplier_acc_eva(stage):
    # Define the mapping of stage to factor
    stage_to_factor_new = {
        -6: 2/8, -5: 2/7, -4: 2/6, -3: 2/5, -2: 2/4, -1: 2/3,
        0: 2/2,
        1: 3/2, 2: 4/2, 3: 5/2, 4: 6/2, 5: 7/2, 6: 8/2
    }

    # Return the corresponding factor or a default value if the stage is out of range
    return stage_to_factor_new.get(stage, "Invalid stage")

def bP_none_moves(move):
    target =  move.get("target", None)
    if target == "normal":
        damage = move.get("damage")
        if damage is None:
            damage = 5
        return damage
    
def type_colors(type_str):
    _type_colors = {
        "Normal": "#A8A77A",
        "Fire": "#EE8130",
        "Water": "#6390F0",
        "Electric": "#F7D02C",
        "Grass": "#7AC74C",
        "Ice": "#96D9D6",
        "Fighting": "#C22E28",
        "Poison": "#A33EA1",
        "Ground": "#E2BF65",
        "Flying": "#A98FF3",
        "Psychic": "#F95587",
        "Bug": "#A6B91A",
        "Rock": "#B6A136",
        "Ghost": "#735797",
        "Dragon": "#6F35FC",
        "Dark": "#705746",
        "Steel": "#B7B7CE",
        "Fairy": "#D685AD"
    }

    return _type_colors.get(type_str, "Unknown")

def calc_exp_gain(base_experience, w_pkmn_level):
    exp = int((base_experience * w_pkmn_level) / 7)
    return exp

def read_csv_file(csv_file):
    item_id_mapping = {}
    with open(csv_file, newline='', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            item_id_mapping[row['name'].lower()] = int(row['item_id'])
    return item_id_mapping

def capitalize_each_word(item_name):
    # Replace hyphens with spaces and capitalize each word
    return ' '.join(word.capitalize() for word in item_name.replace("-", " ").split())

def read_descriptions_csv(csv_file):
    descriptions = {}
    with open(csv_file, newline='', encoding='utf-8') as file:
        reader = csv.reader(file)
        next(reader)  # Skip the header row
        for row in reader:
            item_id = int(row[0])
            version_group_id = int(row[1])
            language_id = int(row[2])
            description = row[3].strip('"')
            key = (item_id, version_group_id, language_id)
            descriptions[key] = description
    return descriptions

def get_id_and_description_by_item_name(item_name: str) -> str:
    """
    Retrieve the item ID and description based on the given item name.

    This function normalizes the item name by capitalizing each word, 
    then looks up the item ID from a CSV mapping. If found, it retrieves
    the item description from a descriptions CSV using a fixed version group
    and language ID.

    Args:
        item_name (str): The name of the item to look up.

    Returns:
        tuple:
            - item_id (str or None): The ID of the item if found, else None.
            - description (str or None): The description of the item if found, else None.
    """
    item_name = capitalize_each_word(item_name)
    item_id_mapping = read_csv_file(csv_file_items)
    item_id = item_id_mapping.get(item_name.lower())
    if item_id is None:
        return None, None
    descriptions = read_descriptions_csv(csv_file_descriptions)
    key = (item_id, 11, 9)  # Assuming version_group_id 11 and language_id 9
    description = descriptions.get(key, None)
    return description

import math

def calculate_stat(base: int, level: int, iv: int, ev: int, nature_multiplier: float = 1.0) -> int:
    """
    Calculates a Pokémon's stat (other than HP).
    """
    # The formula for stats other than HP
    ev_bonus = math.floor(ev / 4)
    base_and_iv = (2 * base + iv + ev_bonus)
    level_modifier = math.floor(base_and_iv * level / 100)

    stat_total = (level_modifier + 5) * nature_multiplier
    return math.floor(stat_total)