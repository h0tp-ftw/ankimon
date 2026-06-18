import json

from aqt.utils import showInfo, showWarning
from ..pyobj.error_handler import show_warning_with_traceback
from PyQt6.QtWidgets import *
from PyQt6.QtGui import *
from PyQt6.QtCore import *
from aqt import mw
import re

from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.translator import Translator
from ..pyobj.test_window import TestWindow
from ..pyobj.reviewer_obj import Reviewer_Manager

def MainPokemon(
    pokemon_data: dict,
    main_pokemon: PokemonObject,
    logger: ShowInfoLogger,
    translator: Translator,
    reviewer_obj: Reviewer_Manager,
    test_window: TestWindow,
):
    from ..functions.update_main_pokemon import set_main_from_record

    # Switch the active/main Pokémon. Shared with the in-review team-cycle hotkey
    # via set_main_from_record: it saves the outgoing main's progress and
    # preserves the incoming Pokémon's stored HP (no free heal).
    set_main_from_record(pokemon_data, main_pokemon)

    logger.log_and_showinfo(
        "info",
        translator.translate(
            "picked_main_pokemon", main_pokemon_name=main_pokemon.name.capitalize()
        ),
    )

    # Update UI components
    class Container(object):
        pass

    reviewer = Container()
    reviewer.web = mw.reviewer.web
    reviewer_obj.update_life_bar(reviewer, 0, 0)

    if test_window.isVisible():
        test_window.display_first_encounter()

    from ..singletons import pokemon_pc

    pokemon_pc.refresh_pokemon_grid()
