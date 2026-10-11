import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def check_ankiweb_version():
    # If the user is on the ankiweb version, maybe check_evolution_for_pokemon doesn't have the "levelMove" implementation yet in that branch?
    # Or maybe the pokedex.json in their version doesn't have evoLevel/evoType/evoMove?
    # I should check the commit history of pokedex.json or the evolution function to see when levelMove was added!
    pass
