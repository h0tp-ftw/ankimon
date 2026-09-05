import json
import time

from aqt import mw

from aqt.qt import (
    QDialog,
    QFont,
    QLabel,
    QPainter,
    QPixmap,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)

from aqt.utils import showWarning

from PyQt6.QtGui import QIcon, QColor, QFontMetrics, QPainterPath

from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
    QLineEdit,
)

from ..utils import random_item, load_custom_font

from ..functions.drawing_utils import draw_gender_symbols, draw_stat_boosts

from ..functions.pokedex_functions import (
    get_pokemon_diff_lang_name,
    get_pretty_name_for_name,
    search_pokedex,
)

from ..functions.pokemon_functions import find_experience_for_level

from ..pyobj.ankimon_tracker import AnkimonTracker
from ..pyobj.InfoLogger import ShowInfoLogger

from ..pyobj.translator import Translator

from .error_handler import show_warning_with_traceback

from ..business import (
    calculate_present_power,
    format_compact_number,
    resize_pixmap_img,
    type_compatibility_multiplier,
)

from ..resources import (
    pkmnimgfolder,
    addon_dir,
    icon_path,
    battlescene_path,
    battlescene_path_without_dialog,
    battle_ui_path,
    user_path_sprites,
    frontdefault,
    badges_list_path,
    pokedex_image_path,
)


