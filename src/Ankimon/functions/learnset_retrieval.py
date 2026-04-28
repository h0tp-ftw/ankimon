import json
import random

from ..resources import learnset_path


def _get_learnset_moves(pokemon_name, pokemon_level, generation=9):
    """
    Return all moves a Pokémon can know at *pokemon_level* in a single *generation*.
    Falls back to earlier generations if no moves are found.
    """
    with open(learnset_path, "r", encoding="utf-8") as file:
        learnsets = json.load(file)

    pokemon_name = pokemon_name.lower()
    pokemon_learnset = learnsets.get(pokemon_name, {}).get("learnset", {})

    moves = {}
    
    # Try the requested generation first, then fallback to all earlier generations
    for gen in range(generation, 0, -1):  # Try from requested gen down to gen 1
        moves = {}
        target_generation = str(gen)
        
        for move, learn_codes in pokemon_learnset.items():
            best = -1
            for learn_code in learn_codes:
                move_generation, _, move_level = learn_code.partition("L")
                if move_generation != target_generation:
                    continue
                
                learn_level = int(move_level)
                if pokemon_level >= learn_level > best:
                    best = learn_level
            
            if best >= 0:
                moves[move] = best
        
        # If we found moves, return them
        if moves:
            break
    
    return moves


def get_all_pokemon_moves(pokemon_name, pokemon_level, generation=9):
    """Return a list of all move names learnable at or below *pokemon_level*."""
    return list(_get_learnset_moves(pokemon_name, pokemon_level, generation).keys())


def get_random_moves_for_pokemon(pokemon_name, pokemon_level, generation=9):
    """Return up to 4 shuffled move names learnable at or below *pokemon_level*."""
    moves = list(_get_learnset_moves(pokemon_name, pokemon_level, generation).keys())
    random.shuffle(moves)

    return moves[:4]


def get_levelup_move_for_pokemon(pokemon_name, pokemon_level, generation=9):
    """Return a list of moves learned at exactly *pokemon_level* (never None)."""
    all_moves = _get_learnset_moves(pokemon_name, pokemon_level, generation)

    return [move for move, learn_level in all_moves.items() if learn_level == pokemon_level]
