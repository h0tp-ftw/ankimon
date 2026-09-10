import base64
import csv
import functools
import json
import math
from typing import Optional

from .resources import csv_file_items, csv_file_descriptions, effectiveness_chart_file_path

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
    """Scale a pixmap to ``max_width``, keeping its aspect ratio.

    A pixmap whose image failed to load is null and reports a width of 0, so
    the aspect-ratio maths would raise "integer division or modulo by zero" and
    take the whole window down (issue #101). Nothing sensible can be scaled
    from a null pixmap, so hand it back untouched — Qt draws it as a no-op.
    """
    original_width = pixmap.width()
    original_height = pixmap.height()
    if original_width <= 0:
        return pixmap
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

def calculate_cpm(level: int) -> float:
    """CP Multiplier — exponential (saturating) function of level.

    Models the Pokemon GO idea of a level-scaling multiplier that grows
    quickly at low levels and then keeps rewarding levelling across the
    whole playable range, tapering only gradually. The curve asymptotes
    toward ``3.5`` as level grows — reaching about ``2.42`` at level 100,
    roughly 69% of that asymptote, so levels past the (optionally
    disabled) level-100 cap still raise CP — and is smooth and defined
    for any Anki level. The scale is deliberately larger than Pokemon
    GO's real CPM cap (~``0.84``) to tune Ankimon's CP economy.

    Note that ``CP`` scales as ``CPM ** 2``, and that ``cp`` is persisted
    per-Pokemon, so retuning this function strands old-scale values in the
    database — see :func:`pokemon_cp_is_stale`.
    """
    return 3.5 * (1 - math.exp(-max(level, 1) / 85))


