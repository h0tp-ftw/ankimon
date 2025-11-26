from aqt import gui_hooks
from aqt.qt import *
from aqt.utils import showInfo
from ..resources import team_pokemon_path, mypokemon_path
from ..functions.sprite_functions import get_sprite_path
import json

# Simple mapping from Pokemon type -> color. Adjust or expand as desired.
TYPE_COLORS = {
    "fire": "#F08030",
    "water": "#6890F0",
    "grass": "#78C850",
    "electric": "#F8D030",
    "normal": "#A8A878",
    "psychic": "#F85888",
    "rock": "#B8A038",
    "ground": "#E0C068",
    "ice": "#98D8D8",
    "dragon": "#7038F8",
    "dark": "#705848",
    "fairy": "#EE99AC",
    "poison": "#A040A0",
    "bug": "#A8B820",
    "fighting": "#C03028",
    "ghost": "#705898",
    "steel": "#B8B8D0",
    "flying": "#A890F0"
}


def _bg_style_from_types(types: list[str]) -> str:
    """Return a CSS fragment for background (either single color or split gradient).

    - If no types: returns an empty string (caller can use default CSS).
    - If one type: returns `background-color: <color>;`.
    - If two types: returns `background: linear-gradient(90deg, color1 50%, color2 50%);`.
    - If more than two: splits evenly between colors.
    """
    if not types:
        return ""

    # normalize names and map to colors (fall back to normal color)
    default = TYPE_COLORS.get("normal")
    colors = [TYPE_COLORS.get(t.lower(), default) for t in types]

    if len(colors) == 1:
        return f"background-color: {colors[0]};"

    # build gradient stops
    n = len(colors)
    portion = 100.0 / n
    stops = []
    for i, c in enumerate(colors):
        start = round(i * portion, 4)
        end = round((i + 1) * portion, 4)
        stops.append(f"{c} {start}% {end}%")

    # modern browsers accept multiple color stops so we construct one
    # linear-gradient with adjacent stops
    # linear-gradient(90deg, color1 0% 50%, color2 50% 100%) etc.
    stops_css = ", ".join(stops)
    # use a diagonal split (135deg) for the gradient so multi-type boxes are split diagonally
    return f"background: linear-gradient(135deg, {stops_css});"
import aqt
import base64
import os
from pathlib import Path

def png_to_base64(path):
    """Convert a PNG file to a Base64 string for inline HTML embedding."""
    if not os.path.exists(path):
        return ""  # fallback: empty string if file doesn't exist
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("utf-8")


def _find_pokeball_data_uri():
    """Try to locate a pokeball image in known addon locations and return a data URI or empty string."""
    root = Path(__file__).parent.parent
    candidates = [
        root / "addon_files" / "pokeball.png",
        root / "user_files" / "web" / "pokeball.png",
        root / "user_files" / "web" / "images" / "pokeball.png",
    ]
    for p in candidates:
        try:
            if p.exists():
                return png_to_base64(str(p))
        except Exception:
            continue
    return ""

# cache the pokeball data uri once
POKEBALL_DATA_URI = _find_pokeball_data_uri()

def load_pokemon_team():
    with open(mypokemon_path, "r", encoding="utf-8") as file:
        pokemon_data = json.load(file)
        return pokemon_data
    """Load the player's Pokémon Team from a JSON string (in this case, hardcoded)"""
    with open(team_pokemon_path, "r", encoding="utf-8") as file:
        team_data = json.load(file)

    matching_pokemon = []

    # Loop through each Pokémon in the team and find corresponding Pokémon in 'mypokemon_path'
    for pokemon_in_team in team_data:
        individual_id = pokemon_in_team.get('individual_id')
        # Find Pokémon in 'mypokemon_path' with matching individual_id
        for pokemon_in_my_pokemon in pokemon_data:
            if pokemon_in_my_pokemon.get('individual_id', '') == individual_id:
                matching_pokemon.append(pokemon_in_my_pokemon)

    return matching_pokemon

