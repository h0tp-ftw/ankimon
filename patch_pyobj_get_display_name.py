import re

file_path = "src/Ankimon/pyobj/test_window.py"
with open(file_path, "r") as f:
    content = f.read()

search = """
    def _get_display_name(self, pokemon):
        \"\"\"Helper to safely get localized or pretty name for normal and special forms.\"\"\"
        if hasattr(pokemon, "name") and any(
            f in pokemon.name.lower() for f in ["-mega", "-gmax"]
        ):
            return get_pretty_name_for_name(pokemon.name)
        return get_pokemon_diff_lang_name(
            int(pokemon.id), int(self.settings_obj.get("misc.language"))
        )
"""

replacement = """
    def _get_display_name(self, pokemon):
        \"\"\"Helper to safely get localized or pretty name for normal and special forms.\"\"\"
        if hasattr(pokemon, "name") and any(
            f in pokemon.name.lower() for f in ["-mega", "-gmax"]
        ):
            return get_pretty_name_for_name(pokemon.name)

        try:
            p_id = int(pokemon.id)
        except ValueError:
            return get_pretty_name_for_name(pokemon.name)

        return get_pokemon_diff_lang_name(
            p_id, int(self.settings_obj.get("misc.language"))
        )
"""

content = content.replace(search, replacement)

with open(file_path, "w") as f:
    f.write(content)
