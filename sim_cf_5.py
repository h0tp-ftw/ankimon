import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def check():
    # If the user asks if they still inflict damage to themselves:
    # poke_engine applies volatileStatus confusion
    # But does poke_engine have the self-hit logic?
    # Actually poke_engine does not simulate the *start* of the turn logic like self-hitting for confusion. It simulates a move pair.
    # It assumes if you sent a move, you didn't hit yourself!
    pass
