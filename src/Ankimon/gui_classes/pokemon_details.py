import math
import json
from typing import Any, Callable
import re

from aqt import qconnect
from PyQt6.QtGui import QPixmap, QPainter, QIcon, QColor, QPolygonF, QPen, QBrush
from PyQt6.QtCore import (
    Qt,
    QPointF,
    QRectF,
    QPropertyAnimation,
    QEasingCurve,
    pyqtProperty,
)
from PyQt6.QtWidgets import QScrollArea
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QWidget,
    QMessageBox,
    QTabWidget,
    QGridLayout,
    QSizePolicy,
)

from ..services import services
from ..business import (
    calculate_pokemon_go_cp,
    pokemon_go_raw_stats,
    cp_breakdown_tooltip,
    split_string_by_length,
)
from ..pyobj.attack_dialog import AttackDialog
from ..pyobj.pokemon_trade import PokemonTrade
from ..pyobj.error_handler import show_warning_with_traceback
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.translator import Translator
from ..functions.pokedex_functions import (
    get_pokemon_diff_lang_name,
    get_pokemon_descriptions,
    get_all_pokemon_moves,
    get_pretty_name_for_name,
    find_details_move,
    search_pokedex,
    search_pokedex_by_id,
)
from ..functions.pokemon_functions import find_experience_for_level
from ..functions.friendship_evolution import evolution_readiness
from ..functions.gui_functions import type_icon_path, move_category_path
from ..functions.sprite_functions import get_sprite_path
from ..gui_entities import MovieSplashLabel
from ..utils import format_move_name, load_custom_font
from ..resources import (
    icon_path,
    addon_dir,
    pokemon_tm_learnset_path,
)
from ..texts import (
    attack_details_window_template,
    attack_details_window_template_end,
    remember_attack_details_window_template,
    remember_attack_details_window_template_end,
)

# Scaled sprite pixmaps are cached per (path, shiny, gender) so re-opening the
# details panel doesn't re-load and re-scale the same image from disk.
_SCALED_PIXMAP_CACHE: dict = {}


def _lookup_move_data(attack: str):
    """Find move data using raw/normalized keys, without localized names."""
    move = find_details_move(attack)
    if move:
        return move
    normalized = re.sub(r"[^a-z0-9]", "", attack.lower())
    move = find_details_move(normalized)
    if move:
        return move
    return find_details_move("tackle")


