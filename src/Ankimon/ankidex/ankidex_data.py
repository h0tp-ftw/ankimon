"""Widget-free Ankidex payload builder (extracted from ``ankidex_obj.Ankidex``).

Keeps the collection-data query out of the Qt dialog so the same logic can be
unit-tested without constructing a ``QWebEngineView`` (and reused by other hosts
without spinning up a hidden window). The Qt-bound ``Ankidex`` window delegates
here; the heavy ``encounter_functions`` import is done lazily so this module
stays cheap to import and easy to stub in tests.
"""

from ..functions import encounter_data


# Tiers the live wild-encounter roll draws from (encounter_functions.
# get_all_pokemon_in_tier), in the order generate_random_pokemon() walks them.
_ENCOUNTER_TIERS = (
    "Normal",
    "Baby",
    "Ultra",
    "Legendary",
    "Mythical",
    "Mega",
    "Gmax",
    "Starter",
)

# The roll rolls a wild level in [main - 3, main + 3] and gates each candidate on
# THAT level, not on the player's. Mirror the window so a species whose minimum
# generate level sits just above the player's level is not falsely "Unavailable".
_LVL_VARIATION = 3


def _max_wild_level(player_level):
    """Highest wild level generate_random_pokemon() can roll for this player."""
    if player_level == 100:
        return 100  # the roll pins the wild level to 100 at the cap
    return max(1, player_level + _LVL_VARIATION)


def build_encounterable_ids(settings, player_level, owned_ids=None):
    """Return the ids the wild-encounter roll has been PERMANENTLY unlocked for.

    "Unlocked" is a progression predicate — main-Pokemon level, generation
    toggles, active region, and the Pokemon currently in the collection. It is
    deliberately NOT "the next roll can produce this".

    The live roll also weights each tier by how far through today's review goal
    the player is, and the shipped legacy path
    (``_modify_percentages_legacy``, the ``review_ratio < 0.4`` branch) folds
    *every* non-Normal tier into "Normal" until 40% of ``battle.daily_average``
    is done — at main level 80 that is 373 of these ids. Modelling it here would
    flip ~370 species to "Unavailable" every morning and back each afternoon,
    and would churn the two Ankidex form lists that read this same set (298
    species' form strips change length across that boundary, 142 vanish
    outright). ``get_total_reviews()`` also resets at Anki's daily rollover, and
    the payload is only rebuilt when the window is shown — so a rate-scoped
    answer could not stay true anyway. The per-day rate gate is therefore left
    to the roll, and this set answers "has my account unlocked this?".

    Within that scope it mirrors ``generate_random_pokemon()``'s per-candidate
    guards, from the SAME sources the roll uses, instead of unioning the raw
    ``encounter_data`` tier lists:

    * **Tier gate** — a tier whose main-Pokemon level threshold is not met has
      probability 0, and the roll's fallback only ever degrades to "Normal", so
      such a tier is unreachable. (``Starter`` opens at 80, ``Mythical`` at 75, ...)
    * **Guard 1, generation** — ``check_id_ok()``.
    * **Guard 2, level** — ``check_min_generate_level()`` compared against the
      highest wild level the roll can produce (``player_level + 3``), not the
      player's own level.
    * **Guard 3, Mega/Gmax base ownership** — ``_player_owns_base_form()``
      against ``owned_ids``, the roll's own predicate. ``owned_ids`` must be the
      currently-owned set the roll gates on (``load_collected_pokemon_ids()``),
      NOT the wider "has ever been caught" set the sprite states use. When it is
      None the guard is skipped, which is the only way a Mega/Gmax id can come
      back for a player who cannot actually roll it.
    * **Regional forms** — resolved exactly like the roll's form-resolution step:
      a variant is reachable only once its BASE species is in the pool, and when
      no region is active the roll offers variants from *every* region.

    Guard 4 (``_meets_prerequisites``) is deliberately NOT applied here. The
    payload ships ``prerequisites`` separately and the SPA gates the badge on it;
    applying it twice would also drop those ids out of the form lists, which are
    reference UI and must keep listing a form the player has not unlocked yet.
    """
    from ..functions.encounter_functions import (
        OVERHAUL_LEVEL_THRESHOLDS,
        _get_regional_form_lookup,
        _player_owns_base_form,
        check_id_ok,
        check_min_generate_level,
        get_all_pokemon_in_tier,
        search_pokedex_by_id,
    )

    try:
        player_level = int(player_level)
    except (TypeError, ValueError):
        player_level = 1
    max_wild_level = _max_wild_level(player_level)

    def is_eligible(pid):
        """The roll's Guard 1 (generation) + Guard 2 (minimum generate level)."""
        if not check_id_ok(pid):
            return False
        name = search_pokedex_by_id(pid)
        if not name or name == "Pokémon not found":
            return False
        return max_wild_level >= check_min_generate_level(str(name).lower())

    ids = set()
    for tier in _ENCOUNTER_TIERS:
        # Tier weight is forced to 0 below its threshold, and a failed roll
        # degrades straight to "Normal" — never sideways into a gated tier.
        if player_level < OVERHAUL_LEVEL_THRESHOLDS.get(tier, 0):
            continue
        # Guard 3 applies to these two tiers only, exactly as the roll scopes it.
        ownership_gated = tier in ("Mega", "Gmax") and owned_ids is not None
        for pid in get_all_pokemon_in_tier(tier):
            if not is_eligible(pid):
                continue
            if ownership_gated and not _player_owns_base_form(pid, owned_ids):
                continue
            ids.add(pid)

    # Regional forms. The roll only reaches a variant *after* rolling its base
    # species, so a variant is encounterable exactly when its base is — and with
    # no active region the roll draws variants from every region, not none.
    active_region = settings.get("misc.active_region") if settings is not None else None
    if isinstance(active_region, str):
        active_region = active_region.lower().strip()
    else:
        active_region = None
    region_scoped = bool(active_region) and active_region not in ("no region", "")

    lookup = _get_regional_form_lookup()
    for base_id in list(ids):
        forms = lookup.get(base_id, {})
        if region_scoped:
            variants = forms.get(active_region, [])
        else:
            variants = [v for region_ids in forms.values() for v in region_ids]
        for variant_id in variants:
            if is_eligible(variant_id):
                ids.add(variant_id)

    # Explicit exclusions: never spawnable regardless of tier / generation.
    # Applied last so a regional form can never slip back in past them.
    for uid in getattr(encounter_data, "UNAVAILABLE", []):
        ids.discard(uid)

    return ids


