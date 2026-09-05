"""Data layer for the Profile + Team screens.

Plain (non-Qt) helper so the unified shell (``ankimon_items_web/shop_obj.py``)
can host the Profile and Team screens without duplicating their data logic.
Reads/writes the trainer card, captured-Pokémon DB and settings; builds the
payloads the profile.js / team.js pages consume.
"""

import json
import re

from ..services import services

from ..utils import get_all_sprites, POKEMON_NAME_LOOKUP
from ..resources import trainer_sprites_path

MAX_TEAM_SIZE = 6

# Base species that legitimately end in "mega" / "max" and are NOT form names.
_NOT_FORMS = {"yanmega"}


def _capitalize_name(s):
    """Capitalize the first letter of each word segment, preserving separators
    (so "mr-mime" -> "Mr-Mime", "dragonite" -> "Dragonite")."""
    return re.sub(r"(^|[\s\-/])([a-z])", lambda m: m.group(1) + m.group(2).upper(), str(s))


def _species_name(s):
    """Canonical species display name via the shared utils.POKEMON_NAME_LOOKUP
    (single source of truth — e.g. "mrmime" -> "Mr. Mime"), falling back to
    segment-wise capitalization for names the lookup doesn't cover."""
    s = str(s)
    key = s.replace(" ", "").replace("-", "").replace("_", "").lower()
    return POKEMON_NAME_LOOKUP.get(key) or _capitalize_name(s)


def format_pokemon_name(raw):
    """Display name for a Pokémon. Reformats stored form names like
    "baxcaliburmega" -> "Mega Baxcalibur", "xgmax" -> "Gmax X"; the plain
    species name is resolved through the shared canonical lookup
    (utils.POKEMON_NAME_LOOKUP). Yanmega (a real base species) is left alone."""
    if not raw:
        return ""
    s = str(raw)
    low = s.lower()
    if low not in _NOT_FORMS:
        m = re.match(r"^(.+?)mega([xy])$", low)
        if m:
            return "Mega " + _species_name(m.group(1)) + " " + m.group(2).upper()
        m = re.match(r"^(.+?)mega$", low)
        if m:
            return "Mega " + _species_name(m.group(1))
        m = re.match(r"^(.+?)gigantamax$", low)
        if m:
            return "Gigantamax " + _species_name(m.group(1))
        m = re.match(r"^(.+?)gmax$", low)
        if m:
            return "Gmax " + _species_name(m.group(1))
    from ..functions.pokedex_functions import format_lore_name
    return format_lore_name(_species_name(s))


def _format_with_level(s):
    """Reformat a "name (Level N)" string, leaving the level suffix intact."""
    if not s:
        return s
    m = re.match(r"^(.*?)(\s*\(Level\s*\d+\))\s*$", str(s), re.IGNORECASE)
    if m:
        return format_pokemon_name(m.group(1).strip()) + " " + m.group(2).strip()
    return format_pokemon_name(s)


# ------------------------------------------------------------------
# Trainer-sprite descriptions for the sprite-picker modal. ~1400 sprites are
# named like "acetrainerf-gen6xy"; this turns each into a pretty label
# ("Ace Trainer"), a generation/variant sublabel ("Gen 6 · XY"), a browsing
# category and a gender ("m"/"f"/""). Gender is a FILTER only — it is never
# shown in the label (both sexes read the same).
# ------------------------------------------------------------------
_SPRITE_GEN_RE = re.compile(r"^gen(\d+)([a-z0-9]*)$")

_SPRITE_VARIANT = {
    "rb": "RB", "rby": "RBY", "jp": "JP", "rs": "RS", "dp": "DP", "pt": "Pt",
    "bw": "BW", "bw2": "BW2", "xy": "XY", "oras": "ORAS", "frlg": "FRLG",
    "lgpe": "LGPE", "usum": "USUM", "sm": "SM", "hgss": "HGSS", "gs": "GS",
    "champion": "Champion", "title": "Title", "kanto": "Kanto", "johto": "Johto",
    "two": "II", "main": "Main", "c": "C", "masters": "Masters", "anime": "Anime",
    "casual": "Casual", "s": "Scarlet", "v": "Violet", "pwt": "PWT",
    "isekai": "Isekai", "shuffle": "Shuffle",
}
_SPRITE_VKEYS = sorted(_SPRITE_VARIANT, key=len, reverse=True)

