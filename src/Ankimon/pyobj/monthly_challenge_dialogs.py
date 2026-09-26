"""Qt dialogs for offering and confirming monthly challenge Pokémon."""

import os
from html import escape

from aqt import mw
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QImageReader, QMovie
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from ..functions.sprite_functions import get_sprite_path
from ..resources import icon_path
from ..services import services


class MonthlyChallengeDialog(QDialog):
    """Require an explicit accept or reject choice instead of Escape."""

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
        else:
            super().keyPressEvent(event)


def _challenge_palette():
    """Read the current theme whenever a dialog opens."""
    from aqt.theme import theme_manager

    if theme_manager.night_mode:
        return {
            "bg": "#0d1117",
            "bg_darker": "#161b22",
            "bg_card_hover": "#252d3f",
            "border": "#2d3748",
            "text": "#f0f6fc",
            "accent_blue": "#58a6ff",
            "accent_green": "#3fb950",
            "blue_solid": "#2474a8",
            "btn_bg": "rgba(88, 166, 255, 0.08)",
            "btn_hover": "rgba(88, 166, 255, 0.18)",
            "btn_primary_bg": "#3fb950",
            "btn_primary_hover": "#2ea043",
            "update_btn_text": "#0d1117",
        }
    return {
        "bg": "#ffffff",
        "bg_darker": "#f0f2f5",
        "bg_card_hover": "#e9ecef",
        "border": "#d0d7de",
        "text": "#24292f",
        "accent_blue": "#0969da",
        "accent_green": "#2da44e",
        "blue_solid": "#1a6fb0",
        "btn_bg": "rgba(9, 105, 218, 0.08)",
        "btn_hover": "rgba(9, 105, 218, 0.18)",
        "btn_primary_bg": "#2da44e",
        "btn_primary_hover": "#2ea043",
        "update_btn_text": "#e6ffea",
    }


def _show_sprites():
    try:
        if services.settings is not None:
            return services.settings.get("gui.show_sprites_across_ankimon", True)
    except Exception:
        pass
    return True


