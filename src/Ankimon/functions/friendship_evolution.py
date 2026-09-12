"""Friendship- and time-of-day-based evolution helpers.

Single source of truth for the in-game day/night clock, a species' friendship
evolution(s), whether a Pokémon is ready to evolve right now, and triggering that
evolution via :class:`EvoWindow`. Friendship evolutions live in
``pokemon_evolution.csv`` as level-up rows (``evolution_trigger_id == 1``) with a
positive ``minimum_happiness`` and an optional ``time_of_day``; the legacy
level-up code skips them because their ``minimum_level`` is blank.

Settings are read through the service registry (``services.settings``), fetched
per call rather than cached at import time so a registry that is populated after
this module loads is always seen.
"""

from __future__ import annotations

import functools
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple, Optional

from .pokedex_functions import (
    _LEVEL_EVO_TRIGGERS,
    _csv_gender_id,
    _evolution_row_gender_id,
    _load_moves_cache,
    evolution_rows_for_evolved_species,
    filter_gender_split_forms,
    gender_allows_evolution,
    pokemon_evolves_from_id,
    return_name_for_id,
)
from ..services import services

# Reference value for friendship progress bars: the bar reads "full" at this
# value, and it's the fallback bar denominator for species with no friendship
# evolution. NOTE: friendship is NOT capped here — it keeps climbing past 400 as
# a flex stat (the bar just stays full; the raw number is what grows).
MAX_FRIENDSHIP = 400


class FriendshipEvolution(NamedTuple):
    """A single friendship-based evolution (immutable, hashable, cacheable).

    Attributes:
        evo_id: National Pokédex id of the evolved species.
        evo_name: Capitalised display name of the evolved species.
        min_happiness: Friendship value required to evolve.
        time_of_day: ``"day"``, ``"night"``, or ``None`` (no time requirement).
        known_move_type: Optional lower-case type name the Pokémon must know a
            move of before this evolution becomes possible (e.g. ``"fairy"``).
            Read from the CSV's ``known_move_type_id`` column via
            :func:`_move_type_name`; only type 18 (Fairy) appears in the bundled
            data today, but any id that resolves through the type table works.
    """

    evo_id: int
    evo_name: str
    min_happiness: int
    time_of_day: Optional[str]
    known_move_type: Optional[str] = None


class LevelEvolution(NamedTuple):
    """A single plain level-up evolution (counterpart to :class:`FriendshipEvolution`).

    Attributes:
        evo_id: National Pokédex id of the evolved species.
        evo_name: Capitalised display name of the evolved species.
        min_level: Level required to evolve.
        time_of_day: ``"day"``, ``"night"``, or ``None`` if the level-up
            evolution has no time-of-day requirement (e.g. Cosmoem -> Solgaleo
            by day / Lunala at night, both at Lv53).
    """

    evo_id: int
    evo_name: str
    min_level: int
    time_of_day: Optional[str] = None


class _DefaultSettings:
    """Answers every setting with the caller's default.

    Stands in for an unbound ``services.settings``.
    """

    @staticmethod
    def get(key, default=None):
        return default


_DEFAULT_SETTINGS = _DefaultSettings()


def _settings():
    """Return the settings seam, or a defaults-only stand-in when it is unset.

    ``services.settings`` is ``None`` until the registry is populated — early
    boot, a profile swap in flight, a partially-wired headless run — and a bare
    ``services.settings.get(...)`` then raises ``AttributeError`` from
    ``evolution_readiness`` / ``get_time_of_day``, both of which sit on render
    and review paths that otherwise degrade rather than raise (they already
    coerce junk ids, junk friendship and junk hours). Every setting read in this
    module is a preference with a sensible default, so answering with that
    default is strictly better than raising. Mirrors the explicit ``None``
    check the sibling clock in ``pokedex_functions.get_time_of_day`` makes, and
    the ``try/except`` in :func:`_active_region` below.
    """
    settings_obj = services.settings
    return settings_obj if settings_obj is not None else _DEFAULT_SETTINGS


def _now_in_configured_tz() -> datetime:
    """Return the current time in the user's configured time zone.

    Auto-detect (default) uses the device's local time via ``datetime.now()``;
    when ``evolution.timezone_auto`` is off, a fixed ``evolution.timezone_offset``
    (hours, clamped to ±14) is applied instead.
    """
    settings_obj = _settings()  # registry-backed; no singletons/aqt import

    if settings_obj.get("evolution.timezone_auto", True):
        return datetime.now()
    try:
        offset = float(settings_obj.get("evolution.timezone_offset", 0.0))
    except (TypeError, ValueError):
        offset = 0.0
    offset = max(-14.0, min(14.0, offset))  # clamp to the valid UTC range
    return datetime.now(timezone(timedelta(hours=offset)))


def _format_utc_offset(offset: float) -> str:
    """Format a UTC offset in hours, e.g. ``UTC+5:30`` / ``UTC-5`` / ``UTC+0``."""
    sign = "-" if offset < 0 else "+"
    hours, minutes = divmod(int(round(abs(offset) * 60)), 60)
    return f"UTC{sign}{hours}:{minutes:02d}" if minutes else f"UTC{sign}{hours}"


def _coerce_hour(value: Any, default: int) -> int:
    """Coerce a configured day/night boundary hour to an ``int`` in ``0-23``.

    The bounds are advanced (non-UI) config, so a hand-edited value can be a
    string / ``None`` / junk; fall back to ``default`` and clamp rather than raise.
    """
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(23, hour))


