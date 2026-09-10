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
    ("raticate", "alolatotem"): "totemalola",
    ("marowak", "alolatotem"): "totem",
    ("pikachu", "original"): "originalcap",
    ("pikachu", "hoenn"): "hoenncap",
    ("pikachu", "sinnoh"): "sinnohcap",
    ("pikachu", "unova"): "unovacap",
    ("pikachu", "kalos"): "kaloscap",
    ("pikachu", "alola"): "alolacap",
    ("pikachu", "partner"): "partnercap",
    ("pikachu", "world"): "worldcap",
    ("tauros", "paldeacombat"): "paldeacombatbreed",
    ("tauros", "paldeablaze"): "paldeablazebreed",
    ("tauros", "paldeaaqua"): "paldeaaquabreed",
    ("darmanitan", "galar"): "galarstandard",
    ("greninja", "bond"): "battlebond",
    ("rockruff", "dusk"): "owntempo",
    ("minior", "meteor"): "redmeteor",
    ("mimikyu", "totem"): "totemdisguised",
    ("mimikyu", "bustedtotem"): "totembusted",
    ("necrozma", "duskmane"): "dusk",
    ("necrozma", "dawnwings"): "dawn",
    ("ogerpon", "wellspring"): "wellspringmask",
    ("ogerpon", "hearthflame"): "hearthflamemask",
    ("ogerpon", "cornerstone"): "cornerstonemask",
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
    Pokédex key, then a metadata-derived key. Missing battle-only forms inherit
    from their declared parent form before trying the base species (including
    its default form). A visited set makes malformed parent cycles harmless.
    """
    candidates = []
    visited = set()

    def add(key: str) -> None:
        if key and key not in candidates:
            candidates.append(key)

    def visit(name) -> None:
        normalized = _normalize_key(name)
        if not normalized or normalized in visited:
            return
        visited.add(normalized)

        base_species = _pokedex_string(normalized, "baseSpecies")
        forme = _pokedex_string(normalized, "forme")
        base_forme = _pokedex_string(normalized, "baseForme")
        if base_species:
            species_key = _normalize_key(base_species)
            add(normalized)
            if forme:
                add(species_key + _form_suffix(species_key, forme))
        elif base_forme:
            add(normalized + _form_suffix(normalized, base_forme))
            add(normalized)
        else:
            add(normalized)

        # E.g. Urshifu-Rapid-Strike-Gmax inherits Rapid-Strike's TMs, not the
        # default Single-Strike table. Do not infer ancestry by stripping name
        # suffixes or choosing an arbitrary member of a list-valued battleOnly.
        for field in ("changesFrom", "battleOnly"):
            parent = _pokedex_string(normalized, field)
            if parent:
                visit(parent)
        if base_species:
            visit(base_species)

    visit(internal_name)
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
