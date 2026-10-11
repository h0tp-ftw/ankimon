import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def check_speed():
    # speed works normally, but how does priority work?
    # the battle_engine processes it as `user_priority` vs `opponent_priority`.
    pass
