import json
import hashlib
from html import escape
import requests
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QFrame, QCheckBox, QTextBrowser, QSizePolicy
from PyQt6.QtGui import QPixmap, QFont, QIcon, QColor
from PyQt6.QtCore import QSize, Qt
from aqt.utils import showWarning, showInfo
from aqt import mw, utils
from ..resources import pokeapi_db_path, moves_file_path, pokedex_path, icon_path
from ..functions.sprite_functions import get_sprite_path
from datetime import datetime
import uuid
from ..functions.pokedex_functions import get_base_experience, get_growth_rate
from ..utils import get_tier_by_id
from .error_handler import show_warning_with_traceback
from ..services import services
import os

# --- Module-level functions for Monthly Challenges ---

def create_monthly_challenge_pokemon(pokemon_data, make_shiny=False):
    """Creates a Pokémon dictionary from monthly challenge data."""
    base_stats = pokemon_data.get("stats", {})
    return {
        "name": pokemon_data["name"],
        "nickname": pokemon_data.get("nickname", ""),
        "id": pokemon_data["id"],
        "level": pokemon_data.get("level", 1),
        "ability": pokemon_data.get("ability", "No Ability"),
        "type": pokemon_data.get("type", ["Normal"]),
        "stats": base_stats,
        "ev": pokemon_data.get("ev", {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}),
        "iv": pokemon_data.get("iv", {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15}),
        "attacks": pokemon_data.get("attacks", ["Tackle"]),
        "growth_rate": pokemon_data.get("growth_rate", "medium"),
        "base_experience": pokemon_data.get("base_experience", 64),
        "gender": pokemon_data.get("gender", "N"),
        "shiny": pokemon_data.get("shiny", False) or make_shiny,
        "xp": pokemon_data.get("xp", 0),
        "current_hp": pokemon_data.get("current_hp", base_stats.get("hp")),
        "friendship": pokemon_data.get("friendship", 0),
        "pokemon_defeated": pokemon_data.get("pokemon_defeated", 0),
        "everstone": pokemon_data.get("everstone", False),
        "mega": pokemon_data.get("mega", False),
        "special_form": pokemon_data.get("special_form", None),
        "tier": pokemon_data.get("tier", "Normal"),
        "captured_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "individual_id": pokemon_data["individual_id"],
        "is_favorite": pokemon_data.get("is_favorite", False),
        "held_item": pokemon_data.get("held_item", None)
    }

def add_pokemon_to_collection(new_pokemon, refresh_callback=None, parent_window=None):
    """Adds a Pokémon to the user's collection in the database."""
    try:
        db = services.db
        db.save_pokemon(new_pokemon)
        if refresh_callback:
            refresh_callback()

        # Refresh open PC Box window. Read services.pokemon_pc directly:
        # `from ..singletons import pokemon_pc` lands in the lazy __getattr__
        # factory and would force-construct a PC window the user never opened.
        from ..utils import is_alive
        if is_alive(services.pokemon_pc):
            services.pokemon_pc.refresh_pokemon_grid()
        return True
    except Exception as e:
        show_warning_with_traceback(parent=parent_window, exception=e, message="Error adding Pokemon to collection")
        return False

