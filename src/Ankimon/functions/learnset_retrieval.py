import json
import random

from ..resources import learnset_path

# === Cache learnset data ===
_learnset_cache = None


def _load_learnset_cache():
    """Load learnset JSON once and cache it in memory"""
    global _learnset_cache
    if _learnset_cache is None:
        try:
            with open(learnset_path, "r", encoding="utf-8") as file:
                _learnset_cache = json.load(file)
        except Exception as e:
            print(f"Error loading learnset cache: {e}")
            _learnset_cache = {}
    return _learnset_cache


def clear_learnset_cache():
    """Clear the learnset cache if data is updated"""
    global _learnset_cache
    _learnset_cache = None


def clean_pokeapi_name(name: str) -> str:
    name_lower = name.lower()

    # Handle female forms (PokéAPI "-female" -> Smogon "f")
    if name_lower.endswith("-female"):
        return name[:-7] + "f"

    suffixes_to_strip = [
        "-standard",
        "-normal",
        "-altered",
        "-land",
        "-red-striped",
        "-male",
        "-ordinary",
        "-aria",
        "-average",
        "-disguised",
        "-amped",
        "-ice",
        "-single-strike",
        "-zero",
        "-curly",
        "-two-segment",
        "-green-plumage",
        "-plant",
        "-mask",
    ]
    for suffix in suffixes_to_strip:
        if name_lower.endswith(suffix):
            return name[: -len(suffix)]

    return name


DEOXYS_EXCLUSIONS = {
    "deoxys": {
        "spikes",
        "superpower",
        "extremespeed",
        "zapcannon",
        "irondefense",
        "amnesia",
        "agility",
        "counter",
        "mirrorcoat",
    },
    "deoxysattack": {
        "spikes",
        "extremespeed",
        "cosmicpower",
        "irondefense",
        "amnesia",
        "agility",
        "recover",
        "counter",
        "mirrorcoat",
        "doubleteam",
    },
    "deoxysdefense": {
        "superpower",
        "extremespeed",
        "cosmicpower",
        "zapcannon",
        "agility",
        "doubleteam",
    },
    "deoxysspeed": {
        "spikes",
        "superpower",
        "cosmicpower",
        "zapcannon",
        "irondefense",
        "amnesia",
        "counter",
        "mirrorcoat",
    },
}