def _coerce_int(value: Any, default: int) -> int:
    """Coerce a Pokémon stat (friendship / level) to an ``int``.

    The SQLite store can hand these back as JSON strings (``"160"``) via trades /
    imports / migrations; fall back to ``default`` for ``None`` / non-numeric junk
    rather than raising on later arithmetic.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_time_of_day(now: Optional[datetime] = None) -> str:
    """Return the current in-game time of day as ``"day"`` or ``"night"``.

    The day window is ``[day_start_hour, night_start_hour)`` (defaults 6-18, so
    night spans midnight); everything else is night. A misconfigured
    ``day_start_hour >= night_start_hour`` just yields an always-night window.

    Args:
        now: Optional :class:`datetime` to evaluate; defaults to the configured
            time zone. Useful for testing.

    Returns:
        ``"day"`` or ``"night"``.
    """
    settings_obj = _settings()  # registry-backed; no singletons/aqt import

    moment = now if now is not None else _now_in_configured_tz()
    hour = moment.hour
    day_start = _coerce_hour(settings_obj.get("evolution.day_start_hour", 6), 6)
    night_start = _coerce_hour(settings_obj.get("evolution.night_start_hour", 18), 18)
    return "day" if day_start <= hour < night_start else "night"


def current_time_label(now: Optional[datetime] = None) -> str:
    """Return a human-readable label for the current time of day.

    Examples:
        ``"☀️ Day · 09:12"`` or, with a manual time zone,
        ``"🌙 Night · 21:34 · UTC-5"``.

    Args:
        now: Optional :class:`datetime`. When omitted, the user's configured
            time zone is used.

    Returns:
        A short, emoji-prefixed label including the current ``HH:MM`` time (and
        the UTC offset when a manual time zone is configured).
    """
    settings_obj = _settings()  # registry-backed; no singletons/aqt import

    moment = now if now is not None else _now_in_configured_tz()
    time_of_day = get_time_of_day(moment)
    icon = "☀️ Day" if time_of_day == "day" else "🌙 Night"
    label = f"{icon} · {moment.strftime('%H:%M')}"
    if not settings_obj.get("evolution.timezone_auto", True):
        try:
            offset = float(settings_obj.get("evolution.timezone_offset", 0.0))
            label += f" · {_format_utc_offset(offset)}"
        except (TypeError, ValueError):
            pass
    return label


# Canonical Pokémon type ids, matching the ``known_move_type_id`` column of the
# veekun-sourced ``pokemon_evolution.csv`` (standard in-game type ordering).
# Only Fairy (18) appears in the bundled CSV today, but the full table costs
# nothing and keeps any future move-type-gated evolution resolvable. Unknown /
# junk ids resolve to None ("no requirement") rather than blocking evolutions.
_MOVE_TYPE_NAMES = {
    1: "normal",
    2: "fighting",
    3: "flying",
    4: "poison",
    5: "ground",
    6: "rock",
    7: "bug",
    8: "ghost",
    9: "steel",
    10: "fire",
    11: "water",
    12: "grass",
    13: "electric",
    14: "psychic",
    15: "ice",
    16: "dragon",
    17: "dark",
    18: "fairy",
}


def _move_type_name(type_id) -> Optional[str]:
    """Return the lower-case type name for a ``known_move_type_id``.

    Maps the veekun-schema numeric type id through :data:`_MOVE_TYPE_NAMES`
    into the lower-case keys used by ``moves.json`` entries (``move["type"]``).
    Returns ``None`` when the id can't be resolved, so a data gap degrades to
    "no move requirement" instead of blocking evolutions. Never raises.
    """
    try:
        return _MOVE_TYPE_NAMES.get(int(type_id))
    except (TypeError, ValueError):
        return None


def _is_plain_level_row(row: dict) -> bool:
    """Return ``True`` if a CSV row is a plain level-up evolution.

    A "plain level-up" row has ``evolution_trigger_id == 1``, a positive
    ``minimum_level`` and *no* friendship requirement (blank/zero
    ``minimum_happiness``). It is used to detect evolved species that are already
    reachable by levelling up, so the friendship helper can leave those to the
    level path (see :func:`get_friendship_evolutions_for_species`).
    """
    try:
        if int(row.get("evolution_trigger_id", 0)) != 1:
            return False
    except (TypeError, ValueError):
        return False
    try:
        if int(row.get("minimum_happiness", "") or 0) > 0:
            return False
    except (TypeError, ValueError):
        pass
    try:
        return int(row.get("minimum_level", "")) > 0
    except (TypeError, ValueError):
        return False


@functools.lru_cache(maxsize=None)
def get_friendship_evolutions_for_species(
    pokemon_id: int,
) -> tuple[FriendshipEvolution, ...]:
    """Return all friendship evolutions for a species, sorted by evolved id.

    Scans every CSV row of each evolved form (not first-match — that would miss
    e.g. Eevee -> Sylveon, whose blank row precedes its friendship row) and keeps
    those with a positive ``minimum_happiness``. Forms also reachable by a plain
    level-up are left to the level path (see :func:`_is_plain_level_row`) so a
    classic level-up evolution isn't silently changed. ``lru_cache``d on
    ``pokemon_id`` (the CSV is static for the process lifetime; the returned
    ``NamedTuple``s are immutable, so the cached tuple is safe to share).

    Args:
        pokemon_id: National Pokédex id of the *pre-evolution* species.

    Returns:
        A tuple of :class:`FriendshipEvolution` entries sorted by ``evo_id``;
        empty if the species has no friendship evolution.
    """
    evolutions: list[FriendshipEvolution] = []
    for evo in pokemon_evolves_from_id(pokemon_id):
        # An evolved species can have several rows (one per evolution method), so
        # scan them all and keep the one carrying a positive minimum_happiness.
        # check_key_in_table's first-match would miss e.g. Sylveon, whose blank
        # row precedes its friendship row in the CSV.
        rows = evolution_rows_for_evolved_species(evo)
        # If this evolved species is also reachable by levelling up, leave it to
        # the level path instead of offering a friendship evolution. The bundled
        # CSV carries no Pokémon *form* data, so it conflates e.g. Kantonian
        # Meowth (level 28) and Alolan Meowth (friendship) onto the same evolved
        # id (Persian); preferring friendship there would silently change a
        # classic level-up evolution. Scoping friendship evolution to species
        # that evolve *purely* by friendship (Eevee, Golbat, Pichu, Riolu, ...)
        # keeps the feature focused and leaves existing level evolutions intact.
        if any(_is_plain_level_row(r) for r in rows):
            continue
        for row in rows:
            try:
                min_happiness = int(row.get("minimum_happiness", ""))
            except (TypeError, ValueError):
                continue
            if min_happiness <= 0:
                continue

            time_raw = (row.get("time_of_day") or "").strip().lower()
            time_of_day = time_raw if time_raw in ("day", "night") else None

            # Optional move-type gate straight from the CSV (e.g. Sylveon's
            # known_move_type_id=18 -> "fairy"). Unresolvable ids degrade to
            # None ("no requirement") rather than blocking the evolution.
            known_move_type = None
            try:
                raw_type_id = int(row.get("known_move_type_id", "") or 0)
            except (TypeError, ValueError):
                raw_type_id = 0
            if raw_type_id > 0:
                known_move_type = _move_type_name(raw_type_id)

            name = return_name_for_id(int(evo))
            evo_name = name.capitalize() if name else str(evo)

            evolutions.append(
                FriendshipEvolution(
                    evo_id=int(evo),
                    evo_name=evo_name,
                    min_happiness=min_happiness,
                    time_of_day=time_of_day,
                    known_move_type=known_move_type,
                )
            )
            break  # one friendship row per evolved species is enough

    evolutions.sort(key=lambda e: e.evo_id)
    return tuple(evolutions)


@functools.lru_cache(maxsize=None)
def get_level_evolutions_for_species(
    pokemon_id: int,
) -> tuple[LevelEvolution, ...]:
    """Return all plain level-up evolutions for a species, sorted by evolved id.

    Prioritises the form-aware ``pokedex.json`` data (so regional forms such as
    Alolan Marowak / Galarian Weezing and move- or defeat-based evolutions such
    as Mr. Mime and Pawmot are surfaced with their real ``actual_id``), then
    falls back to the bundled ``pokemon_evolution.csv`` for species that carry no
    ``pokedex.json`` evolution data. Only plain level-up rows are kept
    (``evolution_trigger_id == 1`` with a positive ``minimum_level`` and no
    friendship requirement) so the two helpers never double-count a row; the
    friendship helper handles ``minimum_happiness`` rows. ``lru_cache``d on
    ``pokemon_id`` (both data sources are static for the process lifetime).

    Args:
        pokemon_id: National Pokédex id of the *pre-evolution* species.

    Returns:
        A tuple of :class:`LevelEvolution` entries sorted by ``evo_id``; empty if
        the species has no plain level-up evolution.
    """
    # Lazy imports: keep them local so tests may monkeypatch the pokedex cache /
    # id lookup on ``pokedex_functions`` without a stale module-level binding.
    from .pokedex_functions import (
        _load_pokedex_cache,
        safe_int,
        search_pokedex_by_id,
    )

    evolutions: list[LevelEvolution] = []

    # 1. PRIORITY: form-aware evolution from pokedex.json. This carries the
    #    regional/mega/gmax forms (and their real actual_id), move-based
    #    (levelMove) and defeat-based (minimumDefeated) methods that the base
    #    species CSV cannot express.
    pokedex_data = _load_pokedex_cache()
    internal_name = search_pokedex_by_id(pokemon_id)

    if internal_name in pokedex_data:
        evo_list = pokedex_data[internal_name].get("evos")
        if evo_list:
            for target_evo_name in evo_list:
                normalized = (
                    target_evo_name.lower()
                    .replace(" ", "")
                    .replace("-", "")
                    .replace("'", "")
                    .replace(".", "")
                    .replace(":", "")
                )
                target_data = pokedex_data.get(normalized) or pokedex_data.get(
                    target_evo_name.lower()
                )
                if not target_data:
                    continue

                # In Smogon-style pokedex.json, evoLevel is stored on the
                # evolved species.
                min_level = safe_int(target_data.get("evoLevel"))
                evo_type = target_data.get("evoType")
                condition = (target_data.get("evoCondition") or "").lower()
                target_id = safe_int(
                    target_data.get("actual_id") or target_data.get("species_id")
                )

                # Move-based (levelMove) and defeat-based (minimumDefeated)
                # evolutions have no fixed evoLevel; treat them as eligible from
                # Lv1 so the readiness path can gate them on the move / count.
                is_eligible = min_level > 0
                if evo_type == "levelMove" or condition == "minimumdefeated":
                    is_eligible = True
                    if min_level <= 0:
                        min_level = 1

                if target_id > 0 and is_eligible:
                    time_of_day = None
                    if "day" in condition:
                        time_of_day = "day"
                    elif "night" in condition:
                        time_of_day = "night"

                    evolutions.append(
                        LevelEvolution(
                            evo_id=target_id,
                            evo_name=target_data.get("name", target_evo_name),
                            min_level=min_level,
                            time_of_day=time_of_day,
                        )
                    )

        # A species listed in pokedex.json is authoritative for its evolutions
        # (the CSV conflates regional forms onto one id), so return its results
        # even when empty rather than falling through to the CSV.
        evolutions.sort(key=lambda e: e.evo_id)
        return tuple(evolutions)

    # 2. LEGACY FALLBACK: species CSV lookup for anything absent from pokedex.json.
    for evo in pokemon_evolves_from_id(pokemon_id):
        # Scan every row for this evolved species and keep the first that is a
        # plain level-up row. An evolved species may carry several method rows
        # (e.g. a level-up row *and* a friendship row), so first-match would pick
        # the wrong one when the level row isn't listed first.
        for row in evolution_rows_for_evolved_species(evo):
            # Level-up trigger only (item/trade/etc. evolutions are out of scope).
            try:
                trigger_id = int(row.get("evolution_trigger_id", 0))
            except (TypeError, ValueError):
                continue
            if trigger_id != 1:
                continue

            # Exclude friendship evolutions — those are handled by the friendship
            # helper and must not be double-counted here.
            try:
                min_happiness = int(row.get("minimum_happiness", "") or 0)
            except (TypeError, ValueError):
                min_happiness = 0
            if min_happiness > 0:
                continue

            try:
                min_level = int(row.get("minimum_level", ""))
            except (TypeError, ValueError):
                continue
            if min_level <= 0:
                continue

            time_raw = (row.get("time_of_day") or "").strip().lower()
            time_of_day = time_raw if time_raw in ("day", "night") else None

            name = return_name_for_id(int(evo))
            evo_name = name.capitalize() if name else str(evo)

            evolutions.append(
                LevelEvolution(
                    evo_id=int(evo),
                    evo_name=evo_name,
                    min_level=min_level,
                    time_of_day=time_of_day,
                )
            )
            break  # one level-up row per evolved species is enough

    evolutions.sort(key=lambda e: e.evo_id)
    return tuple(evolutions)


def _move_type_of(attack: Any) -> Optional[str]:
    """Return the lower-case type of one move name, or ``None``.

    A quiet lookup over the startup-loaded ``moves.json`` cache only.
    Deliberately *not* :func:`find_details_move`, which answers unknown names
    with the default "tackle" entry — that would misreport the type AND pop a
    warning dialog through ``services.ui.warn`` mid-review. An unknown or junk
    name simply carries no type evidence (None).
    """
    if not isinstance(attack, str):
        return None
    name = attack.strip()
    if not name:
        return None
    try:
        moves = _load_moves_cache() or {}
    except Exception:
        return None
    # Same normalization ladder find_details_move uses, minus the fallback.
    for candidate in (
        name.lower(),
        name.replace(" ", "").replace("-", "").lower(),
        name.replace(" ", "").lower(),
        name.replace("-", "").lower(),
    ):
        move_data = moves.get(candidate)
        if isinstance(move_data, dict):
            move_type = move_data.get("type")
            if isinstance(move_type, str) and move_type.strip():
                return move_type.strip().lower()
    return None


def _known_move_types(pokemon: Any) -> frozenset:
    """Return the set of move types the Pokémon knows, lower-cased.

    Accepts a dict or an object with an ``attacks`` attribute (the two shapes
    :func:`evolution_readiness` supports). Move names resolve through the quiet
    :func:`_move_type_of` cache lookup; unknown moves contribute nothing rather
    than raising, so the result is best-effort by construction.
    """
    if pokemon is None:
        return frozenset()
    if isinstance(pokemon, dict):
        attacks = pokemon.get("attacks") or []
    else:
        attacks = getattr(pokemon, "attacks", None) or []
    types: set = set()
    for attack in attacks:
        move_type = _move_type_of(attack)
        if move_type:
            types.add(move_type)
    return frozenset(types)


# Display names for the CSV ``gender_id`` values (veekun convention).
_GENDER_LABELS = {1: "Female", 2: "Male"}


def _pokemon_gender(pokemon: Any) -> Any:
    """Return the raw ``gender`` of a Pokémon dict/object, or ``None``.

    Same dual-shape handling as :func:`_known_move_types`; the value is passed
    straight to :func:`_csv_gender_id`, which is what decides whether it is
    recognised ("M"/"F") or degrades to "no gate".
    """
    if pokemon is None:
        return None
    if isinstance(pokemon, dict):
        return pokemon.get("gender")
    return getattr(pokemon, "gender", None)


def _level_gender_gate(evo_id: int, caller_gender_id: Optional[int]) -> bool:
    """Return whether a level-up evolution target is open to this gender.

    ``caller_gender_id is None`` (no/unrecognised gender) fails **open** — the
    same no-check-without-data convention ``check_evolution_for_pokemon`` and
    ``check_evolution_by_item`` use for their gender gates, and deliberately the
    opposite of the fail-closed move-type gate: a missing moveset is a fact we
    could not read, while a missing gender is a save that never stored one.

    Thin wrapper over ``pokedex_functions.gender_allows_evolution`` so the PC's
    manual "Evolve now" readiness and the automatic level-up path can't drift
    apart on what the gate means; only the level trigger scope is fixed here.
    """
    return gender_allows_evolution(evo_id, caller_gender_id, _LEVEL_EVO_TRIGGERS)


def _satisfies_move_gate(
    evo: FriendshipEvolution, known_move_types: Optional[frozenset]
) -> bool:
    """Return whether ``evo``'s move-type requirement is satisfied.

    ``known_move_types is None`` means "moves unknown" — the caller had no
    attacks data at all. Every gate then evaluates False so a move-gated
    evolution is never offered on missing data (fail-closed); non-gated
    evolutions are unaffected and stay eligible.

    Note that :func:`evolution_readiness` always passes a concrete frozenset:
    :func:`_known_move_types` answers a Pokémon with no readable moveset with an
    *empty* set, which fails the same way. The ``None`` case exists for direct
    callers (and for the parameter's own default) and behaves identically.
    """
    if evo.known_move_type is None:
        return True
    if known_move_types is None:
        return False
    return evo.known_move_type in known_move_types


def _select_evolution(
    evos: tuple[FriendshipEvolution, ...],
    time_of_day: str,
    known_move_types: Optional[frozenset] = None,
) -> FriendshipEvolution:
    """Pick the most appropriate friendship evolution for the current time.

    First drops evolutions whose optional move-type requirement (CSV
    ``known_move_type_id``, e.g. Sylveon's Fairy-move gate) isn't met by the
    Pokémon's current moveset; when the moveset is unknown (``None``), gated
    rows are dropped too — a move-gated evolution must never be selected on
    unconfirmed data.

    Among the survivors it prefers the most specific requirement first: a
    satisfied move-type gate (in-game, a Fairy move *forces* Sylveon over the
    time-gated Espeon/Umbreon), then an evolution eligible right now (its
    ``time_of_day`` matches, or it has none), an explicitly time-gated row
    beating a blank-time one, then lowest ``evo_id``. If none is eligible now,
    falls back to the lowest remaining ``evo_id`` so the UI can still show e.g.
    "waiting for Night".

    If *every* row is move-gated and none is satisfied there is no reachable
    row to rank, so the lowest ``evo_id`` comes back as a representative for the
    UI ("needs a Fairy-type move"). Callers MUST NOT read that as eligible:
    :func:`evolution_readiness` re-checks the chosen row with
    :func:`_satisfies_move_gate` and folds the result into ``ready``, so the
    branch can't fail open. (No bundled species reaches it today — Eevee, the
    only species with gated rows, also has ungated ones.)

    Args:
        evos: Non-empty tuple from :func:`get_friendship_evolutions_for_species`.
        time_of_day: Current time of day (``"day"`` or ``"night"``).
        known_move_types: Lower-case move types the Pokémon knows, or ``None``
            when the moveset is unknown (move-gated rows then never win).

    Returns:
        The chosen :class:`FriendshipEvolution`.
    """
    valid_evos = [e for e in evos if _satisfies_move_gate(e, known_move_types)]
    if not valid_evos:
        # Reaching here means every row carries a move gate and none is met (an
        # ungated row would have satisfied trivially). Ranking them by "most
        # specific requirement" is meaningless when none is reachable, so return
        # the lowest evo_id as a stable representative for the UI and let the
        # caller's _satisfies_move_gate re-check keep it out of `ready`.
        return min(evos, key=lambda e: e.evo_id)

    eligible_now = [e for e in valid_evos if e.time_of_day in (time_of_day, None)]
    if eligible_now:
        # Sort: satisfied move gate first (a known Fairy move outranks the
        # day/night clock — Sylveon over Espeon/Umbreon), then explicit-time
        # rows over blank-time rows, then lowest evo_id. Each ``is None`` /
        # ``==`` predicate sorts falsy-first via the bool key. Every row here
        # has already passed its gate, so the first key only ever promotes a
        # SATISFIED gate.
        return min(
            eligible_now,
            key=lambda e: (
                e.known_move_type is None,
                e.time_of_day is None,
                e.evo_id,
            ),
        )
    # Nothing matches the current time; still return a representative.
    return min(valid_evos, key=lambda e: e.evo_id)


def evolution_readiness(pokemon: Any, now: Optional[datetime] = None) -> dict:
    """Compute manual-evolution readiness for a single Pokémon.

    Covers both friendship/time and plain level-up evolutions, so the PC's
    "Evolve now" button and ✨ badge work for either. Friendship takes precedence
    (``method="friendship"``); otherwise a level-up evolution is considered
    (``method="level"``), else ``method=None``. Accepts a dict or an object with
    ``id`` / ``friendship`` / ``everstone`` / ``level`` (missing -> 0 / False / 1).

    Args:
        pokemon: A Pokémon dict or object.
        now: Optional :class:`datetime` for the time-of-day check.

    Returns:
        A readiness dict with keys: ``evolvable, ready, method, evo_id,
        evo_name, min_happiness, current_friendship, friendship_remaining,
        required_time, time_ok, status_text, bar_max, rejected,
        required_move_type, gated_alternatives``. ``bar_max`` defaults to
        :data:`MAX_FRIENDSHIP` outside the friendship path;
        ``required_move_type``/``gated_alternatives`` describe CSV
        ``known_move_type_id`` gates (Sylveon's Fairy-move requirement) and are
        ``None``/``()`` everywhere else.
    """
    if isinstance(pokemon, dict):
        species_id = pokemon.get("id")
        friendship = _coerce_int(pokemon.get("friendship", 0), 0)
        everstone = pokemon.get("everstone", False)
        evolution_rejected = pokemon.get("evolution_rejected", False)
        level = _coerce_int(pokemon.get("level", 1), 1)
    else:
        species_id = getattr(pokemon, "id", None)
        friendship = _coerce_int(getattr(pokemon, "friendship", 0), 0)
        everstone = getattr(pokemon, "everstone", False)
        evolution_rejected = getattr(pokemon, "evolution_rejected", False)
        level = _coerce_int(getattr(pokemon, "level", 1), 1)

    time_of_day = get_time_of_day(now)

    not_evolvable = {
        "evolvable": False,
        "ready": False,
        "method": None,
        "evo_id": None,
        "evo_name": None,
        "min_happiness": None,
        "current_friendship": friendship,
        "friendship_remaining": 0,
        "required_time": None,
        "time_ok": True,
        "status_text": "",
        "bar_max": MAX_FRIENDSHIP,
        "rejected": False,
        "required_move_type": None,
        "gated_alternatives": (),
    }

    if species_id is None:
        return not_evolvable
    try:
        species_id = int(species_id)
    except (TypeError, ValueError):
        # A malformed/non-numeric id can't be matched against the integer CSV
        # ids; treat it like a missing id rather than raising on the hot path.
        return not_evolvable

    evos = get_friendship_evolutions_for_species(species_id)
    if not evos:
        # No friendship evolution — fall back to a plain level-up evolution so
        # the manual "Evolve now" path covers level evolvers too (auto level-ups
        # are still handled by check_evolution_for_pokemon; this is for mons that
        # rejected, hold an Everstone, or were caught above their evolve level).
        return _level_readiness(
            species_id=species_id,
            level=level,
            everstone=everstone,
            friendship=friendship,
            evolution_rejected=evolution_rejected,
            not_evolvable=not_evolvable,
            time_of_day=time_of_day,
            pokemon=pokemon,
        )

    known_move_types = _known_move_types(pokemon)
    chosen = _select_evolution(evos, time_of_day, known_move_types)
    evo_id = chosen.evo_id
    evo_name = chosen.evo_name
    min_happiness = chosen.min_happiness
    required_time = chosen.time_of_day

    # The chosen row can still carry an UNMET move gate in one case: every row
    # for the species was gated, so _select_evolution had to hand back a
    # representative. Folding move_ok into `ready` is what keeps that branch
    # from failing open (the friendship/time numbers alone would say "ready").
    move_ok = _satisfies_move_gate(chosen, known_move_types)
    required_move_type = None if move_ok else chosen.known_move_type

    # Evolutions the data offers but the current moveset hides. Without this the
    # gate is invisible: a high-friendship Eevee simply shows Espeon/Umbreon and
    # nothing ever tells the player Sylveon exists or what it wants.
    gated_alternatives = tuple(
        (e.evo_name, e.known_move_type)
        for e in evos
        if e.evo_id != evo_id
        and e.known_move_type is not None
        and not _satisfies_move_gate(e, known_move_types)
    )

    friendship_remaining = max(0, min_happiness - friendship)
    time_ok = required_time is None or required_time == time_of_day
    ready = (not everstone) and friendship >= min_happiness and time_ok and move_ok

    status_text = _build_status_text(
        everstone=everstone,
        ready=ready,
        evo_name=evo_name,
        friendship_remaining=friendship_remaining,
        required_time=required_time,
        time_ok=time_ok,
        time_of_day=time_of_day,
        rejected=evolution_rejected,
        required_move_type=required_move_type,
        gated_alternatives=gated_alternatives,
    )

    return {
        "evolvable": True,
        "ready": ready,
        "method": "friendship",
        "evo_id": evo_id,
        "evo_name": evo_name,
        "min_happiness": min_happiness,
        "current_friendship": friendship,
        "friendship_remaining": friendship_remaining,
        "required_time": required_time,
        "time_ok": time_ok,
        "status_text": status_text,
        "bar_max": min_happiness,
        "rejected": evolution_rejected,
        # Unmet move-type gate on the CHOSEN evolution (None when satisfied or
        # ungated), and the (name, move_type) pairs of alternatives currently
        # hidden behind one — e.g. ``(("Sylveon", "fairy"),)`` for an Eevee that
        # knows no Fairy move.
        "required_move_type": required_move_type,
        "gated_alternatives": gated_alternatives,
    }


def _active_region() -> Optional[str]:
    """Return the player's active region, or ``None`` for "No Region"/unset.

    Read through the service seam (``services.settings``); a missing/blank value
    or a ``"No Region"`` sentinel means "no regional preference" and is folded to
    ``None``. Never raises on the hot PC-render path.
    """
    try:
        region = services.settings.get("misc.active_region")
    except Exception:
        return None
    if region:
        region = str(region).strip()
    if not region or region == "No Region":
        return None
    return region


def _filter_evolutions_by_region(
    level_evos: tuple[LevelEvolution, ...],
    active_region: Optional[str],
) -> list[LevelEvolution]:
    """Prefer regional-form targets that match the active region.

    A target with an ``evoRegion`` is kept only when it matches ``active_region``.
    A target with no ``evoRegion`` (the base form) is kept unless a *sibling*
    evolution's ``evoRegion`` matches the active region (in which case the
    regional form supersedes the base form — e.g. under Alola, Cubone prefers
    Alolan Marowak over Kantonian Marowak). Returns the surviving list (may be
    empty; callers fall back to the unfiltered list when so).
    """
    from .pokedex_functions import _load_pokedex_cache, search_pokedex_by_id

    pokedex_data = _load_pokedex_cache()

    def region_of(evo: LevelEvolution) -> Optional[str]:
        name = search_pokedex_by_id(evo.evo_id)
        data = pokedex_data.get(name)
        return data.get("evoRegion") if data else None

    def matches_active(region: Optional[str]) -> bool:
        return bool(
            region and active_region and active_region.lower() == region.lower()
        )

    filtered: list[LevelEvolution] = []
    for evo in level_evos:
        target_region = region_of(evo)
        if target_region:
            if matches_active(target_region):
                filtered.append(evo)
        else:
            # Base form: drop it only when a sibling regional form matches the
            # active region (the regional form supersedes it).
            has_matching_regional_sibling = any(
                matches_active(region_of(other)) for other in level_evos
            )
            if not has_matching_regional_sibling:
                filtered.append(evo)
    return filtered


def _level_readiness(
    *,
    species_id: int,
    level: int,
    everstone: bool,
    friendship: int,
    evolution_rejected: bool,
    not_evolvable: dict,
    time_of_day: str,
    pokemon: Any = None,
) -> dict:
    """Compute readiness for a plain level-up evolution.

    Called by :func:`evolution_readiness` when the species has no friendship
    evolution; returns ``not_evolvable`` unchanged if it has no level-up evolution
    either. Ignores ``evolution_rejected`` (the manual button still shows) but
    DOES enforce the region preference (``misc.active_region``), the CSV
    ``gender_id`` gate (a male Burmy evolves into Mothim, never Wormadam), any
    ``time_of_day`` requirement, a required ``levelMove`` (e.g. Mr. Mime needs
    Mimic) and a ``minimumDefeated`` count (e.g. Pawmot needs 100 defeats):
    ``ready = (not everstone) and level >= min_level and time_ok and gender_ok
    and knows_move and defeated_ok``.

    Returns:
        A readiness dict with ``method="level"`` (or ``not_evolvable``).
    """
    from .pokedex_functions import _load_pokedex_cache, safe_int, search_pokedex_by_id

    level_evos = get_level_evolutions_for_species(species_id)
    if not level_evos:
        return not_evolvable

    # Prefer the regional-form target that matches the active region (falls back
    # to the full set when the region filter would leave nothing).
    filtered = _filter_evolutions_by_region(level_evos, _active_region())
    if filtered:
        level_evos = tuple(filtered)

    # Gender gate from the bundled CSV (veekun ids: 1 = female, 2 = male):
    # Vespiquen needs a female Combee, Salazzle a female Salandit, and Burmy
    # splits into Wormadam (female) / Mothim (male). This mirrors the gate
    # ``check_evolution_for_pokemon`` applies on the automatic level-up path —
    # without it the PC's manual "Evolve now" button is a way around it, and a
    # male Burmy is offered Wormadam (lowest evo_id) instead of Mothim.
    #
    # Unlike the region filter above this narrows the choice but never widens
    # it back: when every target is gated to the other gender the representative
    # is kept only so the UI can say why, and ``gender_ok`` keeps it out of
    # ``ready``.
    pokemon_gender = _pokemon_gender(pokemon)
    caller_gender_id = _csv_gender_id(pokemon_gender)
    gendered = [e for e in level_evos if _level_gender_gate(e.evo_id, caller_gender_id)]
    if gendered:
        level_evos = tuple(gendered)

    # Second gender source, for splits pokemon_evolution.csv cannot express as
    # gendered rows: veekun models Espurr -> Meowstic/Meowstic-F and Lechonk ->
    # Oinkologne/Oinkologne-F as FORMS of one species, so neither carries a
    # gender_id and the gate above sees no split — leaving the lowest-evo_id
    # tie-break to hand every Espurr the male Meowstic. pokedex.json does carry
    # it, on each evolved form's own `gender`. Narrows siblings only (see
    # filter_gender_split_forms), so it can never empty the candidate list and
    # `gender_ok` below stays driven by the CSV gate alone.
    split = set(
        filter_gender_split_forms([e.evo_id for e in level_evos], pokemon_gender)
    )
    if split:
        level_evos = tuple(e for e in level_evos if e.evo_id in split)

    # Among the surviving targets prefer one eligible right now (its time matches
    # or it has no time requirement); an explicit-time row beats a blank-time one.
    eligible_now = [e for e in level_evos if e.time_of_day in (time_of_day, None)]
    if eligible_now:
        chosen = min(eligible_now, key=lambda e: (e.time_of_day is None, e.evo_id))
    else:
        chosen = min(level_evos, key=lambda e: e.evo_id)

    evo_name = chosen.evo_name
    min_level = chosen.min_level
    required_time = chosen.time_of_day

    # Re-check the CHOSEN row rather than trusting the filter: when nothing
    # survived it, `chosen` is a representative whose gate is unmet.
    gender_ok = _level_gender_gate(chosen.evo_id, caller_gender_id)
    required_gender = (
        None
        if gender_ok
        else _GENDER_LABELS.get(
            _evolution_row_gender_id(chosen.evo_id, _LEVEL_EVO_TRIGGERS)
        )
    )

    time_ok = required_time is None or required_time == time_of_day
    ready = (not everstone) and level >= min_level and time_ok and gender_ok

    # Move-based (levelMove) and defeat-based (minimumDefeated) gating from the
    # evolved species' pokedex.json data.
    knows_move = True
    required_move = None
    defeated_ok = True
    required_defeated = None
    defeated_count = 0
    pokedex_data = _load_pokedex_cache()
    target_name = search_pokedex_by_id(chosen.evo_id)
    target_data = pokedex_data.get(target_name)
    if target_data:
        if target_data.get("evoType") == "levelMove":
            required_move = target_data.get("evoMove")
            if required_move:
                knows_move = False
                attacks = []
                if isinstance(pokemon, dict):
                    attacks = pokemon.get("attacks") or []
                elif pokemon is not None:
                    attacks = getattr(pokemon, "attacks", []) or []
                needle = required_move.lower().replace(" ", "").replace("-", "")
                if any(
                    str(a).lower().replace(" ", "").replace("-", "") == needle
                    for a in attacks
                ):
                    knows_move = True

        condition = (target_data.get("evoCondition") or "").lower()
        if condition == "minimumdefeated":
            required_defeated = safe_int(target_data.get("evoDefeated"))
            if required_defeated > 0:
                if isinstance(pokemon, dict):
                    defeated_count = safe_int(pokemon.get("pokemon_defeated", 0))
                elif pokemon is not None:
                    defeated_count = safe_int(getattr(pokemon, "pokemon_defeated", 0))
                defeated_ok = defeated_count >= required_defeated

    if not knows_move or not defeated_ok:
        ready = False

    if everstone:
        status_text = "Everstone prevents evolution"
    elif not gender_ok:
        # Nothing the player does changes a Pokémon's gender, so this outranks
        # the level/move/defeat lines below — none of them are actionable while
        # it holds.
        #
        # Branch on gender_ok, NOT on the label: an unrecognised CSV gender_id
        # (nothing in the bundled data carries one, but the file is regenerated
        # from upstream) leaves `required_gender` None while `ready` is already
        # False, and keying on the label would then fall through to a cheerful
        # "Ready to evolve into ..." line contradicting the button beside it.
        status_text = (
            f"Needs to be {required_gender} to evolve into {evo_name}"
            if required_gender
            else f"{evo_name} has a gender requirement this Pokémon doesn't meet"
        )
    elif not knows_move and required_move:
        status_text = f"Needs to learn {required_move} to evolve"
    elif not defeated_ok and required_defeated:
        status_text = (
            f"Needs to defeat {required_defeated} enemies to evolve "
            f"(current: {defeated_count})"
        )
    elif ready and evolution_rejected:
        status_text = "Evolution rejected — tap Evolve now to override"
    elif ready:
        status_text = f"Ready to evolve into {evo_name}!"
    elif level >= min_level and not time_ok and required_time is not None:
        status_text = (
            f"Ready — waiting for {required_time.capitalize()} "
            f"(now {time_of_day.capitalize()})"
        )
    else:
        if required_move:
            status_text = f"Evolves into {evo_name} knowing {required_move}"
        else:
            status_text = f"Evolves into {evo_name} at Lv{min_level}"
        if required_time is not None:
            status_text += f" · needs {required_time.capitalize()}"

    return {
        "evolvable": True,
        "ready": ready,
        "method": "level",
        "evo_id": chosen.evo_id,
        "evo_name": evo_name,
        "min_happiness": None,
        "current_friendship": friendship,
        "friendship_remaining": 0,
        "required_time": required_time,
        "time_ok": time_ok,
        "status_text": status_text,
        "bar_max": MAX_FRIENDSHIP,
        "rejected": evolution_rejected,
        # Shape parity with the friendship path. Level evolutions gate on a
        # specific move NAME (``evoMove``, surfaced in status_text above), not on
        # a move TYPE from the CSV, so these are always empty here.
        "required_move_type": None,
        "gated_alternatives": (),
    }


def _build_status_text(
    *,
    everstone: bool,
    ready: bool,
    evo_name: str,
    friendship_remaining: int,
    required_time: Optional[str],
    time_ok: bool,
    time_of_day: str,
    rejected: bool = False,
    required_move_type: Optional[str] = None,
    gated_alternatives: tuple = (),
) -> str:
    """Build the human-readable readiness line shown in the UI.

    ``required_move_type`` is an unmet move-type gate on the chosen evolution
    and always wins the line, because nothing else the player does will unblock
    it. ``gated_alternatives`` are ``(name, move_type)`` pairs for evolutions the
    species *can* reach but whose gate the current moveset doesn't meet; they are
    appended as a hint so the branch is discoverable at all (without it a Fairy
    move silently redirects an Eevee to Sylveon with no prior warning).

    Where the hint actually surfaces today: the only consumer of ``status_text``
    is the details panel (``gui_classes/pokemon_details.py``), and it renders the
    line only while ``ready`` is False — which for a friendship evolution is the
    whole climb from 0 to ``min_happiness`` (every capture starts at friendship
    0, Eevee needs 160), so the player sees it for the entire period they can act
    on it. Once ``ready`` flips the panel shows an "Evolve now" button instead
    and the line is not drawn. The hint is still appended to the ready branches
    below so the string stays correct for any consumer that does render it —
    just don't read those variants as something a user sees today.

    Examples:
        - ``"Everstone prevents evolution"``
        - ``"Ready to evolve into Espeon!"``
        - ``"Ready — waiting for Night (now Day)"``
        - ``"40 friendship to evolve into Espeon · needs Day"``
        - ``"40 friendship to evolve into Espeon · Sylveon needs a Fairy-type move"``
        - ``"Needs a Fairy-type move to evolve into Sylveon"``
    """
    if everstone:
        return "Everstone prevents evolution"

    # An unmet gate on the chosen evolution outranks every other line: the
    # friendship bar being full doesn't matter while the moveset blocks it.
    if required_move_type:
        return (
            f"Needs a {required_move_type.capitalize()}-type move "
            f"to evolve into {evo_name}"
        )

    # Build the parts first: an entry with a blank type contributes nothing, and
    # joining an all-blank list would otherwise leave a dangling " · ".
    hint_parts = [
        f"{name} needs a {move_type.capitalize()}-type move"
        for name, move_type in gated_alternatives
        if move_type
    ]
    hint = (" · " + " · ".join(hint_parts)) if hint_parts else ""

    if ready and rejected:
        return "Evolution rejected — tap Evolve now to override" + hint

    if ready:
        return f"Ready to evolve into {evo_name}!" + hint

    # Friendship is high enough but the time of day is wrong.
    if friendship_remaining == 0 and not time_ok and required_time is not None:
        return (
            f"Ready — waiting for {required_time.capitalize()} "
            f"(now {time_of_day.capitalize()})" + hint
        )

    # Still needs more friendship.
    text = f"{friendship_remaining} friendship to evolve into {evo_name}"
    if required_time is not None:
        text += f" · needs {required_time.capitalize()}"
    return text + hint


def check_friendship_evolution_for_pokemon(
    individual_id,
    pokemon_id,
    evo_window,
    everstone: bool = False,
    friendship: int = 0,
    evolution_rejected: bool = False,
    now: Optional[datetime] = None,
    attacks: Optional[list] = None,
    pokemon_defeated: Optional[int] = None,
) -> Optional[int]:
    """Prompt a friendship (or defeat-milestone) evolution for a Pokémon if ready.

    Honors the ``evolution.friendship_time_enabled`` toggle, the Everstone, and a
    prior rejection. When ready, opens :meth:`EvoWindow.ask_pokemon_evo`.

    Two evolution kinds auto-prompt through this victory-time checker:

    * friendship/time evolutions (``method == "friendship"``); and
    * ``minimumDefeated`` evolutions (a subset of ``method == "level"`` — e.g.
      Pawmo -> Pawmot after 100 defeats), whose defeat count comes from the
      caller's in-memory state (with a guarded DB fallback for legacy callers).

    Plain level-up evolutions are NOT auto-prompted here (those are handled by
    ``check_evolution_for_pokemon`` on level-up; the manual PC button covers the
    rest), so a level-ready Pokémon without a defeat milestone stays silent.

    Args:
        individual_id: The individual Pokémon's DB id.
        pokemon_id: National Pokédex id of the current species/form.
        evo_window: The evolution window used to prompt the offer.
        everstone: Whether an Everstone is held (suppresses prompting).
        friendship: Current friendship value of the Pokémon.
        evolution_rejected: Whether a prior offer was rejected (suppresses).
        now: Optional :class:`datetime` overriding the configured day/night clock.
        attacks: Optional live moveset; when given it takes precedence over the
            stored one so move-type-gated evolutions (e.g. Sylveon's Fairy-move
            requirement) evaluate against freshest-known data.
        pokemon_defeated: Optional defeat count for ``minimumDefeated``
            evolutions (e.g. Pawmo -> Pawmot). Supply it — together with
            ``attacks`` — from state the caller already holds so this checker
            performs **no synchronous disk I/O** on Anki's review path (coding
            guideline: never do synchronous disk I/O mid-review). ``None``
            keeps the legacy behavior of one guarded DB read for the value(s)
            the caller didn't supply.

    Returns:
        The evolved species id if the evolution was triggered, else ``None``.
    """
    settings_obj = _settings()  # registry-backed; no singletons/aqt import

    if (
        not settings_obj.get("evolution.friendship_time_enabled", True)
        or everstone
        or evolution_rejected
    ):
        return None

    # Defeat count and known attacks for move/defeat-gated evolutions (Sylveon
    # needs a Fairy-type move; Pawmo/Rellor need defeat milestones). Production
    # callers hold both in memory already and pass them in, so the victory-time
    # path does zero synchronous disk I/O. The DB seam below runs only for the
    # values a caller left as None (legacy/test callers), guarded so a
    # missing/unavailable store degrades to 0 / no attacks rather than raising.
    defeats_known = pokemon_defeated is not None
    if pokemon_defeated is None or attacks is None:
        try:
            db = services.db
            if db is not None and hasattr(db, "get_pokemon"):
                record = db.get_pokemon(individual_id) or {}
                if isinstance(record, dict):
                    if not defeats_known:
                        pokemon_defeated = record.get("pokemon_defeated", 0) or 0
                    if attacks is None:
                        attacks = record.get("attacks") or []
        except Exception:
            if not defeats_known:
                pokemon_defeated = 0
            if attacks is None:
                attacks = []
    if pokemon_defeated is None:
        # Caller supplied an explicit None-ish count; treat as no milestone.
        pokemon_defeated = 0

    shim = {
        "id": pokemon_id,
        "friendship": friendship,
        "everstone": everstone,
        "pokemon_defeated": pokemon_defeated,
        # The live moveset lets _select_evolution honor move-type gates
        # (e.g. Sylveon) instead of offering them on unconfirmed data.
        "attacks": attacks,
    }
    readiness = evolution_readiness(shim, now)

    # 1. Friendship/time evolution auto-prompts here.
    if readiness["method"] == "friendship" and readiness["ready"]:
        evo_window.ask_pokemon_evo(individual_id, pokemon_id, readiness["evo_id"])
        return readiness["evo_id"]

    # 2. minimumDefeated evolution auto-prompts on the victory milestone. A plain
    # level-ready Pokémon (no defeat condition) is deliberately NOT prompted here.
    if readiness["method"] == "level" and readiness["ready"]:
        from .pokedex_functions import _load_pokedex_cache, search_pokedex_by_id

        pokedex_data = _load_pokedex_cache()
        target_data = pokedex_data.get(search_pokedex_by_id(readiness["evo_id"]))
        if (
            target_data
            and (target_data.get("evoCondition") or "").lower() == "minimumdefeated"
        ):
            evo_window.ask_pokemon_evo(individual_id, pokemon_id, readiness["evo_id"])
            return readiness["evo_id"]

    return None
