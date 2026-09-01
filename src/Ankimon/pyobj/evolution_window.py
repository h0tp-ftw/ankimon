import random
from typing import Optional

from aqt import mw
from aqt.qt import (
    QFont,
    QLabel,
    QPainter,
    QPixmap,
    QVBoxLayout,
    QWidget,
    QDialog,
    qconnect,
)
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import (
    QPushButton,
)

from ..services import services
from ..utils import load_custom_font, is_alive
from ..functions.pokedex_functions import (
    get_base_experience,
    get_growth_rate,
    return_name_for_id,
    search_pokedex,
)
from ..functions.pokemon_functions import get_random_moves_for_pokemon
from ..move_names import format_move_name
from ..functions.battle_functions import calculate_hp
from ..functions.update_main_pokemon import (
    update_main_pokemon,
)
from ..functions.badges_functions import check_for_badge, receive_badge
from ..pyobj.attack_dialog import AttackDialog
from ..pyobj.settings import Settings
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.translator import Translator
from ..pyobj.test_window import TestWindow
from ..pyobj.reviewer_obj import Reviewer_Manager
from ..pyobj.error_handler import show_warning_with_traceback
from ..business import calculate_cp_from_dict, resize_pixmap_img
from ..resources import (
    addon_dir,
    frontdefault,
    evolve_image_path,
)