def pokemon_go_raw_stats(base_stats: dict, iv: dict, ev: dict):
    """Derive Pokemon GO-style Attack/Defense/Stamina from main-series stats.

    Uses *raw* stat values (base + IV + floor(EV/4)) — **not** the
    level-scaled values from ``calc_stat`` — so that ``CPM`` is the sole
    level multiplier in the CP formula.  Physical and special variants are
    averaged to adapt the 6-stat model to Pokemon GO's 3-stat model.

    Returns ``(attack, defense, stamina)`` as floats.
    """
    def _raw(key):
        return max(1, int(base_stats.get(key, 1)) + max(0, int(iv.get(key, 0))) + max(0, int(ev.get(key, 0))) // 4)

    attack = (_raw("atk") + _raw("spa")) / 2
    defense = (_raw("def") + _raw("spd")) / 2
    stamina = _raw("hp")
    return attack, defense, stamina


def calculate_pokemon_go_cp(
    attack: float, defense: float, stamina: float, level: int
) -> int:
    """Pokemon GO style Combat Power.

    ``CP = floor(Attack × √Defense × √Stamina × CPM² / 10)``

    ``Attack``, ``Defense``, and ``Stamina`` should be *raw* values
    (base + IV + EV/4, **not** level-scaled ``calc_stat`` output).
    ``CPM`` provides all level scaling.  CP is clamped to a minimum of 10
    so sort keys remain well-defined for under-leveled or stub Pokemon.
    """
    cpm = calculate_cpm(level)
    cp = math.floor(
        attack * math.sqrt(max(defense, 1)) * math.sqrt(max(stamina, 1)) * (cpm ** 2) / 10
    )
    return max(10, int(cp))


@functools.lru_cache(maxsize=1)
def _load_type_chart() -> dict:
    """Load ``eff_chart.json`` once and cache the result.

    Returns a nested dict ``{AttackingType: {DefendingType: multiplier}}``
    where keys are capitalised type names (e.g. ``"Fire"``, ``"Water"``).
    Returns an empty dict if the file cannot be read so callers fall back
    to a neutral multiplier.
    """
    try:
        with open(effectiveness_chart_file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def type_compatibility_multiplier(attacker_types, defender_types) -> float:
    """Return a simplified Present Power multiplier for a matchup.

    Uses the maximum per-pair effectiveness from ``eff_chart.json``:

    - ``== 0`` → immune → ``0.2``
    - ``> 1``  → super-effective → ``1.5``
    - ``< 1``  → not-very-effective → ``0.8``
    - otherwise → ``1.0``

    Type arguments may be either strings or iterables of strings. Unknown
    types are silently ignored; if no valid pairings exist, a neutral
    ``1.0`` is returned.
    """
    chart = _load_type_chart()
    if not chart or not attacker_types or not defender_types:
        return 1.0

    if isinstance(attacker_types, str):
        attacker_types = [attacker_types]
    if isinstance(defender_types, str):
        defender_types = [defender_types]

    best: Optional[float] = None
    for atk in attacker_types:
        atk_row = chart.get(str(atk).capitalize())
        if not atk_row:
            continue
        for dfn in defender_types:
            mult = atk_row.get(str(dfn).capitalize())
            if mult is None:
                continue
            if best is None or mult > best:
                best = mult

    if best is None:
        return 1.0
    if best == 0:
        return 0.2
    elif best > 1:
        return 1.5
    elif best < 1:
        return 0.8
    return 1.0


def calculate_present_power(
    cp: int,
    current_hp: int,
    max_hp: int,
    compatibility_multiplier: float = 1.0,
    atk_stage: int = 0,
    spa_stage: int = 0,
) -> int:
    """Present Power = CP × (current HP ÷ max HP) × type multiplier × avg(atk, spa) stage.

    A live threat indicator on the same scale as CP: at full HP with a
    neutral matchup and no boosts, BP equals CP, so the two Pokemon's BP
    readouts are directly comparable. Health is a *fraction* rather than
    an absolute HP count — multiplying by raw HP double-counted stamina
    (already inside CP) and blew BP up to a scale where the numbers meant
    nothing. Atk and Spa stages are averaged to match the CP formula's
    averaging of physical and special. Drops below neutral shrink BP;
    boosts grow it. Rounded down to an int.
    """
    cp = max(int(cp if cp is not None else 0), 0)
    current_hp = max(int(current_hp if current_hp is not None else 0), 0)
    max_hp = max(int(max_hp if max_hp is not None else 0), 0)
    compat = float(compatibility_multiplier if compatibility_multiplier is not None else 1.0)

    if max_hp <= 0:
        # Unknown max HP (stub/corrupt data): treat a living Pokemon as
        # unhurt rather than zeroing its power.
        hp_fraction = 1.0 if current_hp > 0 else 0.0
    else:
        hp_fraction = min(current_hp / max_hp, 1.0)

    def _stage_mult(stage) -> float:
        if stage is None:
            return 1.0
        try:
            val = get_multiplier_stats(int(stage))
        except (TypeError, ValueError):
            return 1.0
        return float(val) if isinstance(val, (int, float)) else 1.0

    stage_factor = (_stage_mult(atk_stage) + _stage_mult(spa_stage)) / 2
    return int(math.floor(cp * hp_fraction * compat * stage_factor))


def format_compact_number(value) -> str:
    """Format a stat for the battle window's fixed-width pixel-art boxes.

    Values under 10,000 keep the full thousands-separated form ("2,564");
    anything larger is compacted to a bounded width ("54.1K", "367K",
    "4.3M" — at most ~5 glyphs) so Battle Power — a product of CP × HP
    that routinely reaches 6-7 digits — can never overflow its box.
    Non-numeric or negative input renders as "0".
    """
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):  # OverflowError: float inf
        n = 0
    n = max(n, 0)
    if n < 10_000:
        return f"{n:,}"
    for divisor, suffix in ((10**9, "B"), (10**6, "M"), (10**3, "K")):
        scaled = n / divisor
        # Enter this tier once the next-smaller tier's display would round
        # past three digits: 999,400 stays "999K", 999,500 becomes "1M".
        if scaled < 0.9995:
            continue
        text = f"{scaled:.1f}" if scaled < 99.95 else f"{scaled:.0f}"
        if text.endswith(".0"):
            text = text[:-2]
        return text + suffix
    return f"{n:,}"  # unreachable for n >= 10,000, defensive fallback


def cp_breakdown_tooltip(pokemon_dict: dict) -> str:
    """Human-readable CP breakdown for Qt tooltips.

    Shows the formula, this Pokemon's substituted values, and the CP
    projected at level 100 (useful for planning evolutions/training).
    Accepts either the caught-Pokemon dict shape ("stats" = base_stats)
    or the to_dict shape ("base_stats" = bases).
    """
    base_stats = pokemon_dict.get("base_stats") or pokemon_dict.get("stats") or {}
    iv = pokemon_dict.get("iv") or {}
    ev = pokemon_dict.get("ev") or {}
    level = int(pokemon_dict.get("level", 1) or 1)
    attack, defense, stamina = pokemon_go_raw_stats(base_stats, iv, ev)
    cpm = calculate_cpm(level)
    cp_at_100 = calculate_pokemon_go_cp(attack, defense, stamina, 100)
    return (
        "CP = floor(Atk × √Def × √Sta × CPM² ÷ 10)\n"
        f"    = floor({attack:.0f} × √{defense:.0f} × √{stamina:.0f}"
        f" × {cpm:.4f}² ÷ 10)\n"
        f"CP at Level 100: {cp_at_100:,}"
    )


def calculate_cp_from_dict(pokemon_dict):
    """Calculate Combat Power from a raw Pokemon dict using the
    Pokemon GO style formula.

    Handles both data formats:
    - Caught Pokemon: "stats" field contains base_stats
    - to_dict() Pokemon: "base_stats" field contains base_stats
    """
    if "base_stats" in pokemon_dict:
        base_stats = pokemon_dict["base_stats"]
    else:
        base_stats = pokemon_dict.get("stats", {})

    # Coerce level the same way cp_breakdown_tooltip does: rows restored
    # from JSON can carry it as a string, and calculate_cpm's max(level, 1)
    # raises TypeError when comparing str/None against int. A level that is
    # not a number at all is meaningless rather than merely mistyped, so it
    # falls back to 1 instead of taking down every CP read on the row.
    try:
        level = int(pokemon_dict.get("level", 1) or 1)
    except (TypeError, ValueError):
        level = 1
    iv = pokemon_dict.get("iv") or {}
    ev = pokemon_dict.get("ev") or {}

    attack, defense, stamina = pokemon_go_raw_stats(base_stats, iv, ev)
    return calculate_pokemon_go_cp(attack, defense, stamina, level)


def pokemon_cp_is_stale(pokemon_dict: dict) -> bool:
    """Whether a stored Pokemon's persisted ``cp`` disagrees with the formula.

    ``cp`` is derived data that is *also* persisted (see
    ``encounter_functions`` at catch time), so retuning :func:`calculate_cpm`
    leaves old-scale values in the database until something rewrites them.
    Migration passes use this to decide whether a repair is needed.

    Never raises: a row whose CP cannot be computed is reported as stale so
    the caller's repair path takes over rather than the check exploding.
    """
    if not isinstance(pokemon_dict, dict):
        return False
    try:
        return pokemon_dict.get("cp") != calculate_cp_from_dict(pokemon_dict)
    except Exception:
        return True

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