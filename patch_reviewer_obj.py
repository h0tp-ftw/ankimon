import re

with open("src/Ankimon/pyobj/reviewer_obj.py", "r") as f:
    content = f.read()

# Fix enemy parts
enemy_parts_str = """        if self.settings.get("gui.hud_pokemon_types"):
            types_html = ""
            for t in getattr(self.enemy_pokemon, "type", []):
                type_img_url = f"/_addons/{addon_package}/addon_sprites/Types/{t.lower()}.png"
                types_html += f'<img src="{type_img_url}" alt="{t}" style="margin-left: 4px; width: 22px; height: 22px; background-color: var(--ankimon-outline); border-radius: 50%; padding: 2px; box-sizing: border-box; flex-shrink: 0; vertical-align:middle;">'
            enemy_parts.append(f'<span style="display:inline-block; vertical-align:middle;">{types_html}</span>')"""

enemy_parts_replacement = """        if self.settings.get("gui.hud_pokemon_types"):
            types_html = ""
            for t in getattr(self.enemy_pokemon, "type", []):
                type_img_url = f"/_addons/{addon_package}/addon_sprites/types/{t.lower()}.png"
                types_html += f'<img src="{type_img_url}" alt="{t}" style="margin-left: 4px; width: 22px; height: 22px; background-color: var(--ankimon-outline); border-radius: 50%; padding: 2px; box-sizing: border-box; flex-shrink: 0; vertical-align:middle;">'
            enemy_parts.append(f'<span style="display:inline-block; vertical-align:middle;">{types_html}</span>')"""
content = content.replace(enemy_parts_str, enemy_parts_replacement)

# Fix main parts
main_parts_str = """            if self.settings.get("gui.hud_pokemon_types"):
                types_html = ""
                for t in getattr(self.main_pokemon, "type", []):
                    type_img_url = f"/_addons/{addon_package}/addon_sprites/Types/{t.lower()}.png"
                    types_html += f'<img src="{type_img_url}" alt="{t}" style="margin-left: 4px; width: 22px; height: 22px; background-color: var(--ankimon-outline); border-radius: 50%; padding: 2px; box-sizing: border-box; flex-shrink: 0; vertical-align:middle;">'
                main_parts.append(f'<span style="display:inline-block; vertical-align:middle;">{types_html}</span>')"""

main_parts_replacement = """            if self.settings.get("gui.hud_pokemon_types"):
                types_html = ""
                for t in getattr(self.main_pokemon, "type", []):
                    type_img_url = f"/_addons/{addon_package}/addon_sprites/types/{t.lower()}.png"
                    types_html += f'<img src="{type_img_url}" alt="{t}" style="margin-left: 4px; width: 22px; height: 22px; background-color: var(--ankimon-outline); border-radius: 50%; padding: 2px; box-sizing: border-box; flex-shrink: 0; vertical-align:middle;">'
                main_parts.append(f'<span style="display:inline-block; vertical-align:middle;">{types_html}</span>')"""
content = content.replace(main_parts_str, main_parts_replacement)

with open("src/Ankimon/pyobj/reviewer_obj.py", "w") as f:
    f.write(content)