# Greedy word-split vocabulary: compound class names → spaced display form.
# Longest match wins, so whole compounds ("acetrainer") beat their pieces; a
# token is only split if the WHOLE thing is consumed, so single-word named
# characters ("misty") fall back to plain capitalization and aren't mangled.
_SPRITE_VOCAB = {
    "acetrainer": "Ace Trainer", "cooltrainer": "Cool Trainer",
    "blackbelt": "Black Belt", "birdkeeper": "Bird Keeper",
    "bugcatcher": "Bug Catcher", "schoolkid": "School Kid", "richboy": "Rich Boy",
    "supernerd": "Super Nerd", "youngcouple": "Young Couple",
    "aromalady": "Aroma Lady", "parasollady": "Parasol Lady",
    "dragontamer": "Dragon Tamer", "ruinmaniac": "Ruin Maniac",
    "pokemaniac": "Poké Maniac", "pokefan": "Poké Fan",
    "pokemonbreeder": "Pokémon Breeder", "pokemonranger": "Pokémon Ranger",
    "battlegirl": "Battle Girl", "ltsurge": "Lt. Surge", "hexmaniac": "Hex Maniac",
    "ninjaboy": "Ninja Boy", "kimonogirl": "Kimono Girl", "officeworker": "Office Worker",
    "risingstar": "Rising Star", "rollerskater": "Roller Skater", "crushgirl": "Crush Girl",
    "securitycorps": "Security Corps", "skytrainer": "Sky Trainer",
    "rocketexecutive": "Rocket Executive", "aetherfoundation": "Aether Foundation",
    "aetheremployee": "Aether Employee", "leaguestaff": "League Staff",
    "scubadiver": "Scuba Diver", "streetthug": "Street Thug", "depotagent": "Depot Agent",
    "firebreather": "Fire Breather", "nurseryaide": "Nursery Aide",
    "furisodegirl": "Furisode Girl", "fairytalegirl": "Fairy Tale Girl",
    "schoolgirl": "School Girl", "youngathlete": "Young Athlete", "poffincook": "Poffin Cook",
    # multi-word named characters
    "cedricjuniper": "Cedric Juniper", "crasherwake": "Crasher Wake",
    "jessiejames": "Jessie & James", "tateandliza": "Tate & Liza",
    "shadowtriad": "Shadow Triad", "pearlclanmember": "Pearl Clan Member",
    "diamondclanmember": "Diamond Clan Member", "pokemoncenterlady": "Pokémon Center Lady",
    # atomic pieces (compounds above win via longest-match)
    "triathlete": "Triathlete", "biker": "Biker", "runner": "Runner", "swimmer": "Swimmer",
    "trainer": "Trainer", "couple": "Couple", "grunt": "Grunt", "worker": "Worker",
    "star": "Star", "snow": "Snow", "skater": "Skater", "team": "Team", "aqua": "Aqua",
    "magma": "Magma", "rocket": "Rocket", "skull": "Skull", "flare": "Flare",
    "galactic": "Galactic", "plasma": "Plasma", "yell": "Yell", "girl": "Girl",
    "boy": "Boy", "jr": "Jr.",
}
_SPRITE_VKEYS_WORDS = sorted(_SPRITE_VOCAB, key=len, reverse=True)

# Inherently single-gender classes that don't follow the …f / …m sibling
# convention — supplements the data-driven gender detection below.
_SPRITE_FEMALE_ROLES = {
    "lass", "beauty", "madame", "lady", "nurse", "idol", "waitress", "kimonogirl",
    "cowgirl", "showgirl", "crushgirl", "policewoman", "battlegirl", "aromalady",
    "parasollady", "beautician", "furisodegirl", "fairytalegirl", "schoolgirl",
    "picnicker",
}
_SPRITE_MALE_ROLES = {
    "youngster", "gentleman", "blackbelt", "richboy", "cueball", "fisherman",
    "policeman", "cameraman", "ninjaboy", "sailor", "biker", "bugcatcher",
    "burglar", "gambler", "guitarist", "schoolboy",
}

# (category, keyword substrings) — first match wins; unmatched → "Characters"
# (the big bucket of named people, browsed via search).
_SPRITE_CATEGORIES = [
    ("Medical", ("nurse", "doctor", "medic", "joy")),
    ("Science", ("scientist", "supernerd", "nerd", "engineer", "researcher", "professor")),
    ("Mystic", ("psychic", "hex", "medium", "channeler", "mystic", "fortune", "seer", "witch", "shaman", "sage", "monk", "cultist")),
    ("Athletic", ("blackbelt", "battlegirl", "swimmer", "hiker", "fisherman", "sailor", "biker", "cyclist", "crusher", "karate", "wrestler", "jogger", "runner", "tuber", "skier", "roughneck", "cueball", "athlete", "boarder")),
    ("Outdoors", ("bugcatcher", "birdkeeper", "camper", "picnicker", "ranger", "aromalady", "gardener", "worker", "ruinmaniac", "dragontamer", "hunter", "farmer", "breeder", "kindler", "backpacker", "collector")),
    ("Performer", ("guitarist", "artist", "juggler", "dancer", "musician", "comedian", "idol", "singer", "painter", "actor", "actress", "entertainer", "performer", "magician", "clown", "poet")),
    ("Youth", ("youngster", "lass", "schoolkid", "twins", "preschooler", "child", "student", "youngcouple", "schoolboy", "schoolgirl", "kindergarten")),
    ("Elegant", ("lady", "madame", "gentleman", "richboy", "socialite", "butler", "maid", "parasollady", "aristocrat", "noble", "beauty", "waiter", "waitress", "rich")),
    ("Official", ("policeman", "officer", "guard", "soldier", "grunt", "rocket", "aqua", "magma", "galactic", "plasma", "flare", "skull", "yell", "admin", "interviewer", "cameraman")),
    ("Trainer", ("acetrainer", "cooltrainer", "pokefan", "pokemaniac", "veteran", "trainer", "expert", "tamer", "gambler", "gamer", "burglar", "tourist", "ninjaboy")),
]
_SPRITE_CATEGORY_ORDER = [
    "Characters", "Trainer", "Athletic", "Outdoors", "Youth", "Elegant",
    "Mystic", "Science", "Performer", "Official", "Medical",
]


