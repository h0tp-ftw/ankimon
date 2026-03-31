import json
from collections import defaultdict
import uuid

from aqt.utils import showInfo, showWarning
from ..pyobj.error_handler import show_warning_with_traceback
from PyQt6.QtWidgets import *
from PyQt6.QtGui import *
from PyQt6.QtCore import *
from aqt import mw
import re

from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.translator import Translator
from ..pyobj.test_window import TestWindow
from ..pyobj.reviewer_obj import Reviewer_Manager
from ..functions.pokedex_functions import search_pokedex, search_pokedex_by_id


def PokemonTrade(individual_id):
     # Load the data from database
    db = mw.ankimon_db
    old_pokemon = db.get_pokemon(individual_id)
    main_pokemon = db.get_main_pokemon()

    #check if player tries to trade mainpokemon
    if not main_pokemon["individual_id"] == individual_id:
        # Create a main window
        window = QDialog()
        window.setWindowTitle(f"Trade Pokemon {old_pokemon['name']}")
        # Create an input field for error code
        trade_code_input = QLineEdit()
        trade_code_input.setPlaceholderText("Enter Pokemon Code you want to Trade for")

        # Create a button to save the input
        trade_button = QPushButton("Trade Pokemon")
        qconnect(
            trade_button.clicked,
            lambda: PokemonTradeIn(trade_code_input.text(), old_pokemon),
        )
        # Information label
        info = "Pokemon Infos have been Copied to your Clipboard! \nNow simply paste this text into Teambuilder in PokemonShowdown. \nNote: Fight in the [Gen 9] Anything Goes - Battle Mode"

        pokemon_ev = ",".join([f"{value}" for stat, value in ev.items()])
        pokemon_iv = ",".join([f"{value}" for stat, value in iv.items()])
        if gender == "M":
            gender = 0
        elif gender == "F":
            gender = 1
        elif gender == "N":
            gender = 2
        else:
            gender = 3  # None

        attacks_ids = []
        for attack in attacks:
            key = re.sub(r"[^a-z0-9]", "", attack.lower())  # “U-turn” → “uturn”
            move_details = find_details_move(key)
            if not move_details:
                raise ValueError(f"Unknown move: {attack}")
            attacks_ids.append(str(move_details["num"]))

        attacks_id_string = ",".join(attacks_ids)  # Concatenated with a delimiter

        # Concatenating details to form a single string
        info = f"{id},{level},{gender},{pokemon_ev},{pokemon_iv},{attacks_id_string}"

        Trade_Info = QLabel(f"{name} Code: {info}")

        # Create a layout and add the labels
        layout = QVBoxLayout()
        layout.addWidget(Trade_Info)
        layout.addWidget(trade_code_input)
        layout.addWidget(trade_button)
        layout.addWidget(trade_code_input)
        # Set the layout for the main window
        window.setLayout(layout)

        # Copy text to clipboard in Anki
        mw.app.clipboard().setText(f"{info}")

        window.exec()
    else:
        showWarning(
            "You cant trade your Main Pokemon ! \n Please pick a different Main Pokemon and then you can trade this one."
        )


def PokemonTradeIn(number_code, old_pokemon):
    if len(number_code) > 15:
        # Split the string into a list of integers
        numbers = [int(num) for num in number_code.split(",")]

        # Extracting specific parts of the list
        pokemon_id = numbers[0]
        level = numbers[1]
        gender_id = numbers[2]
        ev_stats = {
            "hp": numbers[3],
            "atk": numbers[4],
            "def": numbers[5],
            "spa": numbers[6],
            "spd": numbers[7],
            "spe": numbers[8],
        }
        iv_stats = {
            "hp": numbers[9],
            "atk": numbers[10],
            "def": numbers[11],
            "spa": numbers[12],
            "spd": numbers[13],
            "spe": numbers[14],
        }
        attack_ids = numbers[15:]
        attacks = []
        for attack_id in attack_ids:
            move = find_move_by_num(int(attack_id))
            attacks.append(move["name"])
        details = find_pokemon_by_id(pokemon_id)
        name = details["name"]
        type = details["types"]
        if gender_id == 0:
            gender = "M"
        elif gender_id == 1:
            gender = "F"
        elif gender_id == 2:
            gender = "N"
        else:
            gender = None  # None
        stats = details["baseStats"]
        # type = search_pokedex(name, "types")
        # stats = search_pokedex(name, "baseStats")
        growth_rate = get_growth_rate(pokemon_id)
        # Creating a dictionary to organize the extracted information
        stats["xp"] = 0
        new_pokemon = {
                "name": name,
                "gender": gender,
                "ability": ability,
                "level": level,
                "id": pokemon_id,
                "type": type,
                "stats": stats,
                "ev": ev_stats,
                "iv": iv_stats,
                "attacks": attacks,
                "base_experience": base_experience,
                "current_hp": calculate_hp(stats["hp"], level, ev, iv),
                "growth_rate": growth_rate,
        }
        trade_pokemon(old_pokemon, new_pokemon)
        logger.log_and_showinfo("info",f"You have successfully traded your {old_pokemon["name"]} for {name} ")
    else:
        showWarning("Please enter a valid Code!")


