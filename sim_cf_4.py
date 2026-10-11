import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def peek():
    with open('src/Ankimon/poke_engine/instruction_generator.py') as f:
        lines = f.readlines()
        for i, l in enumerate(lines):
            if "confusion" in l:
                print(i, l.strip())

peek()