def _split_sprite_variant(v):
    """Unpack a run-together variant suffix like 'rbchampion' → 'RB Champion'."""
    out = []
    i = 0
    while i < len(v):
        for k in _SPRITE_VKEYS:
            if v.startswith(k, i):
                out.append(_SPRITE_VARIANT[k])
                i += len(k)
                break
        else:
            rest = v[i:]
            out.append(rest.upper() if len(rest) <= 4 else rest.title())
            break
    return " ".join(out)


def _categorize_sprite(name):
    base = re.sub(r"gen\d+[a-z0-9]*", "", name)
    base = " " + base.replace("-", " ") + " "
    base = re.sub(r"\d+", " ", base).lower()
    for cat, kws in _SPRITE_CATEGORIES:
        for kw in kws:
            if kw in base:
                return cat
    return "Characters"


def _pretty_role(root):
    """Greedy word-split a class root into spaced display form, else capitalize.
    Only used when the WHOLE token is a chain of known words — named characters
    (not in the vocab) fall back to plain capitalization."""
    out = []
    i = 0
    while i < len(root):
        for k in _SPRITE_VKEYS_WORDS:
            if root.startswith(k, i):
                out.append(_SPRITE_VOCAB[k])
                i += len(k)
                break
        else:
            return (root[:1].upper() + root[1:]) if root else root
    return " ".join(out)


def _parse_sprite_gender(role, roles):
    """(gender, root) for a role segment — gender is 'm'/'f'/'' and root is the
    role with any gender suffix stripped. Data-driven (a …f/…m sibling exists)
    with a small curated fallback for inherently single-gender classes."""
    if role.endswith("f") and (role[:-1] in roles or (role[:-1] + "m") in roles):
        return "f", role[:-1]
    if role.endswith("m") and (role[:-1] + "f") in roles:
        return "m", role[:-1]
    if (role + "f") in roles:
        return "m", role
    if role in _SPRITE_FEMALE_ROLES:
        return "f", role
    if role in _SPRITE_MALE_ROLES:
        return "m", role
    return "", role


def _describe_sprite(name, roles):
    """(label, sublabel, gen|None, category, gender) for one sprite filename
    stem. ``roles`` is the set of all first segments. Gender is for filtering
    only and is never appended to the label."""
    parts = name.split("-")
    role = parts[0]
    gender, root = _parse_sprite_gender(role, roles)
    label = _pretty_role(root)

    gen = None
    subs = []
    for p in parts[1:]:
        m = _SPRITE_GEN_RE.match(p)
        if m:
            gen = int(m.group(1))
            var = m.group(2)
            subs.append("Gen " + str(gen) + (" · " + _split_sprite_variant(var) if var else ""))
        else:
            mm = re.match(r"^([a-z]+)(\d*)$", p)
            if mm:
                txt = _split_sprite_variant(mm.group(1))
                subs.append(txt + (" " + mm.group(2) if mm.group(2) else ""))
            else:
                subs.append(p.title())
    return label, " · ".join(subs), gen, _categorize_sprite(name), gender