class EvoWindow(QWidget):
    def __init__(
        self,
        logger: ShowInfoLogger,
        settings_obj: Settings,
        main_pokemon: PokemonObject,
        translator: Translator,
        reviewer_obj: Reviewer_Manager,
        test_window: TestWindow,
        achievements: dict,
    ):
        super().__init__()
        self.init_ui()

        # To avoid circular imports, instead of importing those things, we
        # save a reference to them as an attribute
        self.logger = logger
        self.settings_obj = settings_obj
        self.main_pokemon = main_pokemon
        self.translator = translator
        self.reviewer_obj = reviewer_obj
        self.test_window = test_window
        self.achievements = achievements

    def init_ui(self):
        basic_layout = QVBoxLayout()
        self.setWindowTitle("Your Pokémon is about to Evolve")
        self.setLayout(basic_layout)

    def open_dynamic_window(self):
        self.show()

    def _should_show_sprites(self) -> bool:
        """Check if sprites should be shown based on the global setting."""
        if self.settings_obj is None:
            return True
        try:
            return self.settings_obj.get("gui.show_sprites_across_ankimon", True)
        except Exception:
            return True

    def display_evo_complete(self, prevo_id: int, evo_id: int):
        """
        Displays the GUI notification that the given Pokemon has evolved.

        This function handles the overall display logic and calls the
        underlying layout generator to build the GUI content.

        Args:
            prevo_id (int): The identifier (National Pokedex Number) of the Pokémon to evolve.
            evo_id (int): The identifier (National Pokedex Number) of the evolved Pokémon.
        """
        self.clear_layout(self.layout())
        layout = self.layout()
        pkmn_label = self._display_evo_complete_layout(prevo_id, evo_id)
        layout.addWidget(pkmn_label)
        # Give the celebration screen an explicit way to dismiss itself instead
        # of leaving the user to find the OS window-close button (the only exit
        # this screen used to offer).
        close_button = QPushButton("Close")
        qconnect(close_button.clicked, self.close)
        layout.addWidget(close_button)
        self.setStyleSheet("background-color: rgb(14,14,14);")
        self.setLayout(layout)
        self.setMaximumWidth(500)
        self.setMaximumHeight(300)
        self.show()

    def _display_evo_complete_layout(self, prevo_id: int, evo_id: int):
        """
        Creates the GUI layout for the successful evolution.

        This function generates the visual components (images, text, etc.)
        to inform the user that a Pokémon has evolved.

        Args:
            prevo_id (int): The identifier (National Pokedex Number) of the Pokémon to evolve.
            evo_id (int): The identifier (National Pokedex Number) of the evolved Pokémon.
        """
        bckgimage_path = addon_dir / "addon_sprites" / "starter_screen" / "bg.png"
        prevo_name = return_name_for_id(prevo_id)
        evo_name = return_name_for_id(evo_id)

        # Load the background image
        pixmap_bckg = QPixmap()
        pixmap_bckg.load(str(bckgimage_path))

        # Merge the background image and the Pokémon image
        merged_pixmap = QPixmap(pixmap_bckg.size())
        merged_pixmap.fill(
            QColor(0, 0, 0, 0)
        )  # RGBA where A (alpha) is 0 for full transparency

        # merge both images together
        painter = QPainter(merged_pixmap)

        # draw background to a specific pixel
        painter.drawPixmap(0, 0, pixmap_bckg)
        
        # Only load and draw the Pokémon sprite if sprites are enabled
        show_sprites = self._should_show_sprites()
        if show_sprites:
            # Display the Pokémon image
            image_path = frontdefault / f"{evo_id}.png"
            image_pixmap = QPixmap()
            image_pixmap.load(str(image_path))
            image_pixmap = resize_pixmap_img(image_pixmap, 250)
            painter.drawPixmap(125, 10, image_pixmap)

        # custom font
        custom_font = load_custom_font(20, int(self.settings_obj.get("misc.language")))
        message_box_text = (
            f"{(prevo_name).capitalize()} has evolved to {(evo_name).capitalize()} !"
        )
        self.logger.log("game", message_box_text)
        # Draw the text on top of the image
        # Adjust the font size as needed
        painter.setFont(custom_font)
        painter.setPen(QColor(255, 255, 255))  # Text color
        painter.drawText(40, 290, message_box_text)

        painter.end()
        # Set the merged image as the pixmap for the QLabel

        pkmn_label = QLabel()
        pkmn_label.setPixmap(merged_pixmap)
        return pkmn_label

    def ask_pokemon_evo(
        self,
        individual_id: int,
        prevo_id: int,
        evo_id: int,
        item_name: Optional[str] = None,
    ):
        """
        Displays the GUI notification that the given Pokemon is about to evolve.

        This function handles the overall display logic and calls the
        underlying layout generator to build the GUI content.

        Args:
            individual_id (int): The UUID of the Pokemon to evolve.
            prevo_id (int): The identifier (National Pokedex Number) of the Pokémon to evolve.
            evo_id (int): The identifier (National Pokedex Number) of the evolved Pokémon.
            item_name (str, optional): The evolution item (stone) used, consumed
                from the bag when the evolution is confirmed. Defaults to None.
        """

        self.setMaximumWidth(600)
        self.setMaximumHeight(530)
        self.clear_layout(self.layout())
        layout = self.layout()
        pokemon_images, evolve_button, dont_evolve_button = (
            self._ask_pokemon_evo_layout(individual_id, prevo_id, evo_id, item_name)
        )
        layout.addWidget(pokemon_images)
        layout.addWidget(evolve_button)
        layout.addWidget(dont_evolve_button)
        self.setStyleSheet("background-color: rgb(44,44,44);")
        self.setLayout(layout)
        self.show()

    def _ask_pokemon_evo_layout(
        self,
        individual_id: int,
        prevo_id: int,
        evo_id: int,
        item_name: Optional[str] = None,
    ):
        """
        Creates the GUI layout for the upcoming evolution.

        This function generates the visual components (images, text, etc.)
        to inform the user that a Pokémon is about to evolve.

        Args:
            individual_id (int): The UUID of the Pokemon to evolve.
            prevo_id (int): The identifier (National Pokedex Number) of the Pokémon to evolve.
            evo_id (int): The identifier (National Pokedex Number) of the evolved Pokémon.
            item_name (str, optional): The evolution item (stone) used, if any.
        """

        # Update mainpokemon_evolution and handle evolution logic
        prevo_name = return_name_for_id(prevo_id)
        evo_name = return_name_for_id(evo_id)

        # Check if sprites should be shown
        show_sprites = self._should_show_sprites()

        # Load the background image
        pixmap_bckg = QPixmap()
        pixmap_bckg.load(str(evolve_image_path))

        # Merge the background image and the Pokémon image
        merged_pixmap = QPixmap(pixmap_bckg.size())
        merged_pixmap.fill(
            QColor(0, 0, 0, 0)
        )  # RGBA where A (alpha) is 0 for full transparency
        # merged_pixmap.fill(Qt.transparent)
        # merge both images together
        painter = QPainter(merged_pixmap)
        painter.drawPixmap(0, 0, pixmap_bckg)
        
        # Only load, resize, and draw Pokémon sprites if sprites are enabled
        if show_sprites:
            # Display the Pokémon image
            pkmnimage_path = frontdefault / f"{prevo_id}.png"
            pkmnpixmap = QPixmap()
            pkmnpixmap.load(str(pkmnimage_path))
            
            pkmnimage_path2 = frontdefault / f"{(evo_id)}.png"
            pkmnpixmap2 = QPixmap()
            pkmnpixmap2.load(str(pkmnimage_path2))

            # Calculate the new dimensions to maintain the aspect ratio
            max_width = 200
            original_width = pkmnpixmap.width()
            original_height = pkmnpixmap.height()

            if original_width > max_width:
                new_width = max_width
                new_height = (original_height * max_width) // original_width
                pkmnpixmap = pkmnpixmap.scaled(new_width, new_height)

            # Calculate the new dimensions to maintain the aspect ratio
            max_width = 200
            original_width = pkmnpixmap2.width()
            original_height = pkmnpixmap2.height()

            if original_width > max_width:
                new_width = max_width
                new_height = (original_height * max_width) // original_width
                pkmnpixmap2 = pkmnpixmap2.scaled(new_width, new_height)

            painter.drawPixmap(255, 70, pkmnpixmap)
            painter.drawPixmap(255, 285, pkmnpixmap2)
        
        # Draw the text on top of the image
        font = QFont()
        font.setPointSize(12)  # Adjust the font size as needed
        painter.setFont(font)
        # fontlvl = QFont()
        # fontlvl.setPointSize(12)
        # Create a QPen object for the font color
        pen = QPen()
        pen.setColor(QColor(255, 255, 255))
        painter.setPen(pen)
        painter.drawText(
            150, 35, f"{prevo_name.capitalize()} is evolving to {evo_name.capitalize()}"
        )
        painter.drawText(
            95, 430, "Choose to evolve your Pokémon, or cancel to keep it as is"
        )
        # Capitalize the first letter of the Pokémon's name
        # name_label = QLabel(capitalized_name)
        painter.end()
        # Capitalize the first letter of the Pokémon's name

        # Create buttons for catching and killing the Pokémon
        evolve_button = QPushButton("Evolve Pokémon")
        dont_evolve_button = QPushButton("Cancel Evolution")
        qconnect(
            evolve_button.clicked,
            lambda: self.evolve_pokemon(
                individual_id,
                prevo_id,
                prevo_name,
                evo_id,
                evo_name,
                self.main_pokemon,
                item_name,
            ),
        )
        qconnect(
            dont_evolve_button.clicked,
            lambda: self.cancel_evolution(individual_id, prevo_name),
        )

        # Set the merged image as the pixmap for the QLabel
        evo_image_label = QLabel()
        evo_image_label.setPixmap(merged_pixmap)

        return evo_image_label, evolve_button, dont_evolve_button

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def evolve_pokemon(
        self,
        individual_id,
        prevo_id,
        prevo_name,
        evo_id,
        evo_name,
        main_pokemon,
        item_name=None,
    ):
        """Evolve a pokemon and save to database."""
        db = services.db

        try:
            pokemon = db.get_pokemon(individual_id)
            if not pokemon:
                self.logger.log(
                    "error", f"Could not find pokemon with id {individual_id}"
                )
                return

            # Guard against double-evolution: only proceed if this Pokémon is
            # still the expected pre-evolution species. A stale "Evolve now"
            # button or a re-shown evolution window could otherwise re-trigger
            # this on an already-evolved Pokémon (re-rolling its moves/ability).
            if str(pokemon.get("id")) != str(prevo_id):
                self.logger.log(
                    "info",
                    f"Skipping evolution for {individual_id}: already evolved "
                    f"(current id {pokemon.get('id')}, expected pre-evo {prevo_id}).",
                )
                return

            # Persist the pre-evolved species as caught before the id changes so
            # the Pokédex keeps crediting the earlier form (no-op on stores that
            # predate mark_as_caught — arrives with the PC-box/Pokédex leaf).
            # Logged as an error, not a warning: unlike a failed mark on an
            # ordinary save, this one is NOT recoverable. Once the id below is
            # overwritten the pre-evolution is gone from captured_pokemon, so
            # _reconcile_pokedex_history has nothing left to re-derive it from.
            if hasattr(db, "mark_as_caught"):
                try:
                    db.mark_as_caught(int(prevo_id))
                except Exception as e:
                    self.logger.log(
                        "error",
                        f"Failed to mark pre-evolution {prevo_id} as caught; it will "
                        f"be missing from the Pokedex: {e}",
                    )

            pokemon["name"] = evo_name.capitalize()
            pokemon["id"] = evo_id
            pokemon["type"] = search_pokedex(evo_name.lower(), "types")
            attacks = pokemon["attacks"]
            new_attacks = get_random_moves_for_pokemon(
                evo_name.lower(), int(pokemon["level"])
            )
            for new_attack in new_attacks:
                if new_attack not in attacks:
                    if len(attacks) < 4:
                        attacks.append(new_attack)
                    else:
                        dialog = AttackDialog(attacks, new_attack)
                        if dialog.exec() == QDialog.DialogCode.Accepted:
                            selected_attack = dialog.selected_attack
                            try:
                                index_to_replace = attacks.index(selected_attack)
                                attacks[index_to_replace] = new_attack
                                self.logger.log_and_showinfo(
                                    "info",
                                    self.translator.translate(
                                        "replaced_attack",
                                        selected_attack=format_move_name(selected_attack),
                                        new_attack=format_move_name(new_attack),
                                    ),
                                )
                            except ValueError:
                                self.logger.log_and_showinfo(
                                    "info",
                                    self.translator.translate(
                                        "selected_attack_not_found",
                                        selected_attack=format_move_name(selected_attack),
                                    ),
                                )
                        else:
                            self.logger.log_and_showinfo(
                                "info", self.translator.translate("no_attack_selected")
                            )
            pokemon["attacks"] = attacks
            base_stats = search_pokedex(evo_name.lower(), "baseStats")
            pokemon["base_stats"] = base_stats
            pokemon["stats"] = base_stats
            pokemon["xp"] = 0
            hp_stat = int(base_stats["hp"])
            iv = pokemon["iv"]
            ev = pokemon["ev"]
            level = pokemon["level"]
            hp = calculate_hp(hp_stat, level, ev, iv)
            pokemon["current_hp"] = int(hp)
            try:
                pokemon["growth_rate"] = get_growth_rate(int(evo_id))
            except (ValueError, TypeError):
                # Regional/other forms carry 10xxx ids that aren't in
                # poke_species.csv, so get_growth_rate raises for them on this
                # base (until F17's graceful-fallback version lands). Keep the
                # pre-evolution's growth rate — a family-level trait — so the
                # evolution still completes instead of aborting. Matches F17's
                # eventual "medium" fallback for ids it cannot resolve.
                pokemon["growth_rate"] = pokemon.get("growth_rate") or "medium"
            pokemon["base_experience"] = get_base_experience(int(evo_id))
            abilities = search_pokedex(evo_name.lower(), "abilities")
            numeric_abilities = None
            try:
                numeric_abilities = {k: v for k, v in abilities.items() if k.isdigit()}
            except Exception:
                pass
            if numeric_abilities:
                abilities_list = list(numeric_abilities.values())
                pokemon["ability"] = random.choice(abilities_list)
            else:
                pokemon["ability"] = self.translator.translate("no_ability")

            # Carry the nickname across evolution: only rewrite it when the user
            # never set a custom one (empty, or still matching the pre-evolution
            # species by its CSV or pretty name). Custom nicknames are preserved.
            # Regional/hyphenated forms use the pretty pokedex name so the label
            # reads "Mr. Rime" rather than "mr-rime".
            from ..functions.pokedex_functions import get_pretty_name_for_id

            def _normalize_nick(value):
                return (
                    str(value)
                    .lower()
                    .replace(" ", "")
                    .replace("-", "")
                    .replace("'", "")
                    .replace(".", "")
                    .replace(":", "")
                )

            old_nickname = pokemon.get("nickname", "")
            prevo_pretty = get_pretty_name_for_id(prevo_id)
            norm_old = _normalize_nick(old_nickname)
            is_default = (
                not old_nickname
                or norm_old == _normalize_nick(prevo_name)
                or norm_old == _normalize_nick(prevo_pretty)
            )
            if is_default:
                pretty_evo = get_pretty_name_for_id(evo_id)
                if pretty_evo == "Pokémon not found":
                    pretty_evo = evo_name.replace("-", " ").title()
                pokemon["nickname"] = pretty_evo

            # Recompute Combat Power from the evolved base stats so the stored
            # CP isn't left stale after evolution.
            pokemon["cp"] = calculate_cp_from_dict(pokemon)

            # Evolving from a previously-rejected state clears the soft flag so
            # the auto prompt resumes for the new form's future evolutions.
            pokemon["evolution_rejected"] = False

            # Save to database before awarding any evolution achievements.
            if not db.save_pokemon(pokemon):
                self.logger.log("error", f"Failed to save evolved pokemon {individual_id}")
                return

            # Consume the evolution stone (if this evolution was item-triggered)
            # and refresh any open item windows so the count updates live.
            if item_name:
                db.update_item_quantity(item_name, -1)
                from ..singletons import get_item_window, get_items_window

                item_w = get_item_window()
                if item_w is not None and is_alive(item_w):
                    item_w.renewWidgets()

                web_item_w = get_items_window()
                if web_item_w is not None and is_alive(web_item_w):
                    web_item_w.update_ui_data()

            # Award Badge 19 (Fossil) immediately after successful persistence
            # and item consumption, before any UI notifications that could fail
            # and skip the achievement.
            try:
                from ..resources import POKEMON_TIERS
                if int(evo_id) in POKEMON_TIERS.get("Fossil", []):
                    check_fossil = check_for_badge(self.achievements, 19)
                    if check_fossil is False:
                        # receive_badge sets the in-memory achievement only after
                        # save_badges succeeds, so a persistence failure will not
                        # leave achievements marked as awarded.
                        receive_badge(19, self.achievements)
            except Exception as e:
                self.logger.log("error", f"Error checking Badge 19 (Fossil): {e}")

            self.logger.log_and_showinfo(
                "info",
                self.translator.translate(
                    "mainpokemon_has_evolved", prevo_name=prevo_name, evo_name=evo_name
                ),
            )

        except Exception as e:
            show_warning_with_traceback(
                parent=mw, exception=e, message="Error occured in evolving pokemon"
            )
            self.logger.log("error", f"{e}")
            return

        try:  # Update Main Pokemon Object and sync with file
            if main_pokemon is not None and main_pokemon.individual_id == individual_id:
                # Update the in-memory main_pokemon object with the evolved data                # Call update_main_pokemon to ensure file and object are in sync (this will also save to disk)
                main_pokemon, _ = update_main_pokemon(main_pokemon)

                # Update UI as before
                class Container(object):
                    pass

                reviewer = Container()
                reviewer.web = mw.reviewer.web
                self.reviewer_obj.update_life_bar(reviewer, 0, 0)
                if self.test_window.isVisible() is True:
                    self.test_window.display_first_encounter()
        except Exception as e:
            show_warning_with_traceback(
                parent=mw,
                exception=e,
                message="Error occured in updating main_pokemon obj",
            )
        self.display_evo_complete(prevo_id, evo_id)
        check = check_for_badge(self.achievements, 16)
        if check is False:
            receive_badge(16, self.achievements)

        from ..singletons import pokemon_pc

        pokemon_pc.refresh_pokemon_grid()
        # Refresh the open details panel so the just-evolved Pokémon no longer
        # shows stale pre-evolution info / an "Evolve now" button that would
        # otherwise let it be evolved a second time.
        try:
            if pokemon_pc.isVisible():
                pokemon_pc.show_pokemon_details({"individual_id": individual_id})
        except Exception:
            pass

    def cancel_evolution(self, individual_id, prevo_name):
        """Cancel evolution and save changes to database."""
        db = services.db

        try:
            pokemon_to_update = db.get_pokemon(individual_id)
            if not pokemon_to_update:
                self.logger.log(
                    "error",
                    f"Could not find pokemon with individual_id {individual_id} to cancel evolution.",
                )
                return

            # Add logic to learn new moves
            attacks = pokemon_to_update.get("attacks", [])
            level = pokemon_to_update.get("level", 1)
            new_attacks = get_random_moves_for_pokemon(prevo_name.lower(), int(level))

            for new_attack in new_attacks:
                if new_attack not in attacks:
                    if len(attacks) < 4:
                        attacks.append(new_attack)
                    else:
                        dialog = AttackDialog(attacks, new_attack)
                        if dialog.exec() == QDialog.DialogCode.Accepted:
                            selected_attack = dialog.selected_attack
                            try:
                                index_to_replace = attacks.index(selected_attack)
                                attacks[index_to_replace] = new_attack
                                self.logger.log_and_showinfo(
                                    "info",
                                    self.translator.translate(
                                        "replaced_attack",
                                        selected_attack=format_move_name(selected_attack),
                                        new_attack=format_move_name(new_attack),
                                    ),
                                )
                            except ValueError:
                                self.logger.log_and_showinfo(
                                    "info",
                                    self.translator.translate(
                                        "selected_attack_not_found",
                                        selected_attack=format_move_name(selected_attack),
                                    ),
                                )
                        else:
                            self.logger.log_and_showinfo(
                                "info", self.translator.translate("no_attack_selected")
                            )

            pokemon_to_update["attacks"] = attacks
            pokemon_to_update["evolution_rejected"] = True

            # Save to database
            db.save_pokemon(pokemon_to_update)

            # If the main pokemon was the one, update its object in memory
            if self.main_pokemon and self.main_pokemon.individual_id == individual_id:
                self.main_pokemon, _ = update_main_pokemon(self.main_pokemon)

            self.logger.log_and_showinfo(
                "info",
                f"Evolution rejected for {prevo_name}. You can still evolve it anytime from the Pokémon PC.",
            )
            self.close()

        except Exception as e:
            show_warning_with_traceback(
                parent=mw,
                exception=e,
                message="Error occurred while canceling evolution",
            )
            self.logger.log("error", f"Error in cancel_evolution: {e}")
