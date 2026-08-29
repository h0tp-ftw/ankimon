from typing import Optional
from ..resources import (
    pokedex_path,
    pokedesc_lang_path,
    pokenames_lang_path,
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
import functools
import json
import math
import random
import csv
from ..pyobj.error_handler import show_warning_with_traceback
from ..pyobj.pokemon_obj import PokemonObject

GROWTH_RATES = {
    1: "slow",
    2: "medium",
    3: "fast",
    4: "medium-slow",
    5: "slow-then-very-fast",
    6: "fast-then-very-slow",
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


def is_valid_base_stats(base_stats) -> bool:
    """Return whether all six base stats are finite, non-negative numbers."""
    if not isinstance(base_stats, dict):
        return False

    for key in ("hp", "atk", "def", "spa", "spd", "spe"):
        value = base_stats.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            return False
    return True


def _load_pokedex_cache():
    """Load pokedex JSON once and cache it in memory"""
    global _pokedex_cache
    if _pokedex_cache is None:
        try:
            with open(str(pokedex_path), "r", encoding="utf-8") as json_file:
                _pokedex_cache = json.load(json_file)

                # Dynamic enrichment for location-based Hisuian forms. The bundled
                # pokedex.json does not carry evoRegion/evoItem/evoMove for these
                # forms, so the region-aware evolution branches below inject them
                # once, when the cache is first built.
                hisuian_forms = [
                    "decidueyehisui",
                    "typhlosionhisui",
                    "samurotthisui",
                    "sliggoohisui",
                    "braviaryhisui",
                    "avalugghisui",
                    "lilliganthisui",
                ]
                for form in hisuian_forms:
                    if form in _pokedex_cache:
                        _pokedex_cache[form]["evoRegion"] = "Hisui"

                # Kleavor
                if "kleavor" in _pokedex_cache:
                    _pokedex_cache["kleavor"]["evoRegion"] = "Hisui"
                    _pokedex_cache["kleavor"]["evoItem"] = "Black Augurite"

                # Ursaluna
                if "ursaluna" in _pokedex_cache:
                    _pokedex_cache["ursaluna"]["evoRegion"] = "Hisui"
                    _pokedex_cache["ursaluna"]["evoType"] = "useItem"
                    _pokedex_cache["ursaluna"]["evoItem"] = "Peat Block"

                # Wyrdeer
                if "wyrdeer" in _pokedex_cache:
                    _pokedex_cache["wyrdeer"]["evoRegion"] = "Hisui"
                    _pokedex_cache["wyrdeer"]["evoType"] = "levelMove"
                    _pokedex_cache["wyrdeer"]["evoMove"] = "Psyshield Bash"
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

            # Pass 1: map every specific form by its actual_id. Missing/invalid
            # ids resolve to None (default=None) so they are skipped rather than
            # collapsing onto key 0.
            for entry_name, attributes in pokedex_data.items():
                actual_id = safe_int(attributes.get("actual_id"), default=None)
                if actual_id is not None:
                    _pokedex_id_index[actual_id] = entry_name

            # Pass 2: map base species_ids. A base form (actual_id == species_id
            # or no actual_id, and no "baseSpecies") must ALWAYS own its species_id
            # mapping so that form variants (megas/regionals carrying a baseSpecies
            # and a 10xxx actual_id) never shadow the base species entry. Parsing
            # with default=None keeps the ``actual_id is None`` base-form test
            # meaningful for rows that omit actual_id.
            for entry_name, attributes in pokedex_data.items():
                species_id = safe_int(attributes.get("species_id"), default=None)
                if species_id is not None:
                    actual_id = safe_int(attributes.get("actual_id"), default=None)
                    has_base_species = attributes.get("baseSpecies") is not None
                    is_base_form = (
                        actual_id is None or actual_id == species_id
                    ) and not has_base_species
                    if is_base_form or species_id not in _pokedex_id_index:
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
_items_cost_cache = None


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
    """Cache pokemon evolution data to avoid repeated file I/O.

    Returns a plain ``list`` of rows on purpose. ``pokemon_evolution.csv`` has
    no unique key: ``evolves_from_species_id`` repeats for branching evolutions
    (Eevee, Tyrogue, Wurmple, ...) and ``evolved_species_id`` repeats for
    species reachable by several methods, so keying a dict on either column
    would silently drop rows. Filter the list instead.
    """
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


_poke_evo_index = None


def _load_poke_evo_index():
    """Index the evolution rows by ``evolved_species_id`` for O(1) lookup.

    Same lazy-module-global shape as :func:`_load_pokedex_id_index`, and built
    FROM :func:`_load_poke_evo_cache`, so the CSV is parsed once and the rows
    exist once — the index holds references, not copies.

    Keys are ``str`` and lookups stringify, reproducing
    :func:`rows_for_key_in_table`'s ``str(row[col]) == str(value)`` exactly.
    Callers pass both ints (:func:`_evolution_row_gender_id_cached`) and the
    strings :func:`pokemon_evolves_from_id` returns, and a zero-padded
    ``"0700"`` must keep matching nothing — so do NOT ``safe_int`` the key.
    """
    global _poke_evo_index
    if _poke_evo_index is None:
        buckets = {}
        for row in _load_poke_evo_cache():
            # `"col" in row` mirrors rows_for_key_in_table's guard exactly.
            if "evolved_species_id" not in row:
                continue
            buckets.setdefault(str(row["evolved_species_id"]), []).append(row)
        _poke_evo_index = {key: tuple(rows) for key, rows in buckets.items()}
    return _poke_evo_index


def evolution_rows_for_evolved_species(evolved_species_id):
    """Every ``pokemon_evolution.csv`` row for one evolved species.

    In-memory equivalent of ``rows_for_key_in_table("evolved_species_id", ...,
    poke_evo_path)`` — same rows, same order, same string comparison — without
    the synchronous re-parse. Use this on the review path (repo rule: no
    synchronous disk I/O mid-review; static data is parsed once at startup) —
    :func:`warm_evolution_caches` is what does that parsing at startup, so this
    only ever reads memory once the boot has run.

    Returns a tuple of the SHARED row dicts; treat them as read-only, the same
    contract :func:`_load_poke_evo_cache` already has.
    """
    return _load_poke_evo_index().get(str(evolved_species_id), ())


def warm_evolution_caches():
    """Parse ``pokemon_evolution.csv`` and build its index, off the review path.

    The loaders above are lazy, which leaves their FIRST caller deciding when
    the ~500-row parse happens — and every production caller of these rows sits
    on the review path: :func:`_evolution_row_gender_id_cached` for the gender
    gate, ``friendship_evolution``'s level and friendship lookups, all reached
    from ``on_review_card``. Left cold, the first level-up of a session opened
    and parsed the CSV mid-review, which is the synchronous disk I/O the repo
    rule forbids ("static data is parsed once at startup").

    Two callers make that rule true. ``startup.run_startup_background_checks``
    warms on the boot thread, before ``services.startup_finished`` opens the
    review gate; ``profile_hooks``' did-open handler warms again, because a
    profile switch runs :func:`clear_pokedex_caches` while the once-per-process
    boot does not run a second time.

    Purely an optimization, so it must not add a failure mode. Moving the first
    read earlier means a file that is momentarily unreadable at boot (an add-on
    update mid-write, a cold network drive) would otherwise let
    :func:`_load_poke_evo_cache` memoize its empty fallback for the whole
    session, silently answering "this species has no evolution rows" to every
    gate. So an empty result puts both globals back to ``None`` and the lazy
    path simply retries on first use, exactly as it does today.

    Returns the number of rows indexed (0 if the CSV could not be read).
    """
    global _poke_evo_cache, _poke_evo_index
    rows = _load_poke_evo_cache()
    if rows:
        _load_poke_evo_index()
        return len(rows)
    _poke_evo_cache = None
    _poke_evo_index = None
    return 0


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


def _load_items_cost_cache():
    """Cache items.csv to avoid repeated file I/O"""
    global _items_cost_cache
    if _items_cost_cache is None:
        try:
            _items_cost_cache = []
            with open(csv_file_items_cost, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    _items_cost_cache.append(row)
        except Exception as e:
            print(f"Error loading items cost CSV cache: {e}")
            _items_cost_cache = []
    return _items_cost_cache


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
    global \
        _pokedex_cache, \
        _poke_species_cache, \
        _pokemon_csv_cache, \
        _stats_csv_cache, \
        _poke_evo_cache, \
        _poke_evo_index, \
        _moves_cache, \
        _pokedex_id_index, \
        _pokemon_names_cache, \
        _pokemon_descriptions_cache
    _pokedex_cache = None
    _poke_species_cache = None
    _pokemon_csv_cache = None
    _stats_csv_cache = None
    _poke_evo_cache = None
    # Reset with its source cache: the index holds references into the rows
    # _load_poke_evo_cache built, so leaving it warm past a clear would keep
    # the pre-clear rows alive (parity with _pokedex_id_index above).
    _poke_evo_index = None
    # ...and so does the memo built ON TOP of that index. _evolution_row_gender_id_cached
    # answers from _poke_evo_index (and, for form ids >= 10000, from
    # _pokedex_cache via search_pokedex_by_id), so a clear that leaves it warm
    # keeps serving pre-clear verdicts for the rest of the session — the gender
    # gate would then contradict the reloaded data it is supposed to read.
    _evolution_row_gender_id_cached.cache_clear()
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
        if not isinstance(pokemon_name, str):
            return []

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

            # 2. Try normalized version (no spaces, hyphens, apostrophes, dots or colons)
            # This handles cases like "Venusaur-Mega" matching "venusaurmega"
            normalized_name = (
                current_name.replace(" ", "")
                .replace("-", "")
                .replace("'", "")
                .replace(".", "")
                .replace(":", "")
            )
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

    if name.lower() == "eternatus-eternamax":
        return "Eternamax"

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
        "-Attack": "Attack ",
        "-Defense": "Defense ",
        "-Speed": "Speed ",
        "-Sky": "Sky ",
        "-Pirouette": "Pirouette ",
        "-Resolute": "Resolute ",
        "-Black": "Black ",
        "-White": "White ",
        "-Crowned": "Crowned ",
        "-Ice": "Ice Rider ",
        "-Shadow": "Shadow Rider ",
        "-Terastal": "Terastal ",
        "-Stellar": "Stellar ",
        "-Dusk-Mane": "Dusk Mane ",
        "-Dawn-Wings": "Dawn Wings ",
        "-Ultra": "Ultra ",
        "-Unbound": "Unbound ",
        "-Original": "Original Color ",
        "-Rapid-Strike": "Rapid Strike ",
        "-10%": "10% ",
        "-Complete": "Complete ",
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
            raw_name = pokedex_data[internal_name].get(
                "name", internal_name.capitalize()
            )
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
    """Return the growth-rate name for a species/form id.

    Alternate-form ids (>= 10000, e.g. mega/regional actual_ids) are resolved to
    their base species via the pokedex before the CSV lookup. Unknown ids fall
    back to ``"medium"`` instead of raising. Rationale (NR-21 fuzz finding): every
    caller assigns the result straight into ``pokemon["growth_rate"]`` (no
    ``or "medium"`` guard), so a raised ``ValueError`` on 10xxx form ids — reached
    ~5% of fuzz iterations now that F22/F38 make form encounters real — was an
    uncaught crash, and returning ``None`` would merely defer the failure into the
    experience/level maths downstream.
    """
    try:
        species_id = int(species_id)
    except (TypeError, ValueError):
        return "medium"

    cache = _load_poke_species_cache()
    row = cache.get(species_id)

    # Alternate-form actual_id -> resolve to the base species_id via the pokedex.
    if row is None and species_id >= 10000:
        internal_name = search_pokedex_by_id(species_id)
        pokedex_data = _load_pokedex_cache()
        base_species_id = safe_int(
            pokedex_data.get(internal_name, {}).get("species_id")
        )
        if base_species_id:
            row = cache.get(base_species_id)

    if row:
        return GROWTH_RATES.get(safe_int(row.get("growth_rate_id"), 2), "medium")
    return "medium"


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
        base_exp = safe_int(row.get("base_experience"), default=None)
        if base_exp is not None:
            return base_exp
        # Alternate-form rows (mega/regional actual_ids >= 10000) carry an empty
        # base_experience field; fall back to the base species' value via the
        # row's own species_id, mirroring get_growth_rate's form handling.
        base_species_id = safe_int(row.get("species_id"), default=None)
        if base_species_id and base_species_id != actual_id:
            base_row = cache.get(base_species_id)
            if base_row:
                base_exp = safe_int(base_row.get("base_experience"), default=None)
                if base_exp is not None:
                    return base_exp
        return 0
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
                    suffix = raw_pokedex_name[raw_pokedex_name.find("-") :]
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
            services.ui.warn(
                f"Move '{move_name}' not found. Returning default move 'tackle'."
            )
            return move

    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"There is an issue in find_details_move for move: {move_name}. Returning to default move 'tackle'.",
        )
        return moves_data.get("tackle") if moves_data else None


def _get_active_region():
    """Return the normalized active-region name, or ``None`` when unset.

    Routed through the settings seam (``services.settings``) — never aqt.mw —
    so the region-aware evolution branches stay seam-clean and headless-safe.
    "No Region" and empty strings normalize to ``None``.
    """
    try:
        settings_obj = services.settings
        if settings_obj is None:
            return None
        region = settings_obj.get("misc.active_region")
        # The scalar settings layer may hand back a non-string (int/None/list);
        # only strings carry a region name, everything else normalizes to None.
        if isinstance(region, str):
            region = region.strip()
            if region in ("", "No Region"):
                return None
            return region
    except Exception:
        pass
    return None


def return_identifier_for_item_id(item_id):
    """Return the string identifier of an item given its numeric id (items.csv)."""
    # Guard None/invalid before coercion so a bad item_id can never accidentally
    # match a row whose own id is missing/invalid (both would coerce to 0).
    target_id = safe_int(item_id, default=None)
    if target_id is None:
        return None
    try:
        with open(csv_file_items_cost, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                if safe_int(row.get("id"), default=None) == target_id:
                    return row.get("identifier")
    except Exception:
        pass
    return None


def _csv_gender_id(gender) -> Optional[int]:
    """Normalize Ankimon's ``"M"/"F"/"Genderless"`` values to CSV gender ids.

    ``pokemon_evolution.csv`` follows the veekun convention: 1 = female,
    2 = male. Returns ``None`` for anything unrecognised (including
    "Genderless") so a gate never matches on junk data.
    """
    if isinstance(gender, str):
        gender = gender.strip().upper()
        # Accept both the internal short form ("M"/"F", what
        # pick_random_gender stores) and long-form variants seen in legacy
        # saves. Anything else (incl. "Genderless") -> None -> no gate.
        if gender in ("M", "MALE"):
            return 2
        if gender in ("F", "FEMALE"):
            return 1
    return None


# ``evolution_trigger_id`` values of the bundled veekun CSV, grouped by the code
# path that consults them. Scoping the gender lookup to the trigger the caller is
# actually evaluating keeps one method's gate from leaking onto another: today
# every gendered species has exactly one row, but the CSV is regenerated from
# upstream data and a second, ungendered path to the same species would
# otherwise silently inherit the gate.
_ITEM_EVO_TRIGGERS = (2, 3)  # 2 = trade-with-held-item, 3 = use-item
_LEVEL_EVO_TRIGGERS = (1,)  # 1 = level-up (plain, timed, and levelMove)


def _evolution_row_gender_id(evolved_species_id, trigger_ids=None) -> Optional[int]:
    """Return the ``gender_id`` gating ``evolved_species_id``, if any.

    Scans the bundled ``pokemon_evolution.csv`` rows whose
    ``evolved_species_id`` matches and returns the first parseable non-blank
    ``gender_id`` (veekun convention: 1 = female, 2 = male). ``None`` means the
    species' evolution rows carry no gender gate. Anything that isn't an integer
    id degrades to ``None`` ("no gate") rather than raising.

    The coercion happens HERE rather than inside the cached helper so that an
    unhashable argument still returns ``None`` instead of dying in
    ``lru_cache``'s key lookup, and so the cache keys on the normalized int
    (``475`` and ``"475"`` share one entry).

    Args:
        evolved_species_id: National Pokédex id of the *evolved* species.
        trigger_ids: Optional tuple of ``evolution_trigger_id`` values to scope
            the scan to (:data:`_ITEM_EVO_TRIGGERS` / :data:`_LEVEL_EVO_TRIGGERS`).
            ``None`` scans every row.
    """
    try:
        species_ref = int(evolved_species_id)
    except (TypeError, ValueError):
        return None
    return _evolution_row_gender_id_cached(species_ref, trigger_ids)


@functools.lru_cache(maxsize=None)
def _evolution_row_gender_id_cached(species_ref: int, trigger_ids) -> Optional[int]:
    """CSV-backed body of :func:`_evolution_row_gender_id` (see it for semantics).

    Form-variant ids (>= 10000, e.g. Wormadam-Sandy 10004) resolve to their base
    species first — the CSV only carries base-species rows (repo tripwire: "IDs
    >= 10000 must be resolved to their base species via check_id_ok()").

    ``lru_cache``d because this sits on the review path: a level-up runs it for
    every eligible evolution candidate. The rows now come from the startup-built
    in-memory index (:func:`evolution_rows_for_evolved_species`) rather than a
    fresh ~500-row CSV parse per cache miss, so the cache saves the scan rather
    than the file I/O (coding guideline: no synchronous disk I/O mid-review —
    static data is parsed once at startup).
    ``poke_evo_path`` points into the shipped, read-only ``data_files`` dir, so
    the CSV really is immutable for the process and the memoized
    ``Optional[int]`` is safe to share. The key space is bounded by
    species x trigger-scope (a few thousand small ints at most), so ``maxsize=None``
    can't grow without bound. Same caveat as the other CSV caches here: a
    transient read failure would be memoized for the session.
    """
    if species_ref >= 10000:
        # Form variant: the CSV keys on the base species id only.
        form_name = search_pokedex_by_id(species_ref)
        base_species = (
            safe_int(search_pokedex(form_name, "species_id"))
            if (form_name and form_name != "Pokémon not found")
            else 0
        )
        if base_species > 0:
            species_ref = base_species
    wanted_triggers = {str(t) for t in trigger_ids} if trigger_ids is not None else None
    for csv_row in evolution_rows_for_evolved_species(species_ref):
        if (
            wanted_triggers is not None
            and (csv_row.get("evolution_trigger_id") or "").strip()
            not in wanted_triggers
        ):
            continue
        raw_gender = (csv_row.get("gender_id") or "").strip()
        if not raw_gender:
            continue
        try:
            return int(raw_gender)
        except (TypeError, ValueError):
            continue
    return None


def gender_allows_evolution(evolved_species_id, caller_gender_id, trigger_ids) -> bool:
    """Return whether a gender may take one evolution, per the CSV gate.

    The single place the gate's semantics live, so the three call sites that
    apply it to a ``pokedex.json`` entry (:func:`check_evolution_by_item`,
    :func:`check_evolution_for_pokemon` and the web bag's inline eligibility
    loop) and the one that applies it to a bare id
    (``friendship_evolution._level_gender_gate``) cannot drift apart.

    ``caller_gender_id is None`` — no gender, or one :func:`_csv_gender_id`
    doesn't recognise, "Genderless" included — fails **open**, keeping the
    historical no-check behavior for callers and saves without gender data. A
    species whose rows carry no ``gender_id`` is likewise unrestricted.

    Args:
        evolved_species_id: National Pokédex id of the *evolved* species.
        caller_gender_id: The Pokémon's gender as a CSV id (1 = female,
            2 = male), or ``None`` for "unknown — don't gate".
        trigger_ids: Trigger scope to consult (:data:`_ITEM_EVO_TRIGGERS` /
            :data:`_LEVEL_EVO_TRIGGERS`), so one method's gate never leaks
            onto another.
    """
    if caller_gender_id is None:
        return True
    required_gender_id = _evolution_row_gender_id(evolved_species_id, trigger_ids)
    return required_gender_id is None or required_gender_id == caller_gender_id


def evolution_target_species_id(target_data) -> int:
    """National id of the evolved species a ``pokedex.json`` entry describes.

    ``actual_id`` carries it for base species and forms alike; ``species_id``
    is the fallback for entries that predate it. ``0`` when neither resolves.
    """
    if not isinstance(target_data, dict):
        return 0
    return safe_int(target_data.get("actual_id") or target_data.get("species_id"))


def evolution_gender_allows(target_data, gender, trigger_ids) -> bool:
    """:func:`gender_allows_evolution` for a ``pokedex.json`` evolution entry.

    Takes the raw Ankimon gender string (``"M"``/``"F"``/``"Genderless"``/
    ``None``) and the evolved species' own pokedex entry, so callers holding a
    ``target_data`` dict apply the gate identically without restating the
    id-extraction and the comparison.
    """
    return gender_allows_evolution(
        evolution_target_species_id(target_data),
        _csv_gender_id(gender),
        trigger_ids,
    )


def _pokedex_form_gender(species_ref) -> Optional[str]:
    """Return ``"M"``/``"F"`` when pokedex.json marks a species/form single-gender.

    This is the ``gender`` field on the *evolved* entry (e.g. ``meowstic`` -> M,
    ``meowsticf`` -> F). ``None`` for anything unlabelled or unresolvable.
    """
    try:
        name = search_pokedex_by_id(species_ref)
    except Exception:
        return None
    if not name or name == "Pokémon not found":
        return None
    entry = (_load_pokedex_cache() or {}).get(name)
    if not isinstance(entry, dict):
        return None
    form_gender = entry.get("gender")
    return form_gender if form_gender in ("M", "F") else None


def filter_gender_split_forms(evo_ids, gender):
    """Narrow sibling evolution targets to the ones this gender can reach.

    A SECOND gender source, needed because ``pokemon_evolution.csv`` cannot see
    every split. veekun models Espurr -> Meowstic and Lechonk -> Oinkologne as
    two *forms of one species* rather than as two gendered evolutions, so those
    rows carry no ``gender_id`` and :func:`_evolution_row_gender_id` answers
    "no gate" for both candidates — leaving the lowest-``evo_id`` tie-break to
    hand every Espurr the MALE Meowstic and every Lechonk the MALE Oinkologne,
    which is the exact failure the CSV gate fixes for Burmy. pokedex.json does
    carry the split, on the evolved forms' own ``gender`` field.

    Deliberately applied ONLY as a tie-break between siblings whose labels
    disagree. A lone target carrying a ``gender`` (Bounsweet -> Steenee, both
    female-only) is stating a species property, not an evolution requirement,
    and must never block an evolution — every such line in the bundled data has
    a single-gender pre-evolution anyway, so gating on it could only ever hurt a
    save whose stored gender disagrees with its own species. Unlabelled siblings
    are kept, and a gender matching no sibling falls back to the full set, so
    this can never empty the candidate list.

    Args:
        evo_ids: Candidate evolved-species ids (form ids >= 10000 included).
        gender: The Pokémon's gender (``"M"``/``"F"``/...); unrecognised values
            disable the filter.

    Returns:
        The surviving ids, in the order given.
    """
    ids = list(evo_ids)
    caller_gender_id = _csv_gender_id(gender)
    if caller_gender_id is None or len(ids) < 2:
        return ids
    labels = {evo_id: _pokedex_form_gender(evo_id) for evo_id in ids}
    if len({label for label in labels.values() if label}) < 2:
        # Siblings agree (or none is labelled): a species property, not a split.
        return ids
    wanted = "M" if caller_gender_id == 2 else "F"
    matching = [evo_id for evo_id in ids if labels.get(evo_id) in (wanted, None)]
    return matching or ids


def check_evolution_by_item(pokemon_id, item_id, gender=None):
    """
    Check if a Pokémon evolves using a specific item.

    Region-aware and driven exclusively by pokedex.json form data: a candidate
    evolution qualifies when its ``evoType`` is ``"useItem"`` **or** ``"trade"``
    and its ``evoItem`` matches the supplied item. ``trade`` is accepted because
    Ankimon has no trading, so the 16 trade-with-held-item species (Onix->Steelix
    via Metal Coat, Poliwhirl->Politoed via King's Rock, Seadra->Kingdra via
    Dragon Scale, ...) are evolved by applying the held item directly. Regional
    forms (``evoRegion``) are preferred when the active region matches; a plain
    form is suppressed only when a matching regional sibling exists for the
    current region.

    Args:
        pokemon_id (int): The ID of the (pre-evolution) Pokémon.
        item_id (int): The ID of the item.
        gender (str | None): The Pokémon's gender ("M"/"F"/"Genderless"). When
            given, gender-gated evolutions from ``pokemon_evolution.csv``
            (Gallade needs a male Kirlia, Froslass a female Snorunt/Kirlia)
            only match when the gender agrees; ``None`` keeps the historical
            no-gender-check behavior for callers without gender data.

    Returns:
        int | None: The evolved species id if the Pokémon evolves with the given
        item, otherwise ``None``.
    """
    try:
        pokedex_data = _load_pokedex_cache()
        internal_name = search_pokedex_by_id(pokemon_id)

        if internal_name in pokedex_data:
            details = pokedex_data[internal_name]
            evo_list = details.get("evos")

            if evo_list:
                item_name = return_identifier_for_item_id(item_id)
                if item_name:
                    active_region = _get_active_region()
                    eligible_evos = []

                    for target_evo_name in evo_list:
                        normalized_target = (
                            target_evo_name.lower()
                            .replace(" ", "")
                            .replace("-", "")
                            .replace("'", "")
                            .replace(".", "")
                            .replace(":", "")
                        )
                        target_data = pokedex_data.get(
                            normalized_target
                        ) or pokedex_data.get(target_evo_name.lower())

                        if target_data and target_data.get("evoType") in (
                            "useItem",
                            "trade",
                        ):
                            # Gender gate from the bundled CSV (veekun ids:
                            # 1 = female, 2 = male). Gallade (475) requires a
                            # male Kirlia, Froslass (478) a female Snorunt.
                            # Only enforced when the caller supplies a
                            # RECOGNISED gender ("M"/"F"); None/unrecognised
                            # keeps the historical no-check behavior so
                            # callers without gender data are unaffected.
                            if not evolution_gender_allows(
                                target_data, gender, _ITEM_EVO_TRIGGERS
                            ):
                                continue

                            # Normalize both sides by stripping spaces, hyphens and
                            # apostrophes so pokedex.json display names (e.g.
                            # "King's Rock") match items.csv identifiers (e.g.
                            # "kings-rock"), which drop the apostrophe.
                            required_item = (
                                (target_data.get("evoItem") or "")
                                .lower()
                                .replace(" ", "")
                                .replace("-", "")
                                .replace("'", "")
                            )
                            normalized_item_name = (
                                (item_name or "")
                                .lower()
                                .replace(" ", "")
                                .replace("-", "")
                                .replace("'", "")
                            )
                            if required_item == normalized_item_name:
                                target_region = target_data.get("evoRegion")

                                if target_region:
                                    if (
                                        active_region
                                        and active_region.lower()
                                        == target_region.lower()
                                    ):
                                        eligible_evos.append(target_data)
                                else:
                                    # A plain form is allowed unless a regional
                                    # sibling matches the active region + method.
                                    has_matching_regional_sibling = False
                                    for sibling_name in evo_list:
                                        sib_norm = (
                                            sibling_name.lower()
                                            .replace(" ", "")
                                            .replace("-", "")
                                            .replace("'", "")
                                            .replace(".", "")
                                            .replace(":", "")
                                        )
                                        sib_data = pokedex_data.get(
                                            sib_norm
                                        ) or pokedex_data.get(sibling_name.lower())
                                        if (
                                            sib_data
                                            and sib_data.get("evoRegion")
                                            and active_region
                                            and sib_data.get("evoRegion").lower()
                                            == active_region.lower()
                                        ):
                                            if (
                                                sib_data.get("evoType")
                                                == target_data.get("evoType")
                                                and (
                                                    sib_data.get("evoItem") or ""
                                                ).lower()
                                                == (
                                                    target_data.get("evoItem") or ""
                                                ).lower()
                                            ):
                                                has_matching_regional_sibling = True
                                                break
                                    if not has_matching_regional_sibling:
                                        eligible_evos.append(target_data)

                    if eligible_evos:
                        eligible_evos.sort(key=lambda x: 0 if x.get("evoRegion") else 1)
                        target_data = eligible_evos[0]
                        evo_id = safe_int(
                            target_data.get("actual_id")
                            or target_data.get("species_id")
                        )
                        if evo_id > 0:
                            return evo_id
        return None
    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"Error checking item evolution for Pokémon ID {pokemon_id}",
        )
        return None


def get_time_of_day():
    """Return the current time of day as 'day' or 'night' self-contained without friendship_evolution dependency."""
    try:
        from datetime import datetime, timedelta, timezone

        settings_obj = services.settings  # registry-backed; no singletons/aqt import

        # Check if settings_obj exists and is fully initialized
        if settings_obj is None:
            return "day"

        # Use timezone_auto (bool, default True) to decide between the local clock
        # and a fixed offset. timezone_offset is a float (default 0.0) and must
        # never be compared to the string "auto" — this matches main's settings
        # schema ("evolution.timezone_auto"/"evolution.timezone_offset").
        if settings_obj.get("evolution.timezone_auto", True):
            moment = datetime.now()
        else:
            try:
                offset_hours = float(settings_obj.get("evolution.timezone_offset", 0.0))
                offset_hours = max(
                    -14.0, min(14.0, offset_hours)
                )  # clamp to valid UTC range
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
        night_start = coerce_hour(
            settings_obj.get("evolution.night_start_hour", 18), 18
        )

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
    individual_id,
    pokemon_id,
    level,
    evo_window,
    everstone=False,
    evolution_rejected=False,
    current_attacks=None,
    gender=None,
):
    """
    Check if a Pokémon evolves by a level (or move-based level-up) condition.

    Region-aware and driven exclusively by pokedex.json form data. Handles plain
    level-ups, day/night-gated level-ups, move-based level-ups (``evoType ==
    "levelMove"``), and defeat-count evolutions (``evoCondition ==
    "minimumdefeated"``). Regional forms (``evoRegion``) are preferred when the
    active region matches; a plain form is suppressed only when a matching
    regional sibling exists for the current region.

    Args:
        individual_id (str): The ID of the individual Pokémon.
        pokemon_id (int): The ID of the Pokémon species/form.
        level (int): The current level of the Pokémon.
        evo_window (object): The evolution window used to prompt the evolution.
        everstone (bool): Whether the Pokémon is holding an Everstone.
        evolution_rejected (bool): Whether the user previously rejected this
            evolution. When True the automatic prompt is suppressed.
        current_attacks (list | None): The Pokémon's just-updated moveset, when
            the caller has fresher data than the database (e.g. immediately
            after new level-up moves were learned). ``None`` falls back to the
            stored moveset from the DB seam. Move-based evolutions evaluate
            against this list so learning the required move on this very level
            triggers the offer on that same event instead of one level late.
        gender (str | None): The Pokémon's gender (``"M"``/``"F"``/...). When a
            recognised gender is supplied, CSV ``gender_id`` gates are enforced:
            Vespiquen needs a female Combee, Salazzle a female Salandit,
            Wormadam a female Burmy and Mothim a male one. ``None`` keeps the
            historical no-check behavior for callers without gender data.

    Returns:
        int | None: The evolution ID if an evolution is found, or None otherwise.
    """
    from .. import utils

    # Suppress auto-evolution prompts during bulk PC-box resolves (utils flag set
    # by the mobile/bulk paths), when Everstone-held, or after user rejection.
    if getattr(utils, "in_bulk_resolve", False) or evolution_rejected or everstone:
        return None

    # A dead/never-built evolution window (F31 lazy singletons can leave
    # services.evo_window None) means there's nowhere to prompt the offer; skip
    # it so the caller's defeat-persistence path is never aborted by an
    # AttributeError from evo_window.ask_pokemon_evo(...). The offer re-fires on
    # the next defeat once a live window exists.
    if evo_window is None:
        return None

    try:
        current_time = get_time_of_day()

        # Form-aware evolution from pokedex.json (covers base + regional forms).
        pokedex_data = _load_pokedex_cache()
        internal_name = search_pokedex_by_id(pokemon_id)

        if internal_name in pokedex_data:
            details = pokedex_data[internal_name]
            evo_list = details.get("evos")

            if evo_list:
                active_region = _get_active_region()
                eligible_evos = []

                for target_evo_name in evo_list:
                    normalized_target = (
                        target_evo_name.lower()
                        .replace(" ", "")
                        .replace("-", "")
                        .replace("'", "")
                        .replace(".", "")
                        .replace(":", "")
                    )
                    target_data = pokedex_data.get(
                        normalized_target
                    ) or pokedex_data.get(target_evo_name.lower())

                    if target_data:
                        # In Smogon-style pokedex.json, evoLevel is on the evolved species.
                        condition = (target_data.get("evoCondition") or "").lower()

                        if condition == "minimumdefeated":
                            evo_defeated = safe_int(target_data.get("evoDefeated"))
                            if evo_defeated > 0:
                                pokemon_data = services.db.get_pokemon(individual_id)
                                if pokemon_data:
                                    pokemon_obj = PokemonObject.from_dict(pokemon_data)
                                    if (
                                        pokemon_obj.pokemon_defeated or 0
                                    ) >= evo_defeated:
                                        evo_id = safe_int(
                                            target_data.get("actual_id")
                                            or target_data.get("species_id")
                                        )
                                        if evo_id > 0:
                                            evo_window.ask_pokemon_evo(
                                                individual_id, pokemon_id, evo_id
                                            )
                                            return evo_id
                            continue

                        min_level = safe_int(target_data.get("evoLevel"))
                        is_level_evo = (
                            min_level > 0
                            and level >= min_level
                            and target_data.get("evoType")
                            not in ("useItem", "trade", "levelFriendship")
                        )

                        # Move-based level-up evolution (e.g. Wyrdeer, Mr. Mime-Galar).
                        if target_data.get("evoType") == "levelMove":
                            required_move = target_data.get("evoMove")
                            knows_move = False

                            # Prefer the caller's just-updated moveset when
                            # provided (the DB may still hold the pre-level-up
                            # moveset at this point); fall back to the stored
                            # one otherwise.
                            if current_attacks is not None:
                                p_attacks = current_attacks
                            else:
                                p_attacks = None
                                try:
                                    pkmn_data = services.db.get_pokemon(individual_id)
                                except Exception:
                                    pkmn_data = None
                                if pkmn_data and "attacks" in pkmn_data:
                                    p_attacks = pkmn_data["attacks"]
                            if (
                                required_move
                                and p_attacks
                                and any(
                                    isinstance(a, str)
                                    and a.lower().replace(" ", "").replace("-", "")
                                    == required_move.lower()
                                    .replace(" ", "")
                                    .replace("-", "")
                                    for a in p_attacks
                                )
                            ):
                                knows_move = True
                            # Fail closed when the moveset can't be fetched:
                            # a move-gated evolution must never fire on
                            # unconfirmed data (mirrors friendship_evolution.py's
                            # conservative levelMove handling). knows_move stays
                            # False so the evolution simply doesn't trigger.

                            if knows_move:
                                is_level_evo = True

                        if is_level_evo:
                            # Gender gate from the bundled CSV (veekun ids:
                            # 1 = female, 2 = male): Vespiquen needs a female
                            # Combee, Salazzle a female Salandit, and Burmy
                            # splits into Wormadam (female) / Mothim (male).
                            # Only enforced when the caller supplies a
                            # RECOGNISED gender ("M"/"F"); None/unrecognised
                            # keeps the historical no-check behavior so
                            # callers without gender data are unaffected.
                            if not evolution_gender_allows(
                                target_data, gender, _LEVEL_EVO_TRIGGERS
                            ):
                                continue

                            time_of_day = None
                            if "day" in condition:
                                time_of_day = "day"
                            elif "night" in condition:
                                time_of_day = "night"

                            if time_of_day is None or time_of_day == current_time:
                                target_region = target_data.get("evoRegion")

                                if target_region:
                                    if (
                                        active_region
                                        and active_region.lower()
                                        == target_region.lower()
                                    ):
                                        eligible_evos.append(target_data)
                                else:
                                    has_matching_regional_sibling = False
                                    for sibling_name in evo_list:
                                        sib_norm = (
                                            sibling_name.lower()
                                            .replace(" ", "")
                                            .replace("-", "")
                                            .replace("'", "")
                                            .replace(".", "")
                                            .replace(":", "")
                                        )
                                        sib_data = pokedex_data.get(
                                            sib_norm
                                        ) or pokedex_data.get(sibling_name.lower())
                                        if (
                                            sib_data
                                            and sib_data.get("evoRegion")
                                            and active_region
                                            and sib_data.get("evoRegion").lower()
                                            == active_region.lower()
                                        ):
                                            if sib_data.get("evoType") not in (
                                                "useItem",
                                                "trade",
                                                "levelFriendship",
                                            ):
                                                has_matching_regional_sibling = True
                                                break
                                    if not has_matching_regional_sibling:
                                        eligible_evos.append(target_data)

                if eligible_evos:
                    # Second gender source for splits the CSV cannot express as
                    # gendered rows (Espurr -> Meowstic/Meowstic-F, Lechonk ->
                    # Oinkologne/Oinkologne-F are FORMS in the veekun schema).
                    # Narrows siblings only; never empties the list.
                    by_id = {}
                    for candidate in eligible_evos:
                        by_id.setdefault(
                            evolution_target_species_id(candidate), candidate
                        )
                    kept = set(filter_gender_split_forms(list(by_id), gender))
                    if kept:
                        eligible_evos = [
                            candidate
                            for candidate in eligible_evos
                            if evolution_target_species_id(candidate) in kept
                        ]

                    eligible_evos.sort(key=lambda x: 0 if x.get("evoRegion") else 1)
                    target_data = eligible_evos[0]
                    evo_id = safe_int(
                        target_data.get("actual_id") or target_data.get("species_id")
                    )
                    if evo_id > 0:
                        evo_window.ask_pokemon_evo(individual_id, pokemon_id, evo_id)
                        return evo_id

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
    evolves_from_ids = []  # List to hold the ids of Pokémon that evolve into the given ID
    try:
        poke_species_data = _load_poke_species_cache()  # Use cache instead of file I/O
        for row in poke_species_data.values():
            # Safely check if 'evolves_from_species_id' exists and is a valid number
            evolves_from_species_id = row.get("evolves_from_species_id", None)
            if evolves_from_species_id:
                try:
                    # Convert to integer and compare
                    if safe_int(evolves_from_species_id) == safe_int(pokemon_id):
                        evolves_from_ids.append(row["id"])
                except ValueError:
                    # Handle the case where 'evolves_from_species_id' is not a valid integer
                    continue

        # Return the list of evolves_from_species_id or an empty list if no matches
        # if evolves_from_ids:
        # services.ui.warn(f"Evolves from IDs: {evolves_from_ids}")
        # else:
        #    services.ui.warn(f"No evolutions found for Pokémon ID '{pokemon_id}'")

        return evolves_from_ids
    except Exception as e:
        # Use a more specific error message
        show_warning_with_traceback(
            exception=e,
            message=f"Error in pokemon_evolves_from_id function: {e} with pokemon_id {pokemon_id}",
        )
        return []


def get_pokemon_evolution_data(pokemon_id):
    """Returns the evolution data for a given Pokémon ID by matching evolved_species_id."""
    evolution_data = None  # Initialize variable to hold evolution data

    try:
        poke_evo_data = _load_poke_evo_cache()  # Use cache instead of file I/O
        # Search for the given Pokémon ID in the evolved_species_id column
        for row in poke_evo_data:
            try:
                # Compare the evolved_species_id with the given pokemon_id (as an integer)
                if safe_int(row.get("evolved_species_id")) == safe_int(pokemon_id):
                    # If a match is found, store the evolution data
                    evolution_data = row
                    break  # No need to continue once we find a match
            except ValueError:
                # Handle case where evolved_species_id is not a valid integer
                continue

        # Check if evolution data was found, log a message if not
        if not evolution_data:
            # services.ui.warn(f"No evolution data found for Pokémon ID '{pokemon_id}'")
            pass
    except Exception as e:
        show_warning_with_traceback(
            parent=mw,
            exception=e,
            message=f"Error retrieving evolution data for Pokémon ID {pokemon_id}",
        )
    return evolution_data


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
        services.ui.warn(
            f"Name for Pokemon with ID '{pokemon_id}' not found in the CSV."
        )
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
