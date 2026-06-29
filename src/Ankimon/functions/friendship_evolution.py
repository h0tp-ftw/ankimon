"""Friendship- and time-of-day-based evolution helpers.

Single source of truth for the in-game day/night clock, a species' friendship
evolution(s), whether a Pokémon is ready to evolve right now, and triggering that
evolution via :class:`EvoWindow`. Friendship evolutions live in
``pokemon_evolution.csv`` as level-up rows (``evolution_trigger_id == 1``) with a
positive ``minimum_happiness`` and an optional ``time_of_day``; the legacy
level-up code skips them because their ``minimum_level`` is blank.

``settings_obj`` is imported lazily inside each function, not at module top level:
``singletons`` imports ``pc_box`` (which imports this module) before binding
``settings_obj``, so a top-level import would crash at addon load.
"""

from __future__ import annotations

import functools
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple, Optional

from .pokedex_functions import (
    pokemon_evolves_from_id,
    return_name_for_id,
    rows_for_key_in_table,
)
from ..resources import poke_evo_path
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
    """

    evo_id: int
    evo_name: str
    min_happiness: int
    time_of_day: Optional[str]


class LevelEvolution(NamedTuple):
    """A single plain level-up evolution (counterpart to :class:`FriendshipEvolution`).

    Attributes:
        evo_id: National Pokédex id of the evolved species.
        evo_name: Capitalised display name of the evolved species.
        min_level: Level required to evolve.
    """

    evo_id: int
    evo_name: str
    min_level: int


def _now_in_configured_tz() -> datetime:
    """Return the current time in the user's configured time zone.

    Auto-detect (default) uses the device's local time via ``datetime.now()``;
    when ``evolution.timezone_auto`` is off, a fixed ``evolution.timezone_offset``
    (hours, clamped to ±14) is applied instead.
    """
    settings_obj = services.settings  # registry-backed; no singletons/aqt import

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
    settings_obj = services.settings  # registry-backed; no singletons/aqt import

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
    settings_obj = services.settings  # registry-backed; no singletons/aqt import

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
        rows = rows_for_key_in_table("evolved_species_id", evo, poke_evo_path)
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

            name = return_name_for_id(int(evo))
            evo_name = name.capitalize() if name else str(evo)

            evolutions.append(
                FriendshipEvolution(
                    evo_id=int(evo),
                    evo_name=evo_name,
                    min_happiness=min_happiness,
                    time_of_day=time_of_day,
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

    Like :func:`get_friendship_evolutions_for_species` but keeps level-up rows
    (``evolution_trigger_id == 1``) with a positive ``minimum_level`` and no
    friendship requirement, so the two helpers never double-count a row.
    ``lru_cache``d on ``pokemon_id``.

    Args:
        pokemon_id: National Pokédex id of the *pre-evolution* species.

    Returns:
        A tuple of :class:`LevelEvolution` entries sorted by ``evo_id``; empty if
        the species has no plain level-up evolution.
    """
    evolutions: list[LevelEvolution] = []
    for evo in pokemon_evolves_from_id(pokemon_id):
        # Scan every row for this evolved species and keep the first that is a
        # plain level-up row. An evolved species may carry several method rows
        # (e.g. a level-up row *and* a friendship row), so first-match would pick
        # the wrong one when the level row isn't listed first.
        for row in rows_for_key_in_table("evolved_species_id", evo, poke_evo_path):
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

            name = return_name_for_id(int(evo))
            evo_name = name.capitalize() if name else str(evo)

            evolutions.append(
                LevelEvolution(
                    evo_id=int(evo),
                    evo_name=evo_name,
                    min_level=min_level,
                )
            )
            break  # one level-up row per evolved species is enough

    evolutions.sort(key=lambda e: e.evo_id)
    return tuple(evolutions)


def _select_evolution(
    evos: tuple[FriendshipEvolution, ...], time_of_day: str
) -> FriendshipEvolution:
    """Pick the most appropriate friendship evolution for the current time.

    Prefers an evolution eligible right now (its ``time_of_day`` matches, or it
    has none); among those, an explicitly time-gated row beats a blank-time one,
    then lowest ``evo_id``. If none is eligible now, falls back to the lowest
    ``evo_id`` so the UI can still show e.g. "waiting for Night".

    Args:
        evos: Non-empty tuple from :func:`get_friendship_evolutions_for_species`.
        time_of_day: Current time of day (``"day"`` or ``"night"``).

    Returns:
        The chosen :class:`FriendshipEvolution`.
    """
    eligible_now = [e for e in evos if e.time_of_day in (time_of_day, None)]
    if eligible_now:
        # Prefer explicit-time rows (e.g. Espeon@day) over blank-time rows, then
        # lowest evo_id. ``time_of_day is None`` sorts last via the bool key.
        return min(eligible_now, key=lambda e: (e.time_of_day is None, e.evo_id))
    # Nothing matches the current time; still return a representative.
    return min(evos, key=lambda e: e.evo_id)


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
        required_time, time_ok, status_text, bar_max, rejected``. ``bar_max``
        defaults to :data:`MAX_FRIENDSHIP` outside the friendship path.
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
        )

    chosen = _select_evolution(evos, time_of_day)
    evo_id = chosen.evo_id
    evo_name = chosen.evo_name
    min_happiness = chosen.min_happiness
    required_time = chosen.time_of_day

    friendship_remaining = max(0, min_happiness - friendship)
    time_ok = required_time is None or required_time == time_of_day
    ready = (not everstone) and friendship >= min_happiness and time_ok

    status_text = _build_status_text(
        everstone=everstone,
        ready=ready,
        evo_name=evo_name,
        friendship_remaining=friendship_remaining,
        required_time=required_time,
        time_ok=time_ok,
        time_of_day=time_of_day,
        rejected=evolution_rejected,
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
    }