def _get_learnset_moves(pokemon_name, pokemon_level, generation=9):
    """
    Return all moves a Pokémon can know at *pokemon_level* in a single *generation*.
    Falls back to earlier generations if no moves are found.
    Resolves PokéAPI/Smogon name mismatches and handles Mega/Gigantamax/special
    forms by falling back to the base form learnset.
    """
    learnsets = _load_learnset_cache()

    # Try standard key normalization first
    norm_name = (
        pokemon_name.lower()
        .replace("-", "")
        .replace(" ", "")
        .replace("'", "")
        .replace(".", "")
    )
    pokemon_learnset = learnsets.get(norm_name, {}).get("learnset", {})

    # Fallback 1: clean PokéAPI suffix mismatches (e.g. "darmanitan-galar-standard" -> "darmanitangalar")
    if not pokemon_learnset:
        cleaned_name = clean_pokeapi_name(pokemon_name)
        cleaned_norm = (
            cleaned_name.lower()
            .replace("-", "")
            .replace(" ", "")
            .replace("'", "")
            .replace(".", "")
        )
        pokemon_learnset = learnsets.get(cleaned_norm, {}).get("learnset", {})
        if pokemon_learnset:
            # Adopt the resolved key so form-specific exclusions and later
            # fallbacks operate on the name that actually matched.
            norm_name = cleaned_norm

    # Fallback 2: reverse lookup canonical key using pokedex ID index
    if not pokemon_learnset:
        try:
            from .pokedex_functions import (
                search_pokedex,
                search_pokedex_by_id,
                safe_int,
            )

            actual_id = safe_int(search_pokedex(pokemon_name, "actual_id"))
            if actual_id:
                canonical_key = search_pokedex_by_id(actual_id)
                if canonical_key and canonical_key != "Pokémon not found":
                    pokemon_learnset = learnsets.get(canonical_key, {}).get(
                        "learnset", {}
                    )
                    if pokemon_learnset:
                        # Adopt the canonical key for the same reason as above.
                        norm_name = canonical_key
        except Exception:
            pass

    # Fallback 3: base form for Mega/Gigantamax/Special forms if no level-up moves found
    has_lvl_moves = any(
        any("L" in code for code in codes) for codes in pokemon_learnset.values()
    )
    if not pokemon_learnset or not has_lvl_moves:
        # Use pokedex to find the base form via species_id
        try:
            from .pokedex_functions import (
                search_pokedex_by_id,
                search_pokedex,
            )

            # Use search_pokedex to handle normalized names and fallbacks
            species_id = search_pokedex(norm_name, "species_id")

            if species_id and not isinstance(species_id, list):
                base_name = search_pokedex_by_id(species_id)
                if (
                    base_name
                    and base_name != "Pokémon not found"
                    and base_name != norm_name
                ):
                    base_learnset = learnsets.get(base_name, {}).get("learnset", {})
                    pokemon_learnset = {**base_learnset, **pokemon_learnset}
        except Exception:
            pass

    moves = {}

    # Try the requested generation first, then fallback to all earlier generations
    for gen in range(generation, 0, -1):  # Try from requested gen down to gen 1
        moves = {}
        target_generation = str(gen)

        for move, learn_codes in pokemon_learnset.items():
            if norm_name in DEOXYS_EXCLUSIONS and move in DEOXYS_EXCLUSIONS[norm_name]:
                continue
            best = -1
            for learn_code in learn_codes:
                if not learn_code or learn_code[0] != target_generation:
                    continue

                # Parse method: L (level-up) and R (relearn) are the only
                # level-based sources. Every other code (M/E/T/V/D and S) is
                # ignored. In particular 'S' encodes an event-distribution INDEX
                # (e.g. Mewtwo "9S8", Charizard "9S11"), NOT a character level, so
                # parsing its trailing digits as a learn level leaks event-only
                # moves (Psystrike, Flare Blitz) into ordinary low-level movesets.
                method = learn_code[1] if len(learn_code) > 1 else ""
                if method == "L":
                    try:
                        learn_level = int(learn_code[2:])
                    except ValueError:
                        continue
                elif method == "R":
                    learn_level = 1  # Relearn moves can be learned at any level >= 1
                else:
                    continue

                if pokemon_level >= learn_level > best:
                    best = learn_level

            if best >= 0:
                moves[move] = best

        # If we found moves, return them
        if moves:
            break

    return moves


def get_all_pokemon_moves(pokemon_name, pokemon_level, generation=9):
    """Return a list of all move names learnable at or below *pokemon_level*."""
    return list(_get_learnset_moves(pokemon_name, pokemon_level, generation).keys())


def get_random_moves_for_pokemon(pokemon_name, pokemon_level, generation=9):
    """Return up to 4 shuffled move names learnable at or below *pokemon_level*."""
    moves = list(_get_learnset_moves(pokemon_name, pokemon_level, generation).keys())
    random.shuffle(moves)

    return moves[:4]


def get_levelup_move_for_pokemon(pokemon_name, pokemon_level, generation=9):
    """Return a list of moves learned at exactly *pokemon_level* (never None)."""
    all_moves = _get_learnset_moves(pokemon_name, pokemon_level, generation)

    return [
        move for move, learn_level in all_moves.items() if learn_level == pokemon_level
    ]


def get_evolution_moves_for_pokemon(pokemon_name, pokemon_level, generation=9):
    """Return the moves this species learns *on evolution* (never None).

    Showdown encodes those as level 0 ("9L0"), which is not a level any Pokemon
    ever reaches, so they can only be granted by evolving. Keep them out of
    get_levelup_move_for_pokemon: that one runs on every single level-up, and a
    move that is never "learned at this level" would be re-offered forever.
    """
    all_moves = _get_learnset_moves(pokemon_name, pokemon_level, generation)

    return [move for move, learn_level in all_moves.items() if learn_level == 0]
