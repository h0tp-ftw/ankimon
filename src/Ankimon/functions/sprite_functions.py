import os
import json

from aqt import mw

from ..resources import pkmnimgfolder, pokedex_path

SUBSTITUTE_PATH = f"{pkmnimgfolder}/front_default/substitute.png"


def _get_pokemon_id_from_pokedex(pokemon_name):
    """Get the sprite ID for a Pokémon form from pokedex (handles Mega/Gmax forms)."""
    try:
        with open(pokedex_path, "r", encoding="utf-8") as f:
            pokedex = json.load(f)
        
        pokemon_key = pokemon_name.lower().replace(" ", "").replace("-", "")
        if pokemon_key in pokedex:
            return pokedex[pokemon_key].get("actual_id") or pokedex[pokemon_key].get("num")
    except Exception as e:
        mw.logger.log("debug", f"Error looking up pokemon ID in pokedex: {e}")
    
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


def _try_gendered(back: bool, id: int, gif: bool, shiny: bool, female: bool):
    path = _path_format(back, id, gif, shiny, female)
    if os.path.exists(path):
        mw.logger.log("debug", f"Sprite found: {path}")
        return path

    if female:
        # requested gendered but not found, try non-gendered
        path = _path_format(back, id, gif, shiny, False)
        if os.path.exists(path):
            mw.logger.log("debug", f"Sprite found (gender fallback): {path}")
            return path


def _try_back(back: bool, id: int, gif: bool, shiny: bool, female: bool):
    path = _try_gendered(back, id, gif, shiny, female)
    if path:
        return path

    if back:
        # requested back, fallback to front
        path = _try_gendered(False, id, gif, shiny, female)
        if path:
            return path


def get_sprite_path(side: str, sprite_type: str, id: int, shiny: bool, gender: str, pokemon_name: str = None):
    """Return the path to the sprite of the Pokémon with robust fallbacks.
    
    Args:
        side: "front" or "back"
        sprite_type: "gif" or "png"
        id: Pokémon ID (base form)
        shiny: Whether the Pokémon is shiny
        gender: "M" or "F"
        pokemon_name: Optional Pokémon name (used for Mega/Gmax forms to lookup correct sprite ID)
    """

    gif = sprite_type == "gif"
    female = gender == "F"
    back = side == "back"
    
    lookup_id = id

    # For Mega/Gmax forms, try to get the form-specific ID from pokedex
    if pokemon_name and any(form in pokemon_name.lower() for form in ["mega", "gmax", "gigantamax"]):
        forme_id = _get_pokemon_id_from_pokedex(pokemon_name)
        if forme_id:
            lookup_id = forme_id
            mw.logger.log("debug", f"Using Mega/Gmax form ID {lookup_id} for {pokemon_name}")

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

    # Fallback to the generic substitute image
    mw.logger.log(
        "warning",
        f"Unable to find sprite for {pokemon_name} ID {id} (Side: {side} Sprite: {sprite_type} Shiny: {shiny}, Gender: {gender}). Returning substitute.",
    )
    return SUBSTITUTE_PATH
