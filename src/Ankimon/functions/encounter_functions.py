from __future__ import annotations

import json
import math
import os
import random
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Optional, Union

# Tooltip import - fallback for headless test environment
try:
    from aqt.utils import tooltip
except (ImportError, ModuleNotFoundError):
    # Fallback for harness tests (no Qt)
    def tooltip(msg, period=2000):
        print(f"[Ankimon Tooltip] {msg}")

from ..services import services
from ..events import events
from ..functions.pokemon_functions import (
    find_experience_for_level,
    get_levelup_move_for_pokemon,
    pick_random_gender,
    shiny_chance,
)
from ..functions.pokedex_functions import (
    check_evolution_for_pokemon,
    get_all_pokemon_moves,
    get_base_experience,
    get_effort_values,
    get_growth_rate,
    get_pretty_name_for_name,
    return_name_for_id,
    safe_int,
    search_pokedex,
    search_pokedex_by_id,
)
from ..functions.friendship_evolution import (
    check_friendship_evolution_for_pokemon,
)
from ..pyobj.error_handler import show_warning_with_traceback
from ..functions.trainer_functions import xp_share_gain_exp
from ..functions.badges_functions import check_for_badge, receive_badge
from ..functions.drawing_utils import tooltipWithColour
from ..utils import (
    get_ev_spread,
    is_alive,
    limit_ev_yield,
    load_collected_pokemon_ids,
    play_effect_sound,
    scale_ev_spread_to_level,
)
from ..business import calc_experience, calculate_cp_from_dict
from ..const import gen_ids
from . import encounter_data

if TYPE_CHECKING:
    # Type-hint-only imports (several pull in Qt). `from __future__ import
    # annotations` keeps the signatures below as strings so these never run.
    from ..pyobj.ankimon_tracker import AnkimonTracker
    from ..pyobj.pokemon_obj import PokemonObject
    from ..pyobj.reviewer_obj import Reviewer_Manager
    from ..pyobj.test_window import TestWindow
    from ..pyobj.trainer_card import TrainerCard
    from ..pyobj.InfoLogger import ShowInfoLogger
    from ..pyobj.evolution_window import EvoWindow
    from ..pyobj.translator import Translator

# Shared singletons used as bare module globals below. They are bound to the live
# registry objects by core.bind_runtime_globals() after composition, exactly as
# the old `from ..singletons import ...` did (a snapshot of stable objects).
# Function parameters of the same name shadow these where the faint/defeat
# helpers operate on the Pokemon passed in.
main_pokemon = None
ankimon_tracker_obj = None
trainer_card = None
settings_obj = None
translator = None
ankimon_db = None
pokemon_pc = None


ALL_NATURES = [
    "Hardy",
    "Lonely",
    "Brave",
    "Adamant",
    "Naughty",
    "Bold",
    "Docile",
    "Relaxed",
    "Impish",
    "Lax",
    "Timid",
    "Hasty",
    "Serious",
    "Jolly",
    "Naive",
    "Modest",
    "Mild",
    "Quiet",
    "Bashful",
    "Rash",
    "Calm",
    "Gentle",
    "Sassy",
    "Careful",
    "Quirky",
]


_regional_lookup_built = False


def _build_regional_lookup() -> None:
    """Populates encounter_data.REGIONAL_FORM_LOOKUP from pokedex.json.

    Maps species_id -> {region -> [actual_id, ...]} for all encounterable
    regional forms. Called lazily on first use via
    :func:`_get_regional_form_lookup` (import-time file IO is not allowed on
    main). Silently no-ops if pokedex.json is unavailable (e.g. first-run
    before data files exist).
    """
    try:
        from ..functions.pokedex_functions import _load_pokedex_cache

        pokedex = _load_pokedex_cache()
        aid_to_sid: dict[int, int] = {
            v["actual_id"]: v["species_id"]
            for v in pokedex.values()
            if "actual_id" in v and "species_id" in v
        }
        for region, aids in encounter_data.REGIONAL_FORMS.items():
            for aid in aids:
                sid = aid_to_sid.get(aid)
                if sid is None:
                    continue
                region_map = encounter_data.REGIONAL_FORM_LOOKUP.setdefault(sid, {})
                region_map.setdefault(region, []).append(aid)
    except Exception as e:
        print(f"[Ankimon] Warning: Could not build regional form lookup: {e}")


def _get_regional_form_lookup():
    """Return encounter_data.REGIONAL_FORM_LOOKUP, building it on first use.

    Lazy re-expression of exp's module-level ``_build_regional_lookup()`` call:
    the first encounter that needs regional-form data triggers the (one-off)
    pokedex.json read; importing this module stays IO-free.
    """
    global _regional_lookup_built
    if not _regional_lookup_built:
        _build_regional_lookup()
        _regional_lookup_built = True
    return encounter_data.REGIONAL_FORM_LOOKUP


def _in_bulk_resolve() -> bool:
    """True while the mobile-sync bulk resolver is replaying reviews.

    ``utils.in_bulk_resolve`` is set by the mobile-review sync leaf (F29),
    which has not landed on main yet — ``getattr`` keeps this a safe ``False``
    until it does. While bulk-resolving, per-battle tooltips/dialogs are
    suppressed so replaying a large backlog cannot spam the UI.
    """
    from .. import utils

    return bool(getattr(utils, "in_bulk_resolve", False))


_percentages_cache = {
    "percentages": None,
    "total_reviews": None,
    "trainer_level": None,
    "main_pokemon_level": None,
    "daily_average": None,
}

# ==============================================================================
# ENCOUNTER OVERHAUL CONFIGURATION (DEVELOPER TOGGLES & BALANCING COEFFICIENTS)
# ==============================================================================
USE_OVERHAUL_ENCOUNTER_SYSTEM = False

# EP Mastery Index Components Weights
EP_WEIGHT_TRAINER_LEVEL = 0.25  # T_norm weight
EP_WEIGHT_DEX_COMPLETION = 0.25  # D_norm weight
EP_WEIGHT_SESSION_PROGRESS = 0.25  # S_norm weight
EP_WEIGHT_CORE_TEAM_POWER = 0.25  # C_norm weight

# Scale limits for EP components
TRAINER_LEVEL_CAP = 50.0
CORE_TEAM_POWER_CAP = 16000.0

# Master Rarity Parameters: (Beginner Base Rate, Master Max Rate)
OVERHAUL_TIER_PARAMS = {
    "Normal": (96.98, 84.70),
    "Baby": (2.30, 3.0),
    "Ultra": (0.35, 4.50),
    "Gmax": (0.15, 2.50),
    "Starter": (0.10, 1.80),
    "Mega": (0.05, 1.50),
    "Legendary": (0.05, 1.50),
    "Mythical": (0.02, 0.50),
}

# Main Pokémon level thresholds for unlocking tiers
OVERHAUL_LEVEL_THRESHOLDS = {
    "Ultra": 30,
    "Legendary": 50,
    "Mega": 60,
    "Gmax": 65,
    "Mythical": 75,
    "Starter": 80,
}

# Dry spell thresholds for independent pity (Pi reviews)
OVERHAUL_PITY_THRESHOLDS = {
    "Ultra": 100,
    "Gmax": 150,
    "Starter": 175,
    "Mega": 200,
    "Legendary": 200,
    "Mythical": 400,
}

# Pity divisor/scaling factor (the quadratic denominator)
OVERHAUL_PITY_DIVISOR = 50.0
# ==============================================================================

# AUTO-BATTLE OVERRIDE SYSTEM
# Override state for auto-battle: None, "catch", or "defeat"
_auto_battle_override: Optional[Literal["catch", "defeat"]] = None


def get_auto_battle_setting(settings_source=None) -> int:
    """Read ``battle.automatic_battle`` clamped to the valid [0, 3] range.

    Centralizes the parse-with-fallback logic that used to be copy-pasted
    across ``handle_enemy_faint`` and the reviewer-button shortcut functions
    (with drifting exception handling between the copies). Falls back to 0
    (manual mode) on any missing/non-numeric/out-of-range value.
    """
    source = settings_source if settings_source is not None else settings_obj
    try:
        value = int(source.get("battle.automatic_battle"))
        if not (0 <= value <= 3):
            return 0
        return value
    except (ValueError, TypeError):
        return 0


def toggle_auto_battle_override(
    action: Literal["catch", "defeat"],
) -> Optional[Literal["catch", "defeat"]]:
    """
    Toggle the auto-battle override for the current encounter.
    
    Args:
        action: "catch" or "defeat"
    
    Returns:
        Current override state as a string: None, "catch", or "defeat"
    """
    global _auto_battle_override
    
    # If the same action is already set, clear it (toggle off)
    if _auto_battle_override == action:
        _auto_battle_override = None
        tooltip(f"Override removed: Auto-battle behavior restored")
    else:
        # Set the new override
        _auto_battle_override = action
        action_display = "Catch" if action == "catch" else "Defeat"
        tooltip(f"Override set: Will {action_display} when fainted!")
    
    return _auto_battle_override


