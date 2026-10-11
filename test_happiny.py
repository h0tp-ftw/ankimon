import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

from Ankimon.pyobj.pokemon_obj import PokemonObject
from Ankimon.functions.pokedex_functions import check_evolution_by_item
from harness.fixtures import build_pokemon

def check_happiny():
    # Happiny's prevo item is Oval Stone.
    # What's its item ID?
    from Ankimon.functions.pokedex_functions import return_id_for_item_name
    oval_id = return_id_for_item_name("oval-stone")
    print("Oval stone id:", oval_id)
    print("Does happiny evolve with it?", check_evolution_by_item(440, oval_id))

check_happiny()