def _build_pokemon_grid(pokemon_list, id_prefix="pokemon", max_items=6):
    """Return an HTML string with a compact responsive grid for given pokemon_list."""
    pokemon_list = pokemon_list[:max_items] if len(pokemon_list) > max_items else pokemon_list
    if len(pokemon_list) == 0:
        return ""

    style = """
        <style>
        .poke-grid{display:flex;justify-content:center;flex-wrap:wrap;gap:10px;padding:6px;margin:0}
        .poke-item{padding:10px;border-radius:6px;flex:0 0 calc(25% - 10px);min-width:200px;box-sizing:border-box;text-align:center;background-color:transparent}
        /* Sprite wrapper gives a modern light dark-grey panel for the image */
        .poke-sprite-wrap{display:block;margin:0 auto 10px;opacity:0.8;padding:8px;border-radius:10px;background:linear-gradient(180deg,#3a3a3a,#2b2b2b);box-shadow:0 6px 18px rgba(0,0,0,0.25);width:90px;height:90px;background-color:#e6e6e6;background-blend-mode: soft-light;display:flex;align-items:center;justify-content:center}
        .poke-sprite{width:120px;height:120px;opacity:1;object-fit:contain;display:block}
        .poke-item h3{margin:0 0 6px 0;font-size:1.05em;font-weight:700;color:#222}
        .poke-item p{margin:2px 0;font-size:0.85em;color:rgba(0,0,0,0.75)}
        /* Description block: modern light grey panel */
        .poke-desc{background:#f4f5f7;padding:8px;border-radius:8px;margin-top:8px;color:#2b2b2b;font-size:0.9em;line-height:1.3}
        .poke-level{font-weight:700;color:#0066cc}
        .poke-hp{color:#cc0000}
        .poke-types{font-style:italic;color:#006600}
        @media(max-width:800px){.poke-item{flex:0 0 calc(50% - 10px);min-width:120px}}
        @media(max-width:420px){.poke-item{flex:0 0 calc(100% - 10px);min-width:0}}
        </style>
    """

    html = style + "<div class='poke-grid'>"

    for p in pokemon_list:
        name = p.get('name', 'Unknown')
        nickname = p.get('nickname', '')
        display_name = nickname if nickname else name
        level = p.get('level', 1)
        gender = p.get('gender', 'M')
        current_hp = p.get('current_hp', 0)
        types = p.get('type', [])
        type_str = '/'.join(types) if types else 'Normal'

        safe_id = f"{id_prefix}-{name.lower().replace(' ', '-')}"
        sprite_path = get_sprite_path('front', 'png', p.get('id', 132), p.get('shiny', False), gender)
        sprite_src = png_to_base64(sprite_path)  # convert PNG to Base64

        # compute inline background style based on types
        bg_style = _bg_style_from_types(types)
        style_attr = f" style=\"{bg_style}\"" if bg_style else ""

        # add pokeball background to the sprite-wrap if available
        pokeball_style = ""
        if POKEBALL_DATA_URI:
            pokeball_style = f" style='background-image: url({POKEBALL_DATA_URI}); background-size:100px 100px; background-position:center; background-repeat:no-repeat; background-blend-mode: soft-light;'"

        html += (
            f"<div id=\"{safe_id}\" class=\"poke-item\"{style_attr}>"
            f"<div class=\"poke-sprite-wrap\"{pokeball_style}>"
            f"<img src=\"{sprite_src}\" class=\"poke-sprite\" alt=\"{display_name}\"/>"
            f"</div>"
            f"<div class=\"poke-desc\">"
            f"<h3>{display_name}</h3>"
            f"<p class=\"poke-level\">Level {level}</p>"
            f"<p class=\"poke-hp\">HP: {current_hp}</p>"
            f"<p class=\"poke-types\">{type_str}</p>"
            f"</div>"
            f"</div>"
        )

    html += "</div>"
    return html

def deck_browser_will_render(deck_browser, content):
    # Add your custom div to the overview content (use helper for layout)
    pokemon_list = load_pokemon_team()

    custom_div = _build_pokemon_grid(pokemon_list, id_prefix="pokemon")
    content.stats += custom_div

def on_overview_will_render_content(overview, content):
    # Add your custom div to the overview content (use helper for layout)
    pokemon_list = load_pokemon_team()
    custom_div = _build_pokemon_grid(pokemon_list, id_prefix="pokemon")
    content.table += custom_div

# Register the hooks
gui_hooks.deck_browser_will_render_content.append(deck_browser_will_render)
gui_hooks.overview_will_render_content.append(on_overview_will_render_content)
