import os
from numbers import Integral

from ..services import services
from ..resources import pkmnimgfolder

SUBSTITUTE_PATH = f"{pkmnimgfolder}/front_default/substitute.png"

# Cache for validated sprite paths to avoid repeated filesystem calls during UI rendering
_PATH_VALIDITY_CACHE = {}


def _clear_sprite_cache():
    """Clear the sprite path validation cache. Used primarily for testing."""
    global _PATH_VALIDITY_CACHE
    _PATH_VALIDITY_CACHE.clear()


def _load_pokedex():
    """Return the in-memory pokedex cache.

    The import is deliberately lazy (function-local, not module top level) to
    preserve main's import-cycle discipline: ``pokedex_functions`` imports
    ``pyobj.pokemon_obj``, which imports this module — a top-level
    ``from .pokedex_functions import ...`` here would close that loop and break
    whichever module in it loads first.
    """
    from .pokedex_functions import _load_pokedex_cache

    return _load_pokedex_cache()


def _get_pokemon_id_from_pokedex(pokemon_name):
    """Get the sprite ID for a Pokémon form from pokedex (handles Mega/Gmax forms)."""
    try:
        from .pokedex_functions import safe_int

        pokedex = _load_pokedex()

        pokemon_key = pokemon_name.lower().replace(" ", "").replace("-", "")
        if pokemon_key in pokedex:
            pdata = pokedex[pokemon_key]
            # Ensure we return an integer ID
            return safe_int(pdata.get("actual_id")) or safe_int(pdata.get("num"))
    except Exception as e:
        services.logger.log("debug", f"Error looking up pokemon ID in pokedex: {e}")

    return None


def _path_format(back: bool, id: int, gif: bool, shiny: bool, female: bool):
    side = "back" if back else "front"
    base_path = f"{side}_default_gif" if gif else f"{side}_default"
    sprite_type = "gif" if gif else "png"

    if shiny and female:
        return f"{pkmnimgfolder}/{base_path}/shiny/female/{id}.{sprite_type}"

    if shiny:
        return f"{pkmnimgfolder}/{base_path}/shiny/{id}.{sprite_type}"

    if female:
        return f"{pkmnimgfolder}/{base_path}/female/{id}.{sprite_type}"

    return f"{pkmnimgfolder}/{base_path}/{id}.{sprite_type}"


def _get_cached_valid_path(path):
    """Return cached validated path if valid, otherwise None.

    This caches the result of path containment validation and existence checks
    to avoid repeated synchronous filesystem calls during UI rendering.
    """
    if path not in _PATH_VALIDITY_CACHE:
        sprite_root = os.path.realpath(os.fspath(pkmnimgfolder))
        resolved_path = os.path.realpath(path)
        try:
            is_contained = os.path.commonpath((sprite_root, resolved_path)) == sprite_root
            _PATH_VALIDITY_CACHE[path] = resolved_path if is_contained and os.path.exists(resolved_path) else None
        except ValueError:
            # Handles cross-drive path scenarios on Windows
            _PATH_VALIDITY_CACHE[path] = None

    return _PATH_VALIDITY_CACHE[path]


def _try_gendered(back: bool, id: int, gif: bool, shiny: bool, female: bool):
    """Return a gendered sprite only when its resolved path stays in the sprite root."""
    path = _path_format(back, id, gif, shiny, female)
    cached_path = _get_cached_valid_path(path)
    if cached_path:
        services.logger.log("debug", f"Sprite found: {path}")
        return cached_path

    if female:
        # requested gendered but not found, try non-gendered
        path = _path_format(back, id, gif, shiny, False)
        cached_path = _get_cached_valid_path(path)
        if cached_path:
            services.logger.log("debug", f"Sprite found (gender fallback): {path}")
            return cached_path


def _try_back(back: bool, id: int, gif: bool, shiny: bool, female: bool):
    path = _try_gendered(back, id, gif, shiny, female)
    if path:
        return path

    if back:
        # requested back, fallback to front
        path = _try_gendered(False, id, gif, shiny, female)
        if path:
            return path


