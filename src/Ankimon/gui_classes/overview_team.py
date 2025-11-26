import json
import base64
import os
from pathlib import Path
 
import aqt
from aqt import gui_hooks, mw
from aqt.qt import *
from aqt.utils import showInfo

from ..resources import team_pokemon_path, mypokemon_path, pokeball_path
from ..functions.sprite_functions import get_sprite_path
from ..utils import png_to_base64


"""Overview team UI helpers.

This module provides utilities to build a compact HTML/CSS grid representing the
player's Pokémon team for use in Anki overview/deck views. It includes:

- `load_pokemon_team()` to read the user's Pokemon JSON file.
- `_build_pokemon_grid()` which returns an HTML string for the team grid.
- Styling helpers and utilities to embed sprite images and an optional
    pokéball background image as a base64 data-uri.

The functions are intentionally self-contained so the HTML can be injected
directly into Anki's overview content via the `gui_hooks` hooks registered at
the bottom of the file.
"""


# Simple mapping from Pokemon type -> hex color.
# Keys should be lowercase type names returned by your data (e.g. 'fire').
# Adjust or expand these values to change the appearance of type backgrounds.
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
    """Return a CSS fragment for background (single color or split gradient).

    This helper produces a short CSS fragment suitable for insertion into an
    element's `style` attribute. It maps each type name to a hex color using
    `TYPE_COLORS`. For multiple types it constructs a diagonal linear-gradient
    (135deg) splitting the element between the colors.

    Args:
        types: list[str] - Pokémon type names (e.g. ['fire'] or ['fire','flying']).

    Returns:
        str: CSS fragment, for example:
            - "background-color: #F08030;" (single type)
            - "background: linear-gradient(135deg, #A 0% 50%, #B 50% 100%);" (multi)
    """

    if not types:
        return ""

    # Map each type to its color, falling back to the 'normal' color if unknown.
    default = TYPE_COLORS.get("normal")
    colors = [TYPE_COLORS.get(t.lower(), default) for t in types]

    if len(colors) == 1:
        return f"background-color: {colors[0]};"

    # Build even gradient stops for N colors (e.g. two colors -> 0% 50%, 50% 100%).
    n = len(colors)
    portion = 100.0 / n
    stops = []
    for i, c in enumerate(colors):
        start = round(i * portion, 4)
        end = round((i + 1) * portion, 4)
        stops.append(f"{c} {start}% {end}%")

    stops_css = ", ".join(stops)
    # Use a diagonal split so multi-type boxes are visually diagonal.
    return f"background: linear-gradient(135deg, {stops_css});"

# cache the pokeball data uri once
POKEBALL_DATA_URI = png_to_base64(str(pokeball_path))

def load_pokemon_team():
    """Load Pokémon records for use by the overview grid.

    Preferred behavior:
      1. If a `team_pokemon_path` (team/order file) exists, read it and
         extract the ordered `individual_id` values. Then load all
         Pokémon from `mypokemon_path` and return an ordered list that
         matches the `team_pokemon_path` order (skipping missing entries).
      2. If no team file exists or the resolution fails, fall back to
         returning the full list parsed from `mypokemon_path`.

    This ensures the overview grid shows the player's selected team in
    the same order the rest of the addon expects.

    Returns:
        list[dict]: ordered list of Pokémon dictionaries. May be empty if
                    files are missing or parsing fails.
    """
    try:
        # If a team ordering file exists, prefer it and resolve individual ids
        if os.path.exists(team_pokemon_path):
            try:
                with open(team_pokemon_path, "r", encoding="utf-8") as tf:
                    team_entries = json.load(tf)
            except Exception:
                team_entries = []

            individual_ids = [e.get("individual_id") for e in team_entries if e.get("individual_id") is not None]

            # load all stored pokemon
            try:
                with open(mypokemon_path, "r", encoding="utf-8") as mf:
                    all_pok = json.load(mf)
            except Exception:
                all_pok = []

            by_ind = {p.get("individual_id"): p for p in all_pok if p.get("individual_id") is not None}
            ordered = [by_ind.get(ind) for ind in individual_ids]
            ordered = [p for p in ordered if p]
            if ordered:
                return ordered

        # Fallback: return the full list from mypokemon_path
        with open(mypokemon_path, "r", encoding="utf-8") as file:
            pokemon_data = json.load(file)
            return pokemon_data
    except Exception:
        # On any error return an empty list to avoid breaking the UI
        return []

