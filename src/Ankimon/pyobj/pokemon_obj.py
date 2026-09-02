from typing import Union
import uuid
import json
import os
from typing import Optional

from ..services import services

# NOTE: give_item is imported lazily inside give_back_held_item() (below) rather
# than here. utils imports pokedex_functions which imports this module, so a
# top-level `from ..utils import give_item` forms an import cycle that breaks
# whichever module in it loads first. The lazy import sidesteps that entirely.
from ..functions.sprite_functions import get_sprite_path

from ..poke_engine.objects import Pokemon
from ..resources import pkmnimgfolder, mainpokemon_path, mypokemon_path


class PokemonObject:
    def __init__(
        self,
        type,
        name: str,
        id: int,
        shiny: bool,
        level: int,
        ability,
        gender: str,
        growth_rate: str,
        captured_date: Optional[str],
        tier: str,
        individual_id: str,
        current_hp=None,
        base_stats=None,
        attacks=None,
        base_experience=0,
        hp=None,
        ev=None,
        iv=None,
        battle_status="Fighting",
        xp=0,
        position=(0, 0),
        nickname="",
        moves=None,
        ev_yield=None,
        friendship=0,
        everstone=False,
        evolution_rejected=False,
        pokemon_defeated=0,
        is_favorite=False,
        held_item: Union[str, None] = None,
        **kwargs,
    ):
        # Unique identifier
        self.individual_id = individual_id
        self.name = name
        self.nickname = nickname
        self.shiny = shiny
        self.id = id
        self.level = level
        self.ability = ability
        self.type = type
        self.gender = gender
        self.tier = tier
        self.everstone = everstone
        self.evolution_rejected = evolution_rejected
        self.pokemon_defeated = pokemon_defeated

        if not ability or str(ability).strip().lower() in ("none", "no ability", ""):
            self.ability = "Run Away"
        else:
            self.ability = ability

        # Stats
        self.base_stats = base_stats or {
            "hp": 1,
            "atk": 1,
            "def": 1,
            "spa": 1,
            "spd": 1,
            "spe": 1,
        }
        self.ev = {
            k: int(v)
            for k, v in (
                ev or {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
            ).items()
        }
        default_iv = {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15}
        iv_data = iv if isinstance(iv, dict) else {}

        def normalize_iv(value):
            try:
                return max(0, min(31, int(value)))
            except (TypeError, ValueError):
                return 15

        self.iv = {
            key: normalize_iv(iv_data.get(key, default))
            for key, default in default_iv.items()
        }
        self.ev_yield = {
            k: int(v)
            for k, v in (
                ev_yield or {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
            ).items()
        }

        # Attacks and moves
        self.attacks = list(attacks) if attacks else ["Struggle"]
        self.moves = list(moves) if moves else []

        # Experience and growth
        self.base_experience = base_experience
        self.growth_rate = growth_rate
        self.xp = xp
        self.friendship = friendship

        # Battle and status
        self.battle_status = str(battle_status)
        self.position = (
            tuple(position) if isinstance(position, (list, tuple)) else (0, 0)
        )
        self.stat_stages = kwargs.get(
            "stat_stages",
            {
                "atk": 0,
                "def": 0,
                "spa": 0,
                "spd": 0,
                "spe": 0,
                "accuracy": 0,
                "evasion": 0,
            },
        )
        self.volatile_status = set(kwargs.get("volatile_status", []))
        self.nature = kwargs.get("nature", "serious")
        self.held_item = held_item

        # HP calculation. ``hp`` is an explicit parameter, so looking for it in
        # ``kwargs`` silently ignored persisted values.  Keep both HP fields
        # numeric and preserve 0, which represents a fainted Pokemon.
        self.max_hp = self.calculate_max_hp()
        self.hp = self._normalize_hp(hp, self.max_hp, self.max_hp)
        self.current_hp = self._normalize_hp(current_hp, self.hp, self.max_hp)

        self.is_favorite = is_favorite
        self.captured_date = captured_date

    @property
    def display_name(self) -> str:
        """Return the nickname if present and not redundant, else the official pretty name."""
        try:
            from ..functions.pokedex_functions import (
                get_pokemon_diff_lang_name,
                get_pretty_name_for_name,
            )

            # Access the language setting via the service seam if available.
            lang = 9
            settings = services.settings
            if settings is not None:
                lang = int(settings.get("misc.language", 9))

            # int-cast like the sibling properties: ids read from legacy /
            # migrated JSON records may be strings, and a str id would raise
            # inside get_pokemon_diff_lang_name's `>= 10000` comparison.
            pretty_name = get_pokemon_diff_lang_name(int(self.id), lang)
            if pretty_name == "No Translation in this language":
                pretty_name = get_pretty_name_for_name(self.name)

            if self.nickname:
                # Check if the nickname is just a variation of the internal/pretty name.
                def normalize(s):
                    return (
                        str(s)
                        .lower()
                        .replace(" ", "")
                        .replace("-", "")
                        .replace("'", "")
                        .replace(".", "")
                        .replace(":", "")
                    )

                norm_nick = normalize(self.nickname)
                if norm_nick != normalize(self.name) and norm_nick != normalize(
                    pretty_name
                ):
                    return self.nickname

            return pretty_name
        except Exception:
            try:
                from ..functions.pokedex_functions import get_pretty_name_for_name

                return (
                    self.nickname
                    if self.nickname
                    else get_pretty_name_for_name(self.name)
                )
            except Exception:
                return self.nickname if self.nickname else self.name.title()

    @property
    def pokedex_id(self) -> int:
        """Return the base species Pokédex ID (resolving form ids >= 10000 to their species)."""
        try:
            from ..functions.pokedex_functions import (
                search_pokedex_by_id,
                search_pokedex,
                safe_int,
            )

            actual_id = int(self.id)
            if actual_id >= 10000:
                internal_name = search_pokedex_by_id(actual_id)
                if internal_name and internal_name != "Pokémon not found":
                    sid = safe_int(search_pokedex(internal_name, "species_id"))
                    if sid:
                        return sid
            return actual_id
        except Exception:
            # Keep the declared `-> int` contract even for garbage ids.
            try:
                return int(getattr(self, "id", 1))
            except (TypeError, ValueError):
                return 1

    @property
    def generation(self) -> int:
        """Return the generation in which this Pokémon (or form) was introduced."""
        try:
            from ..functions.pokedex_functions import (
                search_pokedex_by_id,
                search_pokedex,
            )
            from ..const import gen_ids
            from ..functions import encounter_data

            actual_id = int(self.id)

            # 1. Check for a regional-form intro gen first. REGIONAL_FORME_GEN is
            # owned by the encounter-overhaul leaf (functions/encounter_data.py);
            # until that lands this getattr yields {} and regional forms fall
            # through to their base-species generation below.
            if actual_id >= 10000:
                internal_name = search_pokedex_by_id(actual_id)
                if internal_name and internal_name != "Pokémon not found":
                    forme = search_pokedex(internal_name, "forme") or ""
                    intro_gen = getattr(encounter_data, "REGIONAL_FORME_GEN", {}).get(
                        forme
                    )
                    if intro_gen:
                        return intro_gen

            # 2. Fallback to base-species generation.
            species_id = self.pokedex_id

            # Sort by max_id to match the lowest possible gen.
            sorted_gens = sorted(gen_ids.items(), key=lambda x: x[1])
            for gen_key, max_val in sorted_gens:
                if species_id <= max_val:
                    # Parse "gen_1" -> 1
                    try:
                        return int(gen_key.split("_")[1])
                    except Exception:
                        continue

            # If the ID is beyond known gens, return the last gen.
            if sorted_gens:
                return int(sorted_gens[-1][0].split("_")[1])
            return 1
        except Exception:
            # Emergency fallback based on common ID ranges if imports fail.
            # int-cast defensively: a str id would crash the fallback itself.
            try:
                sid = int(getattr(self, "id", 1))
            except (TypeError, ValueError):
                sid = 1
            if sid <= 151:
                return 1
            if sid <= 251:
                return 2
            if sid <= 386:
                return 3
            if sid <= 493:
                return 4
            if sid <= 649:
                return 5
            if sid <= 721:
                return 6
            if sid <= 809:
                return 7
            if sid <= 905:
                return 8
            return 9

    @classmethod
    def calc_stat(
        cls,
        stat_name: str,
        base_stat_val: int,
        level: int,
        iv: int,
        ev: int,
        nature: str,
    ) -> int:
        if stat_name == "hp":
            hp = (
                10 + level + int((2 * base_stat_val + iv + int(ev / 4)) * level / 100)
            )  # Formula found on bulbapedia
            return int(hp)
        elif stat_name in ("atk", "def", "spa", "spd", "spe"):
            nature_mult = PokemonObject.get_nature_stat_mult(
                stat_name, nature
            )  # Formula found on bulbapedia
            stat = (
                5 + int((2 * base_stat_val + iv + int(ev / 4)) * level / 100)
            ) * nature_mult
            return int(stat)
        raise ValueError(f"Received an unknown stat_name : {stat_name}")

    @property
    def stats(self) -> dict:
        _dict = {}
        for key, val in self.base_stats.items():
            if key not in ("hp", "atk", "def", "spa", "spd", "spe"):
                continue
            _dict[key] = PokemonObject.calc_stat(
                key, val, self.level, self.iv[key], self.ev[key], self.nature
            )
        return _dict

    @stats.setter
    def stats(self, value):
        raise AttributeError(
            "Setting the value of the stats of a Pokemon is forbidden as they are automatically calculated using their base stats. You can instead set the base_stats of the Pokemon."
        )

    @property
    def cp(self) -> int:
        """Combat Power — Pokemon GO style formula.

        ``CP = floor(Attack × √Defense × √Stamina × CPM² / 10)``

        Uses raw stats (base + IV + EV/4) so CPM is the sole level
        multiplier.  See :func:`business.calculate_pokemon_go_cp`.

        Memoized: attribute assignment to ``level``, ``ev``, ``iv``, or
        ``base_stats`` invalidates the cache via ``__setattr__``. Mutating
        those containers in-place (e.g. ``self.ev["atk"] += 1``) does not,
        so call :meth:`invalidate_cp_cache` explicitly at those sites.
        """
        cached = getattr(self, "_cached_cp", None)
        if cached is not None:
            return cached
        # Local import to avoid a circular dependency with ``business``.
        from ..business import calculate_pokemon_go_cp, pokemon_go_raw_stats

        attack, defense, stamina = pokemon_go_raw_stats(
            self.base_stats, self.iv, self.ev
        )
        cp_val = calculate_pokemon_go_cp(attack, defense, stamina, self.level)
        object.__setattr__(self, "_cached_cp", cp_val)
        return cp_val

    def invalidate_cp_cache(self) -> None:
        """Drop memoized CP so the next ``cp`` access recomputes.

        Call after mutating ``ev``/``iv``/``base_stats`` dict contents
        in place (attribute reassignment is caught automatically by
        ``__setattr__``).
        """
        object.__setattr__(self, "_cached_cp", None)

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in ("level", "ev", "iv", "base_stats"):
            object.__setattr__(self, "_cached_cp", None)

    @classmethod
    def get_nature_stat_mult(cls, stat_name: str, nature: str) -> float:
        if stat_name == "atk":
            if nature.lower() in ("lonely", "brave", "adamant", "naughty"):
                return 1.1
            if nature.lower() in ("bold", "timid", "modest", "calm"):
                return 0.9
        elif stat_name == "def":
            if nature.lower() in ("bold", "relaxed", "impish", "lax"):
                return 1.1
            if nature.lower() in ("lonely", "hasty", "mild", "gentle"):
                return 0.9
        elif stat_name == "spa":
            if nature.lower() in ("modest", "mild", "quiet", "rash"):
                return 1.1
            if nature.lower() in ("adamant", "impish", "jolly", "careful"):
                return 0.9
        elif stat_name == "spd":
            if nature.lower() in ("calm", "gentle", "sassy", "careful"):
                return 1.1
            if nature.lower() in ("naughty", "lax", "naive", "rash"):
                return 0.9
        elif stat_name == "spe":
            if nature.lower() in ("timid", "hasty", "jolly", "naive"):
                return 1.1
            if nature.lower() in ("brave", "relaxed", "quiet", "sassy"):
                return 0.9
        return 1.0

    def to_dict(self):
        return {
            "name": self.name,
            "nickname": self.nickname,
            "level": self.level,
            "gender": self.gender,
            "id": self.id,
            "ability": self.ability,
            "type": self.type,
            "base_stats": self.base_stats,
            "stats": self.stats,  # Calculated stats
            "cp": self.cp,
            "nature": self.nature,
            "ev": self.ev,
            "iv": self.iv,
            "attacks": self.attacks,
            "base_experience": self.base_experience,
            "growth_rate": self.growth_rate,
            "everstone": self.everstone,
            "evolution_rejected": self.evolution_rejected,
            "shiny": self.shiny,
            "captured_date": getattr(self, "captured_date", None),
            "individual_id": self.individual_id,
            "mega": getattr(self, "mega", False),
            "special_form": getattr(self, "special_form", None),
            "xp": self.xp,
            "hp": self.hp,  # Current HP
            "friendship": self.friendship,
            "pokemon_defeated": self.pokemon_defeated,
            "tier": self.tier,  # Added tier
            "is_favorite": getattr(self, "is_favorite", False),  # Added with default
            # Additional fields from your example
            "current_hp": getattr(self, "current_hp", self.hp),
            "held_item": self.held_item,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

    def get_stats(self):
        """Return the stats of the Pokémon."""
        return vars(self)

    # Allowlist of legitimate writable data attributes for update_stats.
    # Only these fields can be set via setattr to prevent accidentally
    # shadowing methods or properties with plain values.
    _WRITABLE_ATTRS = frozenset({
        "individual_id", "name", "nickname", "shiny", "id", "level", "ability",
        "type", "gender", "tier", "everstone", "evolution_rejected",
        "pokemon_defeated", "base_stats", "ev", "iv", "ev_yield", "attacks",
        "moves", "base_experience", "growth_rate", "xp", "friendship",
        "battle_status", "position", "stat_stages", "volatile_status", "nature",
        "held_item", "hp", "current_hp", "is_favorite", "captured_date",
        "mega", "special_form"
    })

    @staticmethod
    def _normalize_hp(value, fallback, max_hp):
        """Return an integer HP value constrained to the Pokemon's valid range."""
        try:
            maximum = max(0, int(max_hp))
        except (TypeError, ValueError, OverflowError):
            maximum = 1

        try:
            fallback_value = int(fallback)
        except (TypeError, ValueError, OverflowError):
            fallback_value = maximum

        try:
            hp_value = fallback_value if value is None else int(value)
        except (TypeError, ValueError, OverflowError):
            hp_value = fallback_value

        return max(0, min(hp_value, maximum))

    def update_stats(self, **kwargs):
        """Update the attributes of the Pokémon object with keyword arguments."""
        for key, value in kwargs.items():
            if key not in self._WRITABLE_ATTRS:
                continue
            setattr(self, key, value)
        # Derived caches — recompute from the (possibly updated)
        # base_stats/level/iv/ev so they don't go stale.
        self.max_hp = self.calculate_max_hp()
        raw_hp = getattr(self, "hp", None)
        raw_current_hp = getattr(self, "current_hp", None)
        hp_fallback = raw_current_hp if raw_current_hp is not None else self.max_hp
        self.hp = self._normalize_hp(raw_hp, hp_fallback, self.max_hp)
        self.current_hp = self._normalize_hp(raw_current_hp, self.hp, self.max_hp)
        self._update_battle_stats()  # Update battle stats

    def reset_stats(self):
        """Reset the stats of the Pokémon to default values."""
        self.hp = self.max_hp
        self.battle_status = "Fighting"
        self._update_battle_stats()

    def _update_battle_stats(self):
        """Update battle stats with current stats, EVs, and IVs."""
        self._battle_stats = {}
        # Only update battle stats with valid keys
        for d in [self.stats, self.iv, self.ev]:
            for key, value in d.items():
                self._battle_stats[key] = value

    def calculate_max_hp(self):
        ev, iv = self.ev["hp"], self.iv["hp"]
        hp = (
            10
            + self.level
            + int((2 * self.base_stats["hp"] + iv + int(ev / 4)) * self.level / 100)
        )
        hp = int(hp)
        return hp

    def get_sprite_path(self, side, sprite_type):
        return get_sprite_path(
            side, sprite_type, self.id, self.shiny, self.gender, pokemon_name=self.name
        )

    def to_engine_format(self):
        from ..poke_engine.helpers import normalize_name

        return {
            "identifier": normalize_name(self.name),
            "level": self.level,
            "nature": getattr(self, "nature", "serious"),
            "evs": (
                self.ev.get("hp", 0),
                self.ev.get("atk", 0),
                self.ev.get("def", 0),
                self.ev.get("spa", 0),
                self.ev.get("spd", 0),
                self.ev.get("spe", 0),
            ),
            "types": [normalize_name(t) for t in self.type],
            "hp": self.hp,
            "maxhp": self.max_hp,
            "ability": normalize_name(self.ability) if self.ability else "none",
            "item": normalize_name(self.held_item) if self.held_item else None,
            "attack": self.stats.get("atk", 0),
            "defense": self.stats.get("def", 0),
            "special_attack": self.stats.get("spa", 0),
            "special_defense": self.stats.get("spd", 0),
            "speed": self.stats.get("spe", 0),
            "ivs": (
                self.iv.get("hp", 0),
                self.iv.get("atk", 0),
                self.iv.get("def", 0),
                self.iv.get("spa", 0),
                self.iv.get("spd", 0),
                self.iv.get("spe", 0),
            ),
            "attack_boost": self.stat_stages.get("atk", 0),
            "defense_boost": self.stat_stages.get("def", 0),
            "special_attack_boost": self.stat_stages.get("spa", 0),
            "special_defense_boost": self.stat_stages.get("spd", 0),
            "speed_boost": self.stat_stages.get("spe", 0),
            "accuracy_boost": self.stat_stages.get("accuracy", 0),
            "evasion_boost": self.stat_stages.get("evasion", 0),
            "status": self.battle_status if self.battle_status != "fighting" else None,
            "volatile_status": set(normalize_name(vs) for vs in self.volatile_status),
            "moves": [{"id": normalize_name(move)} for move in self.attacks],
        }

    @classmethod
    def from_engine_format(cls, engine_data):
        """Create PokemonObject from poke-engine data"""
        return cls(
            name=engine_data["identifier"].capitalize(),
            level=engine_data["level"],
            hp=engine_data["hp"],
            base_stats={
                "hp": engine_data.get("maxhp", 0),
                "atk": engine_data["attack"],
                "def": engine_data["defense"],
                "spa": engine_data["special_attack"],
                "spd": engine_data["special_defense"],
                "spe": engine_data["speed"],
            },
            ev={
                k: v
                for k, v in zip(
                    ["hp", "atk", "def", "spa", "spd", "spe"], engine_data["evs"]
                )
            },
            iv={
                k: v
                for k, v in zip(
                    ["hp", "atk", "def", "spa", "spd", "spe"], engine_data["ivs"]
                )
            },
            battlestatus=engine_data.get("status", "fighting"),
            moves=engine_data["moves"],
            stat_stages={
                "atk": engine_data["stat_stages"]["attack"],
                "def": engine_data["stat_stages"]["defense"],
                "spa": engine_data["stat_stages"]["special_attack"],
                "spd": engine_data["stat_stages"]["special_defense"],
                "spe": engine_data["stat_stages"]["speed"],
                "accuracy": engine_data["stat_stages"]["accuracy"],
                "evasion": engine_data["stat_stages"]["evasion"],
            },
            volatile_status=set(engine_data.get("volatile_status", [])),
            nature=engine_data.get("nature", "serious"),
            held_item=engine_data.get("item", ""),
        )

    def to_poke_engine_Pokemon(self) -> Pokemon:
        _dict = self.to_engine_format()
        pokemon = Pokemon(
            identifier=_dict["identifier"],
            level=_dict["level"],
            types=_dict["types"],
            hp=_dict["hp"],
            maxhp=_dict["maxhp"],
            ability=_dict["ability"],
            item=_dict["item"],
            attack=_dict["attack"],
            defense=_dict["defense"],
            special_attack=_dict["special_attack"],
            special_defense=_dict["special_defense"],
            speed=_dict["speed"],
            nature=_dict.get("nature", "serious"),
            evs=_dict.get("evs", (85,) * 6),
            attack_boost=_dict.get("attack_boost", 0),
            defense_boost=_dict.get("defense_boost", 0),
            special_attack_boost=_dict.get("special_attack_boost", 0),
            special_defense_boost=_dict.get("special_defense_boost", 0),
            speed_boost=_dict.get("speed_boost", 0),
            accuracy_boost=_dict.get("accuracy_boost", 0),
            evasion_boost=_dict.get("evasion_boost", 0),
            status=_dict.get("status", None),
            terastallized=_dict.get("terastallized", False),
            volatile_status=_dict.get("volatile_status", set()),
            moves=_dict.get("moves", []),
        )
        return pokemon

    def reset_bonuses(self):
        """
        This method resets various bonuses and status effects currently applied
        to the pokemon.

        This method is typically used to reset the stat boosts of the main
        Pokemon when the opponent gets KOed, preventing the user from
        steamrolling every wild pokemon once the main pokemon is setup with
        stat boosts.

        Args:
            None

        Returns:
            None
        """
        self.stat_stages = {
            "atk": 0,
            "def": 0,
            "spa": 0,
            "spd": 0,
            "spe": 0,
            "accuracy": 0,
            "evasion": 0,
        }

    def give_held_item(self, held_item: str) -> None:
        """
        Assigns a held item to the Pokémon and updates the database.

        If the Pokémon is already holding an item, it is removed first.
        """
        db = services.db

        # If the pokemon already holds an object, we remove it to make room for the new one.
        if self.held_item:
            self.remove_held_item()

        db.update_item_quantity(held_item, -1)
        self.held_item = held_item

        # Save to captured_pokemon in database
        pokemon_data = db.get_pokemon(self.individual_id)
        if pokemon_data:
            pokemon_data["held_item"] = held_item
            db.save_pokemon(pokemon_data)

        # Also update main_pokemon if this is the main pokemon
        main_pokemon = db.get_main_pokemon()
        if main_pokemon and main_pokemon.get("individual_id") == self.individual_id:
            main_pokemon["held_item"] = held_item
            db.save_main_pokemon(main_pokemon)

        # Sync the in-memory main_pokemon singleton if it is the target.
        main_pkmn = services.main_pokemon
        if (
            main_pkmn is not None
            and getattr(main_pkmn, "individual_id", None) == self.individual_id
        ):
            main_pkmn.held_item = held_item

    def remove_held_item(self) -> None:
        """
        Removes the held item from the Pokémon and updates the database.
        """
        if self.held_item is None:
            return

        db = services.db

        from ..utils import (
            give_item,
        )  # lazy: avoids the utils<->pokedex<->pokemon_obj cycle

        give_item(self.held_item)  # We put the item back in the item bag
        self.held_item = None

        # Save to captured_pokemon in database
        pokemon_data = db.get_pokemon(self.individual_id)
        if pokemon_data:
            pokemon_data["held_item"] = None
            db.save_pokemon(pokemon_data)

        # Also update main_pokemon if this is the main pokemon
        main_pokemon = db.get_main_pokemon()
        if main_pokemon and main_pokemon.get("individual_id") == self.individual_id:
            main_pokemon["held_item"] = None
            db.save_main_pokemon(main_pokemon)

        # Sync the in-memory main_pokemon singleton if it is the target.
        main_pkmn = services.main_pokemon
        if (
            main_pkmn is not None
            and getattr(main_pkmn, "individual_id", None) == self.individual_id
        ):
            main_pkmn.held_item = None


class PokemonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, PokemonObject):
            data = obj.__dict__.copy()
            # Convert complex types to serializable formats
            data["volatile_status"] = list(data["volatile_status"])
            data["stat_stages"] = data.get("stat_stages", {})
            data["moves"] = data.get("attacks", [])
            return data
        return super().default(obj)