def get_auto_battle_override() -> Optional[Literal["catch", "defeat"]]:
    """Get the current override state."""
    return _auto_battle_override


def clear_auto_battle_override() -> None:
    """Clear the override state (called when a new encounter starts)."""
    global _auto_battle_override
    _auto_battle_override = None

def calculate_mastery_index_ep(total_reviews, daily_average, trainer_level):
    """
    Calculate the Encounter Potential (EP) Mastery Index (0.0 to 100.0).
    EP = 0.25 * T_norm + 0.25 * D_norm + 0.25 * S_norm + 0.25 * C_norm
    """
    # 1. T_norm (Trainer Level)
    level_val = trainer_level if trainer_level is not None else 1
    t_norm = min((level_val / TRAINER_LEVEL_CAP) * 100.0, 100.0)

    # 2. D_norm (Pokedex Completion)
    d_norm = 0.0
    try:
        from ..functions.pokedex_functions import _load_pokedex_cache

        pokedex_data = _load_pokedex_cache()
        db = services.db
        if pokedex_data and db is not None:
            caught_ids = db.get_all_pokemon_ids()
            caught_species = set()
            for pid in caught_ids:
                if pid >= 10000:
                    name = search_pokedex_by_id(pid)
                    if name and name != "Pokémon not found":
                        base_id = safe_int(search_pokedex(name, "species_id"))
                        if base_id:
                            caught_species.add(base_id)
                else:
                    caught_species.add(pid)

            unique_species_in_game = {
                safe_int(v.get("species_id"))
                for v in pokedex_data.values()
                if v.get("species_id")
            }
            unique_species_in_game.discard(0)
            total_species_count = (
                len(unique_species_in_game) if unique_species_in_game else 1
            )
            d_norm = (
                len(caught_species & unique_species_in_game) / total_species_count
            ) * 100.0
    except Exception as e:
        print(f"[Ankimon] Warning: Error calculating Dex Completion for EP: {e}")

    # 3. S_norm (Session Progress)
    daily_goal = daily_average if daily_average and daily_average > 0 else 100.0
    s_norm = min((total_reviews / daily_goal) * 100.0, 100.0)

    # 4. C_norm (Core Team Power)
    c_norm = 0.0
    try:
        db = services.db
        if db is not None:
            all_pkmn = db.get_all_pokemon()
            if all_pkmn:
                cps = []
                for p in all_pkmn:
                    try:
                        cp = calculate_cp_from_dict(p)
                        cps.append(cp)
                    except Exception:
                        pass
                cps.sort(reverse=True)
                top_6 = cps[:6]
                avg_top_6_cp = sum(top_6) / len(top_6) if top_6 else 0.0
                c_norm = min((avg_top_6_cp / CORE_TEAM_POWER_CAP) * 100.0, 100.0)
    except Exception as e:
        print(f"[Ankimon] Warning: Error calculating Core Team Power for EP: {e}")

    ep = (
        (EP_WEIGHT_TRAINER_LEVEL * t_norm)
        + (EP_WEIGHT_DEX_COMPLETION * d_norm)
        + (EP_WEIGHT_SESSION_PROGRESS * s_norm)
        + (EP_WEIGHT_CORE_TEAM_POWER * c_norm)
    )
    return max(0.0, min(ep, 100.0))


def load_pity_trackers(db=None) -> dict:
    """Load the per-tier dry-spell counters persisted via the user_data table.

    ``db`` defaults to :data:`services.db`. The encounter-rate simulator (F23)
    injects an explicit provider so it can read the live pity counters
    read-only without mutating the global service registry.
    """
    default_pity = {
        "Ultra": 0,
        "Gmax": 0,
        "Starter": 0,
        "Mega": 0,
        "Legendary": 0,
        "Mythical": 0,
    }
    try:
        if db is None:
            db = services.db
        if db is not None:
            stored = db.get_user_data("ankimon_pity_trackers")
            if isinstance(stored, dict):
                for k in default_pity:
                    if k in stored:
                        default_pity[k] = int(stored[k])
    except Exception as e:
        print(f"[Ankimon] Warning: Error loading pity trackers: {e}")
    return default_pity


def save_pity_trackers(trackers: dict):
    """Persist the per-tier dry-spell counters via the user_data table."""
    try:
        db = services.db
        if db is not None:
            db.set_user_data("ankimon_pity_trackers", trackers)
    except Exception as e:
        print(f"[Ankimon] Warning: Error saving pity trackers: {e}")


def _modify_percentages_overhaul(
    total_reviews, daily_average, trainer_level, *, main_level=None, ep=None, db=None
):
    """
    Overhaul calculation for encounter percentages based on the Mastery Index (EP),
    Exponential Rarity Scaling, and Independent Pity systems.

    The ``main_level``/``ep``/``db`` arguments are simulation-injection hooks used
    by the encounter-rate simulator (F23). They all default to the live behaviour
    so ordinary callers are unaffected:

    * ``ep`` — a pre-computed Mastery Index (0-100). When ``None`` it is derived
      from live pokedex/team state via :func:`calculate_mastery_index_ep`.
    * ``main_level`` — main-Pokémon level for the tier gates. When ``None`` the
      live ``main_pokemon`` global is used.
    * ``db`` — read-only provider for the pity lookup; defaults to services.db.
    """
    if ep is None:
        ep = calculate_mastery_index_ep(total_reviews, daily_average, trainer_level)

    # Generate base weights
    weights = {}
    for tier, (base, max_val) in OVERHAUL_TIER_PARAMS.items():
        weights[tier] = base * ((max_val / base) ** (ep / 100.0))

    # Apply level thresholds
    if main_level is not None:
        level_val = main_level
    else:
        level_val = (
            main_pokemon.level
            if main_pokemon
            and hasattr(main_pokemon, "level")
            and main_pokemon.level is not None
            else 1
        )
    for tier, limit in OVERHAUL_LEVEL_THRESHOLDS.items():
        if level_val < limit:
            weights[tier] = 0.0

    # Apply pity multipliers (read-only; simulation never persists trackers)
    pity_trackers = load_pity_trackers(db=db)
    for tier in OVERHAUL_PITY_THRESHOLDS:
        p_i = pity_trackers.get(tier, 0)
        t_i = OVERHAUL_PITY_THRESHOLDS[tier]
        multiplier = 1.0 + (max(0, (p_i - t_i) / OVERHAUL_PITY_DIVISOR)) ** 2
        weights[tier] = weights[tier] * multiplier

    total_sum = sum(weights.values())
    percentages = {}
    for tier in weights:
        percentages[tier] = (
            (weights[tier] / total_sum) * 100.0 if total_sum > 0.0 else 0.0
        )

    return percentages


def modify_percentages(total_reviews, daily_average, trainer_level):
    """
    Modify Pokémon encounter percentages based on total reviews, trainer level, and main Pokémon level.

    Thin dispatcher: routes to the overhaul or legacy calculation based on the
    active system flag. The simulator (F23) calls the ``_modify_percentages_*``
    helpers directly with injected state, so it never has to flip this module's
    ``USE_OVERHAUL_ENCOUNTER_SYSTEM`` global.
    """
    if USE_OVERHAUL_ENCOUNTER_SYSTEM:
        return _modify_percentages_overhaul(total_reviews, daily_average, trainer_level)
    return _modify_percentages_legacy(total_reviews, daily_average, trainer_level)