def PokemonCollectionDetailsSplit(
    name: str,
    level: int,
    id: int,
    shiny: bool,
    ability: str,
    type: list[str],
    detail_stats: dict[Any, Any],
    attacks: list[str],
    base_experience: int,
    growth_rate,
    ev: dict[str, int],
    iv: dict[str, int],
    gender: str,
    nickname: str,
    individual_id: str,
    pokemon_defeated: int,
    everstone: bool,
    captured_date: str,
    language: int,
    gif_in_collection,
    remove_levelcap: bool,
    logger: ShowInfoLogger,
    refresh_callback,
    initial_tab_index: int = 0,
    tab_changed_callback=None,
    nature: str = "serious",
    base_stats: dict = None,
    old_stats: dict = None,
    friendship: int = 0,
    evolution_rejected: bool = False,
    friendship_time_enabled: bool = True,
    trigger_evo_callback: Callable = None,
    show_sprites: bool = True,
):
    """Build the details panel as split components.

    Returns ``(header_widget, stats_tabs, footer_widget, stats_dict)`` so
    callers can place the scrollable header, the fixed Stats/IV/EV tabs and the
    action footer independently (and diff ``stats_dict`` against the previous
    Pokémon for the stat-bar slide animation via ``old_stats``).

    Callers that only need the classic single-layout panel should use
    :func:`PokemonCollectionDetails` instead.
    """
    try:
        # Manual-evolution readiness (friendship/level and, on richer data sets,
        # defeat/move methods). The stub carries every key evolution_readiness
        # may consume; unknown keys are ignored.
        pkmn_data_stub = {
            "id": id,
            "level": level,
            "friendship": friendship,
            "evolution_rejected": evolution_rejected,
            "individual_id": individual_id,
            "everstone": everstone,
            "attacks": attacks,
            "pokemon_defeated": pokemon_defeated,
            # Needed for the CSV gender_id gate (Wormadam/Mothim, Vespiquen,
            # Salazzle): without it this panel's status line would silently skip
            # a gate the PC grid and the automatic level-up path both enforce.
            "gender": gender,
        }
        readiness = evolution_readiness(pkmn_data_stub)

        # For Mega/Gmax and Regional forms, the species CSV often has no entry
        # or is hyphenated — use the pretty pokedex name instead.
        if any(
            f in name.lower()
            for f in ["mega", "gmax", "alola", "galar", "hisui", "paldea"]
        ):
            lang_name = get_pretty_name_for_name(name)
            # Use species_id for the description since the descriptions CSV
            # only has base species.
            desc_id = (
                search_pokedex(
                    name.lower().replace(" ", "").replace("-", ""), "species_id"
                )
                or id
            )
            lang_desc = get_pokemon_descriptions(int(desc_id), language)
        else:
            lang_name = get_pokemon_diff_lang_name(int(id), language)
            lang_desc = get_pokemon_descriptions(int(id), language)
        description = lang_desc
        typelayout = QHBoxLayout()
        # Display the Pokémon image
        pkmnimage_label = QLabel()
        pkmnimage_path = get_sprite_path(
            "front", "gif" if gif_in_collection else "png", id, shiny, gender, name
        )

        if not show_sprites:
            # Keep the image area and surrounding layout stable for focus mode.
            pkmnimage_label.setFixedSize(150, 150)
        elif gif_in_collection:
            pkmnimage_label = MovieSplashLabel(pkmnimage_path)
        else:
            cache_key = (str(pkmnimage_path), shiny, gender)
            if cache_key in _SCALED_PIXMAP_CACHE:
                pkmnpixmap = _SCALED_PIXMAP_CACHE[cache_key]
            else:
                pkmnpixmap = QPixmap()
                if not pkmnpixmap.load(str(pkmnimage_path)):
                    logger.log_and_showinfo(
                        "warning", f"Failed to load Pokémon image: {pkmnimage_path}"
                    )
                max_width = 150
                original_width = pkmnpixmap.width()
                if original_width > 0:
                    original_height = pkmnpixmap.height()
                    new_width = max_width
                    new_height = (original_height * max_width) // original_width
                    pkmnpixmap = pkmnpixmap.scaled(
                        new_width,
                        new_height,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    _SCALED_PIXMAP_CACHE[cache_key] = pkmnpixmap

            pkmnimage_label.setPixmap(pkmnpixmap)

        # Load and set type icons
        typeimage_file = f"{type[0].lower()}.png"
        typeimage_path = addon_dir / "addon_sprites" / "Types" / typeimage_file
        pkmntype_label = QLabel()
        pkmntypepixmap = QPixmap()
        if not show_sprites:
            pkmntype_label.setFixedSize(50, 50)
        elif pkmntypepixmap.load(str(typeimage_path)):
            # Optional: Scale type icon to a fixed size (e.g., 50x50) to fit nicely
            pkmntypepixmap = pkmntypepixmap.scaled(
                50, 50, Qt.AspectRatioMode.KeepAspectRatio
            )
            pkmntype_label.setPixmap(pkmntypepixmap)
        else:
            logger.log_and_showinfo(
                "warning", f"Failed to load type icon: {typeimage_path}"
            )

        if len(type) > 1:
            type_image_file2 = f"{type[1].lower()}.png"
            typeimage_path2 = addon_dir / "addon_sprites" / "Types" / type_image_file2
            pkmntype_label2 = QLabel()
            pkmntypepixmap2 = QPixmap()
            if not show_sprites:
                pkmntype_label2.setFixedSize(50, 50)
            elif pkmntypepixmap2.load(str(typeimage_path2)):
                # Optional: Scale second type icon similarly
                pkmntypepixmap2 = pkmntypepixmap2.scaled(
                    50, 50, Qt.AspectRatioMode.KeepAspectRatio
                )
                pkmntype_label2.setPixmap(pkmntypepixmap2)
            else:
                logger.log_and_showinfo(
                    "warning", f"Failed to load type icon: {typeimage_path2}"
                )

        # Custom font
        custom_font = load_custom_font(20, language)
        namefont = load_custom_font(30, language)
        namefont.setUnderline(True)

        # Resolve species_id for the [No. XXX] display. We use the internal
        # 'name' to look up the species_id because forms share dex numbers.
        lookup_name = name.lower().replace(" ", "").replace("-", "")
        species_id = search_pokedex(lookup_name, "species_id")
        if not species_id:
            species_id = id

        dex_prefix = f"[No. {str(species_id).zfill(3)}] "

        # Avoid redundant "Name (Name)" display: nickname is dropped when it is
        # empty, matches the formatted species name, or matches the raw
        # internal name.
        def normalize_name(s):
            if not s:
                return ""
            return "".join(c for c in str(s).lower() if c.isalnum())

        base_display_name = lang_name  # Already formatted by format_lore_name
        shiny_star = " ⭐ " if shiny else ""

        is_redundant = (
            not nickname
            or not str(nickname).strip()
            or normalize_name(nickname) == normalize_name(base_display_name)
            or normalize_name(nickname) == normalize_name(name)
        )

        if is_redundant:
            capitalized_name = f"{dex_prefix}{base_display_name}{shiny_star}"
        else:
            capitalized_name = (
                f"{dex_prefix}{nickname}{shiny_star} ({base_display_name})"
            )

        if (
            language == 11
            or language == 12
            or language == 4
            or language == 3
            or language == 2
            or language == 1
        ):
            result = list(split_string_by_length(description, 30))
        else:
            result = list(split_string_by_length(description, 55))
        description_formated = "\n".join(result)
        description_txt = f"Description: \n {description_formated}"
        lvl = f" Level: {level}"
        from ..localized_text import ability_name, ability_description, nature_name
        ability_local = ability_name(ability, ability.capitalize()) if ability else ""
        ability_txt = f" Ability: {ability_local}"
        ability_desc_local = ability_description(ability) if ability else ""
        nature_display = nature_name(nature or "serious", (nature or "serious").strip().title())
        nature_txt = f" Nature: {nature_display}"
        _stats_dict = {}
        for key in ("hp", "atk", "def", "spa", "spd", "spe"):
            if key in detail_stats:
                # Persisted records may predate full IV/EV dicts — default
                # missing entries to 0 rather than raising KeyError.
                _stats_dict[key] = PokemonObject.calc_stat(
                    key,
                    detail_stats[key],
                    level,
                    (iv or {}).get(key, 0),
                    (ev or {}).get(key, 0),
                    nature,
                )
        _stats_dict["xp"] = detail_stats.get("xp", 0)
        _stats_dict["friendship"] = friendship

        attacks_txt = "MOVES:"
        for attack in attacks:
            attacks_txt += f"\n{format_move_name(attack)}"

        CompleteTable_layout = PokemonDetailsStats(
            _stats_dict,
            growth_rate,
            level,
            remove_levelcap,
            language,
            friendship_bar_max=readiness["bar_max"],
            old_stats=old_stats,
        )

        if gender == "M":
            gender_symbol = "♂"
        elif gender == "F":
            gender_symbol = "♀"
        elif gender == "N":
            gender_symbol = ""
        else:
            gender_symbol = ""

        _cp_stats = base_stats if base_stats is not None else detail_stats
        _attack, _defense, _stamina = pokemon_go_raw_stats(_cp_stats, iv, ev)
        cp_value = calculate_pokemon_go_cp(_attack, _defense, _stamina, level)
        cp_txt = f"CP {cp_value:,}"
        cp_tooltip = cp_breakdown_tooltip(
            {"base_stats": _cp_stats, "iv": iv, "ev": ev, "level": level}
        )

        display_full_name = (
            f"{capitalized_name} - {gender_symbol}"
            if gender_symbol
            else capitalized_name
        )
        name_label = QLabel(display_full_name)
        name_label.setFont(namefont)
        description_label = QLabel(description_txt)
        level_label = QLabel(lvl)
        cp_label = QLabel(cp_txt)
        cp_label.setToolTip(cp_tooltip)
        ability_label = QLabel(ability_txt)
        if ability_desc_local:
            ability_label.setToolTip(ability_desc_local)
        nature_label = QLabel(nature_txt)
        attacks_label = QLabel(attacks_txt)
        pokemon_defeated_label = QLabel(f"Pokémon Defeated: {pokemon_defeated}")
        if captured_date is not None:
            captured_date_label = QLabel(f"Captured: {captured_date.split()[0]}")
        else:
            captured_date_label = QLabel("Captured: N/A")

        level_label.setFont(custom_font)
        cp_label.setFont(custom_font)
        type_label = QLabel("Type:")
        type_label.setFont(custom_font)
        ability_label.setFont(custom_font)
        nature_label.setFont(custom_font)
        attacks_label.setFont(custom_font)
        description_label.setFont(
            load_custom_font(15 if language != 1 else 20, language)
        )
        pokemon_defeated_label.setFont(custom_font)
        captured_date_label.setFont(custom_font)

        if gif_in_collection is False:
            pkmnimage_label.setFixedHeight(100)
        pkmnimage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        type_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignCenter
        )
        ability_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nature_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        attacks_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pokemon_defeated_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        captured_date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        attacks_label.setFixedWidth(230)
        attacks_label.setFixedHeight(80)

        # Evolution UI: show the requirement line whenever the Pokémon is not
        # ready (e.g. "40 friendship to evolve into Espeon · needs Day").
        # When ready, the classic path renders its own "Evolve now" button only
        # if the caller did not supply trigger_evo_callback; callback callers
        # render their evolve button in the right-hand column instead.
        evolution_req_widget = None
        # A secondary note shown alongside the Evolve button when the user
        # previously rejected this evolution (soft state) — the manual button
        # stays available so they can still evolve on demand.
        evolution_note_widget = None
        # The friendship/time evolution feature is gated behind a master toggle,
        # so its UI must only appear when the toggle is on. Every other
        # evolution method (level, and any future methods) is base-game
        # behaviour and always shows.
        show_evolution_ui = readiness["method"] is not None and (
            readiness["method"] != "friendship" or friendship_time_enabled
        )
        if show_evolution_ui:
            if trigger_evo_callback is None and readiness["ready"]:
                evo_name = readiness["evo_name"] or "the next form"
                evolve_now_button = QPushButton(f"✨ Evolve into {evo_name} now")
                evolve_now_button.setFont(custom_font)
                evolve_now_button.setFixedWidth(230)
                evolve_now_button.setStyleSheet(
                    "QPushButton { background-color: #FF69B4; color: white;"
                    " border-radius: 6px; padding: 5px; font-weight: bold; }"
                    " QPushButton:hover { background-color: #FF8DC7; }"
                )

                def evolve_now():
                    # Lazy import: evo_window is a singleton built after this
                    # module is first imported, so importing it at module top
                    # would cycle.
                    from ..singletons import evo_window

                    # ask_pokemon_evo is modeless and returns immediately, so a
                    # refresh here would run BEFORE the user confirms — a no-op.
                    # The real refresh happens inside evolve_pokemon after
                    # confirmation.
                    evo_window.ask_pokemon_evo(individual_id, id, readiness["evo_id"])

                qconnect(evolve_now_button.clicked, evolve_now)
                evolution_req_widget = evolve_now_button

                if readiness.get("rejected"):
                    evolution_note_label = QLabel(
                        "Evolution rejected — tap Evolve now to override"
                    )
                    evolution_note_label.setFont(custom_font)
                    evolution_note_label.setWordWrap(True)
                    evolution_note_label.setFixedWidth(230)
                    evolution_note_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    evolution_note_label.setStyleSheet("color: #FF69B4;")
                    evolution_note_widget = evolution_note_label
            elif not readiness["ready"] and readiness["status_text"]:
                evolution_req_label = QLabel(readiness["status_text"])
                evolution_req_label.setFont(custom_font)
                evolution_req_label.setWordWrap(True)
                evolution_req_label.setFixedWidth(230)
                evolution_req_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                evolution_req_label.setStyleSheet("color: #FF69B4;")
                evolution_req_widget = evolution_req_label

        first_layout = QHBoxLayout()
        TopL_layout_Box = QVBoxLayout()
        TopR_layout_Box = QVBoxLayout()
        TopR_layout_Box.setAlignment(Qt.AlignmentFlag.AlignCenter)

        typelayout_widget = QWidget()
        TopL_layout_Box.addWidget(level_label)
        TopL_layout_Box.addWidget(cp_label)
        TopL_layout_Box.addWidget(pkmnimage_label)

        typelayout.addWidget(type_label)
        typelayout.addWidget(pkmntype_label)
        pkmntype_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if len(type) > 1:
            typelayout.addWidget(pkmntype_label2)
            pkmntype_label2.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
            )

        typelayout_widget.setLayout(typelayout)
        typelayout_widget.setFixedWidth(230)
        TopL_layout_Box.addWidget(
            typelayout_widget, alignment=Qt.AlignmentFlag.AlignCenter
        )
        TopL_layout_Box.addWidget(ability_label)
        TopL_layout_Box.addWidget(nature_label)
        TopL_layout_Box.addWidget(captured_date_label)
        TopL_layout_Box.addWidget(pokemon_defeated_label)
        if evolution_req_widget is not None:
            TopL_layout_Box.addWidget(evolution_req_widget)
        if evolution_note_widget is not None:
            TopL_layout_Box.addWidget(evolution_note_widget)

        attacks_details_button = QPushButton("Attack Details")
        qconnect(attacks_details_button.clicked, lambda: attack_details_window(attacks))
        remember_attacks_details_button = QPushButton("Remember Attacks")
        all_attacks = get_all_pokemon_moves(name, level)
        qconnect(
            remember_attacks_details_button.clicked,
            lambda: remember_attack_details_window(
                individual_id, attacks, all_attacks, logger, refresh_callback
            ),
        )
        forget_attacks_details_button = QPushButton("Forget Attacks")
        qconnect(
            forget_attacks_details_button.clicked,
            lambda: forget_attack_details_window(
                individual_id, attacks, logger, refresh_callback
            ),
        )

        tm_attacks_details_button = QPushButton("Learn attacks from TMs")
        qconnect(
            tm_attacks_details_button.clicked,
            lambda: tm_attack_details_window(
                id, individual_id, attacks, logger, refresh_callback
            ),
        )

        # Pin padding across ALL button states so Anki's hover theme never
        # shifts the geometry.
        _BTN_STYLE = (
            "QPushButton {"
            "  min-width: 230px; max-width: 230px;"
            "  padding: 4px 8px;"
            "  text-align: center;"
            "}"
            "QPushButton:hover {"
            "  padding: 4px 8px;"  # identical padding — no layout reflow on hover
            "}"
            "QPushButton:pressed {"
            "  padding: 4px 8px;"
            "}"
        )
        for btn in [
            attacks_details_button,
            remember_attacks_details_button,
            forget_attacks_details_button,
            tm_attacks_details_button,
        ]:
            btn.setFixedWidth(230)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(_BTN_STYLE)

        TopR_layout_Box.addWidget(attacks_label)
        TopR_layout_Box.setAlignment(attacks_label, Qt.AlignmentFlag.AlignHCenter)
        TopR_layout_Box.addWidget(attacks_details_button)
        TopR_layout_Box.setAlignment(
            attacks_details_button, Qt.AlignmentFlag.AlignHCenter
        )
        TopR_layout_Box.addWidget(remember_attacks_details_button)
        TopR_layout_Box.setAlignment(
            remember_attacks_details_button, Qt.AlignmentFlag.AlignHCenter
        )
        TopR_layout_Box.addWidget(forget_attacks_details_button)
        TopR_layout_Box.setAlignment(
            forget_attacks_details_button, Qt.AlignmentFlag.AlignHCenter
        )
        TopR_layout_Box.addWidget(tm_attacks_details_button)
        TopR_layout_Box.setAlignment(
            tm_attacks_details_button, Qt.AlignmentFlag.AlignHCenter
        )

        # Caller-driven evolution trigger (split-panel path): render the evolve
        # button in the right-hand column and delegate the actual evolution to
        # the callback. The same friendship master-toggle gating applies.
        if readiness["ready"] and trigger_evo_callback and show_evolution_ui:
            translator = services.translator or Translator(language)
            evolve_text = translator.translate(
                "evolve_now_button",
                evo_name=readiness["evo_name"] or "the next form",
            )
            evolve_now_button = QPushButton(evolve_text.upper())
            evolve_now_button.setCursor(Qt.CursorShape.PointingHandCursor)
            evolve_now_button.setObjectName("evolveNowButton")

            # Match TM button typography/color exactly, but increase height to 40px
            evolve_now_button.setFixedHeight(40)
            evolve_now_button.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2563eb, stop:1 #1d4ed8);
                    color: #f8fafc;
                    font-size: 11px;
                    font-weight: 800;
                    letter-spacing: 0.5px;
                    border: 1px solid #1e40af;
                    border-radius: 6px;
                    margin-top: 12px;
                    padding: 4px 8px;
                    text-align: center;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3b82f6, stop:1 #2563eb);
                    border-color: #3b82f6;
                    margin-top: 12px;
                    padding: 4px 8px;
                    text-align: center;
                }
                QPushButton:pressed {
                    background: #1e40af;
                    margin-top: 12px;
                    padding: 5px 8px 3px 8px;
                    text-align: center;
                }
            """)
            qconnect(
                evolve_now_button.clicked,
                lambda: trigger_evo_callback(readiness["method"]),
            )
            TopR_layout_Box.addWidget(evolve_now_button)

        TopR_widget = QWidget()
        TopR_widget.setLayout(TopR_layout_Box)
        TopR_widget.setFixedWidth(240)

        first_layout.addLayout(TopL_layout_Box, 1)
        first_layout.addWidget(TopR_widget)

        # Create container widgets for split parts
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.addWidget(name_label)
        header_layout.addLayout(first_layout)
        header_layout.addWidget(description_label)

        # Create tabbed widget for Stats / IV / EV
        stats_tabs = QTabWidget()

        # Tab 1: Stats
        stats_widget = QWidget()
        stats_widget.setLayout(CompleteTable_layout)
        stats_tabs.addTab(stats_widget, "Stats")

        # Tab 2: IV
        iv_widget = QWidget()
        iv_layout = create_iv_ev_tab_layout(iv, "IV", 31, language)
        iv_widget.setLayout(iv_layout)
        stats_tabs.addTab(iv_widget, "IV")

        # Tab 3: EV
        ev_widget = QWidget()
        ev_layout = create_iv_ev_tab_layout(ev, "EV", 255, language)
        ev_widget.setLayout(ev_layout)
        stats_tabs.addTab(ev_widget, "EV")

        stats_tabs.setFixedHeight(220)

        # Set initial tab and connect callback for persistence
        stats_tabs.setCurrentIndex(initial_tab_index)
        if tab_changed_callback:
            stats_tabs.currentChanged.connect(tab_changed_callback)

        free_pokemon_button = QPushButton("Release Pokémon")
        qconnect(
            free_pokemon_button.clicked,
            lambda: PokemonFree(individual_id, name, logger, refresh_callback),
        )
        trade_pokemon_button = QPushButton("Trade Pokémon")
        qconnect(
            trade_pokemon_button.clicked,
            lambda: PokemonTrade(
                name,
                id,
                level,
                ability,
                iv,
                ev,
                gender,
                attacks,
                individual_id,
                shiny,
                logger,
                refresh_callback,
                nature=nature,
            ),
        )
        rename_button = QPushButton("Rename Pokémon")
        rename_input = QLineEdit()
        rename_input.setPlaceholderText("Enter a new Nickname for your Pokémon")
        qconnect(
            rename_button.clicked,
            lambda: rename_pkmn(
                rename_input.text(), name, individual_id, logger, refresh_callback
            ),
        )

        # Row 1: Action Buttons (Trade / Release)
        actions_layout = QHBoxLayout()
        actions_layout.addWidget(trade_pokemon_button)
        actions_layout.addWidget(free_pokemon_button)

        # Row 2: Rename (Input + Button)
        rename_layout = QHBoxLayout()
        rename_layout.addWidget(rename_input, 1)
        rename_button.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        rename_button.adjustSize()
        rename_layout.addWidget(rename_button, 0)

        # Create container footer widget
        footer_widget = QWidget()
        footer_layout = QVBoxLayout(footer_widget)
        footer_layout.addLayout(actions_layout)
        footer_layout.addLayout(rename_layout)

        # Return the split components and the newly calculated stats dict
        return header_widget, stats_tabs, footer_widget, _stats_dict

    except Exception as e:
        show_warning_with_traceback(
            exception=e, message="Error occured in Pokemon Details Button:"
        )
        # Return empty structures on error
        return QWidget(), QWidget(), QWidget(), {}


def PokemonCollectionDetails(
    name: str,
    level: int,
    id: int,
    shiny: bool,
    ability: str,
    type: list[str],
    detail_stats: dict[Any, Any],
    attacks: list[str],
    base_experience: int,
    growth_rate,
    ev: dict[str, int],
    iv: dict[str, int],
    gender: str,
    nickname: str,
    individual_id: str,
    pokemon_defeated: int,
    everstone: bool,
    captured_date: str,
    language: int,
    gif_in_collection,
    remove_levelcap: bool,
    logger: ShowInfoLogger,
    refresh_callback,
    initial_tab_index: int = 0,
    tab_changed_callback=None,
    nature: str = "serious",
    base_stats: dict = None,
    old_stats: dict = None,
    friendship: int = 0,
    evolution_rejected: bool = False,
    friendship_time_enabled: bool = True,
    trigger_evo_callback: Callable = None,
    show_sprites: bool = True,
):
    """Classic single-layout details panel (backward-compatible entrypoint).

    Assembles the split components from :func:`PokemonCollectionDetailsSplit`
    into one ``QVBoxLayout``, which is what the current PC-box details panel
    consumes (``setLayout``/``clear_layout``). Callers that place the header,
    stats tabs and footer independently should use the Split variant directly.
    """
    header_widget, stats_tabs, footer_widget, _stats_dict = (
        PokemonCollectionDetailsSplit(
            name=name,
            level=level,
            id=id,
            shiny=shiny,
            ability=ability,
            type=type,
            detail_stats=detail_stats,
            attacks=attacks,
            base_experience=base_experience,
            growth_rate=growth_rate,
            ev=ev,
            iv=iv,
            gender=gender,
            nickname=nickname,
            individual_id=individual_id,
            pokemon_defeated=pokemon_defeated,
            everstone=everstone,
            captured_date=captured_date,
            language=language,
            gif_in_collection=gif_in_collection,
            remove_levelcap=remove_levelcap,
            logger=logger,
            refresh_callback=refresh_callback,
            initial_tab_index=initial_tab_index,
            tab_changed_callback=tab_changed_callback,
            nature=nature,
            base_stats=base_stats,
            old_stats=old_stats,
            friendship=friendship,
            evolution_rejected=evolution_rejected,
            friendship_time_enabled=friendship_time_enabled,
            trigger_evo_callback=trigger_evo_callback,
            show_sprites=show_sprites,
        )
    )
    layout = QVBoxLayout()
    layout.addWidget(header_widget)
    layout.addWidget(stats_tabs)
    layout.addWidget(footer_widget)
    return layout


def PokemonDetailsStats(
    detail_stats,
    growth_rate,
    level,
    remove_levelcap,
    language,
    friendship_bar_max=400,
    old_stats=None,
):
    CompleteTable_layout = QVBoxLayout()
    CompleteTable_layout.addSpacing(15)
    # Stat colors
    stat_colors = {
        "hp": QColor(255, 0, 0),  # Red
        "atk": QColor(255, 165, 0),  # Orange
        "def": QColor(255, 255, 0),  # Yellow
        "spa": QColor(0, 0, 255),  # Blue
        "spd": QColor(0, 128, 0),  # Green
        "spe": QColor(255, 192, 203),  # Pink
        "total": QColor(168, 168, 167),  # Beige
        "xp": QColor(58, 155, 220),  # lightblue
        "friendship": QColor(255, 105, 180),  # Hot pink
        # Add any other stats that might appear
        "current_hp": QColor(200, 0, 0),  # Darker red
        "max_hp": QColor(255, 0, 0),  # Red
    }

    # custom font
    custom_font = load_custom_font(20, language)

    # Populate the table and create the stat bars
    # Use short names matching IV/EV to allow fitting in compact views
    display_names = {
        "hp": "HP",
        "atk": "Attack",
        "def": "Defense",
        "spa": "Sp. Atk",
        "spd": "Sp. Def",
        "spe": "Speed",
        "xp": "XP",
        "friendship": "Friendship",
    }

    # 1. Query the max level in the player's collection to dynamically scale
    #    the visual baseline.
    max_level = 100
    # services.db is None for headless/registry-less callers (same idiom as
    # pyobj/settings.py); they keep the default baseline.
    if services.db is not None:
        try:
            cursor = services.db.execute("SELECT MAX(level) FROM captured_pokemon")
            row = cursor.fetchone()
            if row and row[0] is not None:
                max_level = int(row[0])
        except Exception:
            # A broken/locked DB keeps the default baseline too.
            pass

    # 2. Get the maximum stat of the currently selected Pokémon (handles manual
    #    DB/JSON stat edits).
    core_stats = ["hp", "atk", "def", "spa", "spd", "spe"]
    current_max_stat = (
        max(detail_stats.get(s, 0) for s in core_stats)
        if any(s in detail_stats for s in core_stats)
        else 0
    )

    # 3. Determine unified global maximum for this database context
    global_max_stat = max(750, max_level * 7.5, current_max_stat)

    for row, (stat, value) in enumerate(detail_stats.items()):
        # Skip unknown stats that are not in stat_colors
        if stat not in stat_colors:
            continue

        display_name = display_names.get(stat, stat.capitalize())
        stat_item2 = QLabel(display_name)
        max_width_stat_item = 200  # Used for BAR math, not label width anymore
        stat_item2.setFixedWidth(100)  # Match IV/EV width
        stat_item2.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        # Friendship is uncapped (it keeps climbing past the bar's threshold as
        # a "flex" stat), so a full bar alone is ambiguous. Mark the value with
        # a ✓ once it has met/exceeded the threshold the bar fills at, so the
        # player can tell "full bar" means "requirement met" rather than just a
        # high number that happens to be clipped. Coerce defensively: a
        # legacy/None friendship must not blank out the whole details panel.
        friendship_value = 0
        if stat == "friendship":
            try:
                friendship_value = int(value)
            except (TypeError, ValueError):
                friendship_value = 0
        if stat == "friendship" and friendship_value >= int(friendship_bar_max):
            value_item2 = QLabel(f"{friendship_value} ✓")
            value_item2.setToolTip(
                "Friendship requirement reached (it keeps rising beyond this)."
            )
        else:
            value_item2 = QLabel(str(value))
        stat_item2.setFont(custom_font)
        value_item2.setFont(custom_font)
        value_item2.setFixedWidth(80)
        value_item2.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        # Map the previous value (if any) so the bar can slide from it.
        if old_stats and stat in old_stats:
            old_val = old_stats[stat]
            if stat == "xp":
                # find_experience_for_level returns 0 for unknown growth rates
                # / missing CSV rows — clamp to 1 to avoid ZeroDivisionError.
                experience = max(
                    1, int(find_experience_for_level(growth_rate, level, True))
                )
                old_val_mapped = int((int(old_val) / experience) * max_width_stat_item)
            elif stat == "friendship":
                try:
                    old_friendship = int(old_val)
                except (TypeError, ValueError):
                    old_friendship = 0
                # Bar reads 100% exactly at the evolution threshold (bar_max).
                old_val_mapped = min(
                    max_width_stat_item,
                    int(
                        (old_friendship / max(1, friendship_bar_max))
                        * max_width_stat_item
                    ),
                )
            else:
                old_val_mapped = int(
                    (math.sqrt(max(0, old_val)) / math.sqrt(global_max_stat))
                    * max_width_stat_item
                )
        else:
            old_val_mapped = 0

        if stat == "xp":
            # Same zero-experience clamp as the old_stats mapping above.
            experience = max(
                1, int(find_experience_for_level(growth_rate, level, True))
            )
            new_val_mapped = int((int(value) / experience) * max_width_stat_item)
        elif stat == "friendship":
            # Bar reads 100% exactly at the evolution threshold (bar_max).
            new_val_mapped = min(
                max_width_stat_item,
                int(
                    (friendship_value / max(1, friendship_bar_max))
                    * max_width_stat_item
                ),
            )
        else:
            new_val_mapped = int(
                (math.sqrt(max(0, value)) / math.sqrt(global_max_stat))
                * max_width_stat_item
            )

        bar_item2 = AnimatedStatBar(
            stat_colors.get(stat), old_val_mapped, new_val_mapped
        )

        layout_row = QHBoxLayout()
        layout_row.setContentsMargins(0, 0, 0, 0)  # Tight layout
        layout_row.addStretch()  # Add stretch padding at start (Centers the content)
        layout_row.addWidget(stat_item2)
        layout_row.addWidget(value_item2)  # Value is now fixed width

        layout_row.addWidget(bar_item2)
        layout_row.addStretch()  # Ensure alignment logic is identical
        stat_item2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        CompleteTable_layout.addLayout(layout_row)

    return CompleteTable_layout


class AnimatedStatBar(QWidget):
    def __init__(self, color: QColor, old_value: float, new_value: float, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 10)
        self._color = color if color else QColor(128, 128, 128)
        self._current_value = float(old_value)
        self.new_value = float(new_value)

        # Ensure values don't exceed max width
        if self._current_value > 200:
            self._current_value = 200
        if self.new_value > 200:
            self.new_value = 200

        # Parent the animation to this widget (third arg) so Qt destroys it as a
        # child when the bar is deleteLater()'d. Without a parent the animation
        # outlives the widget and keeps ticking into current_value/self.update()
        # after the C++ object is gone — re-selecting a Pokémon mid-slide
        # (pc_box swap_stack_widget) would then raise RuntimeError from inside the
        # animation callback (PyQt aborts on that boundary -> SIGABRT).
        self.animation = QPropertyAnimation(self, b"current_value", self)
        self.animation.setDuration(800)  # 800ms for a smooth slide
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.setStartValue(self._current_value)
        self.animation.setEndValue(self.new_value)
        self.animation.start()

    @pyqtProperty(float)
    def current_value(self):
        return self._current_value

    @current_value.setter
    def current_value(self, val):
        self._current_value = val
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background bar
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 200))  # Semi-transparent black
        painter.drawRoundedRect(0, 0, 200, 10, 3, 3)

        # Foreground colored bar
        painter.setBrush(self._color)
        painter.drawRoundedRect(0, 0, int(self._current_value), 10, 3, 3)


def createStatBar(color, value):
    # Fallback for non-animated uses: a static bar is an animation whose start
    # and end values coincide.
    bar = AnimatedStatBar(color, value, value)
    return bar


def create_iv_ev_tab_layout(
    values: dict[str, int], value_type: str, max_val: int, language: int
) -> QGridLayout:
    """
    Create a layout for displaying IV or EV values in a tab.

    Args:
        values: Dictionary of stat values (hp, atk, def, spa, spd, spe)
        value_type: "IV" or "EV" for display purposes
        max_val: Maximum value (31 for IV, 255 for EV)
        language: Language code for font loading

    Returns:
        QGridLayout containing the stat display
    """
    layout = QGridLayout()
    layout.setContentsMargins(0, 0, 0, 0)  # Remove default margins to use full space

    # --- Chart Section ---
    chart_color = (
        QColor(61, 125, 202, 150) if value_type == "IV" else QColor(255, 165, 0, 150)
    )  # Blue for IV, Orange for EV
    border_color = QColor(61, 125, 202) if value_type == "IV" else QColor(255, 165, 0)

    radar_chart = RadarChart(values, max_val, chart_color, border_color, language)

    # Add chart to center (row 0, col 0)
    layout.addWidget(radar_chart, 0, 0, Qt.AlignmentFlag.AlignCenter)

    # For EV, show total at top left of same cell (overlap)
    # This prevents the text from pushing the chart down/sideways
    if value_type == "EV":
        custom_font = load_custom_font(16, language)
        total_ev = sum(values.values())
        total_label = QLabel(f"TOTAL: {total_ev} / 510")
        total_label.setFont(custom_font)
        # Add margin for visual spacing
        total_label.setStyleSheet("margin-top: 5px; margin-left: 5px;")
        # Top-Left Alignment in the same cell
        layout.addWidget(
            total_label, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )

    # Ensure cell expands to fill space
    layout.setRowStretch(0, 1)
    layout.setColumnStretch(0, 1)

    return layout


class RadarChart(QWidget):
    def __init__(
        self, stats, max_value, fill_color, border_color, language, parent=None
    ):
        super().__init__(parent)
        self.stats = stats
        self.max_value = max_value if max_value > 0 else 1
        self.fill_color = fill_color
        self.border_color = border_color
        self.language = language
        # Minimum size to prevent text clipping
        self.setMinimumSize(340, 180)

        # Order: HP, Attack, Defense, Speed, Sp. Def, Sp. Atk
        # Clockwise starting from Top
        self.stat_keys = ["hp", "atk", "def", "spe", "spd", "spa"]
        self.display_names = {
            "hp": "HP",
            "atk": "Attack",
            "def": "Defense",
            "spe": "Speed",
            "spd": "Sp. Def",
            "spa": "Sp. Atk",
        }

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        # Shift center down to clear top buttons
        center = QPointF(rect.width() / 2, rect.height() / 2 + 5)

        # Radius calculation
        padding = 35
        radius = min(rect.width(), rect.height()) / 2 - padding

        # ensure radius is positive
        if radius < 10:
            radius = 10

        # Draw Background Hexagons (Grid)
        # 4 levels: 25%, 50%, 75%, 100%
        grid_pen = QPen(QColor(150, 150, 150, 100))  # Light gray transparent
        painter.setPen(grid_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for i in range(1, 5):
            r = radius * (i / 4.0)
            self._draw_hexagon(painter, center, r)

        # Draw Axis Lines
        painter.setPen(grid_pen)
        for i in range(6):
            angle_deg = -90 + (i * 60)
            angle_rad = math.radians(angle_deg)
            end_point = QPointF(
                center.x() + radius * math.cos(angle_rad),
                center.y() + radius * math.sin(angle_rad),
            )
            painter.drawLine(center, end_point)

        # Draw Data Polygon
        data_points = []
        for i, key in enumerate(self.stat_keys):
            val = self.stats.get(key, 0)
            # Cap value at max_value
            val = min(val, self.max_value)
            ratio = val / self.max_value

            r = radius * ratio
            angle_deg = -90 + (i * 60)
            angle_rad = math.radians(angle_deg)
            p = QPointF(
                center.x() + r * math.cos(angle_rad),
                center.y() + r * math.sin(angle_rad),
            )
            data_points.append(p)

        poly = QPolygonF(data_points)

        # Fill
        painter.setBrush(QBrush(self.fill_color))
        # Stroke
        pen = QPen(self.border_color)
        pen.setWidth(2)
        painter.setPen(pen)

        painter.drawPolygon(poly)

        # Draw Labels & Values
        # Increase Font Size
        text_color = self.palette().color(self.foregroundRole())
        painter.setPen(text_color)

        # Label font
        label_font = load_custom_font(13, self.language)
        label_font.setBold(True)
        painter.setFont(label_font)

        for i, key in enumerate(self.stat_keys):
            angle_deg = -90 + (i * 60)
            angle_rad = math.radians(angle_deg)

            # Position for label is radius + padding
            # We push it out a bit more
            label_radius = radius + 22

            lx = center.x() + label_radius * math.cos(angle_rad)
            ly = center.y() + label_radius * math.sin(angle_rad)

            # Manual tweaks to prevent overlap
            if key in ["def", "atk"]:
                lx += 15
            elif key in ["spd", "spa"]:
                lx -= 15

            text = self.display_names[key]
            val = self.stats.get(key, 0)

            # Draw Stat Name
            rect_width = 140
            rect_height = 50
            text_rect = QRectF(
                lx - rect_width / 2, ly - rect_height / 2, rect_width, rect_height
            )

            # Check for Max Value (IV=31, EV>=252)
            is_max = False
            if self.max_value == 31 and val == 31:  # IV Case
                is_max = True
            elif self.max_value > 100 and val >= 252:  # EV Case
                is_max = True

            if is_max:
                # Gold color for max values
                painter.setPen(QColor(218, 165, 32))
            else:
                painter.setPen(text_color)

            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, f"{text}\n{val}")

    def _draw_hexagon(self, painter, center, radius):
        points = []
        for i in range(6):
            angle_deg = -90 + (i * 60)
            angle_rad = math.radians(angle_deg)
            p = QPointF(
                center.x() + radius * math.cos(angle_rad),
                center.y() + radius * math.sin(angle_rad),
            )
            points.append(p)
        painter.drawPolygon(QPolygonF(points))


def _create_iv_ev_bar(color: QColor, filled_width: int, max_width: int) -> QPixmap:
    """Create a colored bar pixmap for IV/EV display."""
    pixmap = QPixmap(max_width, 10)
    pixmap.fill(QColor(0, 0, 0, 0))  # Transparent background

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Background bar - same color as Stats bar
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 200))  # Semi-transparent black (same as Stats)
    painter.drawRoundedRect(0, 0, max_width, 10, 3, 3)

    # Filled portion
    if filled_width > 0:
        painter.setBrush(color)
        painter.drawRoundedRect(0, 0, filled_width, 10, 3, 3)

    painter.end()
    return pixmap


def attack_details_window(attacks):
    window = QDialog()
    window.setWindowIcon(QIcon(str(icon_path)))
    layout = QVBoxLayout()
    # HTML content
    html_content = attack_details_window_template
    # Loop through the list of attacks and add them to the HTML content
    for attack in attacks:
        move = _lookup_move_data(attack)
        display_name = format_move_name(attack)
        html_content += f"""
        <tr>
          <td class="move-name">{display_name}</td>
          <td><img src="{type_icon_path(move["type"])}" alt="{move["type"]}"/></td>
          <td><img src="{move_category_path(move["category"].lower())}" alt="{move["category"]}"/></td>
          <td class="basePower">{move["basePower"]}</td>
          <td class="no-accuracy">{move["accuracy"]}</td>
          <td>{move["pp"]}</td>
          <td>{move["shortDesc"]}</td>
        </tr>
        """
    html_content += attack_details_window_template_end

    # Create a QLabel to display the HTML content
    label = QLabel(html_content)
    label.setAlignment(
        Qt.AlignmentFlag.AlignLeft
    )  # Align the label's content to the top
    label.setScaledContents(True)  # Enable scaling of the pixmap

    layout.addWidget(label)
    window.setLayout(layout)
    window.exec()


def remember_attack_details_window(
    individual_id, attack_set, all_attacks, logger, refresh_callback=None
):
    window = QDialog()
    window.setWindowIcon(QIcon(str(icon_path)))
    outer_layout = QVBoxLayout(window)
    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    content_widget = QWidget()
    layout = QHBoxLayout(content_widget)
    html_content = remember_attack_details_window_template
    for attack in all_attacks:
        move = find_details_move(attack) or _lookup_move_data(attack)
        html_content += f"""
        <tr>
          <td class="move-name">{format_move_name(attack)}</td>
          <td><img src="{type_icon_path(move["type"])}" alt="{move["type"]}"/></td>
          <td><img src="{move_category_path(move["category"].lower())}" alt="{move["category"]}"/></td>
          <td class="basePower">{move["basePower"]}</td>
          <td class="no-accuracy">{move["accuracy"]}</td>
          <td>{move["pp"]}</td>
          <td>{move["shortDesc"]}</td>
        </tr>
        """
    html_content += remember_attack_details_window_template_end
    label = QLabel(html_content)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft)
    label.setScaledContents(True)
    attack_layout = QVBoxLayout()
    for attack in all_attacks:
        remember_attack_button = QPushButton(f"Remember {attack}")
        qconnect(
            remember_attack_button.clicked,
            lambda checked, a=attack: remember_attack(
                individual_id, attack_set, a, logger, refresh_callback
            ),
        )
        attack_layout.addWidget(remember_attack_button)
    attack_layout_widget = QWidget()
    attack_layout_widget.setLayout(attack_layout)
    layout.addWidget(label)
    layout.addWidget(attack_layout_widget)
    scroll_area.setWidget(content_widget)
    outer_layout.addWidget(scroll_area)
    window.resize(1000, 400)
    window.exec()


def forget_attack_details_window(
    individual_id: str,
    attack_set: list[str],
    logger: "ShowInfoLogger",
    refresh_callback=None,
) -> None:
    """
    Creates a window that will allow the user to erase moves from a Pokemon.

    Args:
        individual_id (str): The Pokemon's unique identifier (uuid).
        attack_set (list[str]): The Pokemon's move set.
        logger: Logger object that can log info and display windows containing messages.
        refresh_callback: Optional callable invoked after a move is forgotten.

    Returns:
        None
    """
    window = QDialog()
    window.setWindowIcon(QIcon(str(icon_path)))
    outer_layout = QVBoxLayout(window)
    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    content_widget = QWidget()
    layout = QHBoxLayout(content_widget)
    html_content = remember_attack_details_window_template
    for attack in attack_set:
        move = _lookup_move_data(attack)
        display_name = format_move_name(attack)
        html_content += f"""
        <tr>
          <td class="move-name">{display_name}</td>
          <td><img src="{type_icon_path(move["type"])}" alt="{move["type"]}"/></td>
          <td><img src="{move_category_path(move["category"].lower())}" alt="{move["category"]}"/></td>
          <td class="basePower">{move["basePower"]}</td>
          <td class="no-accuracy">{move["accuracy"]}</td>
          <td>{move["pp"]}</td>
          <td>{move["shortDesc"]}</td>
        </tr>
        """
    html_content += remember_attack_details_window_template_end
    label = QLabel(html_content)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft)
    label.setScaledContents(True)
    attack_layout = QVBoxLayout()
    for attack in attack_set:
        forget_attack_button = QPushButton(f"Forget {attack}")
        qconnect(
            forget_attack_button.clicked,
            lambda checked, a=attack: forget_attack(
                individual_id, attack_set, a, logger, refresh_callback
            ),
        )
        attack_layout.addWidget(forget_attack_button)
    attack_layout_widget = QWidget()
    attack_layout_widget.setLayout(attack_layout)
    layout.addWidget(label)
    layout.addWidget(attack_layout_widget)
    scroll_area.setWidget(content_widget)
    outer_layout.addWidget(scroll_area)
    window.resize(1000, 400)
    window.exec()


def remember_attack(
    individual_id: str,
    attacks: list[str],
    new_attack: str,
    logger: ShowInfoLogger,
    refresh_callback=None,
):
    """Learn a new attack using database."""
    db = services.db

    if new_attack in attacks:
        logger.log_and_showinfo("warning", "Your pokemon already knows this move!")
        return

    pokemon_data = db.get_pokemon(individual_id)
    if not pokemon_data:
        logger.log_and_showinfo("warning", "Pokemon not found!")
        return

    attacks = pokemon_data["attacks"]
    if new_attack:
        msg = f"Your {pokemon_data['name'].capitalize()} can learn a new attack !"
        if len(attacks) < 4:
            attacks.append(new_attack)
            msg += f"\n Your {pokemon_data['name'].capitalize()} has learned {new_attack} !"
            logger.log_and_showinfo("info", f"{msg}")
        else:
            dialog = AttackDialog(attacks, new_attack)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected_attack = dialog.selected_attack
                try:
                    index_to_replace = attacks.index(selected_attack)
                    attacks[index_to_replace] = new_attack
                    logger.log_and_showinfo(
                        "info", f"Replaced '{selected_attack}' with '{new_attack}'"
                    )
                except ValueError:
                    logger.log_and_showinfo("info", f"{new_attack} will be discarded.")
            else:
                logger.log_and_showinfo("info", f"{new_attack} will be discarded.")

    pokemon_data["attacks"] = attacks
    db.save_pokemon(pokemon_data)

    # Also update main_pokemon if this is the main pokemon
    main_pokemon = db.get_main_pokemon()
    if main_pokemon and main_pokemon.get("individual_id") == individual_id:
        main_pokemon["attacks"] = attacks
        db.save_main_pokemon(main_pokemon)

    if refresh_callback:
        refresh_callback()


def forget_attack(
    individual_id: str,
    attacks: list[str],
    attack_to_forget: str,
    logger: ShowInfoLogger,
    refresh_callback=None,
) -> None:
    """Forget a move using database."""
    db = services.db

    pokemon_data = db.get_pokemon(individual_id)
    if not pokemon_data:
        logger.log_and_showinfo("warning", "Pokemon not found!")
        return

    attacks = pokemon_data["attacks"]
    if attack_to_forget in attacks:
        if len(attacks) > 1:
            attacks.remove(attack_to_forget)
            msg = f"Your {pokemon_data['name'].capitalize()} forgot {attack_to_forget}."
            logger.log_and_showinfo("info", f"{msg}")
        else:
            msg = f"Your {pokemon_data['name'].capitalize()} only knows this move, you can't forget it!"
            logger.log_and_showinfo("info", f"{msg}")
    else:
        msg = f"Your {pokemon_data['name'].capitalize()} does not know {attack_to_forget}."
        logger.log_and_showinfo("info", f"{msg}")

    pokemon_data["attacks"] = attacks
    db.save_pokemon(pokemon_data)

    # Also update main_pokemon if this is the main pokemon
    main_pokemon = db.get_main_pokemon()
    if main_pokemon and main_pokemon.get("individual_id") == individual_id:
        main_pokemon["attacks"] = attacks
        db.save_main_pokemon(main_pokemon)

    if refresh_callback:
        refresh_callback()


def tm_attack_details_window(
    id: int,
    individual_id: str,
    current_pokemon_moveset: list[str],
    logger: ShowInfoLogger,
    refresh_callback=None,
) -> None:
    """
    Creates a window that will allow the user to learn TM moves.
    """
    from ..pyobj.move_picker import MovePickerDialog

    # 1. Get species/base name for TM lookup
    internal_name = search_pokedex_by_id(id)
    if not internal_name:
        logger.log_and_showinfo("error", f"Could not find Pokémon data for ID: {id}")
        return

    base_name = internal_name.split("-")[0].lower()
    internal_name = internal_name.lower()

    # 2. Load TM learnsets
    try:
        with open(pokemon_tm_learnset_path, "r", encoding="utf-8") as f:
            tm_learnsets = json.load(f)
    except Exception as e:
        logger.log_and_showinfo("error", f"Failed to load TM learnsets: {e}")
        return

    # 3. Get valid TMs for this species (check specific form then base species)
    valid_tms = tm_learnsets.get(internal_name) or tm_learnsets.get(base_name)
    if not valid_tms:
        logger.log_and_showinfo("info", "This Pokémon cannot learn any moves from TMs.")
        return

    # 4. Get owned TMs from DB
    db = services.db
    all_items = db.get_all_items()
    owned_tm_moves = [
        item["item_name"]
        for item in all_items
        if (item.get("extra_data") or {}).get("type") == "TM"
    ]

    # 5. Filter valid TMs by ownership
    learnable_tm_moves = [move for move in valid_tms if move in owned_tm_moves]
    if not learnable_tm_moves:
        logger.log_and_showinfo(
            "info", "You don't own any TMs that this Pokémon can learn."
        )
        return

    # 6. UI: Use MovePickerDialog
    pkmn_data = db.get_pokemon(individual_id)
    nickname = pkmn_data.get("nickname") if pkmn_data else None
    raw_name = pkmn_data.get("name") if pkmn_data else internal_name
    species_name = get_pretty_name_for_name(raw_name)

    title = f"TM Learning: {nickname if nickname else species_name}"
    dialog = MovePickerDialog(title, learnable_tm_moves, current_pokemon_moveset)
    dialog.setWindowTitle("Learn from TMs")

    if dialog.exec():
        new_move = dialog.get_selected_move()
        if new_move:
            remember_attack(
                individual_id,
                current_pokemon_moveset,
                new_move,
                logger,
                refresh_callback,
            )


def rename_pkmn(
    nickname: str,
    pkmn_name: str,
    individual_id: str,
    logger: ShowInfoLogger,
    refresh_callback,
):
    """Rename a pokemon using database."""
    db = services.db

    try:
        pokemon = db.get_pokemon(individual_id)
        if pokemon is not None:
            pokemon["nickname"] = nickname
            db.save_pokemon(pokemon)
            logger.log_and_showinfo(
                "info",
                f"Your {pkmn_name.capitalize()} has been renamed to {nickname}!",
            )
            refresh_callback()
        else:
            services.ui.warn("Pokémon not found.")
    except Exception as e:
        show_warning_with_traceback(exception=e, message=f"An error occurred: {e}")


def PokemonFree(
    individual_id: str, name: str, logger: ShowInfoLogger, refresh_callback
):
    """Release a pokemon using database."""

    # Confirmation dialog
    reply = QMessageBox.question(
        None,
        "Confirm Release",
        f"Are you sure you want to release {name}?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )

    if reply == QMessageBox.StandardButton.No:
        logger.log_and_showinfo("info", "Release cancelled.")
        return

    db = services.db

    # Check if the Pokémon is the main pokemon
    main_pokemon = db.get_main_pokemon()
    if main_pokemon and main_pokemon.get("individual_id") == individual_id:
        logger.log_and_showinfo("info", "You can't free your Main Pokémon!")
        return

    # Get the pokemon from database
    pokemon_to_release = db.get_pokemon(individual_id)
    if not pokemon_to_release:
        logger.log_and_showinfo("info", "No Pokémon found with the specified ID.")
        refresh_callback()
        return

    # Save important stats to history before release
    from datetime import datetime

    history_data = {
        "id": pokemon_to_release.get("id"),
        "name": pokemon_to_release.get("name"),
        "shiny": pokemon_to_release.get("shiny", False),
        "pokemon_defeated": pokemon_to_release.get("pokemon_defeated", 0),
        "individual_id": pokemon_to_release.get("individual_id"),
        "released_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Add to history via database
    if db.add_to_history(history_data):
        pass  # Success
    else:
        logger.log_and_showinfo("error", f"Failed to add {name} to history.")

    # If this Pokémon is the current XP-Share target, clear the setting before
    # it disappears from the DB. Otherwise the dangling individual_id would make
    # xp_share_gain_exp look up a now-missing Pokémon and crash on the next
    # review. str() guards against any id type mismatch in the compare.
    settings_obj = services.settings
    if settings_obj is not None and str(settings_obj.get("trainer.xp_share")) == str(
        individual_id
    ):
        settings_obj.set("trainer.xp_share", None)

    # Delete from database
    db.delete_pokemon(individual_id)
    logger.log_and_showinfo("info", f"{name.capitalize()} has been let free.")

    refresh_callback()
