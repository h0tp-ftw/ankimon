"""Smart Battle, part 2: send out the best team member for each new enemy.

Runs once per encounter, from new_pokemon(), only when ``battle.smart_battle``
is on. It picks from the same team slots the
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
from .smart_battle import smart_battle_enabled
from .smart_move_selection import score_moves

# Another member must beat the current one by this factor to be sent out.
_SWITCH_MARGIN = 1.15

_DEFAULT_LEVEL_GAP = 5
_MAX_LEVEL_GAP = 100


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


def _safe_score(pkmn, enemy):
    try:
        return matchup_score(pkmn, enemy)
    except Exception:
        return None


def can_fight(score):
    """A member is only worth sending out if it can damage this enemy."""
    return score is not None and score > 0


def pick_best(candidates, enemy, current_id=None, margin=_SWITCH_MARGIN, scores=None):
    """candidates: [(individual_id, pokemon_like)]. Returns the id to use, or
    None to keep the current Pokémon. ``scores`` maps ids to precomputed
    matchup scores.

    Members that cannot damage the enemy are never chosen. If the current
    Pokémon is not among the candidates (e.g. level balancing benched it),
    the best candidate is returned with no margin check."""
    scored = []
    current_score = None
    for ind_id, pkmn in candidates:
        if scores is not None and ind_id in scores:
            score = scores[ind_id]
        else:
            score = _safe_score(pkmn, enemy)
        if not can_fight(score):
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


def _with_row_defaults(data):
    """The record with the defaults the PC box applies for keys it may lack.

    Candidates and the incoming Pokémon are built on a copy of the live main,
    and update_stats() skips keys the record lacks, so a missing key would
    keep the OUTGOING Pokémon's value, and the switch would save it into the
    incoming row for good. Monthly-challenge, trade and fossil rows, and old
    migrated catches, can lack these two (same defaults as
    collection_dialog.MainPokemon).
    """
    data = dict(data)
    # ``or``: an explicit null nature would break the stat multipliers.
    data["nature"] = data.get("nature") or "serious"
    data.setdefault("evolution_rejected", False)
    return data


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
    data = _with_row_defaults(data)

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


def _switch_to(main_pokemon, incoming_id, data, settings):
    """Make ``incoming_id`` the active Pokémon. Returns the record it was
    loaded from, or None if the switch was refused (nothing changed).

    Same contract as the Cycle Team hotkey (reviewer_ui.cycle_team_pokemon):
    save the outgoing Pokémon's HP, credit the incoming one's bench rest,
    honour the classic heal-on-swap rule, and only rebind the live
    main_pokemon once both rows are saved. Unlike the hotkey it never waits
    for a busy database: this runs automatically on the review path, so a
    refused save just skips the switch for this encounter.
    """
    from .hp_recovery import credit_rest, swap_heals
    from .update_main_pokemon import _apply_loaded_hp
    from ..battle_loop import (
        _cancel_main_faint_deferral,
        mark_swap_turn_lost,
        outgoing_hp_for_swap,
        publish_active_hp,
    )

    db = services.db
    # The incoming Pokémon has been resting on the bench.
    rested = credit_rest(db, data, getattr(services, "logger", None), settings)
    if rested is not data:
        rested = dict(rested)
        rested["base_stats"] = data["base_stats"]
        rested.setdefault("name", data.get("name"))
    data = _with_row_defaults(rested)

    outgoing_id = getattr(main_pokemon, "individual_id", None)
    if outgoing_id and db.get_main_pokemon():
        if not db.update_pokemon_current_hp(
            str(outgoing_id), outgoing_hp_for_swap(main_pokemon)
        ):
            return None

    incoming = copy.copy(main_pokemon)
    incoming.update_stats(**data)
    # update_stats skips keys the team record lacks, so set the held item
    # explicitly: the switched-in Pokémon must not keep the previous one's.
    incoming.held_item = data.get("held_item")
    _apply_loaded_hp(incoming, data)
    if swap_heals(settings):
        # Classic swap rule: the incoming Pokémon arrives fully healed.
        incoming.hp = incoming.current_hp = incoming.max_hp
    incoming.reset_bonuses()
    incoming.volatile_status = set()

    # to_dict() carries no rest clock, so the save keeps the stored one.
    if not db.save_main_pokemon(incoming.to_dict()):
        return None
    _cancel_main_faint_deferral()
    main_pokemon.__dict__.update(incoming.__dict__)
    # Both are no-ops at the start of an encounter (new_pokemon() has just
    # cleared the engine state); kept so this stays the hotkey's contract.
    publish_active_hp(main_pokemon)
    mark_swap_turn_lost()
    return data


def smart_switch_for_new_enemy(main_pokemon, enemy_pokemon, settings):
    """Swap main_pokemon to the best team member for this enemy.

    Returns the message to show the player, or None if nothing changed.
    Never raises: any problem just leaves the current Pokémon out.
    """
    try:
        if settings is None or not smart_battle_enabled(settings):
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

        # Score each member once. One that cannot damage this enemy (a
        # Splash-only Magikarp, a Harden-only Metapod) must not set the level
        # floor either, or balancing would send it out every encounter and
        # the rotation could never win.
        scores = {i: _safe_score(p, enemy_pokemon) for i, p in candidates}
        able = [(i, p) for i, p in candidates if can_fight(scores[i])]
        if not able:
            return None
        balanced = balance_by_level(able, level_gap(settings))
        benched = len(balanced) < len(able)
        best_id = pick_best(balanced, enemy_pokemon, current_id, scores=scores)
        if best_id is None or best_id not in loaded:
            return None

        data = _switch_to(main_pokemon, best_id, loaded[best_id], settings)
        if data is None:
            return None

        name = getattr(main_pokemon, "display_name", None) or data.get("name", "?")
        level = data.get("level", "?")
        if benched:
            return f"Smart Battle: Go, {name}! (Lv. {level}, levelling up the team)"
        return f"Smart Battle: Go, {name}! (Lv. {level})"
    except Exception as e:
        print(f"[Ankimon] Smart team switch skipped: {e}")
        return None
