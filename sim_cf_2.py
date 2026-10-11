import sys
import os

sys.path.insert(0, os.path.abspath('src'))

# The `poke_engine` fork in ankimon is designed to generate a single battle state transition.
# Wait, Ankimon uses poke_engine purely as a damage calculator and instruction generator for showdown bots originally.
# BUT Ankimon uses `poke_engine` to get instructions for the turn, and then interprets them to apply to `PokemonObject`.
# Let's check `src/Ankimon/battle_loop.py` or similar to see how it uses `get_all_state_instructions`.
