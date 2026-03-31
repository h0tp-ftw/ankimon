import os

from aqt import mw

from ..resources import pkmnimgfolder

SUBSTITUTE_PATH = f"{pkmnimgfolder}/front_default/substitute.png"


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
        # requested gendered gif but not found, try non-gendered
        path = _path_format(back, id, gif, shiny, False)
        if os.path.exists(path):
            mw.logger.log("debug", f"Sprite found (gender fallback): {path}")
            return path


def _try_back(back: bool, id: int, gif: bool, shiny: bool, female: bool):
    path = _try_gendered(back, id, gif, shiny, female)
    if path:
        return path

    if back:
        # requested back

        # special fallback
        if not gif:
            path = f"sprites/missing_back/{id}.png"
            if os.path.exists(path):
                mw.logger.log("debug", f"Sprite found (back fallback): {path}")
                return path

        path = _try_gendered(False, id, gif, shiny, False)
        if path:
            return path


def get_sprite_path(side: str, sprite_type: str, id: int, shiny: bool, gender: str):
    """Return the path to the sprite of the Pokémon with robust fallbacks."""

    gif = sprite_type == "gif"
    female = gender == "F"
    back = side == "back"

    path = _try_back(back, id, gif, shiny, female)
    if path:
        return path

    if gif:
        # requested gif but not found, try png
        path = _try_back(back, id, False, shiny, female)
        if path:
            return path

    # Fallback to the generic substitute image
    mw.logger.log(
        "warning",
        f"Unable to find sprite for ID {id} (Side: {side} Sprite: {sprite_type} Shiny: {shiny}, Gender: {gender}). Returning substitute.",
    )
    return SUBSTITUTE_PATH
