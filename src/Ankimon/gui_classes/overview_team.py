"""Team Pokémon Overview grid for Anki's Deck Browser and Deck Overview.

This module builds a compact HTML/CSS flex-grid showing the player's current
Pokémon team (up to 6 members) and injects it into Anki's Deck Browser and
Deck Overview pages via ``gui_hooks``.

Key public components:

* :func:`load_pokemon_team` — reads from AnkimonDB via the ``services.db``
  seam and falls back to ``team.json`` / ``mypokemon.json``, returning an
  ordered list of Pokémon dictionaries.
* :func:`deck_browser_will_render` — hook callback that prepends the grid
  to the Deck Browser stats area.
* :func:`on_overview_will_render_content` — hook callback that prepends the
  grid to the Deck Overview table area.
* :func:`register_overview_hooks` — registers the two hook callbacks above.

Hook registration is performed by :func:`register_overview_hooks`, invoked
once from the add-on's composition root (``__init__.py``) and gated behind the
``gui.team_deck_view`` user setting.  The (hook, handler) record lives on the
``services`` registry, so an add-on reload swaps the handlers rather than
stacking a duplicate set (F31 reload-safety).

Note:
    Sprites are embedded as base64 data-URIs so the generated HTML is fully
    self-contained — no external image references are needed.
"""

from __future__ import annotations

import html
import json
import os
from typing import Any

from aqt import gui_hooks

from ..business import calculate_cp_from_dict
from ..functions.sprite_functions import get_sprite_path
from ..resources import mypokemon_path, icon_path as pokeball_path, team_pokemon_path
from ..services import services
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

#: Per-path cache of sprite ``data:`` URIs, keyed on ``(path, mtime)`` so a
#: replaced sprite file invalidates naturally.  Avoids re-reading and
#: base64-encoding the same team PNGs on every Deck Browser / Overview render.
_SPRITE_URI_CACHE: dict[str, tuple[float, str]] = {}