class TestWindow(QWidget):
    def __init__(
        self,
        main_pokemon,
        enemy_pokemon,
        settings_obj,
        parent=mw,
        ankimon_tracker_obj: AnkimonTracker = None,
        translator: Translator = None,
        logger: ShowInfoLogger = None,
    ):
        super().__init__(parent)  # <-- set parent here

        # Set as a tool window so it stays above parent but not above all apps
        self.setWindowFlag(Qt.WindowType.Tool, True)

        # Optionally: ensure it raises above the parent when shown
        self.setWindowFlag(
            Qt.WindowType.WindowStaysOnTopHint, False
        )  # Explicitly disable global always-on-top

        self.pkmn_window = False  # if fighting window open
        self.first_start = False
        self.enemy_pokemon = enemy_pokemon
        self.main_pokemon = main_pokemon
        self.settings_obj = settings_obj
        self.ankimon_tracker_obj = ankimon_tracker_obj
        self.logger = logger
        self.translator = translator

        if translator is None:
            self.translator = Translator(
                language=int(settings_obj.get("misc.language"))
            )

        self.test = 1

        self.default_path = f"{pkmnimgfolder}/front_default/substitute.png"

        self.current_view = None
        self.main_label = None
        self.kill_button = None
        self.catch_button = None
        self.nickname_input = None
        self._last_display_time = 0

        self.init_ui()
        # self.update()

    def init_ui(self):
        # Use a single persistent layout
        if self.layout() is None:
            self.setLayout(QVBoxLayout())

        layout = self.layout()
        self.clear_layout(layout)

        # The battle scene is a full-bleed backdrop, not a widget on a page, so
        # the layout keeps no contents margins: QVBoxLayout's default 11px inset
        # left only 534px for a 556px-wide scene, silently cropping 11px off
        # each side (the window is sized to the scene, see setFixedWidth below).
        layout.setContentsMargins(0, 0, 0, 0)

        # Main label that will persist and show everything (Logo, Battle, Death)
        self.main_label = QLabel()
        self.main_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.main_label)

        # Optional buttons for death screen (hidden by default)
        self.button_widget = QWidget()
        self.button_layout = QHBoxLayout()
        self.kill_button = QPushButton()
        self.catch_button = QPushButton()
        self.nickname_input = QLineEdit()
        self.button_layout.addWidget(self.kill_button)
        self.button_layout.addWidget(self.catch_button)
        self.button_layout.addWidget(self.nickname_input)
        self.button_widget.setLayout(self.button_layout)
        layout.addWidget(self.button_widget)
        self.button_widget.hide()

        # Initial Logo
        image_path = addon_dir / "ankimon_logo.png"
        pixmap = QPixmap(str(image_path))
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(400, 400, Qt.AspectRatioMode.KeepAspectRatio)
            self.main_label.setPixmap(scaled_pixmap)

        self.setStyleSheet("background-color: rgb(44,44,44);")
        self._reset_window_title()
        self.setWindowIcon(QIcon(str(icon_path)))
        # Pin the width to the battle scenes' own width (the art is 556x371 with
        # the dialog box, 555x258 without) and let the layout own the height.
        # A fixed 556x300 cropped every view: the scene with its dialog box needs
        # 371, so the message bar at its foot was cut off, and the death screen
        # needs 297 for the pokedex card plus the catch/defeat row. Heights vary
        # per view, and Qt only ever raises a shown top-level window's minimum,
        # so the window grows to whichever view needs the most and then holds
        # steady — no per-view resize flicker.
        self.setFixedWidth(556)

    def open_dynamic_window(self):
        # Create and show the dynamic window
        try:
            if self.pkmn_window == False:
                self.display_first_encounter()
                self.pkmn_window = True
                # self.show()

            if self.isVisible():
                self.close()  # Testfenster schließen, wenn Shift gedrückt wird
            else:
                self.show()

        except Exception as e:
            showWarning(f"Following Error occured when opening window: {e}")

    def display_first_start_up(self):
        if self.first_start == False:
            # Get the geometry of the main screen
            main_screen_geometry = mw.geometry()

            # Calculate the position to center the ItemWindow on the main screen
            x = int(main_screen_geometry.center().x() - self.width() / 2)
            y = int(main_screen_geometry.center().y() - self.height() / 2)

            self.setGeometry(x, y, 256, 256)
            self.move(x, y)

            self.show()

            self.first_start = True
            self.pkmn_window = True

    # Palette of the info boxes baked into the battle-scene backgrounds —
    # the CP/BP tag must be drawn in exactly these colors to blend in.
    _BOX_FILL = QColor(240, 240, 208)
    _BOX_INK = QColor(31, 31, 39)

    def _draw_info_tag(self, painter, x, y, w, h):
        """A small pixel-art plaque matching the baked-in info boxes.

        Ink border with 2px stepped (chunky, not smooth) corners and a cream
        fill — the same language as the background boxes, so the tag reads as
        part of the scene art.
        """
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._BOX_INK)
        painter.drawRect(x + 2, y, w - 4, h)
        painter.drawRect(x, y + 2, w, h - 4)
        painter.setBrush(self._BOX_FILL)
        painter.drawRect(x + 3, y + 3, w - 6, h - 6)
        painter.restore()

    def _draw_cp_pp(self, painter):
        """Draw CP and Battle Power labels for both Pokemon.

        Battle Power = CP * (current_HP / max_HP) * type-matchup multiplier
        * attack-stage factor — same scale as CP, so the two Pokemon's BP
        values are directly comparable.

        The player box has an empty bottom-left column, so its two lines sit
        inside the box, sharing the HP numbers' font and baseline grid. The
        enemy box has no free interior space, so its values go on a slim tag
        fused to the box's bottom border (see _draw_info_tag). Values are
        compacted ("367K") so they can never overflow either box.
        """
        lang = int(self.settings_obj.get("misc.language"))
        # The player lines live on the box's baked 11px baseline grid (HP bar
        # bottom y=216, baselines y=227/238, box interior floor y=240). The
        # language fonts differ in height at the same size — shrink until the
        # ascent fits the pitch (Early GameBoy fits at 18, PKMN Western at 16).
        size = 18
        cp_font = load_custom_font(size, lang)
        metrics = QFontMetrics(cp_font)
        while size > 10 and metrics.ascent() > 10:
            size -= 2
            cp_font = load_custom_font(size, lang)
            metrics = QFontMetrics(cp_font)
        painter.setFont(cp_font)
        painter.setPen(self._BOX_INK)
        try:
            enemy_cp = int(self.enemy_pokemon.cp)
        except (AttributeError, TypeError, ValueError):
            enemy_cp = 0
        try:
            main_cp = int(self.main_pokemon.cp)
        except (AttributeError, TypeError, ValueError):
            main_cp = 0

        enemy_vs_main = type_compatibility_multiplier(
            getattr(self.enemy_pokemon, "type", None),
            getattr(self.main_pokemon, "type", None),
        )
        main_vs_enemy = type_compatibility_multiplier(
            getattr(self.main_pokemon, "type", None),
            getattr(self.enemy_pokemon, "type", None),
        )
        enemy_stages = getattr(self.enemy_pokemon, "stat_stages", None) or {}
        main_stages = getattr(self.main_pokemon, "stat_stages", None) or {}
        enemy_bp = calculate_present_power(
            enemy_cp,
            getattr(self.enemy_pokemon, "hp", 0),
            getattr(self.enemy_pokemon, "max_hp", 0),
            enemy_vs_main,
            enemy_stages.get("atk", 0),
            enemy_stages.get("spa", 0),
        )
        main_bp = calculate_present_power(
            main_cp,
            getattr(self.main_pokemon, "hp", 0),
            getattr(self.main_pokemon, "max_hp", 0),
            main_vs_enemy,
            main_stages.get("atk", 0),
            main_stages.get("spa", 0),
        )

        cp_lbl = self.translator.translate("cp_label")
        bp_lbl = self.translator.translate("bp_label")
        enemy_cp_text = f"{cp_lbl} {format_compact_number(enemy_cp)}"
        enemy_bp_text = f"{bp_lbl} {format_compact_number(enemy_bp)}"

        # Enemy: slim one-line tag hanging under the box. Its top border
        # (y=92..94) lands exactly on the box's bottom border, so the two
        # fuse; width follows the measured text so any language/value fits.
        gap = 14
        cp_w = metrics.horizontalAdvance(enemy_cp_text)
        bp_w = metrics.horizontalAdvance(enemy_bp_text)
        tag_x, tag_y, pad = 39, 92, 7
        tag_w = cp_w + gap + bp_w + 2 * pad
        self._draw_info_tag(painter, tag_x, tag_y, tag_w, 20)
        painter.drawText(tag_x + pad, tag_y + 13, enemy_cp_text)
        painter.drawText(tag_x + pad + cp_w + gap, tag_y + 13, enemy_bp_text)

        # Player: the box's bottom-left column is empty — two lines there,
        # the lower one sharing its baseline with the HP numbers row.
        painter.drawText(326, 227, f"{cp_lbl} {format_compact_number(main_cp)}")
        painter.drawText(326, 238, f"{bp_lbl} {format_compact_number(main_bp)}")

    def _get_display_name(self, pokemon):
        """Helper to safely get localized or pretty name for normal and special forms."""
        if hasattr(pokemon, "name") and any(
            f in pokemon.name.lower() for f in ["-mega", "-gmax"]
        ):
            return get_pretty_name_for_name(pokemon.name)

        try:
            p_id = int(pokemon.id)
        except ValueError:
            return get_pretty_name_for_name(pokemon.name)

        return get_pokemon_diff_lang_name(
            p_id, int(self.settings_obj.get("misc.language"))
        )

    def _same_view_debounced(self, view):
        """True when a duplicate render of ``view`` arrives inside the debounce window.

        Exp debounced every display call against one shared timestamp; on main
        the battle loop legitimately calls ``display_battle()`` right before
        ``handle_enemy_faint`` shows the death screen, so the guard is keyed to
        the CURRENT view: only an immediate repeat of the same view is dropped
        (anti-flicker for duplicate hooks, especially during add-on reloads).
        """
        now = time.time()
        if self.current_view == view and now - self._last_display_time < 0.05:
            return True
        self._last_display_time = now
        return False

    def _trigger_catch_pokemon(self):
        """Catch via the hook_registry seam (same path profile_hooks wires to mw)."""
        # Lazy import: hook_registry/reviewer_ui pull in singletons, which would
        # be circular (and Anki-bound) at module-import time.
        from .. import hook_registry, reviewer_ui

        hook_registry.CatchPokemonHook(reviewer_ui._collected_pokemon_ids)

    def _trigger_defeat_pokemon(self):
        """Defeat via the hook_registry seam (same path profile_hooks wires to mw)."""
        from .. import hook_registry

        hook_registry.DefeatPokemonHook()

    def pokemon_display_first_encounter(self):
        # Main window layout
        layout = QVBoxLayout()

        global message_box_text
        global merged_pixmap, window

        self.ankimon_tracker_obj.attack_counter = 0
        self.ankimon_tracker_obj.caught = 0

        # Capitalize the first letter of the Pokémon's name
        lang_name = self._get_display_name(self.enemy_pokemon)

        # calculate wild pokemon max hp
        message_box_text = f"{self.translator.translate('wild_pokemon_appeared', enemy_pokemon_name=lang_name.capitalize())}"

        bckgimage_path = battlescene_path / self.ankimon_tracker_obj.battlescene_file

        if self.ankimon_tracker_obj.pokemon_encounter > 0:
            bckgimage_path = (
                battlescene_path_without_dialog
                / self.ankimon_tracker_obj.battlescene_file
            )

        msg_font = load_custom_font(32, int(self.settings_obj.get("misc.language")))

        image_label, msg_font = self.window_show(bckgimage_path, lang_name)

        return image_label

    def _load_sprite(self, pokemon, side):
        """Load a Pokémon's sprite, falling back to the substitute.

        ``QPixmap.load`` reports failure by *returning False*, not by raising,
        so the ``try/except`` this replaces never fired: a sprite the user
        never downloaded left a null pixmap behind and the window died in the
        aspect-ratio maths (issue #101). Check the return value instead.

        ``get_sprite_path`` stays inside the ``try`` so a raising lookup still
        reaches the substitute, exactly as before.
        """
        pixmap = QPixmap()
        try:
            loaded = pixmap.load(str(pokemon.get_sprite_path(side, "png")))
        except Exception:
            loaded = False
        if not loaded:
            pixmap.load(str(self.default_path))
        return pixmap

    def window_show(self, bckgimage_path, lang_name):
        ui_path = battle_ui_path

        pixmap_ui = QPixmap()
        pixmap_ui.load(str(ui_path))

        # Load the background image
        pixmap_bckg = QPixmap()
        pixmap_bckg.load(str(bckgimage_path))

        # Display the Pokémon image
        image_label = QLabel()
        pixmap = self._load_sprite(self.enemy_pokemon, "front")

        # Display the Main Pokémon image
        pixmap2 = self._load_sprite(self.main_pokemon, "back")

        # Scale both to a fixed width, keeping the aspect ratio. Reading the
        # dimensions back off the scaled pixmaps keeps the draw offsets correct
        # even when a sprite is missing and cannot be scaled at all.
        pixmap = resize_pixmap_img(pixmap, 150)
        pixmap2 = resize_pixmap_img(pixmap2, 150)

        new_width, new_height = pixmap.width(), pixmap.height()
        new_width2, new_height2 = pixmap2.width(), pixmap2.height()

        # Merge the background image and the Pokémon image
        merged_pixmap = QPixmap(pixmap_bckg.size())
        # merged_pixmap.fill(Qt.transparent)
        merged_pixmap.fill(QColor(0, 0, 0, 0))
        # RGBA where A (alpha) is 0 for full transparency

        # merge both images together
        painter = QPainter(merged_pixmap)

        # Create rounded rectangle path for clipping
        path = QPainterPath()
        path.addRoundedRect(0, 0, merged_pixmap.width(), merged_pixmap.height(), 10, 10)
        painter.setClipPath(path)

        # draw background to a specific pixel
        painter.drawPixmap(0, 0, pixmap_bckg)

        enemy_hp, enemy_max_hp = self._safe_hp_pair(
            self.enemy_pokemon.hp, self.enemy_pokemon.max_hp
        )
        main_hp, main_max_hp = self._safe_hp_pair(
            self.main_pokemon.hp, self.main_pokemon.max_hp
        )

        painter = self.draw_hp_bar(
            118, 76, 8, 116, enemy_hp, enemy_max_hp, painter
        )  # enemy pokemon hp_bar

        painter = self.draw_hp_bar(
            401, 208, 8, 116, main_hp, main_max_hp, painter
        )  # main pokemon hp_bar

        painter.drawPixmap(0, 0, pixmap_ui)

        # Find the Pokemon Images Height and Width
        wpkmn_width = new_width // 2
        wpkmn_height = new_height

        mpkmn_width = new_width2 // 2
        mpkmn_height = new_height2

        # draw pokemon image to a specific pixel
        painter.drawPixmap((410 - wpkmn_width), (170 - wpkmn_height), pixmap)
        painter.drawPixmap((144 - mpkmn_width), (290 - mpkmn_height), pixmap2)

        experience = int(
            find_experience_for_level(
                self.main_pokemon.growth_rate,
                self.main_pokemon.level,
                self.settings_obj.get("misc.remove_level_cap"),
            )
        )

        mainxp_bar_width = 5
        # Guard the divisor: a 0 out of the exp-table lookup would take the
        # whole render down with a ZeroDivisionError, so draw an empty bar
        # instead (issue #101).
        mainpokemon_xp_value = (
            int(((self.main_pokemon.xp or 0) / experience) * 148) if experience else 0
        )

        # Paint XP Bar
        painter.setBrush(QColor(58, 155, 220))
        painter.drawRect(366, 246, int(mainpokemon_xp_value), int(mainxp_bar_width))

        # custom font
        custom_font = load_custom_font(26, int(self.settings_obj.get("misc.language")))
        hp_enemy_text_font = load_custom_font(
            18, int(self.settings_obj.get("misc.language"))
        )
        msg_font = load_custom_font(32, int(self.settings_obj.get("misc.language")))

        # Draw the text on top of the image
        # Adjust the font size as needed
        painter.setFont(custom_font)
        painter.setPen(QColor(31, 31, 39))  # Text color

        enemy_name = self._get_display_name(self.enemy_pokemon)
        main_name = self._get_display_name(self.main_pokemon)

        if self.enemy_pokemon.shiny:
            enemy_name += " 🌠 "

        if self.main_pokemon.shiny:
            main_name += " 🌠 "

        painter.drawText(48, 67, enemy_name)
        painter.drawText(326, 200, main_name)

        # Drawing the gender of each Pokemon
        draw_gender_symbols(
            self.main_pokemon, self.enemy_pokemon, painter, (457, 196), (175, 64)
        )

        draw_stat_boosts(
            self.main_pokemon, self.enemy_pokemon, painter, (326, 155), (48, 25)
        )

        painter.drawText(208, 67, f"{self.enemy_pokemon.level}")
        # painter.drawText(55, 85, gender_text)
        painter.drawText(490, 199, f"{self.main_pokemon.level}")

        hp_x = 442 if main_hp < 100 else 430  # Shift left if 3 digits
        max_hp_x = 487 if main_max_hp < 100 else 480  # Shift left if 3 digits

        painter.drawText(max_hp_x, 238, str(main_max_hp))
        painter.drawText(hp_x, 238, str(main_hp))

        painter.setFont(msg_font)
        painter.setPen(QColor(240, 240, 208))  # Text color

        painter.drawText(40, 320, message_box_text)

        painter.setFont(hp_enemy_text_font)
        painter.setPen(QColor(31, 31, 39))  # Text color
        enemy_hp_x = 41 if enemy_max_hp < 100 else 40  # Shift left if 3 digits
        enemy_max_hp_x = 64 if enemy_max_hp < 100 else 56  # Shift left if 3 digits
        painter.drawText(
            enemy_hp_x,
            84 if enemy_max_hp < 100 else 80,
            str(enemy_hp) + "/",
        )
        painter.drawText(
            enemy_max_hp_x,
            84 if enemy_max_hp < 100 else 88,
            str(enemy_max_hp),
        )

        self._draw_cp_pp(painter)

        painter.end()

        # Set the merged image as the pixmap for the QLabel
        image_label.setPixmap(merged_pixmap)

        return image_label, msg_font

    @staticmethod
    def _safe_hp_pair(hp, max_hp):
        """Return numeric HP values that are safe for rendering."""
        try:
            safe_hp = int(hp) if hp is not None else 0
        except (TypeError, ValueError, OverflowError):
            safe_hp = 0

        try:
            safe_max_hp = int(max_hp) if max_hp is not None else 1
        except (TypeError, ValueError, OverflowError):
            safe_max_hp = 1

        safe_max_hp = max(1, safe_max_hp)
        safe_hp = min(max(0, safe_hp), safe_max_hp)
        return safe_hp, safe_max_hp

    def draw_hp_bar(self, x, y, h, w, hp, max_hp, painter):
        hp, max_hp = self._safe_hp_pair(hp, max_hp)
        hp_ratio = max(0, min(hp / max_hp, 1))
        pokemon_hp_percent = int(hp_ratio * 100)
        hp_bar_value = int(w * hp_ratio)

        # Draw the HP bar
        if pokemon_hp_percent < 25:
            hp_color = QColor(255, 0, 0)  # Red
        elif pokemon_hp_percent < 50:
            hp_color = QColor(255, 140, 0)  # Orange
        elif pokemon_hp_percent < 75:
            hp_color = QColor(255, 255, 0)  # Yellow
        else:
            hp_color = QColor(110, 218, 163)  # Green

        painter.setBrush(hp_color)
        painter.drawRect(int(x), int(y), int(hp_bar_value), int(h))

        return painter

    def pokemon_display_battle(self):
        # No pokemon_encounter increment here — the battle loop owns the
        # per-round counter; incrementing per render double-counted rounds.
        bckgimage_path = battlescene_path / self.ankimon_tracker_obj.battlescene_file

        if self.ankimon_tracker_obj.pokemon_encounter > 1:
            bckgimage_path = (
                battlescene_path_without_dialog
                / self.ankimon_tracker_obj.battlescene_file
            )

        ui_path = battle_ui_path

        pixmap_ui = QPixmap()
        pixmap_ui.load(str(ui_path))

        # Load the background image
        pixmap_bckg = QPixmap()
        pixmap_bckg.load(str(bckgimage_path))

        image_label = QLabel()

        # Display the Pokémon image
        pixmap = self._load_sprite(self.enemy_pokemon, "front")

        # Display the Main Pokémon image
        pixmap2 = self._load_sprite(self.main_pokemon, "back")

        # Scale both to a fixed width, keeping the aspect ratio. Reading the
        # dimensions back off the scaled pixmaps keeps the draw offsets correct
        # even when a sprite is missing and cannot be scaled at all.
        pixmap = resize_pixmap_img(pixmap, 150)
        pixmap2 = resize_pixmap_img(pixmap2, 150)

        new_width, new_height = pixmap.width(), pixmap.height()
        new_width2, new_height2 = pixmap2.width(), pixmap2.height()

        # Merge the background image and the Pokémon image
        merged_pixmap = QPixmap(pixmap_bckg.size())
        # merged_pixmap.fill(Qt.transparent)
        merged_pixmap.fill(
            QColor(0, 0, 0, 0)
        )  # RGBA where A (alpha) is 0 for full transparency

        # merge both images together
        painter = QPainter(merged_pixmap)

        # Create rounded rectangle path for clipping
        path = QPainterPath()
        path.addRoundedRect(0, 0, merged_pixmap.width(), merged_pixmap.height(), 10, 10)
        painter.setClipPath(path)

        # draw background to a specific pixel
        painter.drawPixmap(0, 0, pixmap_bckg)

        enemy_hp, enemy_max_hp = self._safe_hp_pair(
            self.enemy_pokemon.hp, self.enemy_pokemon.max_hp
        )
        main_hp, main_max_hp = self._safe_hp_pair(
            self.main_pokemon.hp, self.main_pokemon.max_hp
        )

        painter = self.draw_hp_bar(
            118, 76, 8, 116, enemy_hp, enemy_max_hp, painter
        )  # enemy pokemon hp_bar

        painter = self.draw_hp_bar(
            401, 208, 8, 116, main_hp, main_max_hp, painter
        )  # main pokemon hp_bar

        painter.drawPixmap(0, 0, pixmap_ui)

        # Find the Pokemon Images Height and Width
        wpkmn_width = new_width // 2
        wpkmn_height = new_height

        mpkmn_width = new_width2 // 2
        mpkmn_height = new_height2

        # draw pokemon image to a specific pixel
        painter.drawPixmap((410 - wpkmn_width), (170 - wpkmn_height), pixmap)
        # Reposition main pokemon to be fully visible when message box disappears
        painter.drawPixmap((144 - mpkmn_width), (270 - mpkmn_height), pixmap2)

        experience = int(
            find_experience_for_level(
                self.main_pokemon.growth_rate,
                self.main_pokemon.level,
                self.settings_obj.get("misc.remove_level_cap"),
            )
        )

        mainxp_bar_width = 5
        # Guard the divisor: a 0 out of the exp-table lookup would take the
        # whole render down with a ZeroDivisionError, so draw an empty bar
        # instead (issue #101).
        mainpokemon_xp_value = (
            int(((self.main_pokemon.xp or 0) / experience) * 148) if experience else 0
        )

        # Paint XP Bar
        painter.setBrush(QColor(58, 155, 220))
        painter.drawRect(366, 246, int(mainpokemon_xp_value), int(mainxp_bar_width))

        # custom font
        custom_font = load_custom_font(26, int(self.settings_obj.get("misc.language")))
        hp_enemy_text_font = load_custom_font(
            18, int(self.settings_obj.get("misc.language"))
        )
        msg_font = load_custom_font(28, int(self.settings_obj.get("misc.language")))

        # Draw the text on top of the image
        # Adjust the font size as needed
        painter.setFont(custom_font)
        painter.setPen(QColor(31, 31, 39))  # Text color

        enemy_name = self._get_display_name(self.enemy_pokemon)
        main_name = self._get_display_name(self.main_pokemon)

        if self.enemy_pokemon.shiny:
            enemy_name += " 🌠"  # Green sparkle

        if self.main_pokemon.shiny:
            main_name += " 🌠"  # Green sparkles

        painter.drawText(48, 67, enemy_name)
        painter.drawText(326, 200, main_name)

        # Drawing the gender of each Pokemon
        draw_gender_symbols(
            self.main_pokemon, self.enemy_pokemon, painter, (457, 196), (175, 64)
        )

        draw_stat_boosts(
            self.main_pokemon, self.enemy_pokemon, painter, (326, 155), (48, 25)
        )

        painter.drawText(208, 67, f"{self.enemy_pokemon.level}")
        painter.drawText(490, 199, f"{self.main_pokemon.level}")

        hp_x = 442 if main_hp < 100 else 430  # Shift left if 3 digits
        max_hp_x = 487 if main_max_hp < 100 else 480  # Shift left if 3 digits

        painter.drawText(max_hp_x, 238, str(main_max_hp))
        painter.drawText(hp_x, 238, str(main_hp))

        painter.setFont(msg_font)
        painter.setPen(QColor(31, 31, 39))  # Text color

        # Drawing enemy pokemon hp
        painter.setFont(hp_enemy_text_font)
        painter.setPen(QColor(31, 31, 39))  # Text color
        enemy_hp_x = 41 if enemy_max_hp < 100 else 40  # Shift left if 3 digits
        enemy_max_hp_x = 64 if enemy_max_hp < 100 else 56  # Shift left if 3 digits
        painter.drawText(
            enemy_hp_x,
            84 if enemy_max_hp < 100 else 80,
            str(enemy_hp) + "/",
        )
        painter.drawText(
            enemy_max_hp_x,
            84 if enemy_max_hp < 100 else 88,
            str(enemy_max_hp),
        )

        self._draw_cp_pp(painter)

        painter.end()

        # Set the merged image as the pixmap for the QLabel
        image_label.setPixmap(merged_pixmap)

        return image_label

    def pokemon_display_item(self, item):
        bckgimage_path = addon_dir / "addon_sprites" / "starter_screen" / "bg.png"
        item_path = user_path_sprites / "items" / f"{item}.png"

        # Load the background image
        pixmap_bckg = QPixmap()
        pixmap_bckg.load(str(bckgimage_path))

        # Display the Pokémon image
        item_label = QLabel()
        item_pixmap = QPixmap()
        item_pixmap.load(str(item_path))

        item_pixmap = resize_pixmap_img(item_pixmap, 100)

        # Merge the background image and the Pokémon image
        merged_pixmap = QPixmap(pixmap_bckg.size())
        merged_pixmap.fill(
            QColor(0, 0, 0, 0)
        )  # RGBA where A (alpha) is 0 for full transparency
        # merged_pixmap.fill(Qt.transparent)

        # merge both images together
        painter = QPainter(merged_pixmap)

        # draw background to a specific pixel
        painter.drawPixmap(0, 0, pixmap_bckg)

        # item = str(item)
        if (
            item.endswith("-up")
            or item.endswith("-max")
            or item.endswith("protein")
            or item.endswith("zinc")
            or item.endswith("carbos")
            or item.endswith("calcium")
            or item.endswith("repel")
            or item.endswith("statue")
        ):
            painter.drawPixmap(200, 50, item_pixmap)
        elif item.endswith("soda-pop"):
            painter.drawPixmap(200, 30, item_pixmap)
        elif (
            item.endswith("-heal")
            or item.endswith("awakening")
            or item.endswith("ether")
            or item.endswith("leftovers")
        ):
            painter.drawPixmap(200, 50, item_pixmap)
        elif item.endswith("-berry") or item.endswith("potion"):
            painter.drawPixmap(200, 80, item_pixmap)
        else:
            painter.drawPixmap(200, 90, item_pixmap)

        # custom font
        custom_font = load_custom_font(26, int(self.settings_obj.get("misc.language")))

        message_box_text = f"{self.translator.translate('received_an_item', item_name=item.capitalize())} !"

        # Draw the text on top of the image
        # Adjust the font size as needed
        painter.setFont(custom_font)
        painter.setPen(QColor(255, 255, 255))  # Text color

        painter.drawText(50, 290, message_box_text)

        custom_font = load_custom_font(20, int(self.settings_obj.get("misc.language")))
        painter.setFont(custom_font)
        # painter.drawText(10, 330, "You can look this up in your item bag.")

        painter.end()

        # Set the merged image as the pixmap for the QLabel
        image_label = QLabel()
        image_label.setPixmap(merged_pixmap)

        return image_label

    def pokemon_display_badge(self, badge_number):
        try:
            global badges

            bckgimage_path = addon_dir / "addon_sprites" / "starter_screen" / "bg.png"
            badge_path = (
                addon_dir / "user_files" / "sprites" / "badges" / f"{badge_number}.png"
            )

            # Load the background image
            pixmap_bckg = QPixmap()
            pixmap_bckg.load(str(bckgimage_path))

            # Display the Pokémon image
            item_pixmap = QPixmap()
            item_pixmap.load(str(badge_path))

            item_pixmap = resize_pixmap_img(item_pixmap, 100)

            # Merge the background image and the Pokémon image
            merged_pixmap = QPixmap(pixmap_bckg.size())
            merged_pixmap.fill(
                QColor(0, 0, 0, 0)
            )  # RGBA where A (alpha) is 0 for full transparency
            # merged_pixmap.fill(Qt.transparent)

            # merge both images together
            painter = QPainter(merged_pixmap)

            # draw background to a specific pixel
            painter.drawPixmap(0, 0, pixmap_bckg)

            # item = str(item)
            painter.drawPixmap(200, 90, item_pixmap)

            # custom font
            custom_font = load_custom_font(
                20, int(self.settings_obj.get("misc.language"))
            )

            message_box_text = self.translator.translate("received_a_badge")

            with open(badges_list_path, "r", encoding="utf-8") as json_file:
                badges = json.load(json_file)

            message_box_text2 = f"{badges[str(badge_number)]}!"

            # Draw the text on top of the image
            # Adjust the font size as needed
            painter.setFont(custom_font)
            painter.setPen(QColor(255, 255, 255))  # Text color

            painter.drawText(120, 270, message_box_text)
            painter.drawText(140, 290, message_box_text2)

            custom_font = load_custom_font(
                20, int(self.settings_obj.get("misc.language"))
            )
            painter.setFont(custom_font)
            # painter.drawText(10, 330, "You can look this up in your item bag.")

            painter.end()

            # Set the merged image as the pixmap for the QLabel
            image_label = QLabel()
            image_label.setPixmap(merged_pixmap)

            return image_label

        except Exception as e:
            show_warning_with_traceback(
                parent=self,
                exception=e,
                message=f"An error occured in badges window {e}",
            )

    def pokemon_display_dead_pokemon(self):
        caught = self.ankimon_tracker_obj.caught
        id = self.enemy_pokemon.id
        level = self.enemy_pokemon.level
        type = self.enemy_pokemon.type

        # Create the dialog
        lang_name = self._get_display_name(self.enemy_pokemon)

        self.setWindowTitle(
            f"{self.translator.translate('catch_or_free', enemy_pokemon_name=lang_name.capitalize())}"
        )

        # Display the Pokémon image. ``search_pokedex`` returns ``[]`` when the
        # name has no match (or on any lookup error), so guard the ``int()`` —
        # falling back to the species id, then to the substitute sprite.
        species_id = search_pokedex(self.enemy_pokemon.name.lower(), "species_id")
        if isinstance(species_id, list) or species_id is None:
            species_id = self.enemy_pokemon.id
        try:
            pkmnimage_file = f"{int(species_id)}.png"
        except (ValueError, TypeError):
            pkmnimage_file = "substitute.png"
        pkmnimage_path = frontdefault / pkmnimage_file

        pkmnimage_label = QLabel()
        pkmnpixmap = QPixmap()

        try:
            pkmnpixmap.load(str(pkmnimage_path))
        except:
            pkmnpixmap.load(str(self.default_path))

        pkmnpixmap_bckg = QPixmap()

        try:
            pkmnpixmap_bckg.load(str(pokedex_image_path))
        except:
            pkmnpixmap_bckg.load(str(self.default_path))

        # Calculate the new dimensions to maintain the aspect ratio
        pkmnpixmap = pkmnpixmap.scaled(230, 230)

        # Create a painter to add text on top of the image
        painter2 = QPainter(pkmnpixmap_bckg)
        painter2.drawPixmap(15, 15, pkmnpixmap)

        # Create level text
        # Draw the text on top of the image
        font = QFont()
        font.setPointSize(20)  # Adjust the font size as needed
        painter2.setFont(font)

        painter2.drawText(270, 107, f"{lang_name}")

        font.setPointSize(17)  # Adjust the font size as needed
        painter2.setFont(font)

        painter2.drawText(315, 192, f"Level: {level}")
        types = self.enemy_pokemon.type or []
        type_text = ", ".join(t.capitalize() for t in types) if types else "Unknown"
        painter2.drawText(322, 225, f"Type: {type_text}")

        painter2.setFont(font)

        fontlvl = QFont()
        fontlvl.setPointSize(12)

        painter2.end()

        # Create a QLabel for the capitalized name
        name_label = QLabel(lang_name.capitalize())
        name_label.setFont(font)

        # Create a QLabel for the level
        level_label = QLabel(f"Level: {level}")
        # Align to the center
        level_label.setFont(fontlvl)

        nickname_input = QLineEdit()
        nickname_input.setPlaceholderText(self.translator.translate("choose_nickname"))
        nickname_input.setStyleSheet("background-color: rgb(44,44,44);")
        nickname_input.setFixedSize(120, 30)  # Adjust the size as needed

        # Create buttons for catching and killing the Pokémon
        catch_button = QPushButton(self.translator.translate("catch_button"))
        catch_button.setFixedSize(175, 30)  # Adjust the size as needed
        catch_button.setFont(
            QFont("Arial", 12)
        )  # Adjust the font size and style as needed
        catch_button.setStyleSheet("background-color: rgb(44,44,44);")
        # catch_button.setFixedWidth(150)
        qconnect(
            catch_button.clicked,
            lambda: self._reset_window_title(self._trigger_catch_pokemon),
        )

        kill_button = QPushButton(self.translator.translate("defeat_button"))
        kill_button.setFixedSize(175, 30)  # Adjust the size as needed
        kill_button.setFont(
            QFont("Arial", 12)
        )  # Adjust the font size and style as needed
        kill_button.setStyleSheet("background-color: rgb(44,44,44);")
        # kill_button.setFixedWidth(150)
        qconnect(
            kill_button.clicked,
            lambda: self._reset_window_title(self._trigger_defeat_pokemon),
        )

        # Set the merged image as the pixmap for the QLabel
        pkmnimage_label.setPixmap(pkmnpixmap_bckg)

        # align things needed to middle
        pkmnimage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        return pkmnimage_label, kill_button, catch_button, nickname_input

    def display_first_encounter(self):
        self.ankimon_tracker_obj.pokemon_encounter = 0
        # Clear the debounce timestamp so a fresh encounter's first battle
        # render is never dropped as a same-view repeat of the PREVIOUS
        # encounter's last render (which could land inside the 50 ms window).
        self._last_display_time = 0
        new_label = self.pokemon_display_first_encounter()
        self.main_label.setPixmap(new_label.pixmap())
        self.button_widget.hide()
        self.setStyleSheet("background-color: rgb(44,44,44);")
        self.current_view = "battle"

    def display_battle(self):
        # Debounce: prevent flicker from duplicate hooks (especially during reloads)
        if self._same_view_debounced("battle"):
            return

        # Update the existing label without clearing the layout
        new_label = self.pokemon_display_battle()
        self.main_label.setPixmap(new_label.pixmap())
        self.button_widget.hide()
        self.current_view = "battle"

    def rate_display_item(self, item):
        Receive_Window = QDialog(mw)
        layout = QHBoxLayout()

        item_widget = self.pokemon_display_item(item)

        layout.addWidget(item_widget)

        Receive_Window.setStyleSheet("background-color: rgb(44,44,44);")
        Receive_Window.setMaximumWidth(512)
        Receive_Window.setMaximumHeight(320)

        Receive_Window.setLayout(layout)
        Receive_Window.show()

    def display_item(self):
        item_name = random_item()
        if item_name is None:
            return

        Receive_Window = QDialog(mw)
        layout = QHBoxLayout()

        item_widget = self.pokemon_display_item(item_name)

        layout.addWidget(item_widget)

        Receive_Window.setStyleSheet("background-color: rgb(44,44,44);")
        Receive_Window.setMaximumWidth(512)
        Receive_Window.setMaximumHeight(320)

        Receive_Window.setLayout(layout)
        Receive_Window.show()

    def display_pokemon_death(self):
        # Debounce duplicate death renders (same guard as display_battle)
        if self._same_view_debounced("death"):
            return

        img_label, kill_btn, catch_btn, nick_input = self.pokemon_display_dead_pokemon()

        # Update the image
        self.main_label.setPixmap(img_label.pixmap())

        # Sync the persistent buttons (update text/placeholder)
        self.kill_button.setText(kill_btn.text())
        self.catch_button.setText(catch_btn.text())
        self.nickname_input.setPlaceholderText(nick_input.placeholderText())

        # Re-connect buttons safely
        try:
            self.kill_button.clicked.disconnect()
            self.catch_button.clicked.disconnect()
        except TypeError:
            pass  # nothing was connected yet
        qconnect(
            self.kill_button.clicked,
            lambda: self._reset_window_title(self._trigger_defeat_pokemon),
        )
        qconnect(
            self.catch_button.clicked,
            lambda: self._reset_window_title(self._trigger_catch_pokemon),
        )

        self.button_widget.show()
        self.setStyleSheet("background-color: rgb(177,147,209);")
        self.current_view = "death"

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()

            if widget:
                widget.deleteLater()

    def closeEvent(self, event):
        self.pkmn_window = False

    def _reset_window_title(self, callback_func=None):
        self.setWindowTitle("Ankimon Window")
        if callback_func:
            callback_func()