def _modify_percentages_legacy(
    total_reviews, daily_average, trainer_level, *, main_level=None
):
    """
    Legacy calculation for encounter percentages (review-ratio buckets +
    trainer-level boost + main-Pokémon-level tier gates).

    ``main_level`` is a simulation-injection hook (F23); when ``None`` the live
    ``main_pokemon`` global is used and the single-slot performance cache is
    active. When a level is injected the cache is bypassed entirely so the
    simulator can never read or poison the live gameplay cache.
    """
    # None-safe: the module global is bound by core.bind_runtime_globals()
    # after composition; fall back to level 1 while unbound (mirrors the
    # guard in _modify_percentages_overhaul).
    if main_level is not None:
        main_pokemon_level = main_level
    else:
        main_pokemon_level = main_pokemon.level if main_pokemon is not None else 1

    # The simulator injects state; its inputs are not part of the cache key, so
    # skip the cache read/write while simulating to keep the live cache honest.
    simulating = main_level is not None

    # Performance Guard: Skip recalculation if inputs haven't changed
    if (
        not simulating
        and _percentages_cache["percentages"] is not None
        and _percentages_cache["total_reviews"] == total_reviews
        and _percentages_cache["trainer_level"] == trainer_level
        and _percentages_cache["main_pokemon_level"] == main_pokemon_level
        and _percentages_cache["daily_average"] == daily_average
    ):
        return _percentages_cache["percentages"]

    # Start with the base percentages — the 8-tier overhaul adds Starter,
    # Mega and Gmax alongside the legacy five tiers.
    percentages = {
        "Baby": 2,
        "Legendary": 0.5,
        "Mythical": 0.2,
        "Normal": 94.3,
        "Starter": 0.5,
        "Ultra": 1.5,
        "Mega": 0.5,
        "Gmax": 0.5,
    }

    # Adjust percentages based on total reviews relative to the daily average
    review_ratio = total_reviews / daily_average if daily_average > 0 else 0

    # Adjust for review progress
    if review_ratio < 0.4:
        percentages["Normal"] += (
            percentages.pop("Baby", 0)
            + percentages.pop("Legendary", 0)
            + percentages.pop("Mythical", 0)
            + percentages.pop("Ultra", 0)
            + percentages.pop("Mega", 0)
            + percentages.pop("Gmax", 0)
            + percentages.pop("Starter", 0)
        )
    elif review_ratio < 0.6:
        percentages["Baby"] += 1.5
        percentages["Normal"] -= 1.5
    elif review_ratio < 0.8:
        percentages["Ultra"] += 1.5
        percentages["Normal"] -= 1.5
    else:
        percentages["Legendary"] += 0.5
        percentages["Ultra"] += 1.5
        percentages["Mega"] += 0.5
        percentages["Gmax"] += 0.5
        percentages["Starter"] += 0.5
        percentages["Mythical"] += 0.2
        percentages["Normal"] -= 3.7

    # Restrict access to certain tiers based on main Pokémon level
    if main_pokemon_level:
        # Define level thresholds for each tier
        level_thresholds = {
            "Ultra": 30,
            "Legendary": 50,
            "Mega": 60,
            "Gmax": 65,
            "Mythical": 75,
            "Starter": 80,
        }

        # Trainer-level boost. Guarded per tier so the tiers popped into
        # "Normal" by the low-review branch above cannot raise KeyError
        # (upstream exp fix 78033e50).
        if trainer_level:
            if trainer_level > 10:
                added = 0
                for tier, boost in [
                    ("Legendary", 0.5),
                    ("Ultra", 1.5),
                    ("Mega", 0.5),
                    ("Gmax", 0.5),
                    ("Starter", 0.5),
                    ("Mythical", 0.2),
                ]:
                    if tier in percentages:
                        percentages[tier] += boost
                        added += boost
                percentages["Normal"] -= added

        for tier in ["Starter", "Ultra", "Legendary", "Mythical", "Mega", "Gmax"]:
            if tier in percentages and main_pokemon_level < level_thresholds.get(
                tier, float("inf")
            ):
                percentages[tier] = 0

    # Force starter probability to 0 and normalize
    percentages["Starter"] = 0  # Comment to activate starters
    total = sum(percentages.values())
    for tier in percentages:
        percentages[tier] = (percentages[tier] / total) * 100 if total > 0 else 0

    # Cache and return (skip while simulating so the live cache stays clean)
    if not simulating:
        _percentages_cache["percentages"] = percentages
        _percentages_cache["total_reviews"] = total_reviews
        _percentages_cache["trainer_level"] = trainer_level
        _percentages_cache["main_pokemon_level"] = main_pokemon_level
        _percentages_cache["daily_average"] = daily_average

    return percentages


def clear_encounter_cache():
    """Clear cache when needed"""
    global _percentages_cache
    _percentages_cache = {
        "percentages": None,
        "total_reviews": None,
        "trainer_level": None,
        "main_pokemon_level": None,
        "daily_average": None,
    }


def _player_owns_base_form(actual_id: int, collected_ids: set) -> bool:
    """Return True if the player owns the base species of this Mega/Gmax form."""
    name = search_pokedex_by_id(actual_id)
    if not name or name == "Pokémon not found":
        return True  # can't determine — allow through
    species_id = safe_int(search_pokedex(name, "species_id"))
    if not species_id:
        return True
    return species_id in collected_ids


def _meets_prerequisites(pokemon_id: int, collected_ids: set) -> bool:
    """Return True if all prerequisite Pokémon for this ID are collected.

    Prerequisite chains are defined in encounter_data.PREREQUISITES.
    Handles forms by checking the species_id prerequisites.
    """
    check_id = pokemon_id
    if pokemon_id not in encounter_data.PREREQUISITES:
        if pokemon_id >= 10000:
            name = search_pokedex_by_id(pokemon_id)
            species_id = safe_int(search_pokedex(name, "species_id"))
            if species_id:
                check_id = species_id

    required = encounter_data.PREREQUISITES.get(check_id)
    if not required:
        return True

    if isinstance(required, tuple) and len(required) == 2 and required[0] == "OR":
        # Any of these must be present
        return any(rid in collected_ids for rid in required[1])

    # All must be present (default behavior for sets)
    return required.issubset(collected_ids)


def get_tier(total_reviews, trainer_level=1, event_modifier=None):
    """_summary_
    Randomly picks the tier for a new enemy Pokemon to be generated from, based on weighted probabilities based on number of reviews and trainer level.

    Args:
        total_reviews (int): Number of reviews done in that Anki session.
        trainer_level (int, optional): Trainer XP level. Defaults to 1.
        event_modifier (?, optional): Unused argument. Defaults to None.

    Returns:
        choice[0]: The first choice of TIER picked randomly (by a random.choices function)
    """
    daily_average = int(settings_obj.get("battle.daily_average"))
    percentages = modify_percentages(total_reviews, daily_average, trainer_level)

    tiers = list(percentages.keys())
    probabilities = list(percentages.values())

    choice = random.choices(tiers, probabilities, k=1)
    return choice[0]


def check_min_generate_level(name):
    evoType = search_pokedex(name.lower(), "evoType")
    evoLevel = search_pokedex(name.lower(), "evoLevel")
    if evoLevel:
        min_level = safe_int(evoLevel)
    elif evoType != []:
        min_level = 100
    else:
        min_level = 1

    # Ensure special forms (Mega/Gmax) and Legendaries inherit correct level caps.
    # We check both species_id and actual_id against the rarity lists.
    species_id = safe_int(search_pokedex(name.lower(), "species_id"))
    actual_id = safe_int(search_pokedex(name.lower(), "actual_id"))

    is_mythical = (species_id in encounter_data.MYTHICAL) or (
        actual_id in encounter_data.MYTHICAL
    )
    is_legendary = (species_id in encounter_data.LEGENDARY) or (
        actual_id in encounter_data.LEGENDARY
    )
    is_ultra = (species_id in encounter_data.ULTRA) or (
        actual_id in encounter_data.ULTRA
    )
    is_starter = (species_id in encounter_data.STARTERS) or (
        actual_id in encounter_data.STARTERS
    )
    is_mega = (species_id in encounter_data.MEGA) or (actual_id in encounter_data.MEGA)
    is_gmax = (species_id in encounter_data.GMAX) or (actual_id in encounter_data.GMAX)

    if is_mythical:
        min_level = max(min_level, 75)
    elif is_gmax:
        min_level = max(min_level, 65)
    elif is_mega:
        min_level = max(min_level, 60)
    elif is_legendary:
        min_level = max(min_level, 50)
    elif is_ultra:
        min_level = max(min_level, 30)
    elif is_starter:
        min_level = max(min_level, 30)

    return min_level


def check_id_ok(id_num: Union[int, list[int]]):
    if isinstance(id_num, list):
        if len(id_num) > 0:
            id_num = id_num[0]
        else:
            return False

    if not isinstance(id_num, int):
        return False

    # Mega/Gmax forms have actual_ids >= 10000, which fall outside
    # the normal gen ranges. Resolve to base species for generation check.
    if id_num >= 10000:
        name = search_pokedex_by_id(id_num)
        if not name or name == "Pokémon not found":
            return True  # fallback

        species_id = safe_int(search_pokedex(name, "species_id"))
        gen_config = [settings_obj.get(f"misc.gen{i}") for i in range(1, 10)]

        # Check base species generation
        base_gen = 0
        for gen, max_id in gen_ids.items():
            if species_id <= max_id:
                base_gen = int(gen.split("_")[1])
                break
        if base_gen == 0:
            return True  # fallback
        if not gen_config[base_gen - 1]:
            return False  # base gen disabled

        # For regional forms, also require the form's intro gen to be enabled
        if id_num in encounter_data.REGIONAL_FORM_REGION:
            forme = search_pokedex(name, "forme") or ""
            intro_gen = None
            for f_name, g in encounter_data.REGIONAL_FORME_GEN.items():
                if f_name in forme:
                    intro_gen = g
                    break

            if intro_gen and not gen_config[intro_gen - 1]:
                return False  # regional form's intro gen disabled

        return True

    generation = 0
    for gen, max_id in gen_ids.items():
        if id_num <= max_id:
            generation = int(gen.split("_")[1])

            gen_config = [settings_obj.get(f"misc.gen{i}") for i in range(1, 10)]
            return gen_config[generation - 1]

    return False


def get_boosted_gens_for_region(region: str) -> list[int]:
    mapping = {
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
    }
    return mapping.get(region, [])


def get_boosted_pool_chance(region: str) -> float:
    return 0.40 if region == "hisui" else 0.30


def get_base_species_gen(actual_id: int) -> int:
    species_id = actual_id
    if actual_id >= 10000:
        name = search_pokedex_by_id(actual_id)
        if name and name != "Pokémon not found":
            species_id = safe_int(search_pokedex(name, "species_id")) or actual_id

    for gen, max_id in gen_ids.items():
        if species_id <= max_id:
            return int(gen.split("_")[1])
    return 0


