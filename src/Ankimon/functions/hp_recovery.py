"""HP recovery rules: healing-item amounts and regeneration while resting.

Aqt-free. The database applies ``regenerate`` to saved rows
(``AnkimonDB.regenerate_pokemon_hp``); the item bag uses ``heal_item_amount``.
"""

import math
from datetime import datetime

# Each healing item restores its classic flat amount or a share of max HP,
# whichever is larger, so a Potion still matters to a Pokemon with 300 HP.
HEAL_ITEMS = {
    "potion": (20, 0.25),
    "sweet-heart": (20, 0.25),
    "berry-juice": (20, 0.25),
    "fresh-water": (30, 0.30),
    "soda-pop": (50, 0.40),
    "super-potion": (60, 0.50),
    "energy-powder": (60, 0.50),
    "lemonade": (70, 0.60),
    "moomoo-milk": (100, 0.70),
    "hyper-potion": (120, 0.80),
    "energy-root": (120, 0.80),
    "full-restore": (1000, 1.0),
    "max-potion": (1000, 1.0),
}

# Rest that takes a Pokemon from empty to full HP.
REGEN_FULL_HEAL_SECONDS = 8 * 60 * 60
# How often the battle loop credits regeneration to the active Pokemon.
REGEN_TICK_SECONDS = 60


def heal_item_amount(item_name, max_hp, flat=None):
    """HP a healing item restores to a Pokemon with ``max_hp``.

    ``flat`` overrides the table's flat amount (and is the whole answer for an
    item the table does not know).
    """
    table_flat, fraction = HEAL_ITEMS.get(item_name, (0, 0.0))
    flat = table_flat if flat is None else max(0, int(flat))
    return max(flat, math.ceil(max(0, int(max_hp)) * fraction))


def heal_item_summary(item_name):
    """One English sentence describing what a healing item restores, or ''."""
    if item_name not in HEAL_ITEMS:
        return ""
    flat, fraction = HEAL_ITEMS[item_name]
    if fraction >= 1:
        return "Restores all HP."
    return f"Restores {round(fraction * 100)}% of max HP (at least {flat} HP)."


def swap_heals(settings):
    """Whether the classic swap rule is on (``battle.heal_on_swap``).

    Under it, switching the active Pokemon fully heals both the one leaving and
    the one arriving, and a mid-battle swap costs no turn.
    """
    try:
        return settings.get("battle.heal_on_swap", False) is True
    except Exception:
        return False


def current_timestamp():
    """Wall-clock seconds; goes through ``datetime`` so the harness clock applies."""
    return datetime.now().timestamp()


def _is_clock(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def keep_rest_clock(record, max_hp):
    """Make a record's rest clock (``hp_regen_at``) agree with its ``current_hp``.

    The clock marks the time up to which regeneration has been credited. A
    wounded Pokemon needs one, so a missing clock starts now; a Pokemon at full
    HP has none, so a later wound cannot be credited with time spent healthy.
    Call it wherever play writes a new ``current_hp``. Without an integer HP
    and max HP there is nothing to decide, and the record is left alone.
    """
    hp = record.get("current_hp")
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (hp, max_hp)):
        return
    if hp >= max_hp:
        record.pop("hp_regen_at", None)
    elif hp > 0 and not _is_clock(record.get("hp_regen_at")):
        record["hp_regen_at"] = current_timestamp()


def regenerate(current_hp, max_hp, regen_at, now):
    """Credit rest between ``regen_at`` and ``now``.

    Returns ``(hp, regen_at)``: the HP after regeneration and the time up to
    which rest has been credited. A missing or future ``regen_at`` starts the
    clock without a gain, a fainted Pokemon does not regenerate, and time spent
    at full HP is not banked. Whole HP only: the returned ``regen_at`` keeps the
    remainder so frequent calls lose nothing.
    """
    if not _is_clock(regen_at):
        return current_hp, now
    if regen_at > now or max_hp < 1 or current_hp <= 0 or current_hp >= max_hp:
        return current_hp, now
    # The epsilon keeps an exact rest (half the time, half the HP) from
    # rounding down to one HP short.
    gained = int((now - regen_at) * max_hp / REGEN_FULL_HEAL_SECONDS + 1e-9)
    if current_hp + gained >= max_hp:
        return max_hp, now
    return current_hp + gained, regen_at + gained * REGEN_FULL_HEAL_SECONDS / max_hp


def credit_rest(db, pokemon_data, logger=None):
    """Regenerate a saved Pokemon that is about to become active.

    Returns the record to load: the re-read row when rest was credited,
    otherwise ``pokemon_data`` unchanged. A failure never blocks the load.
    """
    individual_id = pokemon_data.get("individual_id")
    if not individual_id:
        return pokemon_data
    try:
        gained = db.regenerate_pokemon_hp(str(individual_id))
        if isinstance(gained, dict) and gained:
            rested = db.get_pokemon(str(individual_id))
            if isinstance(rested, dict):
                return rested
    except Exception as exc:
        if logger is not None:
            logger.log("warning", f"Could not regenerate HP: {exc}")
    return pokemon_data
