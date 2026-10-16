from __future__ import annotations

from typing import Optional

from ..services import services
from ..events import events
from ..pyobj.pokemon_obj import PokemonObject

# Qt is optional so this module imports headless. The painters and the tooltip
# label only run under a GUI; headless, tooltipWithColour emits an event instead
# of drawing — which is exactly the battle log / damage numbers / level-up text
# an agent needs to "see".
try:
    from aqt.qt import QPainter, QLabel, Qt, sip
    from PyQt6.QtGui import QColor, QFont, QPalette
    from PyQt6.QtCore import QRect, QPoint, QSize, QTimer
    from PyQt6.QtWidgets import QApplication, QFrame
    _HAVE_QT = True
except Exception:
    _HAVE_QT = False


def tooltipWithColour(
    msg, color, x=0, y=20, xref=1, parent=None, width=0, height=0, centered=False
):
    # Structured event first — the observable record of this on-screen message,
    # emitted in both GUI and headless modes.
    events.emit("tooltip", message=msg, color=color)

    if not _HAVE_QT:
        return

    settings = services.settings
    reviewer_text_message_box = settings.get("gui.reviewer_text_message_box")
    period = int(
        settings.get("gui.reviewer_text_message_box_time") * 1000
    )  # time for pop up message

    class CustomLabel(QLabel):
        def mousePressEvent(self, evt):
            evt.accept()
            self.close()

    aw = parent or QApplication.activeWindow()
    if aw is None:
        return

    if color == "#6A4DAC":
        y_offset = 40
    elif color == "#F7DC6F":
        y_offset = -40
    elif color == "#F0B27A":
        y_offset = -40
    elif color == "#D2B4DE":
        y_offset = -40
    else:
        y_offset = 0

    if reviewer_text_message_box:
        x = aw.mapToGlobal(QPoint(x + round(aw.width() / 2), 0)).x()
        y = aw.mapToGlobal(QPoint(0, aw.height() - (180 + y_offset))).y()
        lab = CustomLabel(aw)
        # These tooltip windows are created for nearly every review. Hiding them
        # leaves each label owned by the reviewer window forever, causing linear
        # widget/RSS growth. Closing with WA_DeleteOnClose frees the C++ widget.
        lab.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        lab.setFrameShape(QFrame.Shape.StyledPanel)
        lab.setLineWidth(2)
        lab.setWindowFlags(Qt.WindowType.ToolTip)
        lab.setText(msg)
        lab.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)

        if width > 0:
            lab.setFixedWidth(width)
        if height > 0:
            lab.setFixedHeight(height)

        p = QPalette()
        p.setColor(QPalette.ColorRole.Window, QColor(color))
        p.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
        lab.setPalette(p)
        lab.show()
        lab.move(QPoint(x - round(lab.width() * 0.5 * xref), y))
        try:
            QTimer.singleShot(
                period, lambda: lab.close() if lab and not sip.isdeleted(lab) else None
            )
        except Exception:
            QTimer.singleShot(
                3000, lambda: lab.close() if lab and not sip.isdeleted(lab) else None
            )
        logger = services.logger
        if logger is not None:
            logger.log_and_showinfo("game", msg)


def show_in_ankimon_window(msg: str) -> bool:
    """Put ``msg`` in the Ankimon Window's own message box, if it's open on
    the battle view — used for confirmations (caught a Pokémon, switched the
    active companion) that used to only ever appear as a floating Anki
    tooltip. With the window open, that tooltip visually overlapped/competed
    with the window's own message box for the same on-screen text; routing
    the message into the window itself instead reads as one coherent log.
    No-op (not an error) when the window doesn't exist, isn't alive, is
    hidden, or is on a different view (death/first-encounter).

    Returns True only when the message really was painted into the window, so
    callers can fall back to their own tooltip in every other case WITHOUT
    double-showing it when the window did take it.
    """
    if not _HAVE_QT:
        return False
    try:
        from ..utils import is_alive

        test_window = services.test_window
        # isVisible() matters as much as is_alive(): TestWindow.closeEvent()
        # leaves current_view alone, and QWidget.close() only hides the widget
        # (no WA_DeleteOnClose here), so a closed window still answers True to
        # both is_alive() and current_view == "battle". Without this check the
        # message would be painted into a window nobody can see AND report
        # success, which suppresses the caller's tooltip fallback — the text
        # would simply vanish.
        if (
            is_alive(test_window)
            and test_window.isVisible()
            and test_window.current_view == "battle"
        ):
            test_window.force_display_battle(message_text=msg)
            return True
    except Exception:
        pass
    return False