def get_all_pokemon_in_tier(tier: str) -> list[int]:
    if tier == "Normal":
        return encounter_data.NORMAL
    if tier == "Baby":
        return encounter_data.BABY
    if tier == "Ultra":
        return encounter_data.ULTRA
    if tier == "Legendary":
        return encounter_data.LEGENDARY
    if tier == "Mythical":
        return encounter_data.MYTHICAL
    if tier == "Mega":
        return encounter_data.MEGA
    if tier == "Gmax":
        return encounter_data.GMAX
    # if tier == "Starter": return encounter_data.STARTERS #Uncomment to activate starters
    if tier == "Starter":
        return []
    return []


def generate_random_pokemon(
    main_pokemon_level: int, ankimon_tracker_obj: AnkimonTracker, *args, **kwargs
):
    """
    Generates a random wild Pokémon with attributes scaled to the level of the player's main Pokémon.

    This function resets the encounter and battle round state in the provided `AnkimonTracker` object.
    It then selects a valid Pokémon that can appear at the current level range, computes its stats,
    determines its moves, ability, and other combat-relevant characteristics, and returns all necessary
    data required for a battle.

    Args:
        main_pokemon_level (int): The level of the player's main Pokémon. Determines the level range of
            the generated wild Pokémon.
        ankimon_tracker_obj (AnkimonTracker): An object used to track battle state, such as the number
            of Pokémon encountered and cards used in the battle.

    Returns:
        tuple: A tuple containing the following elements:
            - name (str): Name of the wild Pokémon.
            - pokemon_id (int): Unique ID of the Pokémon.
            - wild_pokemon_lvl (int): The level of the generated Pokémon.
            - ability (str): The selected ability of the Pokémon.
            - pokemon_type (list[str]): List of type(s) the Pokémon belongs to.
            - base_stats (dict): Dictionary of the Pokémon's base stats.
            - moves (list[str]): List of up to 4 moves the Pokémon can use in battle.
            - base_experience (int): Experience points awarded for defeating the Pokémon.
            - growth_rate (str): Growth rate category of the Pokémon (e.g., "slow", "fast").
            - ev (dict): Effort values (EVs) for each stat.
            - iv (dict): Randomly generated individual values (IVs) for each stat.
            - gender (str): Randomly assigned gender.
            - battle_status (str): Current status of the Pokémon in battle, defaulted to "fighting".
            - final_stats (dict): Final computed stats of the Pokémon.
            - tier (str): Tier from which the Pokémon was selected (e.g., common, rare).
            - ev_yield (dict): Effort values (EVs) awarded upon defeating the Pokémon.
            - is_shiny (bool): Indicates whether the Pokémon is shiny.
            - nature (str): Randomly assigned nature.

    Raises:
        ValueError: If no valid Pokémon can be generated (highly unlikely under normal conditions).
    """
    lvl_variation = 3
    lvl_range = (
        max(1, main_pokemon_level - lvl_variation),
        max(1, main_pokemon_level + lvl_variation),
    )
    wild_pokemon_lvl = random.randint(*lvl_range)
    wild_pokemon_lvl = max(
        1, wild_pokemon_lvl
    )  # Ensures that the wild pokemon's level is at least 1
    if main_pokemon_level == 100:
        wild_pokemon_lvl = 100

    collected_ids = kwargs.get("collected_ids", None)
    if collected_ids is None and args:
        collected_ids = args[0]

    if collected_ids is None:
        collected_ids = load_collected_pokemon_ids()

    # FALLBACK HIERARCHY
    # If a rolled tier fails, try the next one in the list.
    TIER_ORDER = [
        "Mythical",
        "Mega",
        "Legendary",
        "Gmax",
        "Ultra",
        "Starter",
        "Baby",
        "Normal",
    ]
    selected_pokemon_id = None
    selected_tier = None

    # 1. Select the initial tier based on probabilities
    initial_tier = get_tier(ankimon_tracker_obj.get_total_reviews(), trainer_card.level)

    # Find starting point in fallback order
    try:
        start_idx = TIER_ORDER.index(initial_tier)
    except ValueError:
        start_idx = TIER_ORDER.index("Normal")

    active_region = settings_obj.get("misc.active_region")
    if active_region and isinstance(active_region, str):
        active_region = active_region.lower().strip()
    else:
        active_region = None

    def _variant_eligible(variant_id):
        # A regional/boosted variant must pass BOTH guards the base-pool builder
        # applies (Guard 1 generation + Guard 2 min-generate-level); the deleted
        # 500-attempt retry loop enforced the level invariant on the FINAL id.
        if not check_id_ok(variant_id):
            return False
        vname = search_pokedex_by_id(variant_id)
        if not vname or vname == "Pokémon not found":
            return False
        return wild_pokemon_lvl >= check_min_generate_level(str(vname.lower()))

    # Fallback order: try the rolled tier, then degrade STRAIGHT to the common
    # Normal tier. Walking TIER_ORDER forward (rarest -> Normal) instead cascaded
    # an emptied rare tier sideways into the next RARE tier (e.g. a Mega roll that
    # fails its base-ownership/min-level guard fell through to Legendary), which
    # over-represented rare encounters for players who can't yet access the rolled
    # tier. Degrading to Normal keeps the rolled tier's chance while making a
    # failed rare roll resolve to a common encounter, not another rare one.
    rolled_tier = TIER_ORDER[start_idx]
    fallback_tiers = [rolled_tier]
    if rolled_tier != "Normal":
        fallback_tiers.append("Normal")

    for current_tier in fallback_tiers:
        tier_ids = get_all_pokemon_in_tier(current_tier)
        full_pool = []
        for pokemon_id in tier_ids:
            name = search_pokedex_by_id(pokemon_id)
            if not name or name == "Pokémon not found":
                continue

            # Guard 1: Generation check
            if not check_id_ok(pokemon_id):
                continue

            # Guard 2: Level check
            min_allowed_pokemon_lvl = check_min_generate_level(str(name.lower()))
            if wild_pokemon_lvl < min_allowed_pokemon_lvl:
                continue

            # Guard 3: Mega/Gmax base ownership
            if current_tier in ("Mega", "Gmax") and not _player_owns_base_form(
                pokemon_id, collected_ids
            ):
                continue

            # Guard 4: Prerequisite check
            if not _meets_prerequisites(pokemon_id, collected_ids):
                continue

            full_pool.append(pokemon_id)

        if not full_pool:
            continue

        boosted_pool = []

        if active_region:
            boosted_gens = get_boosted_gens_for_region(active_region)

            for pid in full_pool:
                if get_base_species_gen(pid) in boosted_gens:
                    if pid not in boosted_pool:
                        boosted_pool.append(pid)

                # Add eligible regional variants for the active region
                options = (
                    _get_regional_form_lookup().get(pid, {}).get(active_region, [])
                )
                for opt in options:
                    if _variant_eligible(opt) and opt not in boosted_pool:
                        boosted_pool.append(opt)

        if active_region and boosted_pool:
            chance = get_boosted_pool_chance(active_region)
            if random.random() < chance:
                selected_pokemon_id = random.choice(boosted_pool)
            else:
                selected_pokemon_id = random.choice(full_pool)
        else:
            selected_pokemon_id = random.choice(full_pool)

        selected_tier = current_tier
        break

    # Final fallback if somehow everything failed (e.g. settings restrict all IDs)
    if not selected_pokemon_id:
        selected_pokemon_id = 19  # Rattata
        selected_tier = "Normal"
        warn_msg = (
            "Failed to generate a valid Pokémon. Please ensure at least one "
            "generation is enabled in the settings. Defaulting to Rattata."
        )
        if not check_id_ok(selected_pokemon_id):
            warn_msg = (
                "Failed to generate a valid Pokémon and the default (Rattata) is "
                "also restricted by the current settings. Please enable at least "
                "one generation."
            )
        try:
            services.ui.warn(warn_msg)
        except Exception:
            pass

    # --- Regional form resolution ---
    # Apply 7%-per-variant resolution for base species.
    if selected_pokemon_id < 10000:
        region_forms = _get_regional_form_lookup().get(selected_pokemon_id, {})
        eligible_variants = []
        if active_region and active_region not in ("no region", ""):
            variants = region_forms.get(active_region, [])
            for v in variants:
                if _variant_eligible(v):
                    eligible_variants.append(v)
        else:
            for variants in region_forms.values():
                for v in variants:
                    if _variant_eligible(v):
                        eligible_variants.append(v)

        num_eligible = len(eligible_variants)
        if num_eligible > 0:
            if random.random() < 0.07 * num_eligible:
                selected_pokemon_id = random.choice(eligible_variants)
    # --- End form resolution ---

    pokemon_id = selected_pokemon_id
    tier = selected_tier

    # Update pity trackers if overhaul system is active
    if USE_OVERHAUL_ENCOUNTER_SYSTEM:
        try:
            pity_trackers = load_pity_trackers()
            rare_tiers = ["Ultra", "Gmax", "Starter", "Mega", "Legendary", "Mythical"]
            if tier in rare_tiers:
                pity_trackers[tier] = 0
                for rt in rare_tiers:
                    if rt != tier:
                        pity_trackers[rt] += 1
            else:
                for rt in rare_tiers:
                    pity_trackers[rt] += 1
            save_pity_trackers(pity_trackers)
        except Exception as e:
            print(
                f"[Ankimon] Warning: Error updating pity trackers in generate_random_pokemon: {e}"
            )

    name = search_pokedex_by_id(pokemon_id)

    # Now we get all necessary information about the chosen pokemon.
    pokemon_type = search_pokedex(name, "types")
    base_experience = get_base_experience(
        search_pokedex(name, "actual_id")
    )  # Experience that the wild pokemon will give once beaten
    growth_rate = get_growth_rate(search_pokedex(name, "species_id") or pokemon_id)
    ev_yield = get_effort_values(search_pokedex(name, "actual_id"))
    gender = pick_random_gender(name)
    is_shiny = shiny_chance()
    battle_status = "fighting"
    base_stats = search_pokedex(name, "baseStats")

    all_possible_moves = get_all_pokemon_moves(name, wild_pokemon_lvl)
    if len(all_possible_moves) <= 4:
        moves = all_possible_moves
    else:
        moves = random.sample(all_possible_moves, 4)

    ability = "no_ability"  # Default value for ability
    possible_abilities = search_pokedex(name, "abilities")
    if possible_abilities:
        numeric_abilities = {k: v for k, v in possible_abilities.items() if k.isdigit()}
        if numeric_abilities:
            ability = random.choice(list(numeric_abilities.values()))

    # Full competitive spreads gave every wild Pokemon up to 510 EVs from
    # level 1, dwarfing the player's slowly-earned EVs; scale the budget to
    # the wild Pokemon's level so its "training" matches its experience.
    ev = scale_ev_spread_to_level(
        get_ev_spread(random.choice(["random", "pair", "defense", "uniform"])),
        wild_pokemon_lvl,
    )
    stat_names = ["hp", "atk", "def", "spa", "spd", "spe"]
    iv = {stat: random.randint(0, 31) for stat in stat_names}
    nature = random.choice(ALL_NATURES)
    final_stats = base_stats

    ankimon_tracker_obj.pokemon_encounter = 0  # 0: Start of Battle: 1: Current Battle
    ankimon_tracker_obj.cards_battle_round = 0  # Amount of cards in this current battle

    return (
        name,
        pokemon_id,
        wild_pokemon_lvl,
        ability,
        pokemon_type,
        base_stats,
        moves,
        base_experience,
        growth_rate,
        ev,
        iv,
        gender,
        battle_status,
        final_stats,
        tier,
        ev_yield,
        is_shiny,
        nature,
    )