def _empty_payload():
    """Empty-but-valid payload so the SPA still renders when the DB is absent."""
    return {
        "owned": [],
        "ownedNow": [],
        "shinies": [],
        "seen": [],
        "encounterable": [],
        "prerequisites": {},
        "prefs": {
            "viewMode": "grid",
            "sortMode": "id-asc",
            "spriteMode": "static",
        },
        "regional_data": {"boosts": {}, "forms": {}},
        "evolutionNote": "",
    }


def _prefs(settings):
    """Read the Ankidex view prefs, falling back to the legacy pokedex_v2 keys."""
    if settings is None:
        return {"viewMode": "grid", "sortMode": "id-asc", "spriteMode": "static"}
    return {
        "viewMode": settings.get(
            "ankidex.viewMode", settings.get("pokedex_v2.viewMode", "grid")
        ),
        "sortMode": settings.get(
            "ankidex.sortMode", settings.get("pokedex_v2.sortMode", "id-asc")
        ),
        "spriteMode": settings.get(
            "ankidex.spriteMode", settings.get("pokedex_v2.spriteMode", "static")
        ),
    }


def get_ankidex_data(db, settings, tracker=None, player_level=1):
    """Fetch comprehensive collection data for the Ankidex SPA (widget-free).

    ``db`` / ``settings`` are the services-resolved singletons (passed in so this
    stays testable and reusable); ``tracker`` is optional (its
    ``get_ids_in_collection()`` side effect is preserved when present).
    """
    if db is None:
        # Reload-safe: between a profile swap and the next populate() the registry
        # can be unpopulated (db is None). Serve an empty-but-valid payload so the
        # SPA still renders instead of raising AttributeError on db.execute(...).
        return _empty_payload()

    if tracker is not None:
        tracker.get_ids_in_collection()

    # 1. Shiny owned
    cursor = db.execute(
        "SELECT DISTINCT pokedex_id FROM captured_pokemon WHERE shiny = 1 AND pokedex_id IS NOT NULL"
    )
    shiny_owned_ids = [row[0] for row in cursor.fetchall()]

    # 2. Caught status — currently owned.
    # The roll's Guard 3 / Guard 4 gate on THIS set and no other
    # (load_collected_pokemon_ids -> db.get_all_pokemon_ids -> captured_pokemon),
    # so it is kept separate from the wider "has ever been caught" set below.
    # Conflating them let a released Pokemon satisfy a prerequisite here while
    # the roll went on refusing to produce the target.
    cursor = db.execute(
        "SELECT pokedex_id FROM captured_pokemon WHERE pokedex_id IS NOT NULL"
    )
    owned_now = {row[0] for row in cursor.fetchall()}
    caught_ids = set(owned_now)

    # Released Pokemon (from history). Wrapped so an older/unmigrated DB file
    # (e.g. a restored backup predating the pokemon_history table) degrades to
    # "no released entries" instead of failing the whole payload.
    try:
        cursor = db.execute(
            "SELECT DISTINCT json_extract(data, '$.id') FROM pokemon_history"
        )
        for row in cursor.fetchall():
            if row[0]:
                try:
                    caught_ids.add(int(row[0]))
                except (ValueError, TypeError):
                    continue
    except Exception:
        pass

    # Explicitly recorded caught Pokemon (e.g. from evolutions or prior captures)
    if hasattr(db, "get_caught_ids"):
        try:
            caught_ids.update(db.get_caught_ids())
        except Exception:
            pass

    # 3. Seen status (encountered but not caught)
    seen_ids = set()
    if hasattr(db, "get_seen_ids"):
        seen_ids.update(db.get_seen_ids())
    else:
        seen_data = db.get_user_data("pokedex_seen", [])
        if isinstance(seen_data, list):
            seen_ids.update(set(seen_data))
    seen_ids = seen_ids - caught_ids

    # 4. Encounterable IDs — what the account has unlocked for the wild roll.
    encounterable_ids = build_encounterable_ids(settings, player_level, owned_now)

    # 5. Prerequisites
    prereqs = {}
    for k, v in encounter_data.PREREQUISITES.items():
        if isinstance(v, set):
            prereqs[str(k)] = list(v)
        elif isinstance(v, tuple) and len(v) == 2:
            # Handle ("OR", {1007, 1008})
            op, items = v
            prereqs[str(k)] = [op, list(items) if isinstance(items, set) else items]
        else:
            prereqs[str(k)] = v

    return {
        "owned": list(caught_ids),
        # Currently-owned only. "owned" additionally folds in released Pokemon
        # and history, which is right for the sprite/"Completed" states but wrong
        # for a requirement check — the roll counts neither.
        "ownedNow": list(owned_now),
        "shinies": shiny_owned_ids,
        "seen": list(seen_ids),
        "encounterable": list(encounterable_ids),
        "prerequisites": prereqs,
        "prefs": _prefs(settings),
        "regional_data": {
            "boosts": {
                "kanto": [1],
                "johto": [2],
                "hoenn": [3],
                "sinnoh": [4],
                "unova": [5],
                "kalos": [6],
                "alola": [7],
                "galar": [8],
                "paldea": [9],
                "hisui": [4, 8],
            },
            "forms": encounter_data.REGIONAL_FORM_REGION,
        },
        "evolutionNote": "Evolutions in Ankimon can trigger via Level, Friendship, Evolution Stones/Items, Time of Day, or knowing specific Moves.",
    }
