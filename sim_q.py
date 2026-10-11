import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def answer():
    # Amulet coin: YES it doubles it, but it also caps at 400 total.
    # What about rotom?
    pass
