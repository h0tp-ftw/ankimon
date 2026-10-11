import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def peek():
    # evoType is useItem for Oval Stone.
    # In pokedex_functions.py or item_window.py, how is useItem triggered?
    # Do they need to use the item from the bag instead of holding it and leveling up?
    # Because "evoType": "useItem" usually implies using the item from the bag.
    pass
