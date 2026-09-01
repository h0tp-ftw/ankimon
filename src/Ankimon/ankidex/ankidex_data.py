"""Widget-free Ankidex payload builder (extracted from ``ankidex_obj.Ankidex``).

Keeps the collection-data query out of the Qt dialog so the same logic can be
unit-tested without constructing a ``QWebEngineView`` (and reused by other hosts
without spinning up a hidden window). The Qt-bound ``Ankidex`` window delegates
here; the heavy ``encounter_functions`` import is done lazily so this module
stays cheap to import and easy to stub in tests.
"""

from ..functions import encounter_data


def _ankidex_i18n(settings, flavor_species_ids=None):
    """Localized name / type / ability / flavor overlay for the Ankidex SPA.

    Empty dict for English (or on any failure) — the JS then renders the bundled
    English data unchanged. ``flavor_species_ids`` limits the (bulky) flavor map
    to species the dex will actually reveal; None means all.
    """
    try:
        lang = int(settings.get("misc.language", 9)) if settings is not None else 9
    except Exception:
        lang = 9
    if lang == 9:
        return {}

    out = {"names": {}, "types": {}, "abilities": {}, "abilityDesc": {}, "flavor": {}}
    try:
        from ..functions.pokedex_functions import (
            _load_pokemon_names_csv,
            _load_pokemon_descriptions_csv,
            _normalize_language_id,
            get_pokemon_diff_lang_name,
        )
        from ..localized_text import type_name, current_lang_code, _load

        norm_lang = _normalize_language_id(lang)

        names_cache = _load_pokemon_names_csv()  # {(species_id, lang_id): name}
        for (sid, lid), name in names_cache.items():
            if lid == norm_lang and name:
                out["names"][str(sid)] = name

        # Regional / mega / gmax form ids carry a distinct localized name.
        for fid in getattr(encounter_data, "REGIONAL_FORM_REGION", {}):
            try:
                loc = get_pokemon_diff_lang_name(int(fid), lang)
                if loc and loc != "No Translation in this language":
                    out["names"][str(fid)] = loc
            except Exception:
                continue

        desc_cache = _load_pokemon_descriptions_csv()  # {(species_id, lang_id): [txt]}
        for (sid, lid), texts in desc_cache.items():
            if lid != norm_lang or not texts:
                continue
            if flavor_species_ids is not None and sid not in flavor_species_ids:
                continue
            out["flavor"][str(sid)] = " ".join(str(texts[0]).split())

        for eng in (
            "Normal Fire Water Electric Grass Ice Fighting Poison Ground Flying "
            "Psychic Bug Rock Ghost Dragon Dark Steel Fairy"
        ).split():
            out["types"][eng] = type_name(eng, eng)

        code = current_lang_code()
        out["abilities"] = dict(_load("ability_names", code))
        out["abilityDesc"] = dict(_load("ability_desc", code))
    except Exception as e:  # pragma: no cover - defensive
        print(f"[Ankimon] ankidex i18n build failed: {e}")
        return {}
    return out


# Tiers the live wild-encounter roll draws from (encounter_functions.
# get_all_pokemon_in_tier). "Starter" is included on purpose: that function
# returns [] for it (starters come only from the one-time starter picker), so
# gating through it keeps Ankidex's "Available" badge in lockstep with the roll.
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


def build_encounterable_ids(settings):
    """Return the set of Pokemon the live wild-encounter roll can actually produce.

    Built from the SAME source the roll uses — ``get_all_pokemon_in_tier()`` per
    tier, each candidate gated by ``check_id_ok()`` (the roll's generation-toggle
    / regional-form check) — instead of unioning the raw ``encounter_data`` tier
    lists. Reading the raw lists marked species "Available" that can never spawn:
    every Starter (``get_all_pokemon_in_tier('Starter')`` is ``[]``) and, by
    default, every generation whose ``misc.genN`` toggle is off (Gen 9 defaults
    off), e.g. Koraidon / Miraidon / Terapagos.
    """
    from ..functions.encounter_functions import (
        check_id_ok,
        get_all_pokemon_in_tier,
    )

    ids = set()
    for tier in _ENCOUNTER_TIERS:
        for pid in get_all_pokemon_in_tier(tier):
            if check_id_ok(pid):
                ids.add(pid)

    # Explicit exclusions: never spawnable regardless of tier / generation.
    for uid in getattr(encounter_data, "UNAVAILABLE", []):
        ids.discard(uid)

    # Regional forms for the active region — same generation gate as the roll.
    active_region = settings.get("misc.active_region") if settings is not None else None
    regional = getattr(encounter_data, "REGIONAL_FORMS", {})
    if active_region and active_region in regional:
        for pid in regional[active_region]:
            if check_id_ok(pid):
                ids.add(pid)

    return ids


def _empty_payload():
    """Empty-but-valid payload so the SPA still renders when the DB is absent."""
    return {
        "owned": [],
        "shinies": [],
        "seen": [],
        "encounterable": [],
        "prerequisites": {},
        "prefs": {
            "viewMode": "grid",
            "sortMode": "id-asc",
            "spriteMode": "static",
        },
        "i18n": {},
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


def _relevant_species_ids(caught_ids, seen_ids):
    """Species whose flavor text can actually be shown (dex hides it until the
    Pokémon is seen/caught), so we don't ship ~900 descriptions every open."""
    ids = set()
    for i in list(caught_ids) + list(seen_ids):
        try:
            ids.add(int(i))
        except (TypeError, ValueError):
            continue
    return ids


def get_ankidex_data(db, settings, tracker=None):
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

    # 2. Caught status — currently owned
    cursor = db.execute(
        "SELECT pokedex_id FROM captured_pokemon WHERE pokedex_id IS NOT NULL"
    )
    caught_ids = {row[0] for row in cursor.fetchall()}

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

    # 4. Encounterable IDs — gated to exactly what the live roll can produce.
    encounterable_ids = build_encounterable_ids(settings)

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
        "shinies": shiny_owned_ids,
        "seen": list(seen_ids),
        "encounterable": list(encounterable_ids),
        "prerequisites": prereqs,
        "prefs": _prefs(settings),
        "i18n": _ankidex_i18n(
            settings, _relevant_species_ids(caught_ids, seen_ids)
        ),
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
