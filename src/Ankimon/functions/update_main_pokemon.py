import json
import uuid
from collections import defaultdict
from typing import Optional

from ..functions.pokedex_functions import search_pokedex, search_pokedex_by_id
from ..resources import mainpokemon_path
from ..pyobj.pokemon_obj import PokemonObject
from aqt import mw

# default values to fall back in case of load error
MAIN_POKEMON_DEFAULT = {
    "name": "Please Restart Anki",
    "gender": "N",  # Ditto is genderless
    "level": 5,
    "id": 132,
    "ability": "Limber",
    "type": ["Normal"],
    "base_stats": {"hp": 48, "atk": 48, "def": 48, "spa": 48, "spd": 48, "spe": 48},
    "xp": 0,
    "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
    "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
    "attacks": ["Transform", "Tackle"],
    "base_experience": 101,
    "hp": 100,
    "growth_rate": "medium-fast",
    "individual_id": "00000000-0000-0000-0000-000000000"
    + str(uuid.uuid4())[-3:],  # Last 3 digits random
    "tier": "Normal",
    "shiny": False,
    "captured_date": "2000-01-01 00:00:00",
}


def update_main_pokemon(main_pokemon: Optional[PokemonObject] = None):
    """
    Updates or initializes the main Pokémon object using data from the database.
    Falls back to JSON file for backwards compatibility.
    """
    db = mw.ankimon_db

    if main_pokemon is None:
        main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)

    # Normalize xp to 0 if it's None
    if main_pokemon.xp is None:
        main_pokemon.xp = 0

    mainpokemon_empty = True
    
    # Try database first
    if db.is_migrated():
        main_pokemon_data = db.get_main_pokemon()
        if main_pokemon_data:
            mainpokemon_empty = False
            pokemon_name = search_pokedex_by_id(main_pokemon_data["id"])
            main_pokemon_data["base_stats"] = search_pokedex(pokemon_name, "baseStats")
            if "stats" in main_pokemon_data:
                del main_pokemon_data["stats"]
            main_pokemon.update_stats(**main_pokemon_data)
            
            max_hp = main_pokemon.calculate_max_hp()
            main_pokemon.max_hp = max_hp
            if main_pokemon_data.get("current_hp", max_hp) > max_hp:
                main_pokemon_data["current_hp"] = max_hp
            main_pokemon.hp = main_pokemon_data.get("current_hp", max_hp)
            return main_pokemon, mainpokemon_empty
        else:
            return PokemonObject(**MAIN_POKEMON_DEFAULT), mainpokemon_empty
    
    # Fallback to JSON for backwards compatibility
    if mainpokemon_path.is_file():
        with open(mainpokemon_path, "r", encoding="utf-8") as mainpokemon_json:
            try:
                main_pokemon_data = json.load(mainpokemon_json)
                if main_pokemon_data:
                    mainpokemon_empty = False
                    pokemon_name = search_pokedex_by_id(main_pokemon_data[0]["id"])
                    main_pokemon_data[0]["base_stats"] = search_pokedex(
                        pokemon_name, "baseStats"
                    )
                    del main_pokemon_data[
                        0
                    ][
                        "stats"
                    ]  # For legacy code, i.e. for when "stats" in the JSON actually meant "base_stat"
                    main_pokemon.update_stats(**main_pokemon_data[0])
                    save_main_pokemon(
                        main_pokemon
                    )  # Save the updated main Pokémon data
                # if file does load or is empty use default value
                else:
                    main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)
                max_hp = main_pokemon.calculate_max_hp()
                main_pokemon.max_hp = max_hp
                if main_pokemon_data[0].get("current_hp", max_hp) > max_hp:
                    main_pokemon_data[0]["current_hp"] = max_hp
                if main_pokemon_data:
                    main_pokemon.hp = main_pokemon_data[0].get("current_hp", max_hp)
                return main_pokemon, mainpokemon_empty
            except Exception:
                main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)
                return main_pokemon, mainpokemon_empty
    else:
        main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)
        return main_pokemon, mainpokemon_empty



def save_main_pokemon(main_pokemon: PokemonObject):
    """Saves the main Pokémon object to the database."""
    db = mw.ankimon_db
    
    if hasattr(main_pokemon, 'to_dict'):
        data = main_pokemon.to_dict()
    else:
        data = main_pokemon.__dict__
    
    db.save_main_pokemon(data)


def set_main_from_record(pokemon_data: dict, main_pokemon: PokemonObject, heal_to_full: bool = False) -> PokemonObject:
    """Switch the active/main Pokémon to ``pokemon_data`` (a collection record).

    Single source of truth shared by the collection picker (``collection_dialog``)
    and the in-review team-cycle hotkey. It:
      1. Persists the OUTGOING main's in-memory state (xp/HP/status) back to the
         collection, so switching never silently drops progress.
      2. Rebuilds ``main_pokemon`` in place from the record.
      3. Preserves the new main's stored current HP (clamped to max) on the
         authoritative ``hp`` field and its ``current_hp`` mirror -- so switching
         is NOT a free heal.
    Returns the same ``main_pokemon`` object, mutated in place.
    """
    db = mw.ankimon_db

    # 1. Persist the outgoing main before replacing it.
    try:
        if (
            main_pokemon is not None
            and getattr(main_pokemon, "individual_id", None)
            and db.get_main_pokemon()
        ):
            db.save_pokemon(main_pokemon.to_dict())
    except Exception:
        pass  # No active main yet -- nothing to preserve.

    # 2. Build the new main from the record.
    pokemon_id = pokemon_data.get("id")
    pokemon_name = search_pokedex_by_id(pokemon_id)
    base_stats = search_pokedex(pokemon_name, "baseStats")
    new_main = PokemonObject(
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
        nature=pokemon_data.get("nature", "serious"),
        gender=pokemon_data.get("gender", "N"),
        shiny=pokemon_data.get("shiny", False),
        individual_id=pokemon_data.get("individual_id", str(uuid.uuid4())),
        id=pokemon_data.get("id", 133),
        status=pokemon_data.get("status", None),
        volatile_status=set(pokemon_data.get("volatile_status", [])),
        xp=pokemon_data.get("xp", 0),
        nickname=pokemon_data.get("nickname", ""),
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
    for attr in (
        "captured_date", "tier", "friendship", "pokemon_defeated",
        "everstone", "mega", "special_form", "base_experience",
    ):
        if attr in pokemon_data:
            setattr(new_main, attr, pokemon_data[attr])

    # 3. Preserve stored current HP (no free heal). ``hp`` is authoritative;
    #    ``current_hp`` mirrors it. Both clamped to max.
    max_hp = new_main.calculate_max_hp()
    new_main.max_hp = max_hp
    if heal_to_full:
        # Collection picker: keep its long-standing heal-on-pick behaviour.
        new_main.hp = max_hp
    else:
        # Team-cycle hotkey: preserve the incoming Pokémon's stored HP (no free
        # heal). hp is authoritative; current_hp mirrors it.
        stored_hp = pokemon_data.get("current_hp", pokemon_data.get("hp", max_hp))
        try:
            stored_hp = int(stored_hp)
        except (TypeError, ValueError):
            stored_hp = max_hp
        new_main.hp = max(0, min(stored_hp, max_hp))
    new_main.current_hp = new_main.hp

    # 4. Mutate the existing reference in place and persist as the main pokemon.
    main_pokemon.__dict__.update(new_main.__dict__)
    db.save_main_pokemon(main_pokemon.to_dict())
    return main_pokemon