def _build_pokemon_grid(pokemon_list, id_prefix="pokemon", max_items=6):
    """Build an HTML/CSS grid representing Pokémon entries.

    Args:
        pokemon_list (list[dict]): list of Pokémon data dictionaries. Each
            dictionary is expected to contain at least the keys used below:
                - 'id' (int): numeric Pokémon id used to resolve sprite path
                - 'name' (str): display name
                - 'nickname' (str, optional): nickname to display instead
                - 'level' (int, optional): level to show
                - 'gender' (str, optional): 'M' or 'F' used by sprite resolver
                - 'type' (list[str], optional): list of type names (e.g. ['fire'])
                - 'current_hp' or 'max_hp' (optional): numeric hp values
        id_prefix (str): prefix to apply to each card's HTML `id` attribute.
        max_items (int): maximum number of Pokémon to include in the grid.

    Returns:
        str: a single HTML string that includes a small `<style>` block and a
             container `<div class='poke-grid'>` containing all cards. The
             returned HTML is safe to inject directly into Anki overview
             content but does not perform any escaping of values; ensure your
             data is sanitized if it can contain HTML.
    """
    pokemon_list = pokemon_list[:max_items] if len(pokemon_list) > max_items else pokemon_list
    if len(pokemon_list) == 0:
        return ""

    style = """
        <style>
        .poke-grid{display:flex;justify-content:center;flex-wrap:wrap;gap:10px;padding:6px;margin:0}
        .poke-item{padding:10px;border-radius:6px;flex:0 0 calc(25% - 10px);max-width:180px;box-sizing:border-box;text-align:center;background-color:transparent}
        /* Sprite wrapper gives a modern light dark-grey panel for the image */
        .poke-sprite-wrap{display:block;margin:0 auto 10px;opacity:0.8;padding:8px;border-radius:10px;background:linear-gradient(180deg,#3a3a3a,#2b2b2b);box-shadow:0 6px 18px rgba(0,0,0,0.25);width:90px;height:90px;background-color:#e6e6e6;background-blend-mode: soft-light;display:flex;align-items:center;justify-content:center}
        .poke-sprite{width:120px;height:120px;opacity:1;object-fit:contain;display:block}
        .poke-item h3{margin:0 0 6px 0;font-size:1.05em;font-weight:700;color:#222;}
        .poke-item p{margin:2px 0;font-size:0.85em;color:rgba(0,0,0,0.75);}
        /* Description block: modern light grey panel */
        .poke-desc{background:#f4f5f7;padding:8px;border-radius:8px;margin-top:8px;color:#2b2b2b;font-size:0.9em;line-height:1.3}
        .poke-level{font-weight:700;color:#0066cc}
        .poke-hp{color:#cc0000}
        .poke-types{font-style:italic;color:#006600}
        @media(max-width:800px){.poke-item{flex:0 0 calc(30% - 10px);max-width:180px}}
        @media(max-width:420px){.poke-item{flex:0 0 calc(50% - 10px);min-width:80px}}
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
    """Hook called by Anki when the deck browser HTML is being rendered.

    This function is intended to be registered with
    `gui_hooks.deck_browser_will_render_content`. It inserts the generated
    Pokémon grid HTML at the top of the browser's `content.stats` HTML.

    Args:
        deck_browser: the Anki deck browser object (unused directly here).
        content: object with `.stats` attribute containing HTML fragments
                 (this is mutated in-place by prepending our generated HTML).
    """
    pokemon_list = load_pokemon_team()

    custom_div = _build_pokemon_grid(pokemon_list, id_prefix="pokemon")
    # Prepend the pokemon grid so it appears at the top of the overview content
    content.stats = custom_div + (content.stats or "")

def on_overview_will_render_content(overview, content):
    """Hook called by Anki when an overview page is being rendered.

    Registered with `gui_hooks.overview_will_render_content`. It inserts the
    Pokémon grid at the top of `content.table` HTML so it displays first in
    the overview page's content table area.

    Args:
        overview: the overview object provided by Anki (not used directly).
        content: object with `.table` attribute containing HTML fragments.
    """
    pokemon_list = load_pokemon_team()
    custom_div = _build_pokemon_grid(pokemon_list, id_prefix="pokemon")
    # Prepend the pokemon grid to the table content so it appears first
    content.table = custom_div + (content.table or "")

# Register hooks if the setting is enabled
if mw.settings_obj.get("gui.team_deck_view") is True:
    gui_hooks.deck_browser_will_render_content.append(deck_browser_will_render)
    gui_hooks.overview_will_render_content.append(on_overview_will_render_content)