import os
from pathlib import Path
from ..resources import pkmnimgfolder, user_path_sprites, frontdefault

def get_sprite_path(side, sprite_type, id=132, shiny=False, gender="M", form_name=None):
    """
    Returns the path to the sprite of the Pokémon, with several fallbacks.
    - Checks for specific forms (e.g., alola, mega).
    - Checks for shiny and female variants in their respective subfolders.
    - If a GIF is requested but not found, it will fall back to the PNG version.
    - Correctly searches in the '_gif' folder for GIFs and the regular folder for PNGs.
    """
    print(f"\n--- [DEBUG] get_sprite_path called with: side='{side}', sprite_type='{sprite_type}', id={id}, shiny={shiny}, gender='{gender}', form_name='{form_name}' ---")
    
    def find_sprite(check_sprite_type):
        """
        Internal helper function to find a sprite by checking all possible variations.
        It checks for form, shiny, and gender variations in a specific order of priority.
        """
        # Determine the correct base folder based on the sprite type (gif or png)
        folder_name = f"{side}_default_gif" if check_sprite_type == "gif" else f"{side}_default"
        base_path = user_path_sprites / folder_name
        print(f"[DEBUG] find_sprite: Checking for type '{check_sprite_type}' in base folder '{base_path}'")

        # --- Create a list of filenames to check, from most specific to least specific ---
        filenames = []
        # Add form-specific filename first if a form_name is provided
        if form_name:
            filenames.append(f"{id}-{form_name.lower()}.{check_sprite_type}")
        # Always add the standard filename as a fallback
        filenames.append(f"{id}.{check_sprite_type}")
        print(f"[DEBUG] find_sprite: Filenames to check: {filenames}")

        # --- Create a list of subfolder paths to check, from most specific to least specific ---
        subfolder_paths = []
        # 1. Shiny Female (e.g., /shiny/female/)
        if shiny and gender == "F":
            subfolder_paths.append(Path("shiny/female"))
        # 2. Shiny (e.g., /shiny/)
        if shiny:
            subfolder_paths.append(Path("shiny"))
        # 3. Female (e.g., /female/)
        if gender == "F":
            subfolder_paths.append(Path("female"))
        # 4. Root sprite folder (no subfolder)
        subfolder_paths.append(Path("."))
        print(f"[DEBUG] find_sprite: Subfolders to check (in order): {subfolder_paths}")

        # --- Iterate through all possibilities to find the first matching sprite ---
        for subfolder in subfolder_paths:
            for filename in filenames:
                path_to_check = base_path / subfolder / filename
                print(f"[DEBUG] find_sprite: Checking path -> {path_to_check}")
                if os.path.exists(path_to_check):
                    print(f"[DEBUG] find_sprite: SUCCESS! Found sprite at: {path_to_check}")
                    # If a valid sprite is found, return its path immediately
                    return str(path_to_check)
        
        # If no sprite was found after checking all combinations, return None
        print(f"[DEBUG] find_sprite: No '{check_sprite_type}' sprite found after all checks.")
        return None

    # --- Main Logic ---
    # 1. Try to find the requested sprite type (e.g., "gif")
    print(f"[DEBUG] Main: Starting search for primary type '{sprite_type}'")
    found_path = find_sprite(sprite_type)
    if found_path:
        print(f"--- [DEBUG] Returning found path: {found_path} ---")
        return found_path

    # 2. If the requested type was "gif" and it wasn't found, automatically try "png"
    if sprite_type == "gif":
        print("[DEBUG] Main: Primary type 'gif' not found. Falling back to check for 'png'.")
        png_path = find_sprite("png")
        if png_path:
            print(f"--- [DEBUG] Returning found fallback path: {png_path} ---")
            return png_path

    # 3. If no valid sprite is found in any format, return the path to the placeholder image
    placeholder_path = str(frontdefault / "placeholder.png")
    print(f"--- [DEBUG] No sprites found. Returning placeholder: {placeholder_path} ---")
    print(f"[DEBUG] Final resolved form_name used for sprite lookup: {form_name}")
    return placeholder_path