def new_pokemon(
    pokemon: PokemonObject,
    test_window: TestWindow,
    ankimon_tracker: AnkimonTracker,
    reviewer_obj: Reviewer_Manager,
    update_hud: bool = False,
) -> PokemonObject:
    """
    Initializes a new wild Pokémon encounter by generating a random Pokémon,
    updating its stats, setting its HP, and preparing the battle scene.

    This function uses the player's main Pokémon level to generate an appropriately
    leveled wild Pokémon with randomized attributes. It updates the provided `pokemon`
    object with generated data, resets HP, triggers any battle scene randomization,
    and updates the reviewer interface if applicable.

    Args:
        pokemon (PokemonObject): The Pokémon object to be updated with the new wild Pokémon's data.
        test_window (TestWindow): Optional UI window to display the first encounter scene.
        ankimon_tracker (AnkimonTracker): Object tracking battle-related state and handling battle scene randomization.
        reviewer_obj (Reviewer_Manager): Manager object responsible for updating battle review elements like life bars.
        update_hud (bool): When True, immediately repaint the reviewer HUD (used
            by the manual catch/defeat flows); auto-battle transitions leave the
            repaint to the next card to avoid redundant webview evals.

    Returns:
        PokemonObject: The updated `pokemon` object representing the newly generated wild Pokémon ready for battle.
    """
    # Clear any auto-battle override from previous encounter
    clear_auto_battle_override()

    ankimon_tracker.faint_processed = False
    ankimon_tracker.caught = 0

    # Reset the battle simulation state in the battle loop
    try:
        from ..battle_loop import _state

        _state.new_state = None
        _state.mutator_full_reset = 1
    except Exception as e:
        print(f"[Ankimon] Error resetting battle state in new_pokemon: {e}")

    # Force HUD update on next card/refresh via the HUD leaf's public
    # invalidation method, so this call site never has to track the leaf's
    # private cache set. Guarded so it works both before and after that leaf
    # lands (and with the harness recording fakes).
    if reviewer_obj is not None:
        invalidate = getattr(reviewer_obj, "invalidate_hud_cache", None)
        if callable(invalidate):
            invalidate()
    (
        name,
        pkmn_id,
        level,
        ability,
        pkmn_type,
        base_stats,
        enemy_attacks,
        base_experience,
        growth_rate,
        ev,
        iv,
        gender,
        battle_status,
        battle_stats,
        tier,
        ev_yield,
        is_shiny,
        nature,
    ) = generate_random_pokemon(main_pokemon.level, ankimon_tracker_obj)
    pokemon_data = {
        "name": name,
        "id": pkmn_id,
        "level": level,
        "ability": ability,
        "type": pkmn_type,
        "base_stats": base_stats,
        "attacks": enemy_attacks,
        "base_experience": base_experience,
        "growth_rate": growth_rate,
        "ev": ev,
        "iv": iv,
        "gender": gender,
        "nature": nature,
        "battle_status": battle_status,
        "battle_stats": battle_stats,
        "stat_stages": {
            "atk": 0,
            "def": 0,
            "spa": 0,
            "spd": 0,
            "spe": 0,
            "accuracy": 0,
            "evasion": 0,
        },
        "tier": tier,
        "ev_yield": ev_yield,
        "shiny": is_shiny,
    }
    pokemon.update_stats(**pokemon_data)
    max_hp = pokemon.calculate_max_hp()
    pokemon.current_hp = max_hp
    pokemon.hp = max_hp
    pokemon.max_hp = max_hp

    ankimon_tracker.randomize_battle_scene()
    if test_window is not None:
        try:
            test_window.display_first_encounter()
        except RuntimeError:
            pass

    # Track as seen in Pokedex. mark_as_seen ships with the Ankidex leaf;
    # until it lands, fall back to a user_data-backed seen list so no
    # sighting is lost either way.
    db = services.db
    if db is not None:
        if hasattr(db, "mark_as_seen"):
            db.mark_as_seen(pkmn_id)
        else:
            try:
                seen_ids = db.get_user_data("pokedex_seen", [])
                if not isinstance(seen_ids, list):
                    seen_ids = []
                if pkmn_id not in seen_ids:
                    seen_ids.append(pkmn_id)
                    db.set_user_data("pokedex_seen", seen_ids)
            except Exception:
                pass

    # Observable: a fresh wild Pokemon appeared.
    events.emit(
        "encounter",
        pokemon=pokemon.name,
        id=pokemon.id,
        level=pokemon.level,
        tier=pokemon.tier,
        shiny=pokemon.shiny,
        hp=pokemon.hp,
        max_hp=pokemon.max_hp,
    )

    # Re-render the reviewer HUD. refresh_hud() grabs the live webview under
    # Anki and is a recorded no-op headless.
    if update_hud and reviewer_obj is not None:
        reviewer_obj.refresh_hud()

    return pokemon


