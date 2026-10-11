import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def check():
    # In Ankimon, is confusion simulated as a battle effect?
    # Ankimon uses poke_engine. Is there a confusion self-hit in poke_engine?
    pass