def _level_readiness(
    *,
    species_id: int,
    level: int,
    everstone: bool,
    friendship: int,
    evolution_rejected: bool,
    not_evolvable: dict,
) -> dict:
    """Compute readiness for a plain level-up evolution.

    Called by :func:`evolution_readiness` when the species has no friendship
    evolution; returns ``not_evolvable`` unchanged if it has no level-up evolution
    either. Ignores ``evolution_rejected`` (the manual button still shows) and
    time of day: ``ready = (not everstone) and level >= min_level``.

    Returns:
        A readiness dict with ``method="level"`` (or ``not_evolvable``).
    """
    level_evos = get_level_evolutions_for_species(species_id)
    if not level_evos:
        return not_evolvable

    chosen = min(level_evos, key=lambda e: e.evo_id)
    evo_name = chosen.evo_name
    min_level = chosen.min_level

    ready = (not everstone) and level >= min_level

    if everstone:
        status_text = "Everstone prevents evolution"
    elif ready and evolution_rejected:
        status_text = "Evolution rejected — tap Evolve now to override"
    elif ready:
        status_text = f"Ready to evolve into {evo_name}!"
    else:
        status_text = f"Evolves into {evo_name} at Lv{min_level}"

    return {
        "evolvable": True,
        "ready": ready,
        "method": "level",
        "evo_id": chosen.evo_id,
        "evo_name": evo_name,
        "min_happiness": None,
        "current_friendship": friendship,
        "friendship_remaining": 0,
        "required_time": None,
        "time_ok": True,
        "status_text": status_text,
        "bar_max": MAX_FRIENDSHIP,
        "rejected": evolution_rejected,
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
) -> str:
    """Build the human-readable readiness line shown in the UI.

    Examples:
        - ``"Everstone prevents evolution"``
        - ``"Evolution rejected — tap Evolve now to override"``
        - ``"Ready to evolve into Espeon!"``
        - ``"Ready — waiting for Night (now Day)"``
        - ``"40 friendship to evolve into Espeon · needs Day"``
    """
    if everstone:
        return "Everstone prevents evolution"

    if ready and rejected:
        return "Evolution rejected — tap Evolve now to override"

    if ready:
        return f"Ready to evolve into {evo_name}!"

    # Friendship is high enough but the time of day is wrong.
    if friendship_remaining == 0 and not time_ok and required_time is not None:
        return (
            f"Ready — waiting for {required_time.capitalize()} "
            f"(now {time_of_day.capitalize()})"
        )

    # Still needs more friendship.
    text = f"{friendship_remaining} friendship to evolve into {evo_name}"
    if required_time is not None:
        text += f" · needs {required_time.capitalize()}"
    return text


def check_friendship_evolution_for_pokemon(
    individual_id,
    pokemon_id,
    evo_window,
    everstone: bool = False,
    friendship: int = 0,
    evolution_rejected: bool = False,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Prompt a friendship evolution for a Pokémon if it is ready.

    Honors the ``evolution.friendship_time_enabled`` toggle, the Everstone, and a
    prior rejection. When ready, opens :meth:`EvoWindow.ask_pokemon_evo`.

    Returns:
        The evolved species id if the evolution was triggered, else ``None``.
    """
    settings_obj = services.settings  # registry-backed; no singletons/aqt import

    if (
        not settings_obj.get("evolution.friendship_time_enabled", True)
        or everstone
        or evolution_rejected
    ):
        return None

    shim = {"id": pokemon_id, "friendship": friendship, "everstone": everstone}
    readiness = evolution_readiness(shim, now)
    # Only the friendship path auto-prompts here. Level-up evolutions are
    # auto-handled by check_evolution_for_pokemon (pokedex_functions.py); a
    # level-ready Pokémon surfaced by evolution_readiness must NOT be auto-
    # prompted by this friendship checker (the manual PC button still covers it).
    if readiness["method"] == "friendship" and readiness["ready"]:
        evo_window.ask_pokemon_evo(individual_id, pokemon_id, readiness["evo_id"])
        return readiness["evo_id"]
    return None
