import os
import orjson
import logging

logger = logging.getLogger(__name__)

PWD = os.path.dirname(os.path.abspath(__file__))

move_json_location = os.path.join(PWD, 'moves.json')
with open(move_json_location, 'rb') as f:
    all_move_json = orjson.loads(f.read())

pkmn_json_location = os.path.join(PWD, 'pokedex.json')
with open(pkmn_json_location, 'rb') as f:
    pokedex = orjson.loads(f.read())

random_battle_set_location = os.path.join(PWD, 'random_battle_sets.json')
with open(random_battle_set_location, 'rb') as f:
    random_battle_sets = orjson.loads(f.read())


pokemon_sets = random_battle_sets
effectiveness = {}
team_datasets = None