def save_main_pokemon_progress(
    main_pokemon: PokemonObject,
    enemy_pokemon: PokemonObject,
    exp: int,
    achievements: dict,
    logger: ShowInfoLogger,
    evo_window: EvoWindow,
):
    if settings_obj.get("misc.remove_level_cap") is True:
        main_pokemon.xp += exp
        level_cap = None
    elif main_pokemon.level != 100:
        main_pokemon.xp += exp
        level_cap = 100
    else:
        # Cap is on AND the main is already level 100: no XP is granted, but
        # level_cap must still be defined or the while-loop condition below
        # raises NameError and crashes every post-cap defeat (upstream #402).
        level_cap = 100
    try:
        db = services.db
        main_pokemon_data = db.get_main_pokemon()
        if not main_pokemon_data:
            services.ui.warn(translator.translate("missing_mainpokemon_data"))
        else:
            # Persist the defeat count BEFORE the level-up loop so
            # defeat-count evolution conditions (minimumDefeated —
            # Pawmo/Rellor) already see this battle when
            # check_evolution_for_pokemon runs below.
            main_pokemon.pokemon_defeated += 1
            main_pokemon_data["pokemon_defeated"] = main_pokemon.pokemon_defeated
            db.save_main_pokemon(main_pokemon_data)
    except Exception as e:
        services.logger.log("error", f"Error loading main pokemon data: {str(e)}")
        show_warning_with_traceback(
            exception=e, message="Error loading main pokemon data."
        )
        return
    evolution_prompted = False
    levels_gained = 0
    while int(
        find_experience_for_level(
            main_pokemon.growth_rate,
            main_pokemon.level,
            settings_obj.get("misc.remove_level_cap"),
        )
    ) < int(main_pokemon.xp) and (level_cap is None or main_pokemon.level < level_cap):
        if levels_gained >= 10:
            logger.log("error", f"Level-up loop exceeded safety cap of 10 for {main_pokemon.name}")
            next_level_cost = int(find_experience_for_level(
                main_pokemon.growth_rate,
                main_pokemon.level,
                settings_obj.get("misc.remove_level_cap"),
            ))
            main_pokemon.xp = max(0, next_level_cost - 1)
            break
        levels_gained += 1
        current_lvl_xp_cost = int(find_experience_for_level(
            main_pokemon.growth_rate,
            main_pokemon.level,
            settings_obj.get("misc.remove_level_cap"),
        ))
        main_pokemon.level += 1
        events.emit("levelup", pokemon=main_pokemon.name, level=main_pokemon.level)
        msg = ""
        msg += f"Your {main_pokemon.name} is now level {main_pokemon.level} !"
        color = "#6A4DAC"  # pokemon leveling info color for tooltip
        check = check_for_badge(achievements, 5)
        if check is False:
            achievements = receive_badge(5, achievements)
        try:
            services.logger.game_log(f"Level Up: {msg}")
            if not _in_bulk_resolve():
                tooltipWithColour(msg, color)
        except:
            pass
        if not _in_bulk_resolve():
            if settings_obj.get("gui.pop_up_dialog_message_on_defeat") is True:
                logger.log_and_showinfo("info", f"{msg}")
        main_pokemon.xp = int(max(0, int(main_pokemon.xp) - current_lvl_xp_cost))

        # Request to open the pokemon evo window
        evo_id = check_evolution_for_pokemon(
            main_pokemon.individual_id,
            main_pokemon.id,
            main_pokemon.level,
            evo_window,
            main_pokemon.everstone,
            getattr(main_pokemon, "evolution_rejected", False),
        )
        if evo_id is not None:
            evolution_prompted = True
            events.emit(
                "evolution_offered",
                pokemon=main_pokemon.name,
                trigger="level",
                evo_id=evo_id,
            )
            # None-safe: return_name_for_id can return None for an unknown id, so
            # fall back to the numeric id instead of crashing on .capitalize()
            # (mirrors the friendship-evolution path below).
            evo_display_name = return_name_for_id(evo_id)
            evo_display_name = (
                evo_display_name.capitalize() if evo_display_name else str(evo_id)
            )
            if not _in_bulk_resolve():
                logger.log_and_showinfo(
                    "info",
                    translator.translate(
                        "pokemon_about_to_evolve",
                        main_pokemon_name=main_pokemon.name,
                        evo_pokemon_name=evo_display_name,
                        main_pokemon_level=main_pokemon.level,
                    ),
                )

        if main_pokemon_data:
            mainpkmndata = main_pokemon_data
            if mainpkmndata["name"] == main_pokemon.name.capitalize():
                attacks = mainpkmndata["attacks"]
                new_attacks = get_levelup_move_for_pokemon(
                    main_pokemon.name.lower(), int(main_pokemon.level)
                )
                if new_attacks:
                    msg = ""
                    msg += translator.translate(
                        "mainpokemon_can_learn_new_attack",
                        main_pokemon_name=main_pokemon.name.capitalize(),
                    )
                for new_attack in new_attacks:
                    if len(attacks) < 4 and new_attack not in attacks:
                        attacks.append(new_attack)
                        msg += translator.translate(
                            "mainpokemon_learned_new_attack",
                            new_attack_name=new_attack,
                            main_pokemon_name=main_pokemon.name.capitalize(),
                        )
                        color = "#6A4DAC"
                        if not _in_bulk_resolve():
                            tooltipWithColour(msg, color)
                            if (
                                settings_obj.get("gui.pop_up_dialog_message_on_defeat")
                                is True
                            ):
                                logger.log_and_showinfo("info", f"{msg}")
                    elif new_attack not in attacks:
                        if _in_bulk_resolve():
                            # Never pop a move-replace dialog while replaying a
                            # bulk backlog; log and discard instead.
                            try:
                                logger.log(
                                    "info",
                                    f"[Bulk Resolve] Discarded learning new move {new_attack} on {main_pokemon.name}.",
                                )
                            except Exception:
                                pass
                            continue
                        # Ask via the UI port (real dialog under Anki; scripted
                        # choice headless). None => discard the new move.
                        selected_attack = services.ui.choose_attack_to_replace(
                            attacks, new_attack
                        )
                        if selected_attack is not None:
                            index_to_replace = None
                            for index, attack in enumerate(attacks):
                                if attack == selected_attack:
                                    index_to_replace = index
                            # If the attack is found, replace it with 'new_attack'
                            if index_to_replace is not None:
                                attacks[index_to_replace] = new_attack
                                logger.log_and_showinfo(
                                    "info",
                                    f"Replaced '{selected_attack}' with '{new_attack}'",
                                )
                            else:
                                logger.log_and_showinfo(
                                    "info", f"'{selected_attack}' not found in the list"
                                )
                        else:
                            # User declined to replace a move; discard the new one.
                            logger.log_and_showinfo(
                                "info", f"{new_attack} will be discarded."
                            )
                mainpkmndata["attacks"] = attacks
    experience_till_next_level = int(
        find_experience_for_level(
            main_pokemon.growth_rate,
            main_pokemon.level,
            settings_obj.get("misc.remove_level_cap"),
        )
    )
    msg = ""
    msg += translator.translate(
        "mainpokemon_gained_xp",
        main_pokemon_name=main_pokemon.name,
        exp=exp,
        experience_till_next_level=experience_till_next_level,
        main_pokemon_xp=main_pokemon.xp,
    )
    color = "#a17cf7"  # pokemon leveling info color for tooltip
    if not _in_bulk_resolve():
        tooltipWithColour(msg, color)
        if settings_obj.get("gui.pop_up_dialog_message_on_defeat") is True:
            logger.log_and_showinfo("info", f"{msg}")

    # Load existing Pokémon data if it exists
    if main_pokemon_data:
        mainpkmndata = main_pokemon_data
        mainpkmndata["stats"] = main_pokemon.stats
        mainpkmndata["xp"] = int(main_pokemon.xp)
        mainpkmndata["level"] = int(main_pokemon.level)
        # Clone raw EV yield to avoid mutating the in-memory enemy template
        raw_ev_yield = enemy_pokemon.ev_yield.copy()

        # Normalize keys to the standard long form expected by limit_ev_yield
        normalized_yield = {
            "hp": raw_ev_yield.get("hp", 0),
            "attack": raw_ev_yield.get("attack", 0) + raw_ev_yield.get("atk", 0),
            "defense": raw_ev_yield.get("defense", 0) + raw_ev_yield.get("def", 0),
            "special-attack": raw_ev_yield.get("special-attack", 0)
            + raw_ev_yield.get("spa", 0),
            "special-defense": raw_ev_yield.get("special-defense", 0)
            + raw_ev_yield.get("spd", 0),
            "speed": raw_ev_yield.get("speed", 0) + raw_ev_yield.get("spe", 0),
        }

        held_item = mainpkmndata.get("held_item", main_pokemon.held_item)

        # Apply EV-boosting held items
        if held_item == "macho-brace":
            for stat in normalized_yield:
                normalized_yield[stat] *= 2
        else:
            power_item_mapping = {
                "power-weight": "hp",
                "power-bracer": "attack",
                "power-belt": "defense",
                "power-lens": "special-attack",
                "power-band": "special-defense",
                "power-anklet": "speed",
            }
            if held_item in power_item_mapping:
                stat_to_boost = power_item_mapping[held_item]
                normalized_yield[stat_to_boost] += 8

        ev_yield = limit_ev_yield(mainpkmndata["ev"], normalized_yield)
        mainpkmndata["ev"]["hp"] += ev_yield["hp"]
        mainpkmndata["ev"]["atk"] += ev_yield["attack"]
        mainpkmndata["ev"]["def"] += ev_yield["defense"]
        mainpkmndata["ev"]["spa"] += ev_yield["special-attack"]
        mainpkmndata["ev"]["spd"] += ev_yield["special-defense"]
        mainpkmndata["ev"]["spe"] += ev_yield["speed"]
        # Mirror EV gain onto the in-memory object so CP/stats reads
        # stay consistent with the persisted dict until next restart.
        # Dict-item mutation doesn't fire __setattr__, so invalidate
        # the CP cache explicitly.
        main_pokemon.ev["hp"] += ev_yield["hp"]
        main_pokemon.ev["atk"] += ev_yield["attack"]
        main_pokemon.ev["def"] += ev_yield["defense"]
        main_pokemon.ev["spa"] += ev_yield["special-attack"]
        main_pokemon.ev["spd"] += ev_yield["special-defense"]
        main_pokemon.ev["spe"] += ev_yield["speed"]
        main_pokemon.invalidate_cp_cache()
        mainpkmndata["current_hp"] = int(main_pokemon.hp)
        # Friendship is uncapped — it keeps climbing past MAX_FRIENDSHIP (400) so
        # players can flex a super-bonded Pokémon. The progress bar still fills at
        # MAX_FRIENDSHIP; the raw number above it is what keeps growing.
        main_pokemon.friendship += random.randint(5, 9)
        mainpkmndata["friendship"] = main_pokemon.friendship
        # Skip the friendship-evolution offer when the evo window is dead/None
        # (F31 lazy singletons can leave services.evo_window None):
        # check_friendship_evolution_for_pokemon calls evo_window.ask_pokemon_evo(...)
        # unguarded, and an AttributeError here would abort save_main_pokemon_progress
        # before the final ankimon_db.save_main_pokemon(...) below — silently
        # discarding this defeat's level/xp/EV/friendship persistence.
        if not evolution_prompted and evo_window is not None:
            friendship_evo_id = check_friendship_evolution_for_pokemon(
                main_pokemon.individual_id,
                main_pokemon.id,
                evo_window,
                main_pokemon.everstone,
                main_pokemon.friendship,
                getattr(main_pokemon, "evolution_rejected", False),
            )
            if friendship_evo_id is not None:
                evolution_prompted = True
                events.emit(
                    "evolution_offered",
                    pokemon=main_pokemon.name,
                    trigger="friendship",
                    evo_id=friendship_evo_id,
                )
                # return_name_for_id can return None (and pop a spurious warning)
                # if the evolved id is missing from the name CSV; guard the
                # .capitalize() so a data gap can't crash the defeat flow.
                friendship_evo_name = return_name_for_id(friendship_evo_id)
                friendship_evo_name = (
                    friendship_evo_name.capitalize()
                    if friendship_evo_name
                    else str(friendship_evo_id)
                )
                if not _in_bulk_resolve():
                    logger.log_and_showinfo(
                        "info",
                        translator.translate(
                            "pokemon_about_to_evolve_friendship",
                            main_pokemon_name=main_pokemon.name,
                            evo_pokemon_name=friendship_evo_name,
                        ),
                    )
        mainpkmndata["pokemon_defeated"] = main_pokemon.pokemon_defeated
        if hasattr(main_pokemon, "tier"):
            mainpkmndata["tier"] = main_pokemon.tier
        if hasattr(main_pokemon, "is_favorite"):
            mainpkmndata["is_favorite"] = main_pokemon.is_favorite

        # Save to database (replaces JSON file I/O for performance)
        ankimon_db.save_main_pokemon(mainpkmndata)

    return main_pokemon.level


