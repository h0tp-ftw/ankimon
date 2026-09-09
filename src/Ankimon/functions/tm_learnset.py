"""TM learnset lookup and form-name compatibility.

The bundled TM data follows Pokemon Showdown's fully-qualified form keys, while
Ankimon's Pokédex uses a mixture of base-species keys plus ``baseForme`` metadata
and explicit form entries.  Keep that translation here so GUI callers never
need to know either data file's naming quirks.
"""

import json
import re
from typing import Optional

from ..resources import pokemon_tm_learnset_path
from .pokedex_functions import search_pokedex

_tm_learnsets_cache: Optional[dict] = None

# Most form names become a TM suffix simply by removing punctuation (Shield ->
# shield, Normal -> normal, etc.).  These are the few places where the two
# bundled data sets use genuinely different vocabulary.
_FORM_TOKEN_ALIASES = {
    "m": "male",
    "f": "female",
}

_FORM_SUFFIX_ALIASES = {
    ("maushold", "three"): "familyofthree",
    ("maushold", "four"): "familyoffour",
    ("squawkabilly", "green"): "greenplumage",
    ("squawkabilly", "blue"): "blueplumage",
    ("squawkabilly", "yellow"): "yellowplumage",
    ("squawkabilly", "white"): "whiteplumage",
}


def _normalize_key(value) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _pokedex_string(pokemon_name: str, field: str) -> Optional[str]:
    value = search_pokedex(pokemon_name, field)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _form_suffix(species_key: str, form_name: str) -> str:
    form_key = _normalize_key(form_name)
    species_override = _FORM_SUFFIX_ALIASES.get((species_key, form_key))
    if species_override is not None:
        return species_override
    return _FORM_TOKEN_ALIASES.get(form_key, form_key)


def _candidate_keys(internal_name) -> tuple[str, ...]:
    """Return TM-data keys from most-specific to least-specific.

    Base entries with ``baseForme`` (Aegislash, Deoxys, etc.) prefer the
    fully-qualified default form key. Explicit form entries prefer their direct
    Pokédex key, then a metadata-derived key, then the base species as the
    existing UI intended. Duplicate candidates are removed without reordering.
    """
    normalized = _normalize_key(internal_name)
    if not normalized:
        return ()

    base_species = _pokedex_string(internal_name, "baseSpecies")
    forme = _pokedex_string(internal_name, "forme")
    base_forme = _pokedex_string(internal_name, "baseForme")

    candidates = []

    def add(key: str) -> None:
        if key and key not in candidates:
            candidates.append(key)

    if base_species:
        species_key = _normalize_key(base_species)
        add(normalized)
        if forme:
            add(species_key + _form_suffix(species_key, forme))
        add(species_key)
    elif base_forme:
        species_key = normalized
        add(species_key + _form_suffix(species_key, base_forme))
        add(normalized)
    else:
        add(normalized)

    return tuple(candidates)


def _load_tm_learnsets_cache() -> dict:
    global _tm_learnsets_cache
    if _tm_learnsets_cache is None:
        with open(str(pokemon_tm_learnset_path), "r", encoding="utf-8") as json_file:
            loaded = json.load(json_file)
        if not isinstance(loaded, dict):
            raise ValueError("pokemon_tm_learnset.json must contain a JSON object")
        _tm_learnsets_cache = loaded
    return _tm_learnsets_cache


def warm_tm_learnset_cache() -> int:
    """Load bundled TM data off interaction paths and return its entry count.

    A failed/empty warm deliberately leaves the cache cold so a later access can
    retry instead of memoizing a transient startup read failure for the process.
    """
    global _tm_learnsets_cache
    try:
        learnsets = _load_tm_learnsets_cache()
    except Exception:
        _tm_learnsets_cache = None
        raise

    if learnsets:
        return len(learnsets)

    _tm_learnsets_cache = None
    return 0


def get_tm_learnset(internal_name) -> list[str]:
    """Return the TM moves for a Pokédex key/name, including form fallbacks."""
    candidates = _candidate_keys(internal_name)
    if not candidates:
        return []

    learnsets = _load_tm_learnsets_cache()
    for key in candidates:
        if key not in learnsets:
            continue
        moves = learnsets[key]
        if moves is None:
            continue
        if not isinstance(moves, list):
            raise ValueError(f"TM learnset for '{key}' must be a JSON array")
        if moves:
            # Keep the module cache read-only by convention; UI code is free to
            # filter/mutate its result without corrupting subsequent lookups.
            return list(moves)
    return []
