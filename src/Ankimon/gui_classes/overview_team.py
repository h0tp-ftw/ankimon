"""Team Pokémon Overview grid for Anki's Deck Browser and Deck Overview.

This module builds a compact HTML/CSS flex-grid showing the player's current
Pokémon team (up to 6 members) and injects it into Anki's Deck Browser and
Deck Overview pages via ``gui_hooks``.

Key public components:

* :func:`load_pokemon_team` — reads ``team.json`` / ``mypokemon.json`` and
  returns an ordered list of Pokémon dictionaries.
* :func:`deck_browser_will_render` — hook callback that prepends the grid
  to the Deck Browser stats area.
* :func:`on_overview_will_render_content` — hook callback that prepends the
  grid to the Deck Overview table area.

Hook registration is performed at **import time** and is gated behind the
``gui.team_deck_view`` user setting.  Toggling the setting therefore requires
an Anki restart to take effect.

Note:
    Sprites are embedded as base64 data-URIs so the generated HTML is fully
    self-contained — no external image references are needed.
"""

from __future__ import annotations

import json
import os
from typing import Any

from aqt import gui_hooks, mw

from ..functions.sprite_functions import get_sprite_path
from ..resources import mypokemon_path, icon_path as pokeball_path, team_pokemon_path
from ..utils import png_to_base64

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Mapping of Pokémon type name (lower-case) → hex colour used for card
#: backgrounds.  Dual-type Pokémon receive a diagonal CSS gradient that
#: blends both colours.
TYPE_COLORS: dict[str, str] = {
    "bug": "#A8B820",
    "dark": "#705848",
    "dragon": "#7038F8",
    "electric": "#F8D030",
    "fairy": "#EE99AC",
    "fighting": "#C03028",
    "fire": "#F08030",
    "flying": "#A890F0",
    "ghost": "#705898",
    "grass": "#78C850",
    "ground": "#E0C068",
    "ice": "#98D8D8",
    "normal": "#A8A878",
    "poison": "#A040A0",
    "psychic": "#F85888",
    "rock": "#B8A038",
    "steel": "#B8B8D0",
    "water": "#6890F0",
}

#: Maximum number of Pokémon shown in the overview grid.
_MAX_TEAM_SIZE: int = 6

#: Pokéball image encoded as a ``data:image/png;base64,…`` URI, cached once
#: at module load.  Empty string when the source PNG is missing.
POKEBALL_DATA_URI: str = png_to_base64(str(pokeball_path))

# ---------------------------------------------------------------------------
# CSS (injected once per grid)
# ---------------------------------------------------------------------------

_GRID_CSS = """\
<style>
.poke-grid {
    display: flex;
    justify-content: center;
    flex-wrap: wrap;
    gap: 10px;
    padding: 6px;
    margin: 0;
}
.poke-item {
    padding: 10px;
    border-radius: 6px;
    flex: 0 0 calc(25% - 10px);
    max-width: 180px;
    box-sizing: border-box;
    text-align: center;
    background-color: transparent;
}
.poke-sprite-wrap {
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0 auto 10px;
    opacity: 0.8;
    padding: 8px;
    border-radius: 10px;
    background: linear-gradient(180deg, #3a3a3a, #2b2b2b);
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.25);
    width: 90px;
    height: 90px;
    background-color: #e6e6e6;
    background-blend-mode: soft-light;
}
.poke-sprite {
    width: 120px;
    height: 120px;
    opacity: 1;
    object-fit: contain;
    display: block;
}
.poke-item h3 {
    margin: 0 0 6px 0;
    font-size: 1.05em;
    font-weight: 700;
    color: #222;
}
.poke-item p {
    margin: 2px 0;
    font-size: 0.85em;
    color: rgba(0, 0, 0, 0.75);
}
.poke-desc {
    background: #f4f5f7;
    padding: 8px;
    border-radius: 8px;
    margin-top: 8px;
    color: #2b2b2b;
    font-size: 0.9em;
    line-height: 1.3;
}
.poke-level { font-weight: 700; color: #0066cc; }
.poke-hp    { color: #cc0000; }
.poke-types { font-style: italic; color: #006600; }

@media (max-width: 800px) {
    .poke-item { flex: 0 0 calc(30% - 10px); max-width: 180px; }
}
@media (max-width: 420px) {
    .poke-item { flex: 0 0 calc(50% - 10px); min-width: 80px; }
}
</style>
"""

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bg_style_from_types(types: list[str]) -> str:
    """Return a CSS ``background`` fragment for the given Pokémon types.

    * Single type → solid ``background-color``.
    * Multiple types → 135° linear gradient with equal colour stops.
    * Empty list → empty string (no inline style needed).

    Args:
        types: list[str] (e.g. ``["fire", "flying"]``).

    Returns:
        A CSS property string ready to be placed inside a ``style="…"``
        attribute, or an empty string if *types* is empty.
    """
    if not types:
        return ""

    default_color = TYPE_COLORS.get("normal", "#A8A878")
    colors = [TYPE_COLORS.get(t.lower(), default_color) for t in types]

    if len(colors) == 1:
        return f"background-color: {colors[0]};"

    portion = 100.0 / len(colors)
    stops = []
    for idx, color in enumerate(colors):
        start = round(idx * portion, 4)
        end = round((idx + 1) * portion, 4)
        stops.append(f"{color} {start}% {end}%")

    return f"background: linear-gradient(135deg, {', '.join(stops)});"