def get_sprite_path(
    side: str,
    sprite_type: str,
    id: int,
    shiny: bool,
    gender: str,
    pokemon_name: str = None,
):
    """Return the path to the sprite of the Pokémon with robust fallbacks.

    Args:
        side: "front" or "back"
        sprite_type: "gif" or "png"
        id: Pokémon ID (base form)
        shiny: Whether the Pokémon is shiny
        gender: "M" or "F"
        pokemon_name: Optional Pokémon name (used for Mega/Gmax forms to look up
            the correct form-specific sprite ID)
    """

    try:
        if isinstance(id, bool):
            raise ValueError
        if isinstance(id, (int, float, complex)):
            if isinstance(id, complex):
                raise ValueError
            if isinstance(id, float) and not id.is_integer():
                raise ValueError
        elif hasattr(id, "__int__") and hasattr(id, "__float__"):
            try:
                as_float = float(id)
                if as_float != int(as_float):
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError
        id = int(id)
        if id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        services.logger.log("warning", f"Invalid sprite id {id!r}; using substitute sprite.")
        return SUBSTITUTE_PATH

    gif = sprite_type == "gif"
    female = gender == "F"
    back = side == "back"

    lookup_id = id

    # For Mega/Gmax forms, try to get the form-specific ID from pokedex.
    base_species_id = None
    if pokemon_name and any(form in pokemon_name.lower() for form in ["mega", "gmax", "gigantamax"]):
        forme_id = _get_pokemon_id_from_pokedex(pokemon_name)
        if forme_id:
            lookup_id = forme_id
            services.logger.log(
                "debug", f"Using Mega/Gmax form ID {lookup_id} for {pokemon_name}"
            )
        # Also get the base species_id for fallback
        try:
            from .pokedex_functions import safe_int

            pokedex = _load_pokedex()
            pokemon_key = pokemon_name.lower().replace(" ", "").replace("-", "")
            entry = pokedex.get(pokemon_key)
            if isinstance(entry, dict):
                # Coerce like the rest of the codebase (pokedex_functions
                # wraps every species_id read in safe_int); its 0 default
                # (= not found) is normalized back to None.
                base_species_id = safe_int(entry.get("species_id")) or None
        except Exception:
            pass

    # Try requested format first
    path = _try_back(back, lookup_id, gif, shiny, female)
    if path:
        return path

    # If GIF requested but not found, try PNG
    if gif:
        path = _try_back(back, lookup_id, False, shiny, female)
        if path:
            return path

    # If we used a forme ID and still found nothing, fallback to base form ID
    if lookup_id != id:
        path = _try_back(back, id, gif, shiny, female)
        if path:
            return path

        if gif:
            path = _try_back(back, id, False, shiny, female)
            if path:
                return path

    # Final fallback: try species_id (base form) for Mega/Gmax
    if base_species_id and base_species_id != id and base_species_id != lookup_id:
        path = _try_back(back, base_species_id, gif, shiny, female)
        if path:
            return path
        if gif:
            path = _try_back(back, base_species_id, False, shiny, female)
            if path:
                return path

    # Fallback to the generic substitute image
    services.logger.log(
        "warning",
        f"Unable to find sprite for {pokemon_name} ID {id} (Side: {side} Sprite: {sprite_type} Shiny: {shiny}, Gender: {gender}). Returning substitute.",
    )
    return SUBSTITUTE_PATH


def get_relative_sprite_path(
    pokemon_id: int,
    shiny: bool,
    gender: str = "N",
    pokemon_name: str = None,
    sprite_type: str = "png",
) -> str:
    """Return a sprite path relative to the web root (../user_files/sprites/...)."""
    try:
        abs_path = str(
            get_sprite_path(
                "front",
                sprite_type,
                pokemon_id,
                bool(shiny),
                gender,
                pokemon_name,
            )
        )
        norm = abs_path.replace("\\", "/")
        marker = "user_files/sprites/"
        idx = norm.find(marker)
        if idx != -1:
            return "../" + norm[idx:]
    except Exception:
        pass
    return "../user_files/sprites/front_default/0.png"
