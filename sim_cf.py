import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def peek_engine():
    # We saw test_battle_mechanics.py has tests for confusion. Let's see them.
    with open('src/Ankimon/poke_engine/tests/test_battle_mechanics.py') as f:
        lines = f.readlines()
        for i, l in enumerate(lines):
            if "confusion" in l:
                print("Line", i, l.strip())

peek_engine()