def _build_pokeball_style() -> str:
    """Return an inline ``style='…'`` attribute for the pokéball backdrop.

    Returns:
        An HTML attribute string starting with ``" style='…'"`` when the
        pokéball data-URI is available, otherwise an empty string.
    """
    if not POKEBALL_DATA_URI:
        return ""
    return (
        f" style='background-image: url({POKEBALL_DATA_URI}); "
        f"background-size: 100px 100px; background-position: center; "
        f"background-repeat: no-repeat; background-blend-mode: soft-light;'"
    )


def _build_card_html(pokemon: dict[str, Any], id_prefix: str) -> str:
    """Render a single Pokémon card as an HTML fragment.

    Args:
        pokemon: dict[str, Any] with keys such as ``name``, ``nickname``,
            ``level``, ``gender``, ``current_hp``, ``type``, ``id``,
            and ``shiny``.
        id_prefix: Prefix used when generating the card's DOM ``id``.

    Returns:
        An HTML string representing one ``.poke-item`` element.
    """
    name = pokemon.get("name", "Unknown")
    nickname = pokemon.get("nickname", "")
    display_name = nickname or name
    level = pokemon.get("level", 1)
    gender = pokemon.get("gender", "M")
    current_hp = pokemon.get("current_hp", 0)
    types: list = pokemon.get("type", [])
    type_str = "/".join(types) if types else "Normal"

    safe_id = f"{id_prefix}-{name.lower().replace(' ', '-')}"

    sprite_path = get_sprite_path(
        "front", "png", pokemon.get("id", 132), pokemon.get("shiny", False), gender,
    )
    sprite_src = png_to_base64(sprite_path)

    bg_style = _bg_style_from_types(types)
    style_attr = f' style="{bg_style}"' if bg_style else ""
    pokeball_style = _build_pokeball_style()

    return (
        f'<div id="{safe_id}" class="poke-item"{style_attr}>'
        f'  <div class="poke-sprite-wrap"{pokeball_style}>'
        f'    <img src="{sprite_src}" class="poke-sprite" alt="{display_name}"/>'
        f"  </div>"
        f'  <div class="poke-desc">'
        f"    <h3>{display_name}</h3>"
        f'    <p class="poke-level">Level {level}</p>'
        f'    <p class="poke-hp">HP: {current_hp}</p>'
        f'    <p class="poke-types">{type_str}</p>'
        f"  </div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def load_pokemon_team() -> list[dict[str, Any]]:
    """Load the player's Pokémon team for the overview grid.

    Resolution order:

    1. If ``team.json`` exists, read the ordered ``individual_id`` values,
       resolve each against ``mypokemon.json``, and return the matching
       Pokémon in team order (skipping any unresolved entries).
    2. If ``team.json`` is absent **or** resolution yields an empty list,
       fall back to the full contents of ``mypokemon.json``.
    3. On any I/O or parse error, return an empty list (never raises).

    Returns:
        Ordered list of Pokémon dictionaries (`list[dict[str, Any]]`).
        May be empty when no data files exist or parsing fails.
    """
    try:
        if os.path.exists(team_pokemon_path):
            try:
                with open(team_pokemon_path, "r", encoding="utf-8") as fh:
                    team_entries = json.load(fh)
            except (json.JSONDecodeError, OSError):
                team_entries = []

            individual_ids = [
                entry.get("individual_id")
                for entry in team_entries
                if entry.get("individual_id") is not None
            ]

            try:
                with open(mypokemon_path, "r", encoding="utf-8") as fh:
                    all_pokemon = json.load(fh)
            except (json.JSONDecodeError, OSError):
                all_pokemon = []

            pokemon_by_id = {
                p.get("individual_id"): p
                for p in all_pokemon
                if p.get("individual_id") is not None
            }
            ordered = [
                pokemon_by_id[ind]
                for ind in individual_ids
                if ind in pokemon_by_id
            ]
            if ordered:
                return ordered

        # Fallback: return every Pokémon from mypokemon.json.
        with open(mypokemon_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        # Fallback for I/O or parse errors during the final fallback attempt.
        return []
    except Exception as e:
        # Unexpected errors are logged but not allowed to crash Anki's UI.
        if mw:
            mw.progress.chrome_logger.log(f"Ankimon Team Overview Error: {e}")
        return []


def _build_pokemon_grid(
    pokemon_list: list[dict[str, Any]],
    id_prefix: str = "pokemon",
    max_items: int = _MAX_TEAM_SIZE,
) -> str:
    """Build a complete HTML/CSS grid representing the player's team.

    Args:
        pokemon_list: Pokémon dictionaries as returned by
            :func:`load_pokemon_team`.
        id_prefix: Prefix for each card's DOM ``id`` attribute.
        max_items: Maximum cards to render (default :data:`_MAX_TEAM_SIZE`).

    Returns:
        An HTML string containing a ``<style>`` block and the grid markup,
        or an empty string when *pokemon_list* is empty.
    """
    pokemon_list = pokemon_list[:max_items]
    if not pokemon_list:
        return ""

    cards = "".join(
        _build_card_html(pokemon, id_prefix) for pokemon in pokemon_list
    )
    return f"{_GRID_CSS}<div class='poke-grid'>{cards}</div>"


# ---------------------------------------------------------------------------
# Anki hook callbacks
# ---------------------------------------------------------------------------


def deck_browser_will_render(deck_browser: Any, content: Any) -> None:
    """Prepend the team grid to the Deck Browser *stats* area.

    Registered with
    :pyobj:`aqt.gui_hooks.deck_browser_will_render_content`.

    Args:
        deck_browser (aqt.deckbrowser.DeckBrowser): The Deck Browser instance.
        content (aqt.deckbrowser.DeckBrowserContent): Mutable content object.
    """
    team = load_pokemon_team()
    grid_html = _build_pokemon_grid(team, id_prefix="pokemon")
    content.stats = grid_html + (content.stats or "")


def on_overview_will_render_content(overview: Any, content: Any) -> None:
    """Prepend the team grid to the Deck Overview *table* area.

    Registered with
    :pyobj:`aqt.gui_hooks.overview_will_render_content`.

    Args:
        overview (aqt.overview.Overview): The Overview instance.
        content (aqt.overview.OverviewContent): Mutable content object.
    """
    team = load_pokemon_team()
    grid_html = _build_pokemon_grid(team, id_prefix="pokemon")
    content.table = grid_html + (content.table or "")


# ---------------------------------------------------------------------------
# Hook registration (runs at import time, gated by user setting)
# ---------------------------------------------------------------------------

if mw.settings_obj.get("gui.team_deck_view") is True:
    gui_hooks.deck_browser_will_render_content.append(deck_browser_will_render)
    gui_hooks.overview_will_render_content.append(on_overview_will_render_content)