def show_monthly_challenge_dialog(challenge_pokemon, description, parent_window=None):
    """
    Display the main monthly challenge dialog asking the user to accept or reject the Pokémon.
    
    This function creates and shows a modal dialog that presents the monthly challenge
    Pokémon to the user with options to accept or reject it. The dialog includes:
    - A title showing the Pokémon's name with a "(Shiny!!)" indicator if applicable
    - An informational subtitle
    - A sprite of the Pokémon on the left (if sprites are enabled and available)
    - A description of the challenge on the right
    - A Discord link for more information
    - "Accept Pokémon" and "Reject" buttons
    
    This is the primary user interface for the monthly challenge feature and is
    called when a new monthly challenge Pokémon is available to claim.
    
    Side Effects:
        - Displays a modal QDialog with:
            - A fixed-size sprite container (160x160) with a blue background
            - A sprite scaled to 120x120 with aspect ratio preserved
            - A description box with the challenge text
            - A Discord link with custom styling
            - "Accept Pokémon" (green) and "Reject" (transparent) buttons
        - The dialog uses the application's theme manager for dark/light mode support
        - Sprites are conditionally displayed based on user settings
        - The dialog is modal and blocks interaction with parent windows
        - No application state or database is modified by this function
    
    Notes:
        - The dialog window is set to be application modal, preventing interaction
          with other windows until the user makes a choice
        - The window close button is disabled to force the user to accept or reject
        - Sprites are loaded as QMovie objects to support animated GIFs
        - The sprite path is resolved using get_sprite_path() with the appropriate
          parameters for side, sprite_type, ID, shiny status, and gender
        - If a sprite fails to load, the sprite area remains blank (no fallback)
        - The description text is HTML-escaped to prevent XSS issues
        - The Discord link uses the accent color from the theme and opens externally
        - The "Accept Pokémon" button is set as the default button (Enter key)
    """

    from PyQt6.QtWidgets import QSizePolicy
    from PyQt6.QtGui import QMovie, QPixmap
    from PyQt6.QtCore import QSize
    from aqt.theme import theme_manager
    
    parent = parent_window if parent_window is not None else mw
    window = QDialog(parent)
    window.setWindowTitle("Monthly Challenge Begins!")
    window.setWindowIcon(QIcon(str(icon_path)))
    window.setWindowModality(Qt.WindowModality.ApplicationModal)
    window.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    window.setMinimumWidth(620)
    window.setMinimumHeight(380)

    # Check if sprites should be shown
    show_sprites = True
    try:
        from ..services import services
        settings_obj = services.settings
        if settings_obj is not None:
            show_sprites = settings_obj.get("gui.show_sprites_across_ankimon", True)
    except Exception:
        pass

    is_dark = theme_manager.night_mode
    if is_dark:
        bg = "#0d1117"
        bg_card_hover = "#252d3f"
        border = "#2d3748"
        text = "#f0f6fc"
        accent_blue = "#63b3ed"
        blue_solid = "#2474a8"
        accent_green = "#3fb950"
        btn_bg = "rgba(88, 166, 255, 0.08)"
        btn_hover = "rgba(88, 166, 255, 0.18)"
        update_btn_text = "#0d1117"
    else:
        bg = "#ffffff"
        bg_card_hover = "#e9ecef"
        border = "#d0d7de"
        text = "#24292f"
        accent_blue = "#0969da"
        blue_solid = "#1a6fb0"
        accent_green = "#2da44e"
        btn_bg = "rgba(9, 105, 218, 0.08)"
        btn_hover = "rgba(9, 105, 218, 0.18)"
        update_btn_text = "#e6ffea"

    window.setStyleSheet(f"""
        QDialog {{
            background-color: {bg};
            color: {text};
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }}
        QLabel {{
            color: {text};
            background: transparent;
        }}
        QLabel#descLabel {{
            color: #ffffff;
            font-size: 0.95rem;
            font-weight: 700;
            line-height: 1.6;
            background: transparent;
            padding: 0;
        }}
        QFrame#spriteBox {{
            background-color: {blue_solid};
            border-radius: 16px;
        }}
        QFrame#descBox {{
            background-color: {blue_solid};
            border-radius: 12px;
        }}
        QPushButton {{
            padding: 8px 20px;
            border: 1px solid {border};
            border-radius: 8px;
            background: {btn_bg};
            color: {text};
            font-size: 0.85rem;
            font-weight: 600;
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            min-width: 100px;
        }}
        QPushButton:hover {{
            background: {btn_hover};
            border-color: {accent_blue};
        }}
        QPushButton#acceptBtn {{
            background: {accent_green};
            border: none;
            color: {update_btn_text};
            font-weight: 700;
        }}
        QPushButton#acceptBtn:hover {{
            background: #2ea043;
        }}
        QPushButton#rejectBtn {{
            background: transparent;
            border: 1px solid {border};
            color: {text};
        }}
        QPushButton#rejectBtn:hover {{
            background: {bg_card_hover};
            border-color: {text};
        }}
    """)

    layout = QVBoxLayout(window)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(16)

    shiny_text = " (Shiny !!)" if challenge_pokemon.get("shiny", False) else ""
    title_label = QLabel(
        f"<span style='font-size: 1.2rem; font-weight: 800; letter-spacing: -0.3px; color: {text};'>"
        f"!! You've received your monthly challenge Pokémon: "
        f"<b>{escape(challenge_pokemon['name'])}{shiny_text}</b></span>"
    )
    title_label.setWordWrap(True)
    layout.addWidget(title_label)

    info_label = QLabel("This special Pokémon is yours to keep and train!")
    info_label.setStyleSheet(f"color: {text}; font-size: 0.88rem;")
    info_label.setWordWrap(True)
    layout.addWidget(info_label)

    content_layout = QHBoxLayout()
    content_layout.setSpacing(16)
    content_layout.setContentsMargins(0, 8, 0, 8)

    sprite_box = QFrame()
    sprite_box.setObjectName("spriteBox")
    sprite_box.setFixedSize(160, 160)
    sprite_box_layout = QVBoxLayout(sprite_box)
    sprite_box_layout.setContentsMargins(0, 0, 0, 0)
    sprite_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    sprite_label = QLabel()
    sprite_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sprite_label.setFixedSize(120, 120)
    sprite_label.setScaledContents(False)
    sprite_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    
    if show_sprites:
        pokemon_id = challenge_pokemon.get("id", 25)
        pokemon_name = challenge_pokemon.get("name", "Pikachu")
        shiny = challenge_pokemon.get("shiny", False)
        gender = challenge_pokemon.get("gender", "N")
        
        try:
            from ..functions.sprite_functions import get_sprite_path
            sprite_path = get_sprite_path(
                side="front", 
                sprite_type="gif", 
                id=pokemon_id, 
                shiny=shiny, 
                gender=gender, 
                pokemon_name=pokemon_name
            )
            movie = QMovie(sprite_path)
            sprite_label.setMovie(movie)
            movie.start()
        except Exception:
            pass
    
    sprite_box_layout.addWidget(sprite_label)
    content_layout.addWidget(sprite_box, alignment=Qt.AlignmentFlag.AlignTop)

    desc_box = QFrame()
    desc_box.setObjectName("descBox")
    desc_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    desc_box_layout = QVBoxLayout(desc_box)
    desc_box_layout.setContentsMargins(20, 14, 20, 14)
    desc_box_layout.setSpacing(0)
    desc_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    if description:
        desc_label = QLabel()
        desc_label.setObjectName("descLabel")
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        desc_text = escape(description).replace(chr(10), '<br>')
        desc_label.setText(f"<div style='margin: 0; padding: 0;'><b>{desc_text}</b></div>")
        desc_box_layout.addWidget(desc_label)
    else:
        placeholder = QLabel("A special Pokémon awaits you!")
        placeholder.setObjectName("descLabel")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #ffffff; font-size: 0.95rem; font-weight: 700; padding: 0; margin: 0;")
        desc_box_layout.addWidget(placeholder)

    content_layout.addWidget(desc_box)
    layout.addLayout(content_layout)

    discord_label = QLabel(
        f'For more information on monthly challenges and to redeem higher-tier prizes (spoiler: where Shinies are involved!)'
        f' for your performance, please check the '
        f'<a href="https://discord.gg/Fd6fZYQx4r" style="color: {accent_blue}; text-decoration: none;">Ankimon Discord</a>!'
    )
    discord_label.setWordWrap(True)
    discord_label.setStyleSheet(f"color: {text}; font-size: 0.85rem;")
    discord_label.setOpenExternalLinks(True)
    layout.addWidget(discord_label)

    button_layout = QHBoxLayout()
    button_layout.addStretch()

    reject_button = QPushButton("Reject")
    reject_button.setObjectName("rejectBtn")
    reject_button.setMinimumWidth(100)
    button_layout.addWidget(reject_button)

    accept_button = QPushButton("Accept Pokémon")
    accept_button.setObjectName("acceptBtn")
    accept_button.setMinimumWidth(120)
    accept_button.setDefault(True)
    button_layout.addWidget(accept_button)

    layout.addLayout(button_layout)

    accept_button.clicked.connect(window.accept)
    reject_button.clicked.connect(window.reject)

    return window.exec() == QDialog.DialogCode.Accepted