# --- Utility: Sync mainpokemon to mypokemon ---
def sync_mainpokemon_to_mypokemon(main_pokemon):
    """
    Update the relevant entry in mypokemon database with the latest values from mainpokemon.
    Uses database instead of JSON files.
    """
    db = services.db

    # Get main pokemon from database
    main_entry = db.get_main_pokemon()
    if not main_entry:
        return

    main_id = main_entry.get("individual_id", None)
    if not main_id:
        main_id = getattr(main_pokemon, "individual_id", None)
    if not main_id:
        return

    # Save/update in captured_pokemon table
    db.save_pokemon(main_entry)
    return


def kill_pokemon(
    main_pokemon: PokemonObject,
    enemy_pokemon: PokemonObject,
    evo_window: EvoWindow,
    logger: ShowInfoLogger,
    achievements: dict,
    trainer_card: Union[TrainerCard, None] = None,
):
    events.emit(
        "defeat",
        pokemon=enemy_pokemon.name,
        id=enemy_pokemon.id,
        level=enemy_pokemon.level,
        tier=enemy_pokemon.tier,
    )
    if trainer_card is not None:
        trainer_card.gain_xp(
            enemy_pokemon.tier, settings_obj.get("controls.allow_to_choose_moves")
        )

    # Calculate experience based on whether moves are chosen manually
    exp = calc_experience(enemy_pokemon.base_experience, enemy_pokemon.level)
    if settings_obj.get("controls.allow_to_choose_moves"):
        exp *= 0.5

    # Ensure exp is at least 1 and round up if it's a decimal
    exp = max(1, math.ceil(exp))

    # Handle XP share logic
    xp_share_individual_id = settings_obj.get("trainer.xp_share")
    if xp_share_individual_id:
        try:
            exp = xp_share_gain_exp(
                logger,
                settings_obj,
                evo_window,
                main_pokemon.id,
                exp,
                xp_share_individual_id,
            )
        except Exception as e:
            # xp_share_gain_exp's evolution check calls evo_window.ask_pokemon_evo(...)
            # unguarded; a dead/None window (F31 lazy singletons) can raise here.
            # Never let the XP-share side quest abort the main Pokemon's own
            # progress persistence below — fall back to the standard 50% share.
            services.logger.log("error", f"XP-share evolution check failed: {e}")
            exp = int(exp * 0.5)

    msg = ""

    if main_pokemon.held_item == "lucky-egg":
        exp = int(exp * 1.5)
        msg += f"{main_pokemon.name}'s Lucky Egg boosts its XP gained!\n"

    logger.log("info", msg)

    # Save main Pokémon's progress
    main_pokemon.level = save_main_pokemon_progress(
        main_pokemon,
        enemy_pokemon,
        exp,
        achievements,
        logger,
        evo_window,
    )

    ankimon_tracker_obj.general_card_count_for_battle = 0


def save_caught_pokemon(
    enemy_pokemon: PokemonObject,
    nickname: Union[str, None] = None,
    achievements: Union[dict, None] = None,
):
    # Create a dictionary to store the Pokémon's data
    # add all new values like hp as max_hp, evolution_data, description and growth rate
    if enemy_pokemon.tier is not None and achievements is not None:
        if enemy_pokemon.tier == "Normal":
            check = check_for_badge(achievements, 17)
            if check is False:
                achievements = receive_badge(17, achievements)
        elif enemy_pokemon.tier == "Baby":
            check = check_for_badge(achievements, 18)
            if check is False:
                achievements = receive_badge(18, achievements)
        elif enemy_pokemon.tier == "Ultra":
            check = check_for_badge(achievements, 8)
            if check is False:
                achievements = receive_badge(8, achievements)
        elif enemy_pokemon.tier == "Legendary":
            check = check_for_badge(achievements, 9)
            if check is False:
                achievements = receive_badge(9, achievements)
        elif enemy_pokemon.tier == "Mythical":
            check = check_for_badge(achievements, 10)
            if check is False:
                achievements = receive_badge(10, achievements)

    # enemy_pokemon.stats["xp"] = 0
    enemy_pokemon.xp = 0
    # Use to_dict() so the caught record shares the canonical shape with
    # saved main Pokemon (includes base_stats, level-scaled stats, cp,
    # nature). Then override caught-only fields.
    _max_hp = enemy_pokemon.calculate_max_hp()
    caught_pokemon = enemy_pokemon.to_dict()
    caught_pokemon.update(
        {
            # Keep the raw pokedex name — capitalizing breaks form lookups
            # (e.g. "rattata-alola", "charizard-gmax"); pretty names are a
            # display concern (get_pretty_name_for_name).
            "name": enemy_pokemon.name,
            "nickname": nickname or "",
            "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            "friendship": 0,
            "pokemon_defeated": 0,
            "xp": 0,
            "everstone": False,
            "captured_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "individual_id": str(uuid.uuid4()),
            "mega": False,
            "special_form": None,
            "is_favorite": False,
            "held_item": None,
            "hp": _max_hp,
            "current_hp": _max_hp,
        }
    )
    # Recompute CP against the overridden (zeroed) EVs.
    caught_pokemon["cp"] = calculate_cp_from_dict(caught_pokemon)

    # Save to database (replaces JSON file I/O for performance)
    save_success = ankimon_db.save_pokemon(caught_pokemon)

    # Only award Badge 7 ("First Pokemon Caught !") if the save was successful
    if save_success and achievements is not None:
        check = check_for_badge(achievements, 7)
        if check is False:
            achievements = receive_badge(7, achievements)

    try:
        from ..singletons import notify_stats_changed
        notify_stats_changed()
    except Exception:
        pass


