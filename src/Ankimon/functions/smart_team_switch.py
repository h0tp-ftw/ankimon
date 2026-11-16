"""Smart Auto Battle, part 2: send out the best team member for each new enemy.

Runs once per encounter, from new_pokemon(), only when
``controls.move_selection_mode`` is 2. It picks from the same team slots the
Cycle Team hotkey rotates through (the first ``controls.team_cycle_count``
members). After that the player is free to cycle by hand; nothing re-switches
until the next wild Pokémon appears.

Each healthy team member is scored on this matchup:
    offense = share of the enemy's HP its best move takes per turn (max 1)
    danger  = share of its own HP the enemy takes per turn (the enemy picks
              moves at random, so this is the enemy's average move)
    score   = offense / danger
The current Pokémon is kept unless another one scores clearly better, so
close calls don't cause pointless swaps.

Level balancing (``battle.team_level_gap``, default 5, 0 = off): only team
members within that many levels of the lowest-level healthy member are sent
out. A member that has pulled too far ahead sits on the bench until the rest
catch up, so the whole rotation levels together. The player can still cycle
to anyone by hand.
"""

import copy

from ..services import services
from .smart_move_selection import score_moves

# Another member must beat the current one by this factor to be sent out.
_SWITCH_MARGIN = 1.15

_DEFAULT_LEVEL_GAP = 5
_MAX_LEVEL_GAP = 100