def show_monthly_acceptance_dialog(parent_window=None, challenge_pokemon=None):
    """
    Display a confirmation dialog when a user successfully claims a monthly challenge Pokémon.
    
    This function creates and shows a modal dialog that congratulates the user
    on receiving their monthly challenge Pokémon. The dialog includes:
    - A sprite of the claimed Pokémon (if available)
    - The Pokémon's name and level displayed in bold
    - A tip about checking progress in the Ankimon menu
    - A "Let's go!" button to close the dialog
    
    This is intended to be called after the Pokémon has been successfully
    added to the user's collection and the database has been updated.

    Side Effects:
        - Displays a modal QDialog with:
            - A blue rounded rectangle containing the Pokémon sprite
            - A congratulatory message with the Pokémon name and level in bold
            - A tip message with "Ankimon > Profile > Monthly Challenge" in bold
            - A "Let's go!" button
        - The dialog uses the application's theme manager for dark/light mode support
        - Sprites are conditionally displayed based on user settings
        - The dialog is modal and blocks interaction with parent windows
        - No application state or database is modified by this function
    
    Notes:
        - The dialog is purely informational and provides no user input options
          other than closing the dialog
        - The message uses HTML formatting for bold text (<b> tags)
        - Line breaks are handled with <br> tags to ensure proper rendering
        - If the sprite file is missing or show_sprites is False, the sprite
          container remains empty (no fallback sprite is shown)
        - This function should be called after successful Pokémon addition,
          not as part of the addition process itself
    """

    from PyQt6.QtWidgets import QSizePolicy
    from PyQt6.QtGui import QMovie
    from PyQt6.QtCore import QSize
    from aqt.theme import theme_manager
    import os
    
    parent = parent_window if parent_window is not None else mw
    window = QDialog(parent)
    window.setWindowTitle("Monthly Challenge Accepted!")
    window.setWindowIcon(QIcon(str(icon_path)))
    window.setWindowModality(Qt.WindowModality.ApplicationModal)
    window.setMinimumWidth(520)
    window.setMinimumHeight(200)

    # Check if sprites should be shown
    show_sprites = True
    try:
        from ..services import services
        settings_obj = services.settings
        if settings_obj is not None:
            show_sprites = settings_obj.get("gui.show_sprites_across_ankimon", True)
    except Exception:
        pass

    is_dark = theme_manager.night_mode
    if is_dark:
        bg = "#0d1117"
        bg_darker = "#161b22"
        bg_card_hover = "#252d3f"
        border = "#2d3748"
        text = "#f0f6fc"
        accent_blue = "#58a6ff"
        accent_green = "#3fb950"
        blue_solid = "#2474a8"
        btn_bg = "rgba(88, 166, 255, 0.08)"
        btn_hover = "rgba(88, 166, 255, 0.18)"
        btn_primary_bg = "#3fb950"
        btn_primary_hover = "#2ea043"
    else:
        bg = "#ffffff"
        bg_darker = "#f0f2f5"
        bg_card_hover = "#e9ecef"
        border = "#d0d7de"
        text = "#24292f"
        accent_blue = "#0969da"
        accent_green = "#2da44e"
        blue_solid = "#1a6fb0"
        btn_bg = "rgba(9, 105, 218, 0.08)"
        btn_hover = "rgba(9, 105, 218, 0.18)"
        btn_primary_bg = "#2da44e"
        btn_primary_hover = "#2ea043"

    window.setStyleSheet(f"""
        QDialog {{
            background-color: {bg};
            color: {text};
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }}
        QLabel {{
            color: {text};
            background: transparent;
            font-size: 0.95rem;
            line-height: 1.5;
        }}
        QFrame#spriteBox {{
            background-color: {blue_solid};
            border-radius: 12px;
        }}
        QPushButton {{
            padding: 8px 24px;
            border: 1px solid {border};
            border-radius: 8px;
            background: {btn_primary_bg};
            color: {bg};
            font-size: 0.85rem;
            font-weight: 700;
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            min-width: 100px;
            border: none;
        }}
        QPushButton:hover {{
            background: {btn_primary_hover};
        }}
    """)

    layout = QVBoxLayout(window)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(16)

    message_layout = QHBoxLayout()
    message_layout.setSpacing(12)

    sprite_box = QFrame()
    sprite_box.setObjectName("spriteBox")
    sprite_box.setFixedSize(80, 80)
    sprite_box_layout = QVBoxLayout(sprite_box)
    sprite_box_layout.setContentsMargins(0, 0, 0, 0)
    sprite_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    sprite_label = QLabel()
    sprite_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sprite_label.setFixedSize(64, 64)
    sprite_label.setScaledContents(False)
    sprite_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    
    if show_sprites and challenge_pokemon is not None:
        pokemon_id = challenge_pokemon.get("id", 25)
        pokemon_name = challenge_pokemon.get("name", "Pikachu")
        shiny = challenge_pokemon.get("shiny", False)
        gender = challenge_pokemon.get("gender", "N")
        
        try:
            from ..functions.sprite_functions import get_sprite_path
            sprite_path = get_sprite_path(
                side="front", 
                sprite_type="gif", 
                id=pokemon_id, 
                shiny=shiny, 
                gender=gender, 
                pokemon_name=pokemon_name
            )
            
            if os.path.exists(sprite_path):
                movie = QMovie(sprite_path)
                sprite_label.setMovie(movie)
                movie.start()
            else:
                pass
        except Exception:
            pass
    
    sprite_box_layout.addWidget(sprite_label)
    message_layout.addWidget(sprite_box)

    pokemon_name = challenge_pokemon.get("name", "Pokémon") if challenge_pokemon else "Pokémon"
    pokemon_level = challenge_pokemon.get("level", 1) if challenge_pokemon else 1
    
    message_text = (
        f"Congrats, you've successfully received <b>{escape(str(pokemon_name))}</b> <b>Lvl. {escape(str(pokemon_level))}</b>!<br><br>"
        f"Tip: Check your progress at <b>Ankimon → Profile → Monthly Challenge</b> to see your dedication in action!"
    )
    
    message = QLabel(message_text)
    message.setWordWrap(True)
    message.setStyleSheet(f"color: {text}; font-size: 0.95rem; line-height: 1.6; padding: 4px 0;")
    message_layout.addWidget(message)
    layout.addLayout(message_layout)

    button_layout = QHBoxLayout()
    button_layout.addStretch()

    letsgo_button = QPushButton("Let's go!")
    letsgo_button.setMinimumWidth(120)
    letsgo_button.clicked.connect(window.accept)
    button_layout.addWidget(letsgo_button)

    layout.addLayout(button_layout)

    window.exec()

def show_monthly_rejection_dialog(parent_window=None, challenge_pokemon=None):
    """
    Display a confirmation dialog when a user rejects a monthly challenge Pokémon.
    
    This function creates and shows a modal dialog that informs the user their
    rejection was recorded and explains how they can reclaim the Pokémon later.
    The dialog includes a sprite of the rejected Pokémon (if available) and
    provides instructions for accessing the monthly challenge feature through
    the Ankimon menu.
    
    Notes:
        - The dialog's message informs users they can reclaim the Pokémon later
          via Ankimon → Profile → Monthly Challenge
        - The close button is labeled "Alright!" and simply closes the dialog
        - The function does not modify any application state or database
        - This is designed to be called after the rejection decision has been recorded
          in the database, not as the rejection decision itself
    """

    from PyQt6.QtWidgets import QSizePolicy
    from PyQt6.QtGui import QMovie
    from PyQt6.QtCore import QSize
    from aqt.theme import theme_manager
    import os
    
    parent = parent_window if parent_window is not None else mw
    window = QDialog(parent)
    window.setWindowTitle("Monthly Challenge Rejected!")
    window.setWindowIcon(QIcon(str(icon_path)))
    window.setWindowModality(Qt.WindowModality.ApplicationModal)
    window.setMinimumWidth(520)
    window.setMinimumHeight(160)

    # Check if sprites should be shown
    show_sprites = True
    try:
        from ..services import services
        settings_obj = services.settings
        if settings_obj is not None:
            show_sprites = settings_obj.get("gui.show_sprites_across_ankimon", True)
    except Exception:
        pass

    is_dark = theme_manager.night_mode
    if is_dark:
        bg = "#0d1117"
        bg_darker = "#161b22"
        bg_card_hover = "#252d3f"
        border = "#2d3748"
        text = "#f0f6fc"
        accent_blue = "#58a6ff"
        accent_green = "#3fb950"
        blue_solid = "#2474a8"
        btn_bg = "rgba(88, 166, 255, 0.08)"
        btn_hover = "rgba(88, 166, 255, 0.18)"
        btn_primary_bg = "#3fb950"
        btn_primary_hover = "#2ea043"
    else:
        bg = "#ffffff"
        bg_darker = "#f0f2f5"
        bg_card_hover = "#e9ecef"
        border = "#d0d7de"
        text = "#24292f"
        accent_blue = "#0969da"
        accent_green = "#2da44e"
        blue_solid = "#1a6fb0"
        btn_bg = "rgba(9, 105, 218, 0.08)"
        btn_hover = "rgba(9, 105, 218, 0.18)"
        btn_primary_bg = "#2da44e"
        btn_primary_hover = "#2ea043"

    window.setStyleSheet(f"""
        QDialog {{
            background-color: {bg};
            color: {text};
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }}
        QLabel {{
            color: {text};
            background: transparent;
            font-size: 0.95rem;
            line-height: 1.5;
        }}
        QFrame#spriteBox {{
            background-color: {blue_solid};
            border-radius: 12px;
        }}
        QPushButton {{
            padding: 8px 24px;
            border: 1px solid {border};
            border-radius: 8px;
            background: {btn_primary_bg};
            color: {bg};
            font-size: 0.85rem;
            font-weight: 700;
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            min-width: 100px;
            border: none;
        }}
        QPushButton:hover {{
            background: {btn_primary_hover};
        }}
    """)

    layout = QVBoxLayout(window)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(16)

    message_layout = QHBoxLayout()
    message_layout.setSpacing(12)

    sprite_box = QFrame()
    sprite_box.setObjectName("spriteBox")
    sprite_box.setFixedSize(80, 80)
    sprite_box_layout = QVBoxLayout(sprite_box)
    sprite_box_layout.setContentsMargins(0, 0, 0, 0)
    sprite_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    sprite_label = QLabel()
    sprite_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sprite_label.setFixedSize(64, 64)
    sprite_label.setScaledContents(False)
    sprite_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    
    if show_sprites and challenge_pokemon is not None:
        pokemon_id = challenge_pokemon.get("id", 25)
        pokemon_name = challenge_pokemon.get("name", "Pikachu")
        shiny = challenge_pokemon.get("shiny", False)
        gender = challenge_pokemon.get("gender", "N")
        
        try:
            from ..functions.sprite_functions import get_sprite_path
            sprite_path = get_sprite_path(
                side="front", 
                sprite_type="gif", 
                id=pokemon_id, 
                shiny=shiny, 
                gender=gender, 
                pokemon_name=pokemon_name
            )
            
            if os.path.exists(sprite_path):
                movie = QMovie(sprite_path)
                sprite_label.setMovie(movie)
                movie.start()
            else:
                pass
        except Exception:
            pass
    
    sprite_box_layout.addWidget(sprite_label)
    message_layout.addWidget(sprite_box)

    message = QLabel(
        "No problem! If you ever change your mind or decide to take the Tauros by "
        "the horns, head to <b>Ankimon → Profile → Monthly Challenge</b> to reclaim this month's Pokémon or view past challenges. Happy Ankimoning!"
    )
    message.setWordWrap(True)
    message.setStyleSheet(f"color: {text}; font-size: 0.95rem; line-height: 1.6; padding: 4px 0;")
    message_layout.addWidget(message)
    layout.addLayout(message_layout)

    # Button
    button_layout = QHBoxLayout()
    button_layout.addStretch()

    alright_button = QPushButton("Alright!")
    alright_button.setMinimumWidth(120)
    alright_button.clicked.connect(window.accept)
    button_layout.addWidget(alright_button)

    layout.addLayout(button_layout)

    window.exec()

