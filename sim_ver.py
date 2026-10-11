import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

def check_ankiweb_version():
    pass

# The latest AnkiWeb version is v2.2-E according to SOUL.md.
# Wait, let's download the latest Ankiweb version to see what code is in it!