def catch_pokemon(
    enemy_pokemon: PokemonObject,
    ankimon_tracker_obj: AnkimonTracker,
    logger: Union[ShowInfoLogger, None] = None,
    nickname: Union[str, None] = None,
    collected_pokemon_ids: Union[set, None] = None,
    achievements: Union[dict, None] = None,
):
    ankimon_tracker_obj.caught += 1
    if ankimon_tracker_obj.caught > 1:
        if settings_obj.get("gui.pop_up_dialog_message_on_defeat") is True:
            logger.log_and_showinfo(
                "info", translator.translate("already_caught_pokemon")
            )  # Display a message when the Pokémon is caught
        return

    # If we arrive here, this means that ankimon_tracker_obj.caught == 1
    if not nickname:
        nickname = get_pretty_name_for_name(enemy_pokemon.name)
    if collected_pokemon_ids is not None:
        collected_pokemon_ids.add(enemy_pokemon.id)  # Update cache
    save_caught_pokemon(enemy_pokemon, nickname, achievements)
    events.emit(
        "catch",
        pokemon=enemy_pokemon.name,
        id=enemy_pokemon.id,
        shiny=enemy_pokemon.shiny,
        tier=enemy_pokemon.tier,
        nickname=nickname,
    )

    ankimon_tracker_obj.general_card_count_for_battle = 0

    msg = translator.translate(
        "caught_wild_pokemon",
        enemy_pokemon_name=get_pretty_name_for_name(enemy_pokemon.name),
    )

    if settings_obj.get("gui.pop_up_dialog_message_on_defeat") is True:
        if logger is not None:
            logger.log_and_showinfo(
                "info", f"{msg}"
            )  # Display a message when the Pokémon is caught

    color = "#a17cf7"  # 6A4DAC" #pokemon leveling info color for tooltip
    try:
        tooltipWithColour(msg, color)
    except Exception as e:
        if logger is not None:
            show_warning_with_traceback(
                exception=e, message="Error while catching Pokemon:"
            )  # Display a message when the Pokémon is caught

    if is_alive(pokemon_pc):
        pokemon_pc.refresh_pokemon_grid()


def _enemy_protected_by_auto_catch(enemy_pokemon: PokemonObject) -> bool:
    """Whether enemy_pokemon's tier is covered by an "always auto-catch" setting.

    Legendary/Mythical/Ultra/Starter/Mega/Gmax/Regional Pokémon are each
    exempted from being defeated (auto or override) via their own
    battle.auto_catch_* setting, defaulting to on.
    """
    is_mega = enemy_pokemon.id in encounter_data.MEGA
    is_gmax = enemy_pokemon.id in encounter_data.GMAX
    is_regional = enemy_pokemon.id in encounter_data.REGIONAL_FORM_REGION
    is_legendary = enemy_pokemon.tier == "Legendary"
    is_mythical = enemy_pokemon.tier == "Mythical"
    is_ultra = enemy_pokemon.tier == "Ultra"
    is_starter = enemy_pokemon.tier == "Starter"

    return (
        (is_legendary and settings_obj.get("battle.auto_catch_legendary", True))
        or (is_mythical and settings_obj.get("battle.auto_catch_mythical", True))
        or (is_ultra and settings_obj.get("battle.auto_catch_ultra", True))
        or (is_starter and settings_obj.get("battle.auto_catch_starter", True))
        or (is_mega and settings_obj.get("battle.auto_catch_mega", True))
        or (is_gmax and settings_obj.get("battle.auto_catch_gmax", True))
        or (is_regional and settings_obj.get("battle.auto_catch_regional", True))
    )


def handle_enemy_faint(
    main_pokemon: PokemonObject,
    enemy_pokemon: PokemonObject,
    collected_pokemon_ids: set,
    test_window: TestWindow,
    evo_window: EvoWindow,
    reviewer_obj: Reviewer_Manager,
    logger: ShowInfoLogger,
    achievements: dict,
):
    """
    Handles what automatically happens when the enemy Pokémon faints, based on auto-battle settings and user overrides.
    """
    if ankimon_tracker_obj.faint_processed:
        return

    events.emit("faint", who="enemy", pokemon=enemy_pokemon.name, id=enemy_pokemon.id)

    auto_battle_setting = get_auto_battle_setting(settings_obj)

    if auto_battle_setting == 0:
        clear_auto_battle_override()

    # --- CHECK FOR USER OVERRIDE FIRST ---
    if _auto_battle_override == "catch":
        # Override: Force catch
        ankimon_tracker_obj.faint_processed = True
        try:
            catch_pokemon(
                enemy_pokemon,
                ankimon_tracker_obj,
                logger,
                "",
                collected_pokemon_ids,
                achievements,
            )
            new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)
            main_pokemon.reset_bonuses()
            ankimon_tracker_obj.general_card_count_for_battle = 0
        finally:
            clear_auto_battle_override()
        return
        
    elif _auto_battle_override == "defeat":
        # Override: Force defeat, unless the enemy is protected by an
        # "always auto-catch" tier setting (legendary/mythical/ultra/
        # starter/mega/gmax/regional) — an explicit defeat override should
        # not be able to permanently kill a protected Pokémon, e.g. via a
        # misclick or a toggle the user forgot was still armed.
        ankimon_tracker_obj.faint_processed = True
        try:
            if _enemy_protected_by_auto_catch(enemy_pokemon):
                catch_pokemon(
                    enemy_pokemon,
                    ankimon_tracker_obj,
                    logger,
                    "",
                    collected_pokemon_ids,
                    achievements,
                )
            else:
                kill_pokemon(
                    main_pokemon,
                    enemy_pokemon,
                    evo_window,
                    logger,
                    achievements,
                    trainer_card,
                )
            new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)
            main_pokemon.reset_bonuses()
            ankimon_tracker_obj.general_card_count_for_battle = 0
        finally:
            clear_auto_battle_override()
        return
    # --- END OVERRIDE CHECK ---

    # --- Wishlist fast-path (runs after override check) ---
    _wishlist = settings_obj.get("battle.auto_catch_wishlist", [])
    if isinstance(_wishlist, list) and enemy_pokemon.id in _wishlist:
        ankimon_tracker_obj.faint_processed = True
        catch_pokemon(
            enemy_pokemon,
            ankimon_tracker_obj,
            logger,
            "",
            collected_pokemon_ids,
            achievements,
        )
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)
        main_pokemon.reset_bonuses()
        ankimon_tracker_obj.general_card_count_for_battle = 0
        return
    # --- End wishlist fast-path ---

    # The "always auto-catch this tier" safety net is only needed by the
    # normal auto-battle branches below (the wishlist fast-path above and
    # the defeat-override branch above already resolved it or returned
    # without it), so compute it here rather than unconditionally at the
    # top of the function.
    should_catch_always = _enemy_protected_by_auto_catch(enemy_pokemon)

    # --- Normal auto-battle logic (no override) ---
    if auto_battle_setting == 3:  # Catch if uncollected
        enemy_id = enemy_pokemon.id
        # Check cache instead of file
        if (
            enemy_id not in collected_pokemon_ids
            or enemy_pokemon.shiny
            or should_catch_always
        ):
            ankimon_tracker_obj.faint_processed = True
            catch_pokemon(
                enemy_pokemon,
                ankimon_tracker_obj,
                logger,
                "",
                collected_pokemon_ids,
                achievements,
            )
        else:
            ankimon_tracker_obj.faint_processed = True
            kill_pokemon(
                main_pokemon,
                enemy_pokemon,
                evo_window,
                logger,
                achievements,
                trainer_card,
            )
        new_pokemon(
            enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
        )  # Show a new random Pokémon
    elif auto_battle_setting == 1:  # Existing auto-catch
        ankimon_tracker_obj.faint_processed = True
        catch_pokemon(
            enemy_pokemon,
            ankimon_tracker_obj,
            logger,
            "",
            collected_pokemon_ids,
            achievements,
        )
        new_pokemon(
            enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
        )  # Show a new random Pokémon
    elif auto_battle_setting == 2:  # Existing auto-defeat
        if enemy_pokemon.shiny or should_catch_always:
            ankimon_tracker_obj.faint_processed = True
            catch_pokemon(
                enemy_pokemon,
                ankimon_tracker_obj,
                logger,
                "",
                collected_pokemon_ids,
                achievements,
            )
        else:
            ankimon_tracker_obj.faint_processed = True
            kill_pokemon(
                main_pokemon,
                enemy_pokemon,
                evo_window,
                logger,
                achievements,
                trainer_card,
            )
        new_pokemon(
            enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
        )  # Show a new random Pokémon
    else:
        # For Manual mode (auto_battle_setting == 0): show the death/catch screen
        if test_window is not None:
            try:
                test_window.display_pokemon_death()
            except RuntimeError:
                pass

    main_pokemon.reset_bonuses()
    ankimon_tracker_obj.general_card_count_for_battle = 0
    # No explicit clear_auto_battle_override() here: every branch above either
    # already called new_pokemon() (which clears it as its first statement) or
    # is the manual-mode branch, which already cleared it earlier in this
    # function when auto_battle_setting == 0 was detected.


def handle_main_pokemon_faint(
    main_pokemon: PokemonObject,
    enemy_pokemon: PokemonObject,
    test_window: TestWindow,
    reviewer_obj: Reviewer_Manager,
    translator: Translator,
):
    """
    Handles what happens when the main Pokémon faints.
    """
    msg = translator.translate(
        "pokemon_fainted", enemy_pokemon_name=main_pokemon.name.capitalize()
    )
    tooltipWithColour(msg, "#E12939")
    events.emit("faint", who="main", pokemon=main_pokemon.name)
    play_effect_sound(settings_obj, "Fainted")

    main_pokemon.hp = main_pokemon.max_hp
    main_pokemon.current_hp = main_pokemon.max_hp
    main_pokemon.reset_bonuses()

    new_pokemon(
        enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj
    )  # Show a new random Pokémon