class ProfileData:
    def __init__(self, addon_dir, trainer_card, settings_obj, logger):
        self.addon_dir = addon_dir
        self.trainer_card = trainer_card
        self.settings_obj = settings_obj
        self.logger = logger
        # Roster is parsed once on first picker open, then reused. Cleared by a
        # successful team save (which can change levels/membership downstream).
        self._roster_cache = None

    # ------------------------------------------------------------------
    # Profile (identity + badge case)
    # ------------------------------------------------------------------
    def get_profile_data(self):
        tc = self.trainer_card
        try:
            tc.refresh()
        except Exception as e:
            print(f"[Ankimon] profile: trainer_card.refresh failed: {e}")

        try:
            sprite_name = self.settings_obj.get("trainer.sprite") or ""
        except Exception:
            sprite_name = ""

        def _safe(getter, default=None):
            try:
                return getter()
            except Exception:
                return default

        # Only fields profile.js actually renders are shipped. (trainer_id and a
        # numeric badge count were computed here but never read by the renderer —
        # it shows badge_grid instead — so they were dropped to keep the payload
        # honest; re-add them here if/when the Profile rail surfaces them.)
        data = {
            "name": getattr(tc, "trainer_name", "") or "Trainer",
            "sprite_url": (
                f"../addon_sprites/trainers/{sprite_name}.png" if sprite_name else ""
            ),
            "level": _safe(lambda: int(tc.level), 1),
            "xp": _safe(lambda: int(tc.xp), 0),
            "total_xp": _safe(lambda: int(tc.total_xp), 0),
            "xp_for_next_level": _safe(lambda: int(tc.xp_for_next_level()), 0),
            "cash": _safe(lambda: int(tc.cash), 0),
            "favorite_pokemon": format_pokemon_name(
                getattr(tc, "favorite_pokemon", "") or "None"
            ),
            "highest_level_pokemon": _format_with_level(
                _safe(lambda: tc.get_highest_level_pokemon(), "None") or "None"
            ),
            "favorite": self._favorite_stub(),
            "highest": self._highest_stub(),
            "friendship": self._friendship_stub(),
            "league": getattr(tc, "league", "") or "unranked",
            "team": self._team_member_stubs(),
            "recent": self._recent_catches(),
            "badge_grid": self._badge_grid(),
        }
        data.update(self._collection_stats())
        return data

    def _collection_stats(self):
        """Cheap COUNT/aggregate stats for the profile dashboard."""
        db = services.db

        def _q(fn, default=0):
            try:
                return int(fn() or 0)
            except Exception:
                return default

        # 1. Retrieve caught pokedex IDs. NOTE: db.get_all_pokemon_ids() reads
        # ONLY the live captured_pokemon table. We also merge released history and
        # explicit caught IDs to match Ankidex and ensure released species do not drop
        # out of this count.
        try:
            caught_ids = db.get_all_pokemon_ids() or set()
        except Exception:
            caught_ids = set()

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

        if hasattr(db, "get_caught_ids"):
            try:
                caught_ids.update(db.get_caught_ids())
            except Exception:
                pass


        # 2. Map all caught IDs to their base species_id (deduplicating Megas, Gmax, and forms)
        from ..functions.pokedex_functions import _load_pokedex_cache, search_pokedex_by_id, safe_int
        pokedex = _load_pokedex_cache()
        caught_species = set()
        
        for pid in caught_ids:
            internal_name = search_pokedex_by_id(pid)
            if internal_name and internal_name in pokedex:
                info = pokedex[internal_name]
                species_id = safe_int(info.get("species_id")) or pid
                caught_species.add(species_id)
            else:
                caught_species.add(pid)

        dex = len(caught_species)

        return {
            "caught": _q(lambda: db.get_pokemon_count()),
            "dex_seen": dex,
            "shinies": _q(lambda: db.get_shiny_count()),
            "highest_level": _q(lambda: self.trainer_card.highest_pokemon_level()),
        }

    def _row_to_stub(self, row):
        """Base {n, sprite} render stub from a captured_pokemon row whose first
        four columns are pokedex_id, name, shiny, gender (or None if the row is
        empty). Shared by the favorite/highest/friendship stubs so the p-dict
        build and sprite resolution live in one place with fixed column
        positions (no per-query index drift)."""
        if not row or not row[1]:
            return None
        p = {"id": row[0] or 0, "name": row[1], "shiny": bool(row[2]), "gender": row[3]}
        return {"n": format_pokemon_name(row[1]), "sprite": self._sprite_url(p)}

    def _favorite_stub(self):
        """The trainer's main/favorite Pokémon as {n, sprite} (or None)."""
        try:
            row = services.db.execute(
                "SELECT pokedex_id, name, shiny, json_extract(data, '$.gender') "
                "FROM captured_pokemon WHERE is_main = 1 LIMIT 1"
            ).fetchone()
        except Exception:
            row = None
        return self._row_to_stub(row)

    def _highest_stub(self):
        """The highest-level captured Pokémon as {n, l, sprite} (or None)."""
        try:
            row = services.db.execute(
                "SELECT pokedex_id, name, shiny, json_extract(data, '$.gender'), level "
                "FROM captured_pokemon WHERE level IS NOT NULL ORDER BY level DESC LIMIT 1"
            ).fetchone()
        except Exception:
            row = None
        stub = self._row_to_stub(row)
        if stub is None:
            return None
        stub["l"] = int(row[4]) if row[4] is not None else 0
        return stub

    def _friendship_stub(self):
        """The highest-friendship captured Pokémon (the trainer's BFF) as
        {n, fr, sprite} (or None). friendship lives in data.$.friendship."""
        try:
            row = services.db.execute(
                "SELECT pokedex_id, name, shiny, json_extract(data, '$.gender'), "
                "json_extract(data, '$.friendship') AS fr "
                "FROM captured_pokemon WHERE name IS NOT NULL "
                "ORDER BY fr DESC LIMIT 1"
            ).fetchone()
        except Exception:
            row = None
        stub = self._row_to_stub(row)
        if stub is None:
            return None
        try:
            fr_val = int(row[4]) if row[4] is not None else 0
        except (ValueError, TypeError):
            fr_val = 0
        stub["fr"] = fr_val
        return stub

    def _badge_grid(self):
        """All badges as {id, name, unlocked} for the Profile badge case.

        Badge definitions never change at runtime, so the badges.json read is
        cached on the instance — the live refresh re-runs this per gameplay
        event, and re-reading the file each time would be wasteful. Only the
        unlocked set (cheap, from trainer_card) is recomputed."""
        definitions = getattr(self, "_badge_defs_cache", None)
        if definitions is None:
            try:
                badges_path = self.addon_dir / "addon_files" / "badges.json"
                with open(badges_path, "r", encoding="utf-8") as f:
                    definitions = json.load(f)
            except Exception as e:
                print(f"[Ankimon] profile: failed to load badges.json: {e}")
                definitions = {}
            self._badge_defs_cache = definitions

        unlocked = set()
        try:
            for b in getattr(self.trainer_card, "badges", []) or []:
                unlocked.add(int(b))
        except (TypeError, ValueError):
            pass

        grid = []
        for raw_id, name in definitions.items():
            if not name or name.lower() in ("changed", "add"):
                continue
            try:
                bid = int(raw_id)
            except (TypeError, ValueError):
                continue
            grid.append({"id": bid, "name": name, "unlocked": bid in unlocked})
        return grid

    # ------------------------------------------------------------------
    # Team data
    # ------------------------------------------------------------------
    def _team_member_stubs(self):
        """Ordered current-team members as lightweight render stubs."""
        try:
            team_data = services.db.get_team() or []
        except Exception:
            return []

        ordered_ids = [
            str(t.get("individual_id"))
            for t in team_data
            if t.get("individual_id")
        ]
        if not ordered_ids:
            return []

        try:
            rows = services.db.get_pokemons_by_individual_ids(ordered_ids) or []
        except Exception:
            return []
        by_id = {str(p.get("individual_id")): p for p in rows}

        stubs = []
        for ind_id in ordered_ids:
            p = by_id.get(ind_id)
            if not p:
                continue
            stub = {
                "id": ind_id,
                "p": p.get("id") or 0,
                "n": format_pokemon_name(p.get("name") or "?"),
                "l": int(p.get("level") or 0),
                "sprite": self._sprite_url(p),
                "types": self._pokemon_types(p.get("name")),
            }
            if p.get("shiny"):
                stub["s"] = 1
            stubs.append(stub)
        return stubs

    def _pokemon_types(self, raw_name):
        """Type list (title-cased) for a species — drives the team cards' type
        badges + coverage. Uses the cached pokedex, so it's cheap per member."""
        if not raw_name:
            return []
        try:
            from ..functions.pokedex_functions import search_pokedex

            t = search_pokedex(raw_name, "types")
        except Exception:
            return []
        if not isinstance(t, list):
            return []
        return [str(x).title() for x in t if x]

    def get_member_stats(self, individual_id):
        """On-demand {cp, types} for a Pokémon just dropped into a slot — roster
        stubs omit both to stay light for big collections, so the team card
        fetches them when the Pokémon is added."""
        cp = self._calc_cp(individual_id)
        types = []
        try:
            p = services.db.get_pokemon(individual_id)
            if p:
                types = self._pokemon_types(p.get("name"))
        except Exception:
            pass
        return {"cp": cp, "types": types}

    def _sprite_url(self, p):
        """Web URL for a Pokémon's front sprite, resolved by the addon's own
        sprite logic (which handles Mega/Gmax/forms, shiny and gender — the
        pokedex_id alone is NOT enough for megas). Only call this for small
        sets (team, recent), never the whole collection."""
        try:
            from ..functions.sprite_functions import get_relative_sprite_path

            return get_relative_sprite_path(
                p.get("id"),
                bool(p.get("shiny")),
                (p.get("gender") or "N"),
                p.get("name"),
            )
        except Exception as e:
            print(f"[Ankimon] profile: sprite url failed: {e}")
            return None

    def _recent_catches(self):
        """The 6 most recently caught Pokémon as render stubs with resolved
        sprites. Ordered by rowid (row insertion order) DESC — individual_id is
        a random key, so it can't be used for recency; rowid tracks capture
        order since new catches INSERT a new row."""
        try:
            cursor = services.db.execute(
                """
                SELECT individual_id, name, level, pokedex_id, shiny,
                       json_extract(data, '$.gender') AS gender
                FROM captured_pokemon
                ORDER BY json_extract(data, '$.captured_date') DESC, rowid DESC
                LIMIT 6
                """
            )
            rows = cursor.fetchall()
        except Exception as e:
            print(f"[Ankimon] profile: recent catches query failed: {e}")
            return []

        out = []
        for row in rows:
            name = row[1]
            if not name:
                continue
            p = {
                "id": row[3] or 0,
                "name": name,
                "shiny": bool(row[4]),
                "gender": row[5],
            }
            stub = {
                "id": row[0],
                "p": row[3] or 0,
                "n": format_pokemon_name(name),
                "l": int(row[2]) if row[2] is not None else 0,
                "sprite": self._sprite_url(p),
            }
            if row[4]:
                stub["s"] = 1
            out.append(stub)
        return out

    def get_team_data(self):
        """Team screen payload: roster, XP Share holder, and Active Companion."""
        members = self._team_member_stubs()
        for m in members:
            m["cp"] = self._calc_cp(m["id"])

        try:
            xp_share = self.settings_obj.get("trainer.xp_share") or None
            cycle_count = self.settings_obj.get("controls.team_cycle_count", 3)
            sprite_mode = self.settings_obj.get(
                "ankidex.spriteMode",
                self.settings_obj.get("pokedex_v2.spriteMode", "static")
            )
        except Exception:
            xp_share = None
            cycle_count = 3
            sprite_mode = "static"

        # companion/companion_info: the current main Pokémon (is_main=1 in the
        # DB) — team.js renders this as the ⚔ badge and lets it be changed via
        # handle_save_team's companion_id.
        db = services.db
        main_pkmn = db.get_main_pokemon() if db else None
        companion_id = main_pkmn.get("individual_id") if main_pkmn else None

        return {
            "max_size": MAX_TEAM_SIZE,
            "team": members,
            "xp_share": str(xp_share) if xp_share else None,
            "xp_share_info": self._resolve_stub(xp_share, members) if xp_share else None,
            "companion": str(companion_id) if companion_id else None,
            "companion_info": self._resolve_stub(companion_id, members) if companion_id else None,
            "sprite_mode": sprite_mode,
            "team_cycle_count": cycle_count,
        }

    def _resolve_stub(self, individual_id, members=None):
        """Render stub for one Pokémon by individual_id — reuses a team member
        if present, otherwise does a single DB lookup."""
        ind_id = str(individual_id)
        for m in members or []:
            if str(m.get("id")) == ind_id:
                stub = {"id": m["id"], "p": m["p"], "n": m["n"], "l": m["l"]}
                if m.get("s"):
                    stub["s"] = 1
                if m.get("sprite"):
                    stub["sprite"] = m["sprite"]
                return stub
        try:
            p = services.db.get_pokemon(ind_id)
        except Exception:
            p = None
        if not p:
            return None
        stub = {
            "id": ind_id,
            "p": p.get("id") or 0,
            "n": format_pokemon_name(p.get("name") or "?"),
            "l": int(p.get("level") or 0),
            "sprite": self._sprite_url(p),
        }
        if p.get("shiny"):
            stub["s"] = 1
        return stub

    def get_roster_data(self):
        """Every captured Pokémon as a pick stub (cached on instance). Carries
        CP (read from the stored data JSON — cheap, no per-Pokémon recompute) and
        types so the picker can show rich cards, filter by type, and sort by CP.
        Default order is CP desc."""
        if self._roster_cache is not None:
            return self._roster_cache

        try:
            cursor = services.db.execute(
                """
                SELECT individual_id, name, level, pokedex_id, shiny,
                       json_extract(data, '$.cp') AS cp
                FROM captured_pokemon
                ORDER BY COALESCE(json_extract(data, '$.cp'), 0) DESC,
                         level DESC, name ASC
                """
            )
            rows = cursor.fetchall()
        except Exception as e:
            print(f"[Ankimon] profile: get_roster_data query failed: {e}")
            return {"choices": []}

        choices = []
        for row in rows:
            ind_id = row[0]
            name = row[1]
            if not ind_id or not name:
                continue
            pid = row[3] or 0
            shiny = bool(row[4])
            entry = {
                "id": ind_id,
                "p": pid,
                "n": format_pokemon_name(name),
                "l": int(row[2]) if row[2] is not None else 0,
                "cp": int(row[5]) if row[5] is not None else 0,
                "types": self._pokemon_types(name),
            }
            if shiny:
                entry["s"] = 1
            # Mega/Gmax & other forme ids (>= 10000) have no sprite at
            # front_default/<id>.png — the stored pokedex_id is a forme id. Resolve
            # via the addon's own sprite logic (get_sprite_path), which falls back
            # to the base species sprite, so the picker matches every other screen.
            # Only a handful of owned Pokémon are formes, so these filesystem
            # lookups are cheap and run once (the roster is cached on the instance).
            nl = name.lower()
            if pid >= 10000 or "mega" in nl or "gmax" in nl or "gigantamax" in nl:
                sprite = self._sprite_url(
                    {"id": pid, "name": name, "shiny": shiny, "gender": None}
                )
                if sprite:
                    entry["sprite"] = sprite
            choices.append(entry)

        result = {"choices": choices}
        self._roster_cache = result
        return result

    def _calc_cp(self, individual_id):
        """Pokémon-GO-style CP for one Pokémon (team slots only).

        Prefer the *stored* cp — the same value the roster picker and Ankidex
        show — so every screen agrees. Recompute when it's missing or zero
        (a stored 0 is garbage — CP floors at 10), via calculate_cp_from_dict
        (which correctly falls back from '$.base_stats' to '$.stats'): most
        Pokémon keep their base stats under '$.stats' and have no
        '$.base_stats', so reading base_stats alone made them compute a
        garbage (~min) CP."""
        try:
            data = services.db.get_pokemon(individual_id)
            if not data:
                return 0
            cp = data.get("cp")
            if not cp:
                from ..business import calculate_cp_from_dict
                cp = calculate_cp_from_dict(data)
            return int(cp or 0)
        except Exception as e:
            print(f"[Ankimon] profile: CP calc failed for {individual_id}: {e}")
            return 0

    # team.js sends this in place of a real individual_id when the Active
    # Companion selection was never touched this session (the ⚔ button was
    # never clicked, and no slot holding it was removed/replaced) — every
    # OTHER team save (reordering, swapping an unrelated slot, XP Share only)
    # must go through this path too, so it can't be a real id and must be
    # left completely alone here: it used to be treated the same as "no
    # companion" and cleared whatever main Pokémon was already set on every
    # single save that didn't touch the crown, which is the actual regression
    # this sentinel exists to prevent.
    #
    # The literal is mirrored in team.js (``COMPANION_UNCHANGED``). The two
    # are pinned together by test_team_save_companion.py, which reads team.js
    # off disk and asserts this exact string appears in it — a rename on
    # either side that isn't mirrored would otherwise silently make every
    # ordinary save look "touched" again and resurrect the regression above.
    _COMPANION_UNCHANGED = "__companion_unchanged__"

    def handle_save_team(self, team_ids, xp_share_id, companion_id):
        """Persist the chosen team + XP Share holder.

        ``companion_id`` is the Active Companion — whichever team member should
        actually be the one battling (``is_main=1`` in the DB). team.js's ⚔
        button on a team slot sets/clears it; ``_COMPANION_UNCHANGED`` means
        this save never touched that selection at all and the existing
        is_main row (however it got there — the crown, or an older pathway
        like starter selection/PC box) must be left alone.

        Anything else is a companion *change*, and a change never ends with
        the game having no battler at all: a real, valid team-member id is a
        set, and an explicit clear promotes the first member of the team being
        saved. The three ways a change can't be honoured — an id that isn't in
        the team being saved (bad input), a clear with an empty team (nobody to
        promote), and an id whose captured_pokemon row is gone by the time
        set_main_pokemon() runs (released, or a stale roster cache) — all fall
        back to leaving the existing is_main row exactly where it is, and none
        of them report a ``companion`` back. This method never drops it: zero
        is_main=1 rows makes the next load fall through
        ``update_main_pokemon()`` to ``MAIN_POKEMON_DEFAULT``, the level-5
        Ditto named "Please Restart Anki", and a stale-but-real battler beats
        that in every case.

        Whenever the companion is actually set, the live ``main_pokemon``
        object is reloaded from the DB and the reviewer HUD + Ankimon Window
        repaint so the swap is visible immediately. The response carries a
        ``companion`` key back to team.js in that case, because a clear can be
        rewritten into a promotion here and the page would otherwise keep
        showing no crown while the DB has one."""
        companion_touched = companion_id != self._COMPANION_UNCHANGED

        seen = set()
        clean_ids = []
        for raw in team_ids or []:
            ind_id = str(raw) if raw is not None else ""
            if not ind_id or ind_id in seen:
                continue
            seen.add(ind_id)
            clean_ids.append(ind_id)
            if len(clean_ids) >= MAX_TEAM_SIZE:
                break

        team_data = [{"individual_id": ind_id} for ind_id in clean_ids]
        xp_share_id = str(xp_share_id) if xp_share_id else None
        companion_id = str(companion_id) if (companion_touched and companion_id) else None
        # The Active Companion has to actually be a member of the team being
        # saved — otherwise a slot swap in the same save could point
        # set_main_pokemon at a Pokémon that just got dropped from the roster,
        # leaving the battler out of sync with what the team screen shows.
        # An id that fails this check is bad INPUT, not an instruction: drop
        # the companion field from this save entirely (back to "unchanged")
        # rather than letting a bridge race, a stale cached team.js or a
        # third-party caller delete the player's battler.
        if companion_id and companion_id not in clean_ids:
            companion_id = None
            companion_touched = False
        # An explicit clear — the crown toggled off, or the companion's own
        # slot removed/replaced in team.js — means "somebody else battles
        # now", never "nobody does". Leaving zero is_main=1 rows makes the
        # NEXT load fall through update_main_pokemon() to
        # MAIN_POKEMON_DEFAULT: the level-5 Ditto literally named "Please
        # Restart Anki". Promote the first member of the team being saved;
        # if the save empties the team there is nobody to promote, so treat
        # it as "unchanged" and leave the existing battler alone rather than
        # leaving the player with none at all.
        elif companion_touched and not companion_id:
            if clean_ids:
                companion_id = clean_ids[0]
            else:
                companion_touched = False

        try:
            # NOTE: no legacy "trainer.team" config write — the DB team table
            # (services.db.save_team) is the sole source of truth every read path
            # uses; settings.py migrates/deletes the old config key on load.
            self.settings_obj.set("trainer.xp_share", xp_share_id)
            services.db.save_team(team_data)
            # companion_touched now means exactly "this save has an id to
            # set": the sentinel, a rejected id and an unpromotable clear have
            # all been folded into "leave the existing is_main row alone".
            if not companion_touched:
                pass  # leave whatever main Pokémon is already set alone
            elif not services.db.set_main_pokemon(companion_id):
                # set_main_pokemon() returns False when the individual_id has
                # no captured_pokemon row — the id was only checked against the
                # team team.js just sent, so a release or a stale roster cache
                # between page load and save lands here. Nothing was written:
                # the old is_main row still stands, so this is the same
                # "unchanged" outcome as a rejected id, and reporting a
                # companion back would make team.js show a crown the DB never
                # got.
                companion_touched = False
            else:
                from ..functions.update_main_pokemon import update_main_pokemon

                update_main_pokemon(services.main_pokemon)
                # Repaint the reviewer HUD + the Ankimon Window popup so the
                # switch is visible immediately, not just after the next
                # battle turn (same seam cycle_team_pokemon() in reviewer_ui.py
                # uses for its own companion swap).
                try:
                    if services.reviewer is not None:
                        services.reviewer.refresh_hud()
                except Exception:
                    pass
                try:
                    # is_alive(), not a bare None-check: a closed-and-deleted
                    # Ankimon Window is still a non-None sip wrapper, and
                    # calling into it raises "wrapped C/C++ object deleted"
                    # — which the except below would swallow, silently
                    # skipping the repaint this branch exists to do.
                    from ..utils import is_alive

                    test_window = services.test_window
                    if is_alive(test_window) and test_window.isVisible():
                        test_window.main_pokemon = services.main_pokemon
                        if test_window.current_view == "battle":
                            test_window.force_display_battle()
                except Exception:
                    pass
        except Exception as e:
            return {"ok": False, "message": f"Failed to save team: {e}"}

        try:
            if self.trainer_card is not None:
                self.trainer_card.reload_team()
        except Exception:
            pass

        self._roster_cache = None
        result = {"ok": True, "message": "Team saved.", "count": len(team_data)}
        if companion_touched:
            # What the save actually left as the Active Companion — team.js
            # applies this so a clear that was rewritten into a promotion
            # shows its crown straight away instead of after a page reload.
            result["companion"] = companion_id
        return result

    # ------------------------------------------------------------------
    # Trainer sprite picker
    # ------------------------------------------------------------------
    def get_sprite_data(self):
        try:
            names = sorted(get_all_sprites(trainer_sprites_path))
        except Exception as e:
            print(f"[Ankimon] profile: get_all_sprites failed: {e}")
            names = []
        try:
            current = self.settings_obj.get("trainer.sprite") or ""
        except Exception:
            current = ""

        # Set of role bases (first "-" segment) — lets _describe_sprite detect
        # female ("…f") variants only when the base class is itself a sprite.
        roles = {n.split("-")[0] for n in names}
        sprites = []
        present_gens = set()
        present_cats = set()
        present_sex = set()
        for name in names:
            label, sublabel, gen, category, gender = _describe_sprite(name, roles)
            gen_key = str(gen) if gen else "other"
            present_gens.add(gen_key)
            present_cats.add(category)
            if gender:
                present_sex.add(gender)
            sprites.append(
                {
                    "name": name,
                    "label": label,
                    "sublabel": sublabel,
                    "gen": gen_key,
                    "category": category,
                    "gender": gender,
                    "url": f"../addon_sprites/trainers/{name}.png",
                }
            )

        # Filter chips, in a stable order, listing only what's actually present.
        generations = [
            {"key": str(i), "label": f"Gen {i}"}
            for i in range(1, 10)
            if str(i) in present_gens
        ]
        if "other" in present_gens:
            generations.append({"key": "other", "label": "Other"})
        categories = [c for c in _SPRITE_CATEGORY_ORDER if c in present_cats]
        genders = []
        if "m" in present_sex:
            genders.append({"key": "m", "label": "♂ Male"})
        if "f" in present_sex:
            genders.append({"key": "f", "label": "♀ Female"})

        return {
            "sprites": sprites,
            "generations": generations,
            "categories": categories,
            "genders": genders,
            "current": current,
        }

    def handle_set_sprite(self, name):
        if not name:
            return {"ok": False, "message": "No sprite selected."}
        try:
            self.settings_obj.set("trainer.sprite", name)
        except Exception as e:
            return {"ok": False, "message": f"Failed to set sprite: {e}"}
        try:
            if self.trainer_card is not None:
                self.trainer_card.refresh()
        except Exception:
            pass
        return {"ok": True, "message": "Trainer sprite updated.", "current": name}

    def handle_set_name(self, name):
        """Persist a new trainer name (settings + trainer_card). Mirrors
        handle_set_sprite: trims, rejects empty, caps length, refreshes the
        trainer card so the in-memory name updates."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "message": "Name can't be empty."}
        if len(name) > 24:
            name = name[:24]
        try:
            self.settings_obj.set("trainer.name", name)
        except Exception as e:
            return {"ok": False, "message": f"Failed to set name: {e}"}
        try:
            if self.trainer_card is not None:
                self.trainer_card.refresh()
        except Exception:
            pass
        return {"ok": True, "message": "Trainer name updated.", "name": name}
