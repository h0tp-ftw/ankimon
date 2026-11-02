from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *
from ..const import status_colors_label, status_colors_html

def create_status_label(status_name):

    # Get the colors for the given status name
    colors = status_colors_label.get(status_name.lower())

    # If the status name is valid, create and style the QLabel
    if colors:
        label = QLabel(status_name.capitalize())
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            f"background-color: {colors['background']};"
            f"border: 2px solid {colors['outline']};"
            f"border-radius: 5px;"
            f"padding: 5px 10px;"
            f"font-weight: bold;"
            f"color: {colors.get('text_color', '#000000')};"
        )
    else:
        label = QLabel("Unknown Status")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "padding: 5px 10px;"
        )

    return label

def _get_plain_status_text_color(colors):
    """Return an explicit colour for plain-mode status text, or ``None``.

    Plain mode draws no badge background, so ``text_color`` (chosen against
    that background) is not safe: a white-on-black badge colour is invisible
    on Anki's light card. Palettes may supply ``plain_text_color`` that reads
    on both themes; when absent we emit no inline colour and let the value
    inherit. Palettes whose ``text_color`` is theme-specific MUST set
    ``plain_text_color`` (see ``const.py``).
    """
    return colors.get("plain_text_color")

def create_status_html(
    status_name, settings_obj, is_pokemon_owned=False, addon_package="", plain_text=False
):
    xp_bar_spacer = settings_obj.compute_special_variable('xp_bar_spacer')
    hp_bar_thickness = settings_obj.get("gui.review_hp_bar_thickness") * 4
    show_mainpkmn_in_reviewer = int(settings_obj.get("gui.show_mainpkmn_in_reviewer"))
    # Get the colors for the given status name
    colors = status_colors_html.get(status_name.lower())

    # If the status name is valid, create the HTML with inline CSS
    if colors:
        badge_html = ''
        captured_label = (
            '<span id="captured-state">&nbsp;[CAPTURED]</span>'
            if plain_text
            and is_pokemon_owned
            and settings_obj.get("gui.hud_owned_indicator")
            else ""
        )
        if (
            not plain_text
            and is_pokemon_owned
            and settings_obj.get("gui.hud_owned_indicator")
        ):
            pokeball_url = f"/_addons/{addon_package}/web/images/pokeball.png"
            badge_html = f'<img id="owned-indicator-badge" src="{pokeball_url}" style="margin-right: 8px; width: 22px; height: 22px; background-color: var(--ankimon-outline); border-radius: 50%; padding: 2px; box-sizing: border-box; flex-shrink: 0;">'

        status_html = ""
        if plain_text and settings_obj.get("gui.hud_status_badge"):
            plain_text_color = _get_plain_status_text_color(colors)
            color_attr = (
                f' style="color: {plain_text_color};"' if plain_text_color else ""
            )
            status_html = (
                f'<span id="pokestatus"{color_attr}>'
                f'— {colors["name"].upper()}</span>{captured_label}'
            )
        elif settings_obj.get("gui.hud_status_badge"):
            status_html = f"""
            <div id=pokestatus class="Ankimon" style="
                background-color: {colors['background']};
                border: 2px solid {colors['outline']};
                border-radius: 5px;
                padding: 5px 10px;
                font-size: 8px;
                font-weight: bold !important;
                display: inline-block;
                color: {colors.get('text_color', '#000000')};
                text-transform: uppercase;
                text-align: center;
                margin: 4px;
                font-family: Arial, sans-serif;
            ">{colors['name']}</div>
            """

        if plain_text and (status_html or captured_label):
            html = f'<div id="pokestatus-container" class="Ankimon">{status_html}{captured_label if not status_html else ""}</div>'
        elif badge_html or status_html:
            if show_mainpkmn_in_reviewer == 2:
                html = f"""
                <div id=pokestatus-container class="Ankimon" style="display: flex; align-items: center; position: fixed; bottom: {140 + xp_bar_spacer + hp_bar_thickness}px; right: 1%; z-index: 9999;">
                {badge_html}
                {status_html}
                </div>
                """
            elif show_mainpkmn_in_reviewer in (0, 1):
                html = f"""
                <div id=pokestatus-container class="Ankimon" style="display: flex; align-items: center; position: fixed; bottom: {40 + hp_bar_thickness + xp_bar_spacer}px; right: 15%; z-index: 9999;">
                {badge_html}
                {status_html}
                </div>
                """
            else:
                html = ""
        else:
            html = ""
    else:
        html = (
            '<div id="pokestatus-container" class="Ankimon">'
            '<span id="captured-state">[CAPTURED]</span></div>'
            if plain_text
            and is_pokemon_owned
            and settings_obj.get("gui.hud_owned_indicator")
            else "<div>Unknown Status</div>"
        )

    return html
