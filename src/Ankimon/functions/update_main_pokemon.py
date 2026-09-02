import json
import uuid
from typing import Optional

from ..functions.pokedex_functions import search_pokedex, search_pokedex_by_id
from ..resources import mainpokemon_path
from ..pyobj.pokemon_obj import PokemonObject
from ..services import services

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


def _normalize_loaded_hp(pokemon_data, max_hp):
    """Return a safe current HP from persisted data.

    New records store the live value in both ``current_hp`` and ``hp``. Older
    or partially migrated records may have either field missing, null, or
    malformed, so try both before restoring the Pokemon to full health.
    """
    for candidate in (
        pokemon_data.get("current_hp"),
        pokemon_data.get("hp"),
        max_hp,
    ):
        if candidate is None:
            continue
        try:
            current_hp = int(candidate)
        except (TypeError, ValueError, OverflowError):
            continue
        return max(0, min(current_hp, max_hp))

    return max_hp


def _apply_loaded_hp(main_pokemon, pokemon_data):
    """Normalize persisted HP fields and apply one authoritative value."""
    max_hp = main_pokemon.calculate_max_hp()
    current_hp = _normalize_loaded_hp(pokemon_data, max_hp)

    main_pokemon.max_hp = max_hp
    main_pokemon.hp = current_hp
    main_pokemon.current_hp = current_hp
    pokemon_data["hp"] = current_hp
    pokemon_data["current_hp"] = current_hp
    return current_hp


def update_main_pokemon(main_pokemon: Optional[PokemonObject] = None):
    """
    Updates or initializes the main Pokémon object using data from the database.
    Falls back to JSON file for backwards compatibility.
    """
    db = services.db

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
            # Prefer the stored species name; only fall back to a pokedex-id
            # lookup (and backfill it) when the record predates name storage.
            pokemon_name = main_pokemon_data.get("name")
            if not pokemon_name:
                pokemon_name = search_pokedex_by_id(main_pokemon_data["id"])
                main_pokemon_data["name"] = pokemon_name

            main_pokemon_data["base_stats"] = search_pokedex(pokemon_name, "baseStats")
            if "stats" in main_pokemon_data:
                del main_pokemon_data["stats"]
            original_hp = main_pokemon_data.get("hp")
            original_current_hp = main_pokemon_data.get("current_hp")
            main_pokemon.update_stats(**main_pokemon_data)

            current_hp = _apply_loaded_hp(main_pokemon, main_pokemon_data)
            if original_hp != current_hp or original_current_hp != current_hp:
                save_main_pokemon(main_pokemon)
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
                    _apply_loaded_hp(main_pokemon, main_pokemon_data[0])
                    save_main_pokemon(
                        main_pokemon
                    )  # Save the updated main Pokémon data
                # if file does load or is empty use default value
                else:
                    main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)
                return main_pokemon, mainpokemon_empty
            except Exception:
                main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)
                return main_pokemon, mainpokemon_empty
    else:
        main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)
        return main_pokemon, mainpokemon_empty


def save_main_pokemon(main_pokemon: PokemonObject):
    """Saves the main Pokémon object to the database."""
    db = services.db

    if hasattr(main_pokemon, "to_dict"):
        data = main_pokemon.to_dict()
    else:
        data = main_pokemon.__dict__

    db.save_main_pokemon(data)
