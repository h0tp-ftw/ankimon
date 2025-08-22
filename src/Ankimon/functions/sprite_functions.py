import os
from pathlib import Path
from ..resources import user_path_sprites, frontdefault

def get_sprite_path(side, sprite_type, id=132, shiny=False, gender="M", form_name=None):
    """
    Returns the path to the sprite of the Pokémon, with several fallbacks.
    - Checks for specific forms (e.g., alola, therian).
    - Checks for shiny and female variants in their respective subfolders.
    - If a GIF is requested but not found, it will fall back to the PNG version.
    """
    
    def find_sprite(check_sprite_type):
        """Internal helper to find a sprite by checking all possible variations."""
        folder_name = f"{side}_default_gif" if check_sprite_type == "gif" else f"{side}_default"
        base_path = user_path_sprites / folder_name

        # Create a list of filenames to check, from most specific to least specific
        filenames = []
        if form_name:
            filenames.append(f"{id}-{form_name.lower()}.{check_sprite_type}")
        filenames.append(f"{id}.{check_sprite_type}")

        # Create a list of subfolder paths to check, from most specific to least specific
        subfolders = []
        if shiny and gender == "F":
            subfolders.append(Path("shiny/female"))
        if shiny:
            subfolders.append(Path("shiny"))
        if gender == "F":
            subfolders.append(Path("female"))
        subfolders.append(Path(".")) # Root sprite folder

        # Iterate through all possibilities to find the first matching sprite
        for subfolder in subfolders:
            for filename in filenames:
                path_to_check = base_path / subfolder / filename
                if os.path.exists(path_to_check):
                    return str(path_to_check)
        
        return None

    # 1. Try to find the requested sprite type (e.g., "gif")
    found_path = find_sprite(sprite_type)
    if found_path:
        return found_path

    # 2. If the requested type was "gif" and it wasn't found, automatically try "png"
    if sprite_type == "gif":
        png_path = find_sprite("png")
        if png_path:
            return png_path

    # 3. If no valid sprite is found, return the placeholder image
    return str(frontdefault / "placeholder.png")