def check_and_award_monthly_pokemon(logger, defer=True):
    """
    Check for and award the current month's challenge Pokémon to the user.
    
    This function handles the complete monthly challenge workflow including:
    1. Verifying the user has rated the addon (required for eligibility)
    2. Fetching the current month's challenge data from a remote JSON source
    3. Checking if the Pokémon has already been claimed or rejected
    4. Handling edge cases where database tracking values are out of sync
    5. Determining shiny eligibility based on previous challenge performance
    6. Presenting the challenge dialog to the user
    7. Recording the user's decision (accept/reject) in the database
    8. Adding the Pokémon to the user's collection if accepted
    
    Side Effects:
        - Reads/writes user_data in the database:
            - 'rate_this': Read to check eligibility
            - 'monthly_challenge_id': Read/write to track current challenge
            - 'monthly_challenge': Read/write (0=unclaimed, 1=accepted, 2=rejected)
        - Fetches data from a remote GitHub URL (monthly_challenges.json)
        - May add a new Pokémon to the user's collection via add_pokemon_to_collection()
        - May display modal dialogs (show_monthly_challenge_dialog, 
          show_monthly_acceptance_dialog, show_monthly_rejection_dialog)
        - Logs all major events and errors via the provided logger
    
    Notes:
        - Shiny eligibility: If a previous challenge Pokémon exists and has
          defeated at least the threshold number of Pokémon, the current
          challenge Pokémon will be shiny
        - Edge case handling: If the Pokémon exists in the collection but the
          database tracking values are missing (last_challenge_id is None) or
          stale (monthly_challenge == 0), the function reconciles the state
          by setting monthly_challenge_id to the current ID and
          monthly_challenge to 1 (accepted)
        - The function returns early without errors if the monthly challenges
          JSON cannot be fetched (handles offline scenarios gracefully)
        - All exceptions are caught, logged, and swallowed to prevent
          interrupting the user's Anki session
    """

    # Defer the dialog to the next event loop iteration to avoid blocking profile_did_open
    def _do_check():
        ...

    if defer:
        # Defer the dialog to the next event loop iteration to avoid blocking profile_did_open
        from aqt.qt import QTimer
        QTimer.singleShot(0, _do_check)
    else:
        _do_check()

    def _do_check():
        try:
            db = services.db
            if db.get_user_data("rate_this") not in (True, "true"):
                logger.log("info", "Monthly Pokemon check skipped: user has not rated the addon.")
                return

            logger.log("info", "Checking for monthly challenge Pokemon award.")
            now = datetime.now()
            month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
            current_month_str = f"{month_names[now.month - 1]} {now.year}"
            monthly_data_url = "https://raw.githubusercontent.com/h0tp-ftw/ankimon/refs/heads/main/assets/challenges/monthly_challenges.json"
            
            try:
                response = requests.get(monthly_data_url, timeout=2)
                response.raise_for_status()
                monthly_challenges = response.json()
            except requests.exceptions.RequestException as e:
                logger.log("error", f"Could not fetch monthly challenges; likely no internet connection. Details: {e}")
                return  # Exit gracefully if fetching fails

            current_challenge = next((c for c in monthly_challenges if c.get("month") == current_month_str), None)

            if not current_challenge:
                logger.log("info", f"No monthly challenge found for {current_month_str}.")
                return

            challenge_pokemon_data = current_challenge.get("pokemon")
            if not challenge_pokemon_data:
                logger.log("warning", f"Monthly challenge for {current_month_str} is missing 'pokemon' data.")
                return

            challenge_individual_id = challenge_pokemon_data.get("individual_id")
            if not challenge_individual_id:
                logger.log("warning", f"Monthly challenge for {current_month_str} is missing 'individual_id' in 'pokemon' data.")
                return

            last_challenge_id = db.get_user_data("monthly_challenge_id")
            monthly_status = db.get_user_data("monthly_challenge", 0)
            try:
                monthly_status = int(monthly_status)
            except (TypeError, ValueError):
                monthly_status = 0

            # Edge case: Pokémon exists in collection but database tracking values are missing or stale
            pokemon_in_collection = db.get_pokemon(challenge_individual_id) is not None
            
            # RECONCILE FIRST: If Pokémon exists in collection, sync tracking before any reset
            if pokemon_in_collection:
                # Check if we need to reconcile (stale/missing tracking)
                needs_reconciliation = (
                    last_challenge_id is None or 
                    str(last_challenge_id) != str(challenge_individual_id) or
                    monthly_status == 0
                )
                if needs_reconciliation:
                    db.set_user_data("monthly_challenge_id", challenge_individual_id)
                    db.set_user_data("monthly_challenge", 1)
                    logger.log("info", f"Reconciled monthly challenge tracking: Pokémon {challenge_pokemon_data.get('name')} exists in collection, set monthly_challenge_id={challenge_individual_id}, monthly_challenge=1")
                    return

            if last_challenge_id is None or str(last_challenge_id) != str(challenge_individual_id):
                db.set_user_data("monthly_challenge_id", challenge_individual_id)
                db.set_user_data("monthly_challenge", 0)
                monthly_status = 0

            if monthly_status == 2:
                logger.log("info", f"Monthly challenge for {current_month_str} was rejected.")
                return

            if monthly_status == 1 and pokemon_in_collection:
                logger.log("info", f"User already has the Pokémon for {current_month_str} (ID: {challenge_individual_id}).")
                return

            logger.log("info", f"Awarding Pokémon for {current_month_str}: {challenge_pokemon_data.get('name')}")
            make_shiny = False
            prev_id = current_challenge.get("previous_challenge_individual_id")
            threshold = current_challenge.get("defeat_threshold")

            if prev_id and threshold:
                logger.log("info", f"Checking for shiny eligibility: prev_id={prev_id}, threshold={threshold}")
                previous_challenge_pokemon = db.get_pokemon(prev_id)
                if previous_challenge_pokemon:
                    try:
                        meets_threshold = int(previous_challenge_pokemon.get("pokemon_defeated", 0)) >= int(threshold)
                    except (ValueError, TypeError):
                        meets_threshold = False
                    if meets_threshold:
                        logger.log("info", f"Shiny criteria met for {challenge_pokemon_data.get('name')}.")
                        make_shiny = True
            
            new_pokemon = create_monthly_challenge_pokemon(challenge_pokemon_data, make_shiny=make_shiny)
            shiny_text = " (Shiny)" if new_pokemon["shiny"] else ""
            description = current_challenge.get("description", "")
            accepted = show_monthly_challenge_dialog(new_pokemon, description, parent_window=mw)
            if accepted:
                db.set_user_data("monthly_challenge", 1)
                success = add_pokemon_to_collection(new_pokemon, parent_window=mw)
                if success:
                    logger.log("info", f"Successfully awarded {new_pokemon['name']}{shiny_text}.")
                    show_monthly_acceptance_dialog(parent_window=mw, challenge_pokemon=new_pokemon)
                else:
                    db.set_user_data("monthly_challenge", 0)
                    logger.log("error", f"Failed to add {new_pokemon['name']} to collection. Status rolled back.")
            else:
                db.set_user_data("monthly_challenge", 2)
                show_monthly_rejection_dialog(parent_window=mw, challenge_pokemon=new_pokemon)
                logger.log("info", f"User rejected {new_pokemon['name']}{shiny_text}.")

        except Exception as e:
            logger.log("error", f"An unexpected error occurred in check_and_award_monthly_pokemon: {e}")
            # Still failing silently on the user's end, but with more detailed logs for debugging.
            pass

    # Defer execution to avoid blocking the profile_did_open callback
    if defer:
        QTimer.singleShot(0, _do_check)
    else:
        _do_check()

