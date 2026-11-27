"""Smart Battle: pick the strongest move out of the active Pokémon's moveset.

Used by battle_loop.on_review_card when ``battle.smart_battle`` is on.
Every move the active Pokémon knows is scored by its expected damage against the
current enemy, using poke_engine's own damage calculator. That covers type
effectiveness, STAB, power, physical/special vs the right stats, abilities,
items, stat boosts and weather. The score is multiplied by accuracy, so a
reliable move can beat a slightly stronger one that often misses.

This only chooses the move. Catching/defeating is untouched.
"""

import copy
from collections import defaultdict

from ..poke_engine import constants
from ..poke_engine.helpers import normalize_name
from ..poke_engine.objects import State
from ..poke_engine.damage_calculator import (
    calculate_damage,
    get_move,
    type_effectiveness_modifier,
)
from ..poke_engine.find_state_instructions import (
    update_attacking_move,
    user_moves_first,
)


# Moves that make the user faint (or are pure sacrifices). Never auto-picked:
# the engine scores Final Gambit as "damage = your HP", which looks huge.
SELF_KO_MOVES = {
    "finalgambit", "explosion", "selfdestruct", "mistyexplosion",
    "memento", "healingwish", "lunardance", "mindblown",
}


def _accuracy_factor(move_dict):
    acc = move_dict.get(constants.ACCURACY, True)
    if acc is True or acc is None:
        return 1.0
    if acc is False:
        return 0.0
    try:
        return max(0.0, min(float(acc), 100.0)) / 100.0
    except (TypeError, ValueError):
        return 1.0


def _hit_count(move_dict):
    # Multi-hit moves: poke_engine stores e.g. [2, 5] or 2 under "multihit".
    hits = move_dict.get("multihit")
    if isinstance(hits, (list, tuple)) and hits:
        if len(hits) == 2 and hits[0] == 2 and hits[1] == 5:
            return 3.1  # mainline expected value for 2-5 hit moves
        return float(sum(hits)) / len(hits)
    if isinstance(hits, (int, float)) and hits > 0:
        return float(hits)
    return 1.0


def _same_individual(live_state, main_pokemon):
    """True only if the live battle state belongs to this exact Pokémon, not
    just the same species (same guard the battle simulator uses)."""
    state_id = getattr(live_state, "_ankimon_main_individual_id", None)
    my_id = getattr(main_pokemon, "individual_id", None)
    if state_id is None or my_id is None:
        return False
    return str(state_id) == str(my_id)


def _build_state(main_pokemon, enemy_pokemon, live_state, mutator_full_reset):
    """Reuse the ongoing battle state when it belongs to this exact matchup
    (so stat boosts etc. count), otherwise build a fresh one."""
    from .ankimon_hooks_to_poke_engine import reset_side

    if (
        live_state is not None
        and mutator_full_reset == 0
        and _same_individual(live_state, main_pokemon)
        and live_state.user.active.id == normalize_name(main_pokemon.name)
        and live_state.opponent.active.id == normalize_name(enemy_pokemon.name)
    ):
        return copy.deepcopy(live_state)

    return State(
        user=reset_side(main_pokemon.to_poke_engine_Pokemon()),
        opponent=reset_side(enemy_pokemon.to_poke_engine_Pokemon()),
        weather=None,
        field=None,
        trick_room=False,
    )


def _resolved_move(state, move_id, enemy_move_id):
    """The move as the engine will use it this turn.

    calculate_damage() applies the engine's move, ability and item effects,
    but returns only the damage, and some of those effects make the move fail
    by rewriting its accuracy instead: Sucker Punch into a target that is not
    attacking (the wild Pokémon uses Splash on every correct answer), sound
    moves into Soundproof, priority moves into Armor Tail. Same steps as
    calculate_damage(), so the two agree.
    """
    attacking = get_move(move_id)
    defending = get_move(enemy_move_id)
    first = user_moves_first(state, attacking, defending)
    flags = attacking.get(constants.FLAGS) or {}
    if constants.CHARGE in flags:
        attacking = dict(attacking)
        attacking[constants.FLAGS] = {
            k: v for k, v in flags.items() if k != constants.CHARGE
        }
    return update_attacking_move(
        state.user,
        state.user.active,
        state.opponent.active,
        attacking,
        defending,
        first,
        state.weather,
        state.field,
    )


