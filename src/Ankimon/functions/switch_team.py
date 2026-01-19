from aqt import mw
from aqt.utils import tooltip
import json

from ..resources import team_pokemon_path, mypokemon_path, mainpokemon_path
from ..singletons import main_pokemon, logger, reviewer_obj
from ..functions.update_main_pokemon import update_main_pokemon

def switch_team_member():
    """
    Switches the current main pokemon to the next one in the team.
    """
    if not team_pokemon_path.is_file():
         tooltip("No team found.")
         return

    try:
        with open(team_pokemon_path, 'r', encoding='utf-8') as f:
            team_data = json.load(f)
    except json.JSONDecodeError:
        tooltip("Team file is corrupted.")
        return

    # Filter out empty slots if any (though usually None is not saved in json as object usually)
    # The TeamDialog saves them as objects with individual_id.
    team_data = [p for p in team_data if p and p.get('individual_id')]

    if not team_data:
        tooltip("Your team is empty. Add pokemon to your team first!")
        return

    # Get current main pokemon Individual ID
    # main_pokemon is an object
    current_id = main_pokemon.individual_id

    # Find index of current pokemon in the team
    current_index = -1
    for i, p in enumerate(team_data):
        if p['individual_id'] == current_id:
            current_index = i
            break
    
    # Calculate next index
    if current_index == -1:
        # If current is not in team, switch to the first one in team
        next_index = 0
    else:
        next_index = (current_index + 1) % len(team_data)

    next_id = team_data[next_index]['individual_id']

    # 4. Load full pokemon data from mypokemon.json to get the full stats
    if not mypokemon_path.is_file():
        tooltip("Collection file not found.")
        return

    try:
        with open(mypokemon_path, 'r', encoding='utf-8') as f:
            my_pokemon_list = json.load(f)
    except json.JSONDecodeError:
        tooltip("Collection file corrupted.")
        return

    new_pokemon_data = next(
        (p for p in my_pokemon_list if p.get('individual_id') == next_id),
        None
    )

    if not new_pokemon_data:
        tooltip("Error: Pokemon data not found in collection.")
        return

    # 5. Overwrite mainpokemon.json
    # We create a list with one dictionary as expected by update_main_pokemon
    try:
        with open(mainpokemon_path, 'w', encoding='utf-8') as f:
            json.dump([new_pokemon_data], f, indent=4)
    except Exception as e:
        tooltip(f"Error saving main pokemon: {str(e)}")
        return

    # 6. Update singleton
    # This function reloads from the file we just wrote
    update_main_pokemon(main_pokemon)

    # 7. Notify
    tooltip(f"Switched to {main_pokemon.name}!")

    # 8. Update Reviewer UI immediately
    if mw.reviewer.web:
        reviewer_obj.update_life_bar(mw.reviewer, None, None)
