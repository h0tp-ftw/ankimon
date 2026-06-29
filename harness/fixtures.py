"""
harness/fixtures.py — DEV-ONLY save/state construction for the headless harness.

Lets an agent (1) boot on an EXISTING ``ankimon.db`` (load arbitrary progress)
and (2) CONSTRUCT a precise starting state — a specific main/team/box, items, and
a specific wild enemy — so a bug report ("Gengar's Levitate ignores Earthquake",
"X's move Y does 0 damage") can be reproduced head-on and watched resolve through
the event stream.

Safety / accessibility (read before extending):
  * This file lives in ``harness/`` and is NEVER shipped — the ``.ankiaddon`` is
    built from ``src/Ankimon/`` only. It adds ZERO cheat affordance to the add-on:
    it only writes the same plain-JSON ``ankimon.db`` a user can already edit by
    hand with any SQLite tool, and only from this unshipped dev tool. Do NOT move
    any of this into ``src/``, and do NOT add a "spawn" command to the add-on.
  * Saves generated here are throwaway fixtures — keep them in temp dirs, never
    commit one into the repo or attach it to a release (a ready-made save is more
    convenient to a casual cheater than the capability, which already exists).

Fidelity: Pokemon are built from the game's OWN pokedex helpers (base stats,
types, abilities, learnset, base exp, growth rate, EV yield, tier), so a
seeded/forced Pokemon is identical to one the game itself would produce — only the
fields you pin in the spec are overridden.
"""

from __future__ import annotations

import uuid

_STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")
_ZERO_STAGES = {"atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0, "accuracy": 0, "evasion": 0}


# --- spec -> canonical species data -----------------------------------------

def _stat_dict(val, default):
    """Coerce a spec stat block (dict or None) into a full 6-stat int dict."""
    if isinstance(val, dict):
        return {k: int(val.get(k, default)) for k in _STAT_KEYS}
    return {k: int(default) for k in _STAT_KEYS}


def _name_to_id(name):
    from Ankimon.functions.pokedex_functions import search_pokedex
    sid = search_pokedex(name, "id") or search_pokedex(name, "num")
    if sid:
        return int(sid)
    # Fall back to inverting the id->name index the game uses for the reverse map.
    from Ankimon.functions.pokedex_functions import _load_pokedex_id_index
    for i, n in (_load_pokedex_id_index() or {}).items():
        if str(n).lower() == str(name).lower():
            return int(i)
    raise ValueError(f"fixtures: could not resolve a pokedex id for {name!r}")


def _species_data(spec):
    """Resolve a compact spec into the field dict the game uses for a Pokemon.

    spec keys (all optional except a species identifier):
      species|name | id    — which Pokemon (one is required)
      level                — default 50
      ability              — default: the species' first listed ability
      moves|attacks        — default: up to 4 from the level-legal learnset
      ivs|iv, evs|ev       — dict or scalar; default IV 31, EV 0
      nature               — default "serious"
      shiny, gender, held_item, tier
    """
    from Ankimon.functions.pokedex_functions import (
        search_pokedex,
        search_pokedex_by_id,
        get_base_experience,
        get_growth_rate,
        get_effort_values,
    )
    from Ankimon.functions.learnset_retrieval import get_all_pokemon_moves
    from Ankimon.functions.pokemon_functions import pick_random_gender
    from Ankimon.utils import get_tier_by_id
    from Ankimon.poke_engine.helpers import normalize_name

    sid = spec.get("id")
    name = spec.get("species") or spec.get("name")
    if sid is not None and not name:
        name = search_pokedex_by_id(sid)
    if not name or name == "Pokémon not found":
        raise ValueError(f"fixtures: unknown species in spec {spec!r}")
    if sid is None:
        sid = _name_to_id(name)
    sid = int(sid)
    actual_id = search_pokedex(name, "actual_id") or sid

    base_stats = search_pokedex(name, "baseStats")
    types = search_pokedex(name, "types")
    if not base_stats or not types:
        raise ValueError(f"fixtures: pokedex has no data for {name!r}")

    level = int(spec.get("level", 50))

    ability = spec.get("ability")
    if not ability:
        abilities = search_pokedex(name, "abilities") or {}
        numeric = {k: v for k, v in abilities.items() if str(k).isdigit()}
        ability = numeric.get("0") or next(iter(numeric.values()), None) or "Run Away"

    moves = spec.get("moves") or spec.get("attacks")
    if not moves:
        pool = get_all_pokemon_moves(name, level) or ["Tackle"]
        moves = pool[:4]
    # The battle loop resolves moves by the game's normalized key ("Shadow Ball" ->
    # "shadowball"); learnset moves are already normalized, spec-given ones may not be.
    moves = [normalize_name(m) for m in moves]

    return {
        "name": name,
        "id": sid,
        "level": level,
        "ability": ability,
        "type": types,
        "base_stats": base_stats,
        "attacks": list(moves),
        "base_experience": get_base_experience(actual_id) or 0,
        "growth_rate": get_growth_rate(sid) or "medium",
        "ev": _stat_dict(spec.get("evs", spec.get("ev")), default=0),
        "iv": _stat_dict(spec.get("ivs", spec.get("iv")), default=31),
        "ev_yield": get_effort_values(actual_id) or {k: 0 for k in _STAT_KEYS},
        "gender": spec.get("gender") or pick_random_gender(name) or "N",
        "nature": spec.get("nature", "serious"),
        "shiny": bool(spec.get("shiny", False)),
        "tier": spec.get("tier") or get_tier_by_id(sid) or "Normal",
        "held_item": spec.get("held_item"),
        "battle_status": "fighting",
        "stat_stages": dict(_ZERO_STAGES),
    }


def build_pokemon(spec):
    """A faithful PokemonObject from a compact spec (fresh individual_id each call)."""
    from Ankimon.pyobj.pokemon_obj import PokemonObject

    data = _species_data(spec)
    data.update({
        "individual_id": spec.get("individual_id") or str(uuid.uuid4()),
        "nickname": spec.get("nickname", ""),
        "friendship": int(spec.get("friendship", 0)),
        "captured_date": spec.get("captured_date", "2000-01-01 00:00:00"),
        "xp": int(spec.get("xp", 0)),
    })
    pkmn = PokemonObject.from_dict(data)
    # Full HP unless the spec pins a current hp (e.g. to reproduce a low-HP bug).
    pkmn.hp = int(spec.get("hp", pkmn.max_hp))
    pkmn.current_hp = pkmn.hp
    return pkmn


# --- seeding a starting save -------------------------------------------------

def seed_db(seed, db):
    """Write a constructed starting state into ``db`` (the session ankimon.db).

    seed keys (all optional):
      main          — a spec; becomes is_main=1 and team slot 1
      team          — list of specs; appended to the team after main
      box           — list of specs; captured but not on the team (PC)
      items         — {item_name: quantity}
    Returns the individual_ids that were created.
    """
    # update_main_pokemon() only reads the DB when is_migrated() is True; a fresh
    # harness DB is not "migrated", so without this it would ignore a seeded main
    # and fall back to the default placeholder. is_migrated() checks 'migrated_phase2';
    # 'migrated' is the phase-1 flag — set both so every code path treats the DB as live.
    db.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated', 'true')")
    db.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated_phase2', 'true')")
    db._get_connection().commit()

    team = []

    main_spec = seed.get("main")
    main_obj = None
    if main_spec:
        main_obj = build_pokemon(main_spec)
        db.save_main_pokemon(main_obj.to_dict())  # inserts the row with is_main=1
        team.append(main_obj)

    for spec in seed.get("team", []):
        obj = build_pokemon(spec)
        db.save_pokemon(obj.to_dict())
        team.append(obj)

    for spec in seed.get("box", []):
        obj = build_pokemon(spec)
        db.save_pokemon(obj.to_dict())

    if team:
        db.save_team([{"individual_id": o.individual_id} for o in team])

    for item_name, qty in (seed.get("items") or {}).items():
        try:
            db.add_item(item_name, int(qty))
        except Exception:
            pass

    return {
        "main": main_obj.individual_id if main_obj else None,
        "team": [o.individual_id for o in team],
    }


# --- forcing a specific wild encounter --------------------------------------

def set_enemy(services, events, spec):
    """Replace the live wild encounter with a specific species (mirrors new_pokemon).

    The on-screen enemy object is mutated in place (so the battle loop's bound
    globals keep pointing at it), then an ``encounter`` event is emitted exactly
    like a normal wild appearance. Use this to reproduce "the enemy's move/ability
    misbehaves" deterministically.
    """
    data = _species_data(spec)
    ep = services.enemy_pokemon
    ep.update_stats(**data)
    max_hp = ep.calculate_max_hp()
    ep.max_hp = max_hp
    ep.hp = int(spec.get("hp", max_hp))
    ep.current_hp = ep.hp

    try:
        services.tracker.randomize_battle_scene()
    except Exception:
        pass
    if services.test_window is not None:
        try:
            services.test_window.display_first_encounter()
        except Exception:
            pass

    events.emit(
        "encounter",
        pokemon=ep.name, id=ep.id, level=ep.level, tier=ep.tier,
        shiny=ep.shiny, hp=ep.hp, max_hp=ep.max_hp,
    )
    if services.reviewer is not None:
        try:
            services.reviewer.refresh_hud()
        except Exception:
            pass
    return ep