def _cached_sprite_base64(path: str) -> str:
    """Return the base64 ``data:`` URI for *path*, memoized by mtime."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return png_to_base64(path)
    cached = _SPRITE_URI_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    uri = png_to_base64(path)
    _SPRITE_URI_CACHE[path] = (mtime, uri)
    return uri

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
.poke-cp    { font-weight: 700; color: #6a4dac; }
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
    from ..functions.pokedex_functions import format_lore_name
    from ..type_names import format_type_list

    name = pokemon.get("name", "Unknown")
    nickname = pokemon.get("nickname", "") or ""

    def _resolved_display_name() -> str:
        """Mirror PokemonObject.display_name: localized species / regional-form
        name, but keep a genuinely custom nickname. The catch flow auto-stores
        the *English* pretty name as the nickname regardless of language, so it
        is compared against the internal, localized AND English names before
        being treated as custom."""
        try:
            from ..functions.pokedex_functions import (
                get_pokemon_diff_lang_name,
                get_pretty_name_for_name,
            )

            lang = 9
            from ..services import services
            if services.settings is not None:
                lang = int(services.settings.get("misc.language", 9))

            localized = get_pokemon_diff_lang_name(int(pokemon.get("id", 0) or 0), lang)
            english = get_pretty_name_for_name(name)
            if not localized or localized == "No Translation in this language":
                localized = english

            if nickname:
                def norm(s):
                    return "".join(c for c in str(s).lower() if c.isalnum())

                if norm(nickname) not in (norm(name), norm(localized), norm(english)):
                    return nickname
            return localized
        except Exception:
            return nickname or format_lore_name(name)

    # The name is player-controlled (rename flow) and Pokémon dicts can also
    # arrive from external channels (trade codes / monthly-challenge payloads),
    # so HTML-escape every user-facing string before it is interpolated into the
    # raw markup rendered by Anki's QtWebEngine Deck Browser / Overview webview.
    display_name = html.escape(_resolved_display_name())
    level = pokemon.get("level", 1)
    gender = pokemon.get("gender", "M")
    current_hp = pokemon.get("current_hp", 0)
    types: list = pokemon.get("type", [])
    type_str = html.escape(format_type_list(types))

    safe_id = f"{id_prefix}-{name.lower().replace(' ', '-')}"

    sprite_path = get_sprite_path(
        "front",
        "png",
        pokemon.get("id", 132),
        pokemon.get("shiny", False),
        gender,
        pokemon.get("name"),
    )
    sprite_src = _cached_sprite_base64(sprite_path)

    bg_style = _bg_style_from_types(types)
    style_attr = f' style="{bg_style}"' if bg_style else ""
    pokeball_style = _build_pokeball_style()

    cp_value = pokemon.get("cp") or calculate_cp_from_dict(pokemon)
    try:
        cp_value = int(cp_value)
    except (TypeError, ValueError):
        cp_value = calculate_cp_from_dict(pokemon)

    return (
        f'<div id="{safe_id}" class="poke-item"{style_attr}>'
        f'  <div class="poke-sprite-wrap"{pokeball_style}>'
        f'    <img src="{sprite_src}" class="poke-sprite" alt="{display_name}"/>'
        f"  </div>"
        f'  <div class="poke-desc">'
        f"    <h3>{display_name}</h3>"
        f'    <p class="poke-level">Level {level}</p>'
        f'    <p class="poke-cp">CP {cp_value:,}</p>'
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

    1. Priority: query AnkimonDB (SQLite) through the ``services.db`` seam.
       Active team entries are resolved by ``individual_id``; when the team is
       empty the first available Pokémon in the collection are used instead.
       A DB error falls through to the legacy JSON path rather than raising.
    2. Legacy fallback: if ``team.json`` exists, read the ordered
       ``individual_id`` values, resolve each against ``mypokemon.json``, and
       return the matching Pokémon in team order (skipping unresolved entries).
    3. Final fallback: return the full contents of ``mypokemon.json``.
    4. On any DB, I/O, or parse error, return an empty list (never raises).

    Returns:
        Ordered list of Pokémon dictionaries (`list[dict[str, Any]]`), capped
        at :data:`_MAX_TEAM_SIZE`.  May be empty when no data exists or parsing
        fails.
    """
    try:
        # Priority: AnkimonDB (SQLite) through the services.db seam. Any DB
        # hiccup is swallowed here so it can never crash the Deck Browser —
        # execution then falls through to the legacy JSON path below.
        db = services.db
        if db is not None:
            try:
                team_stub = db.get_team()
                if team_stub:
                    team: list[dict[str, Any]] = []
                    for entry in team_stub:
                        ind_id = (
                            entry.get("individual_id")
                            if isinstance(entry, dict)
                            else None
                        )
                        if ind_id:
                            pokemon = db.get_pokemon(ind_id)
                            if pokemon:
                                team.append(pokemon)
                    if team:
                        return team[:_MAX_TEAM_SIZE]

                all_pokemon = db.get_all_pokemon()
                if all_pokemon:
                    return all_pokemon[:_MAX_TEAM_SIZE]
            except Exception as e:
                print(f"Ankimon Team Overview - DB Error: {e}")

        # Legacy JSON fallback: resolve team.json order against mypokemon.json.
        if os.path.exists(team_pokemon_path):
            try:
                with open(team_pokemon_path, "r", encoding="utf-8") as fh:
                    team_entries = json.load(fh)
                if not isinstance(team_entries, list):
                    team_entries = []
            except (json.JSONDecodeError, OSError):
                team_entries = []

            individual_ids = [
                entry.get("individual_id")
                for entry in team_entries
                if isinstance(entry, dict) and entry.get("individual_id") is not None
            ]

            try:
                with open(mypokemon_path, "r", encoding="utf-8") as fh:
                    all_pokemon = json.load(fh)
                if not isinstance(all_pokemon, list):
                    all_pokemon = []
            except (json.JSONDecodeError, OSError):
                all_pokemon = []

            pokemon_by_id = {
                p.get("individual_id"): p
                for p in all_pokemon
                if isinstance(p, dict) and p.get("individual_id") is not None
            }
            ordered = [
                pokemon_by_id[ind] for ind in individual_ids if ind in pokemon_by_id
            ]
            if ordered:
                return ordered[:_MAX_TEAM_SIZE]

        # Final fallback: return every Pokémon from mypokemon.json.
        with open(mypokemon_path, "r", encoding="utf-8") as fh:
            all_pokemon = json.load(fh)
        if isinstance(all_pokemon, list):
            return [p for p in all_pokemon if isinstance(p, dict)][:_MAX_TEAM_SIZE]
        return []
    except (OSError, json.JSONDecodeError, TypeError):
        # I/O / parse / non-list-slice errors during the final fallback.
        return []
    except Exception as e:
        # Unexpected errors are logged but not allowed to crash Anki's UI.
        print(f"Ankimon Team Overview Error: {e}")
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

    cards = "".join(_build_card_html(pokemon, id_prefix) for pokemon in pokemon_list)
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
    try:
        team = load_pokemon_team()
        grid_html = _build_pokemon_grid(team, id_prefix="pokemon")
        content.stats = grid_html + (content.stats or "")
    except Exception as e:
        print(f"Ankimon Team Overview - Deck Browser render error: {e}")