def _fallback_score(move_dict, attacker, defender):
    """Simple power x effectiveness x STAB score when the engine calc fails."""
    power = move_dict.get(constants.BASE_POWER, 0) or 0
    move_type = move_dict.get(constants.TYPE)
    eff = type_effectiveness_modifier(move_type, defender.types) if move_type else 1
    stab = 1.5 if move_type in attacker.types else 1.0
    return power * eff * stab


def score_moves(main_pokemon, enemy_pokemon, enemy_move="splash",
                live_state=None, mutator_full_reset=1):
    """Return [(move_name, score, is_status)] for each move of main_pokemon."""
    attacks = list(getattr(main_pokemon, "attacks", None) or [])
    if not attacks:
        return []

    state = _build_state(main_pokemon, enemy_pokemon, live_state, mutator_full_reset)
    enemy_move_id = normalize_name(enemy_move or "splash")
    if get_move(enemy_move_id) is None:
        enemy_move_id = "splash"

    results = []
    for move_name in attacks:
        move_id = normalize_name(move_name)
        move_dict = get_move(move_id)
        if move_id in SELF_KO_MOVES:
            results.append((move_name, 0.0, True))  # treated like a status move
            continue
        if move_dict is None:
            results.append((move_name, -1.0, True))
            continue

        is_status = move_dict.get(constants.CATEGORY) not in (
            constants.PHYSICAL,
            constants.SPECIAL,
        )
        if is_status:
            results.append((move_name, 0.0, True))
            continue

        turn_state = copy.deepcopy(state)
        try:
            dmg = calculate_damage(
                turn_state, constants.USER, move_id, enemy_move_id
            )
            damage = float(dmg[0]) if dmg else 0.0
        except Exception:
            damage = _fallback_score(move_dict, state.user.active, state.opponent.active)
        try:
            # turn_state is a throwaway copy, so no second deepcopy is needed.
            resolved = _resolved_move(turn_state, move_id, enemy_move_id)
        except Exception:
            resolved = move_dict
        accuracy = _accuracy_factor(resolved)
        # The engine's type chart gives immunities 1/8, and the damage
        # calculator returns that, but the battle engine deals nothing for an
        # Electric move into a Ground type (instruction_generator freezes it).
        if resolved.get(constants.TYPE) == "electric" and "ground" in (
            getattr(turn_state.opponent.active, "types", None) or ()
        ):
            damage = 0.0

        # No multi-hit multiplier: Ankimon's engine resolves Rock Blast,
        # Bullet Seed, Population Bomb etc. as a single hit, so the damage
        # calculator's one-hit number is what actually lands.
        score = damage * accuracy
        results.append((move_name, score, False))
    return results


def choose_best_move(main_pokemon, enemy_pokemon, enemy_move="splash",
                     live_state=None, mutator_full_reset=1, fallback=None):
    """Name of the strongest move in main_pokemon.attacks.

    Damaging moves always win over status moves; a status move is only used if
    no move can deal damage at all (e.g. every move hits an immunity). Returns
    ``fallback`` if nothing usable is found or anything goes wrong.
    """
    try:
        scored = score_moves(
            main_pokemon, enemy_pokemon, enemy_move, live_state, mutator_full_reset
        )
    except Exception:
        return fallback
    if not scored:
        return fallback

    damaging = [m for m in scored if not m[2] and m[1] > 0]
    if damaging:
        return max(damaging, key=lambda m: m[1])[0]
    # Nothing does damage: keep the original (random) pick, unless that pick
    # is a self-KO move and something safer is available.
    if fallback is not None and normalize_name(fallback) in SELF_KO_MOVES:
        for move_name, _score, _status in scored:
            if normalize_name(move_name) not in SELF_KO_MOVES:
                return move_name
    return fallback
