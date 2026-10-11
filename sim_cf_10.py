import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

# In Ankimon, items with evoType: "useItem" are evolutionary stones.
# "It doesn't evolve when holding an oval stone and leveling up during the daytime"
# This makes perfect sense because it's a "useItem" evolution! They have to click it from the bag!
# But wait, in the mainline games, Happiny evolves by HOLDING the Oval Stone and leveling up during the day.
# Let's check Smogon pokedex.json for Happiny/Chansey in Ankimon vs Mainline.
# The user expects: "leveling up holding oval stone"
# The code says: "evoType": "useItem", "evoItem": "Oval Stone"
