import re

with open("src/Ankimon/functions/create_gui_functions.py", "r") as f:
    content = f.read()

badge_html_section = """        if pokemon and settings_obj.get("gui.hud_pokemon_types"):
            for t in getattr(pokemon, "type", []):
                type_img_url = f"/_addons/{addon_package}/addon_sprites/types/{t.lower()}.png"
                badge_html += f'<img src="{type_img_url}" alt="{t}" style="margin-right: 4px; width: 22px; height: 22px; background-color: var(--ankimon-outline); border-radius: 50%; padding: 2px; box-sizing: border-box; flex-shrink: 0;">'
"""

badge_html_replacement = """        if pokemon and settings_obj.get("gui.hud_pokemon_types"):
            for t in getattr(pokemon, "type", []):
                type_img_url = f"/_addons/{addon_package}/addon_sprites/Types/{t.lower()}.png"
                badge_html += f'<img src="{type_img_url}" alt="{t}" style="margin-right: 4px; width: 22px; height: 22px; background-color: var(--ankimon-outline); border-radius: 50%; padding: 2px; box-sizing: border-box; flex-shrink: 0; vertical-align:middle;">'
"""
content = content.replace(badge_html_section, badge_html_replacement)

with open("src/Ankimon/functions/create_gui_functions.py", "w") as f:
    f.write(content)
