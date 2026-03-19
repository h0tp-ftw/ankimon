import json
import random

from ..resources import learnset_path


def _get_learnset_moves(pokemon_name, pokemon_level, generation=9):
    """
    Return all moves a Pokémon can know at *pokemon_level* in a single *generation*.

    Args:
        pokemon_name: Pokémon name (case-insensitive).
        pokemon_level: Current level of the Pokémon.
        generation: Generation to filter learn_codes by (default 9).

    Returns:
        dict mapping move_name -> learn_level for every move learnable
        at or below *pokemon_level* within the specified generation.
    """
    with open(learnset_path, "r", encoding="utf-8") as file:
        learnsets = json.load(file)

    pokemon_name = pokemon_name.lower()
    pokemon_learnset = learnsets.get(pokemon_name, {}).get("learnset", {})

    moves = {}

    target_generation = str(generation)

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