def _move_selection_mode(settings):
    try:
        mode = int(settings.get("controls.move_selection_mode", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return mode if mode in (0, 1, 2) else 0


def matchup_score(candidate, enemy):
    """Higher is better. None if the candidate can't fight (fainted)."""
    my_hp = getattr(candidate, "hp", 0) or 0
    enemy_hp = max(getattr(enemy, "hp", 0) or 0, 1)
    if my_hp <= 0:
        return None

    mine = [s for _, s, status in score_moves(candidate, enemy) if not status]
    best_damage = max(mine) if mine else 0.0

    theirs = score_moves(enemy, candidate)
    if theirs:
        avg_enemy_damage = sum(max(s, 0.0) for _, s, _ in theirs) / len(theirs)
    else:
        avg_enemy_damage = 0.0

    offense = min(best_damage / enemy_hp, 1.0)
    danger = max(avg_enemy_damage / my_hp, 0.01)
    return offense / danger


def level_gap(settings):
    """``battle.team_level_gap`` as an int in [0, 100]; 0 turns balancing off."""
    try:
        gap = int(settings.get("battle.team_level_gap", _DEFAULT_LEVEL_GAP))
    except (TypeError, ValueError, AttributeError):
        gap = _DEFAULT_LEVEL_GAP
    return max(0, min(gap, _MAX_LEVEL_GAP))


def _level(pkmn):
    try:
        return int(getattr(pkmn, "level", 0) or 0)
    except (TypeError, ValueError):
        return 0


def balance_by_level(candidates, gap):
    """Keep only healthy candidates within ``gap`` levels of the lowest-level
    healthy one. gap <= 0, or fewer than two healthy members, changes nothing."""
    healthy = [(i, p) for i, p in candidates if (getattr(p, "hp", 0) or 0) > 0]
    if gap <= 0 or len(healthy) < 2:
        return candidates
    floor = min(_level(p) for _, p in healthy)
    return [(i, p) for i, p in healthy if _level(p) <= floor + gap]


def pick_best(candidates, enemy, current_id=None, margin=_SWITCH_MARGIN):
    """candidates: [(individual_id, pokemon_like)]. Returns the id to use, or
    None to keep the current Pokémon.

    If the current Pokémon is not among the candidates (e.g. level balancing
    benched it), the best candidate is returned with no margin check."""
    scored = []
    current_score = None
    for ind_id, pkmn in candidates:
        try:
            score = matchup_score(pkmn, enemy)
        except Exception:
            score = None
        if score is None:
            continue
        scored.append((score, ind_id))
        if ind_id == current_id:
            current_score = score
    if not scored:
        return None
    best_score, best_id = max(scored, key=lambda t: t[0])
    if best_id == current_id:
        return None
    if current_score is not None and best_score < current_score * margin:
        return None
    return best_id


def _load_candidate(main_pokemon, individual_id):
    """A throwaway PokemonObject for a team member (main_pokemon untouched)."""
    from .pokedex_functions import search_pokedex, search_pokedex_by_id
    from .update_main_pokemon import _apply_loaded_hp

    data = services.db.get_pokemon(individual_id)
    if not data or not data.get("id"):
        return None, None
    if not data.get("name"):
        data["name"] = search_pokedex_by_id(data.get("id", 1))
    base_stats = search_pokedex(data["name"], "baseStats")
    if not isinstance(base_stats, dict) or "hp" not in base_stats:
        return None, None
    data["base_stats"] = base_stats

    pkmn = copy.copy(main_pokemon)
    pkmn.update_stats(**data)
    _apply_loaded_hp(pkmn, dict(data))
    pkmn.reset_bonuses()
    pkmn.volatile_status = set()
    pkmn.held_item = data.get("held_item")
    return pkmn, data


def _team_ids(settings):
    try:
        count = int(settings.get("controls.team_cycle_count", 3))
    except (TypeError, ValueError):
        count = 3
    team = services.db.get_team() or []
    return [e.get("individual_id") for e in team[:max(count, 1)] if e.get("individual_id")]


def smart_switch_for_new_enemy(main_pokemon, enemy_pokemon, settings):
    """Swap main_pokemon to the best team member for this enemy.

    Returns the message to show the player, or None if nothing changed.
    Never raises: any problem just leaves the current Pokémon out.
    """
    try:
        if settings is None or _move_selection_mode(settings) != 2:
            return None
        if services.db is None or main_pokemon is None or enemy_pokemon is None:
            return None
        # The active Pokémon fainted this turn (e.g. a double faint) and its
        # faint handling hasn't run yet. Switching now would hide that faint
        # behind a healthy teammate, so wait: the faint handler starts the
        # next encounter itself, and the switch happens then.
        if (getattr(main_pokemon, "hp", 0) or 0) < 1:
            return None

        ids = _team_ids(settings)
        if len(ids) < 2:
            return None

        current_id = getattr(main_pokemon, "individual_id", None)
        candidates = []
        loaded = {}
        for ind_id in ids:
            if ind_id == current_id:
                candidates.append((ind_id, main_pokemon))
                continue
            pkmn, data = _load_candidate(main_pokemon, ind_id)
            if pkmn is not None:
                candidates.append((ind_id, pkmn))
                loaded[ind_id] = data

        balanced = balance_by_level(candidates, level_gap(settings))
        benched = len(balanced) < len(candidates)
        best_id = pick_best(balanced, enemy_pokemon, current_id)
        if best_id is None or best_id not in loaded:
            return None

        # Same steps as the Cycle Team hotkey (reviewer_ui.cycle_team_pokemon).
        from .update_main_pokemon import _apply_loaded_hp, save_main_pokemon
        from ..battle_loop import _cancel_main_faint_deferral

        data = loaded[best_id]
        _cancel_main_faint_deferral()
        main_pokemon.update_stats(**data)
        # update_stats skips keys the team record lacks, so set the held item
        # explicitly: the switched-in Pokémon must not keep the previous one's.
        main_pokemon.held_item = data.get("held_item")
        _apply_loaded_hp(main_pokemon, data)
        main_pokemon.reset_bonuses()
        main_pokemon.volatile_status = set()
        save_main_pokemon(main_pokemon)

        name = getattr(main_pokemon, "display_name", None) or data.get("name", "?")
        level = data.get("level", "?")
        if benched:
            return f"Smart Battle: Go, {name}! (Lv. {level}, levelling up the team)"
        return f"Smart Battle: Go, {name}! (Lv. {level})"
    except Exception as e:
        print(f"[Ankimon] Smart team switch skipped: {e}")
        return None
