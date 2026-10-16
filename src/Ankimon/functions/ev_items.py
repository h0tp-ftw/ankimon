"""EV-training item effects: vitamins, wings, mochi, and effort candies.

Stat keys match ``PokemonObject.ev`` ("hp"/"atk"/"def"/"spa"/"spd"/"spe").
Vitamins grant +10 EV with no soft cap (unlike the mainline games' 100-EV
vitamin ceiling — deliberately dropped here so a vitamin never "does
nothing" short of the real 252/510 limits). Wings and mochi share the same
+1-per-use effect (mochi is the Kitakami-region reskin of wings in
Scarlet/Violet). The candy tiers mirror Legends: Arceus' S/M/L effort
candies (+2/+10/+999 — the last always hitting the per-stat cap), mapped
onto this game's base/``-l``/``-xl`` item names.

``fresh-start-mochi`` resets EVs to 0 rather than training them, so it is
deliberately not covered here.
"""

VITAMINS = {
    "hp-up": "hp",
    "protein": "atk",
    "iron": "def",
    "calcium": "spa",
    "zinc": "spd",
    "carbos": "spe",
}

_WING_MOCHI_STATS = {
    "health": "hp",
    "muscle": "atk",
    "resist": "def",
    "genius": "spa",
    "clever": "spd",
    "swift": "spe",
}
WINGS = {f"{prefix}-wing": stat for prefix, stat in _WING_MOCHI_STATS.items()}
MOCHI = {f"{prefix}-mochi": stat for prefix, stat in _WING_MOCHI_STATS.items()}

_CANDY_STATS = {
    "health": "hp",
    "mighty": "atk",
    "tough": "def",
    "smart": "spa",
    "courage": "spd",
    "quick": "spe",
}
CANDIES = {}
for _prefix, _stat in _CANDY_STATS.items():
    CANDIES[f"{_prefix}-candy"] = (_stat, 2)
    CANDIES[f"{_prefix}-candy-l"] = (_stat, 10)
    CANDIES[f"{_prefix}-candy-xl"] = (_stat, 999)
del _prefix, _stat

# Standard mainline caps. A single item only ever trains ONE stat, unlike a
# defeated enemy's multi-stat ev_yield (see utils.limit_ev_yield), so the
# randomized overflow reduction that needs is unnecessary here — capping the
# one proposed stat against both ceilings is exactly equivalent.
_STAT_CAP = 252
_TOTAL_CAP = 510


def _cap_single_stat_gain(current_ev: dict, stat: str, amount: int) -> int:
    """How many of ``amount`` EV points can land on ``stat`` right now."""
    per_stat_room = _STAT_CAP - current_ev.get(stat, 0)
    total_room = _TOTAL_CAP - sum(current_ev.values())
    return max(0, min(amount, per_stat_room, total_room))


def ev_item_stat(item_name: str) -> str | None:
    """Which EV stat ``item_name`` trains, or None if it isn't an EV item."""
    name = item_name.lower()
    if name in VITAMINS:
        return VITAMINS[name]
    if name in WINGS:
        return WINGS[name]
    if name in MOCHI:
        return MOCHI[name]
    if name in CANDIES:
        return CANDIES[name][0]
    return None


def apply_ev_item(current_ev: dict, item_name: str) -> tuple[dict, int]:
    """Train one EV stat with ``item_name``.

    Returns ``(new_ev, granted)``: a full ev dict with the trained stat
    updated, and how many EV points actually landed (0 if the item isn't an
    EV item, or the stat is already at the 252/510 cap).
    """
    name = item_name.lower()
    new_ev = dict(current_ev)

    if name in VITAMINS:
        stat, amount = VITAMINS[name], 10
    elif name in WINGS:
        stat, amount = WINGS[name], 1
    elif name in MOCHI:
        stat, amount = MOCHI[name], 1
    elif name in CANDIES:
        stat, amount = CANDIES[name]
    else:
        return new_ev, 0

    if amount <= 0:
        return new_ev, 0

    granted = _cap_single_stat_gain(current_ev, stat, amount)
    new_ev[stat] = current_ev.get(stat, 0) + granted
    return new_ev, granted
