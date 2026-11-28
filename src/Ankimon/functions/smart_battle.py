"""Smart Battle switch (``battle.smart_battle``).

When on, the active Pokémon always uses its strongest move
(``smart_move_selection``) and, at each new wild encounter, the best team
member for the matchup is sent out (``smart_team_switch``).

Because that plays at least as well as choosing moves by hand, it carries the
same 50% Pokémon and Trainer XP penalty as ``controls.allow_to_choose_moves``.
"""


def _truthy(value):
    return value is True or str(value).strip().lower() in ("true", "1")


def smart_battle_enabled(settings):
    try:
        return _truthy(settings.get("battle.smart_battle", False))
    except Exception:
        return False


def reduced_xp(settings):
    """True when the 50% XP penalty applies: Allow Choosing Moves or Smart
    Battle (not stacked)."""
    try:
        if _truthy(settings.get("controls.allow_to_choose_moves", False)):
            return True
    except Exception:
        pass
    return smart_battle_enabled(settings)