def _build_sprite_box(container_size, sprite_size, challenge_pokemon, show_sprites):
    """Keep animation frames scaled before decoding and owned by their label."""
    sprite_box = QFrame()
    sprite_box.setObjectName("spriteBox")
    sprite_box.setFixedSize(container_size, container_size)
    layout = QVBoxLayout(sprite_box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    sprite_label = QLabel()
    sprite_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sprite_label.setFixedSize(sprite_size, sprite_size)
    sprite_label.setScaledContents(False)
    sprite_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    layout.addWidget(sprite_label)

    if show_sprites and challenge_pokemon is not None:
        try:
            sprite_path = get_sprite_path(
                side="front",
                sprite_type="gif",
                id=challenge_pokemon.get("id", 25),
                shiny=challenge_pokemon.get("shiny", False),
                gender=challenge_pokemon.get("gender", "N"),
                pokemon_name=challenge_pokemon.get("name", "Pikachu"),
            )
            if os.path.exists(sprite_path):
                movie = QMovie(sprite_path)
                movie.setParent(sprite_label)
                # QMovie must not cache an unscaled first frame.
                frame_size = QImageReader(sprite_path).size()
                if frame_size.isValid():
                    movie.setScaledSize(
                        frame_size.scaled(
                            sprite_label.size(), Qt.AspectRatioMode.KeepAspectRatio
                        )
                    )
                sprite_label.setMovie(movie)
                movie.start()
        except Exception:
            pass
    return sprite_box


def _dialog_stylesheet(palette, *, decision):
    button_bg = palette["btn_bg" if decision else "btn_primary_bg"]
    button_text = palette["text" if decision else "bg"]
    button_hover = palette["btn_hover" if decision else "btn_primary_hover"]
    button_border = f"1px solid {palette['border']}" if decision else "none"
    return f"""
        QDialog {{
            background-color: {palette["bg"]}; color: {palette["text"]};
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }}
        QLabel {{ color: {palette["text"]}; background: transparent; }}
        QLabel#descLabel {{ color: #ffffff; font-weight: 700; padding: 0; }}
        QFrame#spriteBox {{
            background-color: {palette["blue_solid"]}; border-radius: {16 if decision else 12}px;
        }}
        QFrame#descBox {{ background-color: {palette["blue_solid"]}; border-radius: 12px; }}
        QPushButton {{
            padding: 8px {20 if decision else 24}px;
            border: {button_border}; border-radius: 8px;
            background: {button_bg}; color: {button_text};
            font-weight: {600 if decision else 700}; min-width: 100px;
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }}
        QPushButton:hover {{
            background: {button_hover}; border-color: {palette["accent_blue"]};
        }}
        QPushButton#acceptBtn {{
            background: {palette["accent_green"]}; border: none;
            color: {palette["update_btn_text"]}; font-weight: 700;
        }}
        QPushButton#acceptBtn:hover {{ background: {palette["btn_primary_hover"]}; }}
        QPushButton#rejectBtn {{
            background: transparent; border: 1px solid {palette["border"]}; color: {palette["text"]};
        }}
        QPushButton#rejectBtn:hover {{
            background: {palette["bg_card_hover"]}; border-color: {palette["text"]};
        }}
    """


def _make_dialog(title, parent_window, minimum_size, *, decision=False):
    parent = parent_window if parent_window is not None else mw
    window = MonthlyChallengeDialog(parent) if decision else QDialog(parent)
    window.setWindowTitle(title)
    window.setWindowIcon(QIcon(str(icon_path)))
    window.setWindowModality(Qt.WindowModality.ApplicationModal)
    window.setMinimumSize(*minimum_size)
    if decision:
        window.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    palette = _challenge_palette()
    window.setStyleSheet(_dialog_stylesheet(palette, decision=decision))
    layout = QVBoxLayout(window)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(16)
    return window, layout, palette


def show_monthly_challenge_dialog(challenge_pokemon, description, parent_window=None):
    """Return True only when the user explicitly accepts the offered Pokémon."""
    window, layout, palette = _make_dialog(
        "Monthly Challenge Begins!", parent_window, (620, 380), decision=True
    )
    shiny_text = " (Shiny !!)" if challenge_pokemon.get("shiny", False) else ""
    title_label = QLabel(
        f"<span style='font-weight: 800; letter-spacing: -0.3px; color: {palette['text']};'>"
        f"!! Monthly Challenge Pokémon is here!: "
        f"<b>{escape(challenge_pokemon['name'])}{shiny_text}</b></span>"
    )
    title_label.setWordWrap(True)
    layout.addWidget(title_label)
    info_label = QLabel("This special Pokémon is yours to keep and train!")
    info_label.setWordWrap(True)
    layout.addWidget(info_label)

    content_layout = QHBoxLayout()
    content_layout.setSpacing(16)
    content_layout.setContentsMargins(0, 8, 0, 8)
    if _show_sprites():
        content_layout.addWidget(
            _build_sprite_box(160, 120, challenge_pokemon, True),
            alignment=Qt.AlignmentFlag.AlignTop,
        )
    desc_box = QFrame()
    desc_box.setObjectName("descBox")
    desc_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    desc_layout = QVBoxLayout(desc_box)
    desc_layout.setContentsMargins(20, 14, 20, 14)
    desc_layout.setSpacing(0)
    desc_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    desc_label = QLabel()
    desc_label.setObjectName("descLabel")
    desc_label.setWordWrap(True)
    desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    desc_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    if description:
        desc_text = escape(description).replace("\n", "<br>")
        desc_label.setText(
            f"<div style='margin: 0; padding: 0;'><b>{desc_text}</b></div>"
        )
    else:
        desc_label.setText("A special Pokémon awaits you!")
    desc_layout.addWidget(desc_label)
    content_layout.addWidget(desc_box)
    layout.addLayout(content_layout)

    discord_label = QLabel(
        "For more information, please check the "
        f'<a href="https://discord.gg/hcq53X5mcu" style="color: {palette["accent_blue"]}; text-decoration: none;">Ankimon Discord</a>!'
    )
    discord_label.setWordWrap(True)
    discord_label.setOpenExternalLinks(True)
    layout.addWidget(discord_label)

    buttons = QHBoxLayout()
    buttons.addStretch()
    reject_button = QPushButton("Reject")
    reject_button.setObjectName("rejectBtn")
    reject_button.setMinimumWidth(100)
    reject_button.clicked.connect(window.reject)
    buttons.addWidget(reject_button)
    accept_button = QPushButton("Accept Pokémon")
    accept_button.setObjectName("acceptBtn")
    accept_button.setMinimumWidth(120)
    accept_button.setDefault(True)
    accept_button.clicked.connect(window.accept)
    buttons.addWidget(accept_button)
    layout.addLayout(buttons)
    return window.exec() == QDialog.DialogCode.Accepted


def _show_confirmation(
    title, message_text, button_text, minimum_height, parent_window, challenge_pokemon
):
    window, layout, _ = _make_dialog(title, parent_window, (520, minimum_height))
    message_layout = QHBoxLayout()
    message_layout.setSpacing(12)
    if _show_sprites() and challenge_pokemon is not None:
        message_layout.addWidget(_build_sprite_box(80, 64, challenge_pokemon, True))
    message = QLabel(message_text)
    message.setWordWrap(True)
    message.setStyleSheet("padding: 4px 0;")
    message_layout.addWidget(message)
    layout.addLayout(message_layout)

    buttons = QHBoxLayout()
    buttons.addStretch()
    button = QPushButton(button_text)
    button.setMinimumWidth(120)
    button.clicked.connect(window.accept)
    buttons.addWidget(button)
    layout.addLayout(buttons)
    window.exec()


def show_monthly_acceptance_dialog(parent_window=None, challenge_pokemon=None):
    """Confirm a saved monthly award and point to its progress."""
    pokemon_name = (
        challenge_pokemon.get("name", "Pokémon") if challenge_pokemon else "Pokémon"
    )
    pokemon_level = challenge_pokemon.get("level", 1) if challenge_pokemon else 1
    message_text = (
        f"Congrats, you've successfully received <b>{escape(str(pokemon_name))}</b> <b>Lvl. {escape(str(pokemon_level))}</b>!<br><br>"
        "Tip: Check your progress at <b>Ankimon → Profile → Monthly Challenge</b> to see your dedication in action!"
    )
    _show_confirmation(
        "Monthly Challenge Accepted!",
        message_text,
        "Let's go!",
        200,
        parent_window,
        challenge_pokemon,
    )


def show_monthly_rejection_dialog(parent_window=None, challenge_pokemon=None):
    """Confirm a saved rejection and explain how to reclaim the reward."""
    message_text = (
        "No problem! If you ever change your mind or decide to take the Tauros by "
        "the horns, head to <b>Ankimon → Profile → Monthly Challenge</b> to reclaim this month's Pokémon. Happy Ankimoning!"
    )
    _show_confirmation(
        "Monthly Challenge Rejected!",
        message_text,
        "Alright!",
        160,
        parent_window,
        challenge_pokemon,
    )