def on_overview_will_render_content(overview: Any, content: Any) -> None:
    """Prepend the team grid to the Deck Overview *table* area.

    Registered with
    :pyobj:`aqt.gui_hooks.overview_will_render_content`.

    Args:
        overview (aqt.overview.Overview): The Overview instance.
        content (aqt.overview.OverviewContent): Mutable content object.
    """
    try:
        team = load_pokemon_team()
        grid_html = _build_pokemon_grid(team, id_prefix="pokemon")
        content.table = grid_html + (content.table or "")
    except Exception as e:
        print(f"Ankimon Team Overview - Overview render error: {e}")


# ---------------------------------------------------------------------------
# Hook registration (reload-safe, gated by user setting)
# ---------------------------------------------------------------------------

# Reload safety (F31): the (hook, handler) record lives on the services
# registry — which, unlike a module-level flag, survives a re-execution of this
# module (an add-on reload). It stores the exact handler objects appended, so a
# second boot removes those originals before appending the reloaded module's
# functions, swapping the registration instead of stacking a duplicate set.
_HANDLER_RECORD = "_overview_team_hook_handlers"


def register_overview_hooks() -> None:
    """Register the Deck Browser / Deck Overview team-grid hooks.

    Called once from the add-on's composition root (``__init__.py``). Gated on
    the ``gui.team_deck_view`` setting and reload-safe via the services-anchored
    ``_HANDLER_RECORD`` (see the module docstring). When the setting is off, any
    previously registered handlers are still removed (so toggling off then
    reloading clears the grid) but nothing new is appended.
    """
    # Drop any prior registration first. No-op on a first boot (no record); on a
    # re-boot the stored pairs are removed so the appends below cannot
    # double-register. gui_hooks' remove() tolerates already-absent callbacks.
    for hook, handler in getattr(services, _HANDLER_RECORD, ()):
        hook.remove(handler)
    setattr(services, _HANDLER_RECORD, ())

    settings = services.settings
    if settings is None or settings.get("gui.team_deck_view") is not True:
        return

    handlers = (
        (gui_hooks.deck_browser_will_render_content, deck_browser_will_render),
        (gui_hooks.overview_will_render_content, on_overview_will_render_content),
    )
    for hook, handler in handlers:
        hook.append(handler)
    setattr(services, _HANDLER_RECORD, handlers)