def draw_gender_symbols(
    main_pokemon: PokemonObject,
    enemy_pokemon: PokemonObject,
    painter: QPainter,
    pos_main_pkmn: Optional[tuple[int, int]] = None,
    pos_enemy: Optional[tuple[int, int]] = None,
) -> None:
    """Draw gender symbols for the main and enemy Pokémon on the given painter canvas.

    This function draws gender symbols (♂ for male, ♀ for female) next to the main and enemy Pokémon
    on a canvas using the QPainter object. The gender symbols are drawn at specified positions,
    or default positions if none are provided.

    Args:
        main_pokemon (PokemonObject): The main Pokémon object whose gender symbol will be drawn.
        enemy_pokemon (PokemonObject): The enemy Pokémon object whose gender symbol will be drawn.
        painter (QPainter): The QPainter object used to draw on the canvas.
        pos_main_pkmn (Optional[tuple[int, int]], optional): The (x, y) position where the main
            Pokémon's gender symbol will be drawn. Defaults to aNone.
        pos_enemy (Optional[tuple[int, int]], optional): The (x, y) position where the enemy
            Pokémon's gender symbol will be drawn. Defaults to None.

    Returns:
        None: This function modifies the state of the QPainter object but does not return any value.
    """
    get_gender_symbol = lambda gender: {"M": "♂", "F": "♀"}.get(
        gender, ""
    )  # Gets gender symbol. Returns "" by default
    get_pen_color = lambda gender: (
        QColor(20, 100, 210) if gender == "M" else QColor(210, 20, 20)
    )  # Blue if "M", else Red

    enemy_pokemon_gender_symbol = get_gender_symbol(enemy_pokemon.gender)
    main_pokemon_gender_symbol = get_gender_symbol(main_pokemon.gender)

    color_backup = (
        painter.pen().color()
    )  # Saving the pen's color to reset it after drawing gender symbols

    painter.setPen(
        get_pen_color(enemy_pokemon.gender)
    )  # Text color of the gender symbol
    pos = pos_enemy or (175, 64)
    painter.drawText(pos[0], pos[1], enemy_pokemon_gender_symbol)

    painter.setPen(
        get_pen_color(main_pokemon.gender)
    )  # Text color of the gender symbol
    pos = pos_main_pkmn or (457, 196)
    painter.drawText(pos[0], pos[1], main_pokemon_gender_symbol)

    painter.setPen(
        color_backup
    )  # Going back to the color we had before drawing gender symbols


def draw_stat_boosts(
    main_pokemon: PokemonObject,
    enemy_pokemon: PokemonObject,
    painter: QPainter,
    pos_for_main_pkmn: Optional[tuple[int, int]] = None,
    pos_for_enemy: Optional[tuple[int, int]] = None,
) -> None:
    """Draws visual indicators of stat boosts for two Pokémon using QPainter.

    This function displays the stat boosts (e.g., ATK, DEF, SpA) for both a main Pokémon
    and an enemy Pokémon on a GUI. Each non-neutral boost is represented as a colored rectangle
    containing an abbreviated stat name and its corresponding multiplier.

    Args:
        main_pokemon (PokemonObject): The player's Pokémon whose stat boosts will be drawn.
        enemy_pokemon (PokemonObject): The opposing Pokémon whose stat boosts will be drawn.
        painter (QPainter): The QPainter object used to draw the boost indicators.
        pos_for_main_pkmn (Optional[tuple[int, int]]): The top-left position (x, y) to draw
            the main Pokémon's boosts. If None, nothing will be drawn for the main Pokémon.
        pos_for_enemy (Optional[tuple[int, int]]): The top-left position (x, y) to draw
            the enemy Pokémon's boosts. If None, nothing will be drawn for the enemy Pokémon.

    Returns:
        None: This function performs drawing operations directly via the provided QPainter.

    Notes:
        - Stat stages with a value of 0 (neutral) are not rendered.
        - The function temporarily modifies the painter's pen, brush, and font,
          which are restored to their original state before returning.
    """

    boost_to_mult = {
        0: "x1",
        1: "x1.5",
        2: "x2",
        3: "x2.5",
        4: "x3",
        5: "x3.5",
        6: "x4",
        -1: "x0.67",
        -2: "x0.5",
        -3: "x0.4",
        -4: "x0.33",
        -5: "x0.29",
        -6: "x0.25",
    }

    boost_name_to_abbreviation = {
        "atk": "ATK",
        "def": "DEF",
        "spa": "SpA",
        "spd": "SpD",
        "spe": "SPE",
        "accuracy": "ACC",
        "evasion": "EVD",
    }

    pen_color_backup = (
        painter.pen().color()
    )  # Saving the pen's color to reset it after drawing gender symbols
    brush_color_backup = painter.brush().color()
    font_backup = painter.font()

    w, h = 60, 20

    zipped = zip([main_pokemon, enemy_pokemon], [pos_for_main_pkmn, pos_for_enemy])
    for pokemon, (x, y) in zipped:
        boosts = pokemon.stat_stages
        counter = 0
        for key, val in boosts.items():
            if val == 0:  # Don't draw neutral boost values
                continue

            rect = QRect(QPoint(x + counter * (w + 3), y), QSize(w, h))

            painter.setBrush(QColor(150, 220, 150))
            painter.setPen(QColor(50, 150, 50))
            font = QFont("Early GameBoy", 8, QFont.Weight.Bold)
            if val < 0:
                painter.setBrush(QColor(250, 233, 229))  # Light red background
                painter.setPen(QColor(211, 73, 41))  # Dark red border
            elif val > 0:
                painter.setBrush(QColor(229, 255, 233))  # Light green background
                painter.setPen(QColor(97, 164, 52))  # Dark green border
            painter.setFont(font)

            painter.drawRect(rect)
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                f"{boost_name_to_abbreviation[key]} {boost_to_mult[val]}",
            )

            counter += 1

    painter.setPen(
        pen_color_backup
    )  # Going back to the color we had before drawing gender symbols
    painter.setBrush(brush_color_backup)
    painter.setFont(font_backup)
