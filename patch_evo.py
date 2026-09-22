import re

with open("src/Ankimon/pyobj/evolution_window.py", "r") as f:
    content = f.read()

# Update display_evo_complete signature
content = content.replace(
    "def display_evo_complete(self, prevo_id: int, evo_id: int):",
    "def display_evo_complete(self, prevo_id: int, evo_id: int, is_shiny: bool = False):"
)
content = content.replace(
    "pkmn_label = self._display_evo_complete_layout(prevo_id, evo_id)",
    "pkmn_label = self._display_evo_complete_layout(prevo_id, evo_id, is_shiny)"
)

# Update _display_evo_complete_layout signature
content = content.replace(
    "def _display_evo_complete_layout(self, prevo_id: int, evo_id: int):",
    "def _display_evo_complete_layout(self, prevo_id: int, evo_id: int, is_shiny: bool = False):"
)

# Update _display_evo_complete_layout implementation
old_layout_1 = """            # Display the Pokémon image
            image_path = frontdefault / f"{evo_id}.png"
            image_pixmap = QPixmap()
            image_pixmap.load(str(image_path))"""

new_layout_1 = """            # Display the Pokémon image
            if is_shiny and (frontdefault / "shiny" / f"{evo_id}.png").exists():
                image_path = frontdefault / "shiny" / f"{evo_id}.png"
            else:
                image_path = frontdefault / f"{evo_id}.png"
            image_pixmap = QPixmap()
            image_pixmap.load(str(image_path))"""
content = content.replace(old_layout_1, new_layout_1)

# Modify ask_pokemon_evo body to get is_shiny
old_ask_1 = """        layout = self.layout()
        pokemon_images, evolve_button, dont_evolve_button = (
            self._ask_pokemon_evo_layout(individual_id, prevo_id, evo_id, item_name)
        )"""

new_ask_1 = """        layout = self.layout()
        is_shiny = False
        try:
            pokemon = services.db.get_pokemon(individual_id)
            if pokemon:
                is_shiny = pokemon.get("shiny", False)
        except Exception:
            pass

        pokemon_images, evolve_button, dont_evolve_button = (
            self._ask_pokemon_evo_layout(individual_id, prevo_id, evo_id, item_name, is_shiny)
        )"""
content = content.replace(old_ask_1, new_ask_1)

# Update _ask_pokemon_evo_layout signature
content = content.replace(
    """    def _ask_pokemon_evo_layout(
        self,
        individual_id: int,
        prevo_id: int,
        evo_id: int,
        item_name: Optional[str] = None,
    ):""",
    """    def _ask_pokemon_evo_layout(
        self,
        individual_id: int,
        prevo_id: int,
        evo_id: int,
        item_name: Optional[str] = None,
        is_shiny: bool = False,
    ):"""
)

# Update _ask_pokemon_evo_layout implementation
old_layout_2 = """            # Display the Pokémon image
            pkmnimage_path = frontdefault / f"{prevo_id}.png"
            pkmnpixmap = QPixmap()
            pkmnpixmap.load(str(pkmnimage_path))

            pkmnimage_path2 = frontdefault / f"{(evo_id)}.png"
            pkmnpixmap2 = QPixmap()
            pkmnpixmap2.load(str(pkmnimage_path2))"""

new_layout_2 = """            # Display the Pokémon image
            if is_shiny and (frontdefault / "shiny" / f"{prevo_id}.png").exists():
                pkmnimage_path = frontdefault / "shiny" / f"{prevo_id}.png"
            else:
                pkmnimage_path = frontdefault / f"{prevo_id}.png"

            pkmnpixmap = QPixmap()
            pkmnpixmap.load(str(pkmnimage_path))

            if is_shiny and (frontdefault / "shiny" / f"{evo_id}.png").exists():
                pkmnimage_path2 = frontdefault / "shiny" / f"{evo_id}.png"
            else:
                pkmnimage_path2 = frontdefault / f"{(evo_id)}.png"

            pkmnpixmap2 = QPixmap()
            pkmnpixmap2.load(str(pkmnimage_path2))"""
content = content.replace(old_layout_2, new_layout_2)


old_evolve_1 = """        self.display_evo_complete(prevo_id, evo_id)"""

new_evolve_1 = """        is_shiny = False
        try:
            pokemon = services.db.get_pokemon(individual_id)
            if pokemon:
                is_shiny = pokemon.get("shiny", False)
        except Exception:
            pass
        self.display_evo_complete(prevo_id, evo_id, is_shiny)"""

content = content.replace(old_evolve_1, new_evolve_1)


with open("src/Ankimon/pyobj/evolution_window.py", "w") as f:
    f.write(content)
