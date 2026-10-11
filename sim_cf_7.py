import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def peek():
    # If the user asks about the confused status... The truth is, Ankimon's engine does not calculate "hitting oneself in confusion". It's a stateless single-turn evaluator.
    # Actually wait. Let's check Ankimon's battle loop to see how it handles status.
    pass