def parse_to_canonical(code_str):
    if not code_str:
        return None
    parts = [p.strip() for p in code_str.strip().split(',') if p.strip()]
    if not parts:
        return None
    try:
        if parts[0] == "-200":
            if len(parts) < 18:
                return None
            species_id = int(parts[1])
            level = int(parts[2])
            gender = int(parts[3])
            shiny = int(parts[4])
            evs = [int(x) for x in parts[5:11]]
            ivs = [int(x) for x in parts[11:17]]
            nature = int(parts[17])
            attacks = [int(x) for x in parts[18:]]
        else:
            if len(parts) < 16:
                return None
            species_id = int(parts[0])
            level = int(parts[1])
            gender = int(parts[2])
            shiny = int(parts[3])
            evs = [int(x) for x in parts[4:10]]
            ivs = [int(x) for x in parts[10:16]]
            nature = 12  # Default to Serious
            attacks = [int(x) for x in parts[16:]]
            
        while len(attacks) < 4:
            attacks.append(33)
        attacks = attacks[:4]
        
        canonical = [species_id, level, gender, shiny] + evs + ivs + [nature] + attacks
        return ",".join(map(str, canonical))
    except Exception:
        return None


class PokemonTrade:
    TRADE_VERSION = "02"

    def __init__(self, name, id, level, ability, iv, ev, gender, attacks, individual_id, shiny, logger, refresh_callback, parent_window=None, nature="serious"):
        self.name = name
        self.id = id
        self.level = level
        self.ability = ability
        self.iv = iv
        self.ev = ev
        self.gender = gender
        self.attacks = attacks
        self.individual_id = individual_id
        self.shiny = shiny
        self.nature = nature
        self.refresh_callback = refresh_callback
        self.logger = logger
        self.parent_window = parent_window
        self.pokeapi_db_path = pokeapi_db_path
        self.moves_file_path = moves_file_path
        self.pokedex_path = pokedex_path
        self.check_and_trade()

    def _should_show_sprites(self) -> bool:
        """Check if sprites should be shown based on the setting."""
        try:
            settings_obj = services.settings
            if settings_obj is not None:
                return settings_obj.get("gui.show_sprites_across_ankimon", True)
            return True
        except Exception:
            return True

    def load_pokemon_data(self):
        """Load main pokemon data from database."""
        try:
            db = services.db
            main_pokemon = db.get_main_pokemon()
            return [main_pokemon] if main_pokemon else []
        except Exception as e:
            show_warning_with_traceback(parent=self.parent_window, exception=e, message="Error loading main Pokémon!")
            return []

    def check_and_trade(self):
        pokemon_data = self.load_pokemon_data()
        for pokemon in pokemon_data:
            if self._match_main_pokemon(pokemon):
                self.logger.log_and_showinfo("warning", "You can't trade your Main Pokémon!\nPlease pick a different Main Pokémon.")
                return
        self.open_trade_window()

    def _match_main_pokemon(self, pokemon):
        return (
            pokemon["name"] == self.name and pokemon["id"] == self.id and pokemon["level"] == self.level and
            pokemon["ability"] == self.ability and pokemon["iv"] == self.iv and pokemon["ev"] == self.ev and
            pokemon["gender"] == self.gender and pokemon["attacks"] == self.attacks and pokemon["shiny"] == self.shiny
        )

    def open_trade_window(self):
        parent = self.parent_window if self.parent_window is not None else mw
        window = QDialog(parent)
        window.setWindowTitle(f"Trade Pokémon: {self.name}")
        window.setWindowModality(Qt.WindowModality.ApplicationModal)
        window.setMinimumSize(380, 450)

        main_layout = QVBoxLayout(window)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        title_label = QLabel(f"Trading Away: {self.name}")
        title_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)

        sprites_layout = QHBoxLayout()
        sprites_layout.setSpacing(20)

        your_pokemon_layout = QVBoxLayout()
        your_pokemon_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from PyQt6.QtGui import QMovie, QImage, QPixmap
        your_pokemon_sprite_label = QLabel()
        sprite_size = QSize(64, 64)
        your_pokemon_sprite_label.setMaximumSize(sprite_size)
        your_pokemon_sprite_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        your_pokemon_sprite_label.setScaledContents(False)
        your_pokemon_sprite_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        
        # Only load and display sprite if setting allows
        show_sprites = self._should_show_sprites()
        if show_sprites:
            your_pokemon_gif_path = get_sprite_path(side="front", sprite_type="gif", id=self.id, shiny=getattr(self, "shiny", False), gender=self.gender, pokemon_name=self.name)
            
            your_pokemon_movie = QMovie(your_pokemon_gif_path)
            def set_bw_frame():
                frame = your_pokemon_movie.currentImage()
                if not frame.isNull():
                    gray = QImage(frame.size(), QImage.Format.Format_ARGB32)
                    for y in range(frame.height()):
                        for x in range(frame.width()):
                            color = frame.pixelColor(x, y)
                            alpha = color.alpha()
                            gray_value = int(0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue())
                            gray.setPixelColor(x, y, QColor(gray_value, gray_value, gray_value, alpha))
                    scaled = QPixmap.fromImage(gray).scaled(sprite_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    your_pokemon_sprite_label.setPixmap(scaled)
            your_pokemon_movie.frameChanged.connect(lambda _: set_bw_frame())
            your_pokemon_sprite_label.setMovie(your_pokemon_movie)
            your_pokemon_movie.start()
            set_bw_frame()
        else:
            # Display a transparent placeholder when sprites are hidden to preserve layout geometry
            transparent_pixmap = QPixmap(64, 64)
            transparent_pixmap.fill(Qt.GlobalColor.transparent)
            your_pokemon_sprite_label.setPixmap(transparent_pixmap)
        
        your_pokemon_name_label = QLabel(f"{self.name}")
        your_pokemon_name_label.setFont(QFont("Arial", 12))
        your_pokemon_layout.addWidget(your_pokemon_sprite_label)
        your_pokemon_layout.addWidget(your_pokemon_name_label)
        sprites_layout.addLayout(your_pokemon_layout)

        trade_icon_label = QLabel("->")
        trade_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sprites_layout.addWidget(trade_icon_label)

        other_pokemon_layout = QVBoxLayout()
        other_pokemon_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.other_pokemon_sprite_label = QLabel()
        self.other_pokemon_sprite_label.setMaximumSize(sprite_size)
        self.other_pokemon_sprite_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.other_pokemon_sprite_label.setScaledContents(False)
        self.other_pokemon_sprite_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        
        if show_sprites:
            self.other_pokemon_sprite_label.setPixmap(QPixmap(":/icons/pokeball.png").scaled(sprite_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            # Use transparent 64x64 placeholder to preserve layout geometry
            transparent_pixmap = QPixmap(64, 64)
            transparent_pixmap.fill(Qt.GlobalColor.transparent)
            self.other_pokemon_sprite_label.setPixmap(transparent_pixmap)
            
        self.other_pokemon_name_label = QLabel("")
        self.other_pokemon_name_label.setFont(QFont("Arial", 12))
        other_pokemon_layout.addWidget(self.other_pokemon_sprite_label)
        other_pokemon_layout.addWidget(self.other_pokemon_name_label)
        sprites_layout.addLayout(other_pokemon_layout)

        main_layout.addLayout(sprites_layout)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(separator)

        self.trade_code_layout = QVBoxLayout()
        self.trade_code_layout.setSpacing(5)

        self.legacy_checkbox = QCheckBox("Legacy Mode (Trade with older Ankimon versions)")
        self.legacy_checkbox.setFont(QFont("Arial", 10))
        self.trade_code_layout.addWidget(self.legacy_checkbox)

        self.your_code_label = QLabel("Your Trade Code:")
        self.your_code_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.trade_code_layout.addWidget(self.your_code_label)

        self.code_display_layout = QHBoxLayout()
        self.trade_code_display = QLineEdit()
        self.trade_code_display.setReadOnly(True)
        self.trade_code_display.setFont(QFont("Courier New", 10))
        self.code_display_layout.addWidget(self.trade_code_display)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip("Copy the trade code to your clipboard")
        # Connect once and read the live display text at click time, rather than
        # disconnecting/reconnecting a fresh lambda on every checkbox toggle
        # (which is fragile and can raise TypeError/RuntimeError in PyQt).
        self.copy_button.clicked.connect(lambda: self.copy_to_clipboard(self.trade_code_display.text()))
        self.code_display_layout.addWidget(self.copy_button)
        self.trade_code_layout.addLayout(self.code_display_layout)

        def update_my_trade_code():
            if self.legacy_checkbox.isChecked():
                code = f"{self.id},{self.level},{self.format_gender()},{self.format_shiny()},{self.ev_string()},{self.iv_string()},{self.attack_ids()}"
            else:
                code = f"-200,{self.id},{self.level},{self.format_gender()},{self.format_shiny()},{self.ev_string()},{self.iv_string()},{self.format_nature()},{self.attack_ids()}"
            self.trade_code_display.setText(code)

        self.legacy_checkbox.stateChanged.connect(lambda _: update_my_trade_code())
        update_my_trade_code()

        main_layout.addLayout(self.trade_code_layout)

        self.their_code_label = QLabel("Enter Their Trade Code:")
        self.their_code_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        main_layout.addWidget(self.their_code_label)

        self.trade_code_input = QLineEdit()
        self.trade_code_input.setPlaceholderText("Paste trade code here")
        self.trade_code_input.textChanged.connect(self.update_other_pokemon_sprite)
        main_layout.addWidget(self.trade_code_input)

        self.trade_button = QPushButton("Generate Trade Password")
        self.trade_button.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.trade_button.setStyleSheet("padding: 10px;")
        self.trade_button.clicked.connect(lambda: self.generate_and_show_passwords(window))
        main_layout.addWidget(self.trade_button)

        window.exec()

    def generate_and_show_passwords(self, window):
        code1 = self.trade_code_display.text().strip()
        code2 = self.trade_code_input.text().strip()
        if not code1 or not code2:
            showWarning("Please enter a valid trade code from the other user.")
            return

        canonical1 = parse_to_canonical(code1)
        canonical2 = parse_to_canonical(code2)

        if not canonical1 or not canonical2:
            showWarning("Invalid trade code format. Please check the code.")
            return

        # Same species check using canonical species IDs
        id1 = int(canonical1.split(',')[0])
        id2 = int(canonical2.split(',')[0])
        if id1 == id2:
            showWarning("You cannot trade with a Pokémon of the same species (ID) as the one you're trading away!")
            return

        self.your_code_label.hide()
        self.trade_code_display.hide()
        self.copy_button.hide()
        self.their_code_label.hide()
        self.trade_code_input.hide()
        self.trade_button.hide()

        # Check if we should use legacy hashing (checkbox checked OR either code is unversioned)
        is_legacy = self.legacy_checkbox.isChecked() or (not code1.startswith("-200")) or (not code2.startswith("-200"))

        if is_legacy:
            codes = sorted([code1, code2])
            combo = codes[0] + "|" + codes[1]
            hash_digest = hashlib.sha256(combo.encode()).hexdigest()
            part1 = hash_digest[:len(hash_digest) // 2]
            part2 = hash_digest[len(hash_digest) // 2:]

            if code1 < code2:
                my_part = part1
                self._their_password_part = part2
            else:
                my_part = part2
                self._their_password_part = part1
        else:
            codes = sorted([canonical1, canonical2])
            combo = codes[0] + "|" + codes[1]
            hash_digest = hashlib.sha256(combo.encode()).hexdigest()
            part1 = hash_digest[:len(hash_digest) // 2]
            part2 = hash_digest[len(hash_digest) // 2:]

            if canonical1 < canonical2:
                my_part = part1
                self._their_password_part = part2
            else:
                my_part = part2
                self._their_password_part = part1

        my_part += self.TRADE_VERSION
        self._their_password_part += self.TRADE_VERSION

        self.password_interface = QFrame()
        self.password_layout = QVBoxLayout(self.password_interface)
        self.password_layout.setSpacing(5)

        your_password_label = QLabel("Your Password (To Send to Trade Partner):")
        your_password_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.password_layout.addWidget(your_password_label)

        your_password_display_layout = QHBoxLayout()
        your_password_display = QLineEdit(my_part)
        your_password_display.setReadOnly(True)
        your_password_display.setFont(QFont("Courier New", 10))
        your_password_display_layout.addWidget(your_password_display)

        copy_password_button = QPushButton("Copy")
        copy_password_button.setToolTip("Copy your password part to the clipboard")
        copy_password_button.clicked.connect(lambda: self.copy_to_clipboard(my_part))
        your_password_display_layout.addWidget(copy_password_button)
        self.password_layout.addLayout(your_password_display_layout)

        their_password_label = QLabel("Enter Trade Partner's Password:")
        their_password_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.password_layout.addWidget(their_password_label)

        self.other_password_input = QLineEdit()
        self.other_password_input.setPlaceholderText("Enter the other person's password part")
        self.password_layout.addWidget(self.other_password_input)

        self.password_button = QPushButton("Perform Trade")
        self.password_button.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.password_button.setStyleSheet("padding: 10px;")
        self.password_button.clicked.connect(lambda: self.handle_trade_with_password(window))
        self.password_layout.addWidget(self.password_button)

        window.layout().addWidget(self.password_interface)

    def handle_trade_with_password(self, parent_window):
        their_part_entered = self.other_password_input.text().strip()
        if not their_part_entered:
            showWarning("Please enter the password part from the other user.")
            return

        if len(their_part_entered) < 34:
            showWarning("Incorrect password format.")
            return

        their_version = their_part_entered[-2:]
        if their_version != self.TRADE_VERSION:
            showWarning(f"Trade incompatible due to Ankimon trade versions. \n\nYour version: {self.TRADE_VERSION}, partner's version: {their_version}.\n\nPlease get the latest version of Ankimon for both users!")
            return

        if their_part_entered == self._their_password_part:
            code = self.trade_code_input.text().strip()
            canonical = parse_to_canonical(code)
            if canonical:
                incoming_id = int(canonical.split(',')[0])
                if incoming_id == self.id:
                    showWarning("You cannot trade with a Pokémon of the same species (ID) as the one you're trading away!")
                    return
            self.confirm_trade(parent_window)
        else:
            showWarning("Incorrect password part. Please check with the other user.")

    def copy_to_clipboard(self, text):
        clipboard = mw.app.clipboard()
        clipboard.setText(text)
        showInfo("Trade code copied to clipboard!")

    def update_other_pokemon_sprite(self, code):
        from PyQt6.QtGui import QMovie, QPixmap
        try:
            sprite_size = QSize(64, 64)
            self.other_pokemon_sprite_label.clear()
            
            # Only show sprites if the setting allows
            show_sprites = self._should_show_sprites()
            if not show_sprites:
                # Use transparent placeholder to preserve layout
                transparent_pixmap = QPixmap(64, 64)
                transparent_pixmap.fill(Qt.GlobalColor.transparent)
                self.other_pokemon_sprite_label.setPixmap(transparent_pixmap)
                self.other_pokemon_name_label.setText("")
                # Still attempt to parse and display the Pokémon name even without sprites
                canonical = parse_to_canonical(code)
                if canonical:
                    parts = canonical.split(',')
                    pokemon_id = int(parts[0])
                    other_name = self.get_pokemon_name_by_id(pokemon_id)
                    self.other_pokemon_name_label.setText(other_name)
                return
            
            self.other_pokemon_sprite_label.setPixmap(QPixmap())
            self.other_pokemon_name_label.setText("")
            
            canonical = parse_to_canonical(code)
            if canonical:
                parts = canonical.split(',')
                pokemon_id = int(parts[0])
                gender_id = parts[2]
                shiny_val = int(parts[3])
                
                gender_map = {"0": "M", "1": "F", "2": "N"}
                other_gender = gender_map.get(gender_id, "M")
                other_shiny = (shiny_val == 1)
                
                other_name = self.get_pokemon_name_by_id(pokemon_id)
                self.other_pokemon_name_label.setText(other_name)
                sprite_path = get_sprite_path(side="front", sprite_type="gif", id=pokemon_id, shiny=other_shiny, gender=other_gender, pokemon_name=other_name)
                
                if hasattr(self, '_other_pokemon_movie') and self._other_pokemon_movie is not None:
                    self._other_pokemon_movie.stop()
                    self._other_pokemon_movie.deleteLater()
                    self._other_pokemon_movie = None
                other_pokemon_movie = QMovie(sprite_path)
                self._other_pokemon_movie = other_pokemon_movie
                
                def set_other_frame():
                    frame = other_pokemon_movie.currentImage()
                    if not frame.isNull():
                        scaled = QPixmap.fromImage(frame).scaled(sprite_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        self.other_pokemon_sprite_label.setPixmap(scaled)
                other_pokemon_movie.frameChanged.connect(lambda _: set_other_frame())
                self.other_pokemon_sprite_label.setMovie(other_pokemon_movie)
                other_pokemon_movie.start()
                set_other_frame()
            else:
                self.other_pokemon_sprite_label.setPixmap(QPixmap(":/icons/pokeball.png").scaled(QSize(64, 64), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                self.other_pokemon_name_label.setText("")
        except Exception:
            if self._should_show_sprites():
                self.other_pokemon_sprite_label.setPixmap(QPixmap(":/icons/pokeball.png").scaled(QSize(64, 64), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                # Use transparent 64x64 placeholder
                transparent_pixmap = QPixmap(64, 64)
                transparent_pixmap.fill(Qt.GlobalColor.transparent)
                self.other_pokemon_sprite_label.setPixmap(transparent_pixmap)
            self.other_pokemon_name_label.setText("")

    def get_pokemon_name_by_id(self, pokemon_id):
        try:
            from ..functions.pokedex_functions import _load_pokedex_cache
            # Use the in-memory pokedex cache: update_other_pokemon_sprite calls
            # this on every keystroke in the trade-code input, so re-reading and
            # re-parsing pokedex.json from disk each time stalls the GUI thread.
            pokedex = _load_pokedex_cache()
            # First pass: check actual_id for precise form match (e.g. Mega Diancie)
            for details in pokedex.values():
                if details.get('actual_id') == pokemon_id:
                    return details.get('name', str(pokemon_id))
            # Second pass fallback: check species_id
            for details in pokedex.values():
                if details.get('species_id') == pokemon_id:
                    return details.get('name', str(pokemon_id))
        except Exception as e:
            show_warning_with_traceback(parent=self.parent_window, exception=e, message=f"An error occurred while getting the Pokémon name for ID {pokemon_id}.")
        return str(pokemon_id)

    def confirm_trade(self, parent_window):
        from PyQt6.QtWidgets import QMessageBox
        code = self.trade_code_input.text()
        name = "the other Pokémon"
        parts = [p.strip() for p in code.split(',')]
        if len(parts) > 0:
            if parts[0] == "-200":
                if len(parts) > 1 and parts[1].isdigit():
                    pokemon_id = int(parts[1])
                    name = self.get_pokemon_name_by_id(pokemon_id)
            elif parts[0].isdigit():
                pokemon_id = int(parts[0])
                name = self.get_pokemon_name_by_id(pokemon_id)
        msg = QMessageBox(parent_window)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Confirm Trade")
        msg.setText(f"Are you sure you want to trade your {self.name} for {name}?")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        result = msg.exec()
        if result == QMessageBox.StandardButton.Yes:
            self.trade_pokemon_in(code)

    def trade_pokemon_in(self, number_code):
        code = number_code.strip()
        try:
            numbers = [int(num) for num in code.split(',')]
            if len(numbers) < 16:
                showWarning("Code is incomplete.")
                return
            incoming_id = numbers[0]
            if incoming_id == -200:
                if len(numbers) < 18:
                    showWarning("Code is incomplete.")
                    return
                incoming_id = numbers[1]
            if incoming_id == self.id:
                showWarning("You cannot trade with a Pokémon of the same species (ID) as the one you're trading away!")
                return
            self.process_trade(numbers)
        except ValueError:
            showWarning("Please enter a valid Pokémon Code!")

    def process_trade(self, numbers):
        from ..functions.pokedex_functions import search_pokedex, get_all_pokemon_moves
        import random
        try:
            if len(numbers) > 0 and numbers[0] == -200:
                pokemon_id, level, gender_id, shiny = numbers[1], numbers[2], numbers[3], numbers[4]
                ev_stats = dict(zip(['hp', 'atk', 'def', 'spa', 'spd', 'spe'], numbers[5:11]))
                iv_stats = dict(zip(['hp', 'atk', 'def', 'spa', 'spd', 'spe'], numbers[11:17]))
                nature_id = numbers[17]
                nature = self.nature_from_id(nature_id)
                attacks = [self.find_move_by_num(attack_id)['name'] for attack_id in numbers[18:]]
            else:
                pokemon_id, level, gender_id, shiny = numbers[0], numbers[1], numbers[2], numbers[3]
                ev_stats = dict(zip(['hp', 'atk', 'def', 'spa', 'spd', 'spe'], numbers[4:10]))
                iv_stats = dict(zip(['hp', 'atk', 'def', 'spa', 'spd', 'spe'], numbers[10:16]))
                nature = "serious"
                attacks = [self.find_move_by_num(attack_id)['name'] for attack_id in numbers[16:]]

            details = self.find_pokemon_by_id(pokemon_id)
            if not details:
                raise ValueError(f"Could not find Pokémon details for ID {pokemon_id}")

            base_experience = get_base_experience(details["actual_id"])

            ability = "No Ability"
            possible_abilities = search_pokedex(details["name"], "abilities")
            if possible_abilities:
                numeric_abilities = {k: v for k, v in possible_abilities.items() if k.isdigit()}
                if numeric_abilities:
                    ability = random.choice(list(numeric_abilities.values()))

            if not attacks or any(a == "Unknown Move" for a in attacks):
                all_possible_moves = get_all_pokemon_moves(details["name"], level)
                if len(all_possible_moves) <= 4:
                    attacks = all_possible_moves
                else:
                    attacks = random.sample(all_possible_moves, 4)

            new_pokemon = {
                "name": details["name"],
                "nickname": "",
                "ability": ability,
                "id": pokemon_id,
                "tier": get_tier_by_id(pokemon_id) or "Normal",
                "gender": self.gender_from_id(gender_id),
                "level": level,
                "type": details["types"],
                "stats": details["baseStats"],
                "ev": ev_stats,
                "iv": iv_stats,
                "attacks": attacks,
                "growth_rate": get_growth_rate(details["species_id"]),
                "current_hp": self.calculate_max_hp(details["baseStats"]["hp"], level, ev_stats, iv_stats),
                "base_experience": base_experience,
                "friendship": 0,
                "pokemon_defeated": 0,
                "everstone": False,
                "shiny": bool(shiny),
                "nature": nature,
                "mega": False,
                "special_form": None,
                "capture_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "individual_id": str(uuid.uuid4())
            }
            new_pokemon["xp"] = 0
            self.replace_pokemon(new_pokemon)
        except Exception as e:
            show_warning_with_traceback(parent=self.parent_window, exception=e, message="An error occurred while processing the trade.")

    def calculate_max_hp(self, base_hp, level, ev, iv):
        ev_value = ev["hp"] / 4
        iv_value = iv["hp"]
        return int((((2 * base_hp + iv_value + ev_value) * level) / 100) + level + 10)

    def find_move_by_num(self, move_num):
        with open(self.moves_file_path, 'r', encoding='utf-8') as file:
            moves_data = json.load(file)
            return next((move for move in moves_data.values() if move.get('num') == move_num), {"name": "Unknown Move"})

    def find_move_by_name(self, move_name):
        with open(self.moves_file_path, 'r', encoding='utf-8') as file:
            moves_data = json.load(file)
            move = next((move for move in moves_data.values() if move.get('name').lower() == move_name.lower()), None)
            if move:
                return move['num']
            else:
                return 33

    def find_pokemon_by_id(self, pokemon_id):
        from ..functions.pokedex_functions import _load_pokedex_cache
        # Use the in-memory pokedex cache instead of re-reading/parsing
        # pokedex.json from disk on the GUI thread for every trade lookup.
        # _load_pokedex_cache() swallows a missing/corrupt pokedex.json and
        # returns {} rather than raising, so surface that specific failure here
        # (a bare `except FileNotFoundError` around this call was unreachable).
        pokedex = _load_pokedex_cache()
        if not pokedex:
            self.logger.log_and_showinfo("warning", "Pokedex file not found or failed to load.")
            return None
        # First pass: check actual_id for precise form match (e.g. Mega Diancie)
        for details in pokedex.values():
            if details.get('actual_id') == pokemon_id:
                return details
        # Second pass fallback: check species_id
        for details in pokedex.values():
            if details.get('species_id') == pokemon_id:
                return details
        self.logger.log_and_showinfo("warning",f"No Pokémon found with ID: {pokemon_id}")
        return None

    def gender_from_id(self, gender_id):
        return {0: "M", 1: "F", 2: "N"}.get(gender_id, "N/A")

    def replace_pokemon(self, new_pokemon):
        """Replace the traded pokemon with the new one in the database."""
        try:
            db = services.db
            
            try:
                db.replace_pokemon(new_pokemon, self.individual_id)
                # The traded-away Pokémon's individual_id is now gone from the DB
                # (it was swapped for new_pokemon's fresh id). If it was the
                # XP-Share target, clear the setting so xp_share_gain_exp doesn't
                # later look up a missing Pokémon and crash. str() guards against
                # any id type mismatch in the compare.
                settings_obj = services.settings
                if settings_obj is not None and str(settings_obj.get("trainer.xp_share")) == str(self.individual_id):
                    settings_obj.set("trainer.xp_share", None)
            except Exception as e:
                show_warning_with_traceback(parent=self.parent_window, exception=e, message=f"An error occurred during trade: {e}")

            self.logger.log_and_showinfo("warning",f"Successfully traded for {new_pokemon['name']}!")
            self.refresh_callback()

        except Exception as e:
            show_warning_with_traceback(parent=self.parent_window, exception=e, message="Error updating Pokémon data.")
    
    def format_gender(self):
        gender_map = {"M": 0, "F": 1, "N": 2}
        return gender_map.get(self.gender, 3)
    
    def format_shiny(self):
        return 1 if self.shiny else 0

    def format_nature(self):
        nature_name = getattr(self, 'nature', 'serious').lower()
        natures = [
            "hardy", "lonely", "brave", "adamant", "naughty",
            "bold", "docile", "relaxed", "impish", "lax",
            "timid", "hasty", "serious", "jolly", "naive",
            "modest", "mild", "quiet", "bashful", "rash",
            "calm", "gentle", "sassy", "careful", "quirky"
        ]
        try:
            return natures.index(nature_name)
        except ValueError:
            return 12  # "serious"

    def nature_from_id(self, nature_id):
        natures = [
            "hardy", "lonely", "brave", "adamant", "naughty",
            "bold", "docile", "relaxed", "impish", "lax",
            "timid", "hasty", "serious", "jolly", "naive",
            "modest", "mild", "quiet", "bashful", "rash",
            "calm", "gentle", "sassy", "careful", "quirky"
        ]
        if 0 <= nature_id < len(natures):
            return natures[nature_id]
        return "serious"

    def ev_string(self):
        return ','.join(str(value) for value in self.ev.values())

    def iv_string(self):
        return ','.join(str(value) for value in self.iv.values())

    def attack_ids(self):
        return ','.join([str(self.find_move_by_name(attack)) for attack in self.attacks])
