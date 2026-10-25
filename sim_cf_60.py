import sys
import os

sys.path.insert(0, os.path.abspath('src'))
from harness.headless_env import start_session
env = start_session()

# Wait... it really DOES NOT catch the Pokemon?!
# Let me check `ankimon_items_web/shop_obj.py` to see if there is web bag logic for it.
pass