def trade_pokemon(old_pokemon, new_pokemon):
    """Trades a pokemon by saving the new pokemon to the database."""
    db = mw.ankimon_db
    
    try:
        db.replace_pokemon(new_pokemon, old_pokemon["individual_id"])
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message=f"An error occurred during trade: {e}")

def MainPokemon(
    pokemon_data: dict,
    main_pokemon: PokemonObject,
    logger: ShowInfoLogger,
    translator: Translator,
    reviewer_obj: Reviewer_Manager,
    test_window: TestWindow,
):
    from ..functions.migration import migrate_starter_individual_id

    migrate_starter_individual_id()
    migrate_starter_individual_id()
    db = mw.ankimon_db
    
    # --- Save the existing mainpokemon to mypokemon before replacing ---
    try:
        current_main = db.get_main_pokemon()
        if current_main:
            # Update or save the current main pokemon to captured_pokemon
            db.save_pokemon(current_main)
    except Exception:
        pass  # If no main pokemon exists, just continue

    # --- Now proceed to set the new mainpokemon as before ---
    pokemon_id = pokemon_data.get("id")
    pokemon_name = search_pokedex_by_id(pokemon_id)
    base_stats = search_pokedex(pokemon_name, "baseStats")
    current_hp = PokemonObject.calc_stat(
        "hp",
        base_stats["hp"],
        pokemon_data["level"],
        pokemon_data["iv"]["hp"],
        pokemon_data["ev"]["hp"],
        pokemon_data.get("nature", "serious"),
    )
    # Create NEW PokemonObject instance using class constructor
    new_main_pokemon = PokemonObject(
        name=pokemon_name,
        level=pokemon_data.get("level", 5),
        ability=pokemon_data.get("ability", ["none"]),
        type=pokemon_data.get("type", ["Normal"]),
        base_stats=base_stats,
        ev=pokemon_data.get("ev", defaultdict(int)),
        iv=pokemon_data.get("iv", defaultdict(int)),
        attacks=pokemon_data.get("attacks", ["Struggle"]),
        base_experience=pokemon_data.get("base_experience", 0),
        growth_rate=pokemon_data.get("growth_rate", "medium"),
        current_hp=current_hp,
        gender=pokemon_data.get("gender", "N"),
        shiny=pokemon_data.get("shiny", False),
        individual_id=pokemon_data.get("individual_id", str(uuid.uuid4())),
        id=pokemon_data.get("id", 133),
        status=pokemon_data.get("status", None),
        volatile_status=set(pokemon_data.get("volatile_status", [])),
        xp=pokemon_data.get("xp", 0),
        nickname=pokemon_data.get("nickname", ""),
        # Add common extra fields if constructor supports them
        friendship=pokemon_data.get("friendship", 0),
        pokemon_defeated=pokemon_data.get("pokemon_defeated", 0),
        everstone=pokemon_data.get("everstone", False),
        mega=pokemon_data.get("mega", False),
        special_form=pokemon_data.get("special_form", None),
        tier=pokemon_data.get("tier", None),
        captured_date=pokemon_data.get("captured_date", None),
        is_favorite=pokemon_data.get("is_favorite", False),
        held_item=pokemon_data.get("held_item"),
    )
    # Set any additional fields not in constructor
    extra_fields = [
        "captured_date",
        "tier",
        "friendship",
        "pokemon_defeated",
        "everstone",
        "mega",
        "special_form",
        "current_hp",
        "base_experience",
    ]
    for attr in extra_fields:
        if attr in pokemon_data:
            setattr(new_main_pokemon, attr, pokemon_data[attr])

    # Update existing reference
    main_pokemon.__dict__.update(new_main_pokemon.__dict__)

    # Save to database
    db.save_main_pokemon(main_pokemon.to_dict())

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
