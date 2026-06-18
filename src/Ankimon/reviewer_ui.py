from anki.hooks import wrap
from aqt.reviewer import Reviewer
from aqt.utils import downArrow, tooltip, tr
from aqt import mw

from .singletons import (
    enemy_pokemon,
    main_pokemon,
    ankimon_tracker_obj,
    test_window,
    evo_window,
    logger,
    achievements,
    trainer_card,
    reviewer_obj,
)
from .functions.encounter_functions import (
    catch_pokemon,
    kill_pokemon,
    new_pokemon,
)
from .texts import _bottomHTML_template, button_style

_collected_pokemon_ids = set()


def set_collected_ids(ids):
    global _collected_pokemon_ids
    _collected_pokemon_ids = ids


def catch_shortcut_function():
    if enemy_pokemon.hp < 1:
        catch_pokemon(
            enemy_pokemon,
            ankimon_tracker_obj,
            logger,
            "",
            _collected_pokemon_ids,
            achievements,
        )
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)
    else:
        tooltip("You only catch a pokemon once it's fainted!")


def defeat_shortcut_function():
    if enemy_pokemon.hp < 1:
        kill_pokemon(
            main_pokemon, enemy_pokemon, evo_window, logger, achievements, trainer_card
        )
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)
    else:
        tooltip("Wild pokemon has to be fainted to defeat it!")


_last_team_cycle = 0.0


def cycle_team_pokemon():
    """Switch the active/main Pokémon to the next non-fainted of the first 3 team
    slots, relative to whoever is currently active.

    Reuses ``set_main_from_record`` (the same routine the collection picker uses)
    so the outgoing main's progress is saved and the incoming Pokémon keeps its
    stored HP -- cycling is never a free heal. Fainted (0-HP) members are skipped
    on purpose: switching a 0-HP Pokémon in would just be auto-revived + trigger
    an enemy reroll by the next card's faint handler, i.e. a free heal/escape.
    """
    global _last_team_cycle
    import time
    from .functions.update_main_pokemon import set_main_from_record

    now = time.time()
    if now - _last_team_cycle < 0.3:  # debounce: each switch writes the DB
        return
    _last_team_cycle = now

    db = mw.ankimon_db
    team = db.get_team() or []
    team_ids = [e.get("individual_id") for e in team[:3] if e.get("individual_id")]
    if len(team_ids) < 2:
        tooltip("Need at least 2 team members to cycle.")
        return

    # Start from the current main's slot so "next" is relative to who's active.
    current_id = getattr(main_pokemon, "individual_id", None)
    try:
        start = team_ids.index(current_id)
    except ValueError:
        start = -1  # current main isn't in the first 3 -> first step lands on slot 0

    n = len(team_ids)
    for step in range(1, n + 1):
        cand_id = team_ids[(start + step) % n]
        if cand_id == current_id:
            continue
        record = db.get_pokemon(cand_id)
        if not record or not record.get("id"):
            continue
        stored_hp = record.get("current_hp", record.get("hp", 1))
        try:
            stored_hp = int(stored_hp)
        except (TypeError, ValueError):
            stored_hp = 1
        if stored_hp < 1:
            continue  # skip fainted members (see docstring)

        set_main_from_record(record, main_pokemon)

        try:
            reviewer = mw.reviewer
            if reviewer and reviewer.web:
                reviewer_obj.update_life_bar(reviewer, 0, 0)
        except Exception as e:
            logger.log("error", f"Team cycle HUD refresh failed: {e}")

        name = main_pokemon.nickname or main_pokemon.name
        tooltip(f"Switched to {name} (Lvl {main_pokemon.level}, HP {main_pokemon.hp}/{main_pokemon.max_hp})")
        return

    tooltip("No other healthy team member to switch to.")


def setup_reviewer_ui(catch_shortcut: str, defeat_shortcut: str, reviewer_buttons: bool, team_cycle_shortcut: str = "9"):
    catch_key = str(catch_shortcut).lower()
    defeat_key = str(defeat_shortcut).lower()
    team_cycle_key = str(team_cycle_shortcut).lower()

    def _shortcutKeys_wrap(self, _old):
        original = _old(self)
        original.append((catch_key, lambda: catch_shortcut_function()))
        original.append((defeat_key, lambda: defeat_shortcut_function()))
        original.append((team_cycle_key, lambda: cycle_team_pokemon()))
        return original

    Reviewer._shortcutKeys = wrap(Reviewer._shortcutKeys, _shortcutKeys_wrap, "around")

    if reviewer_buttons is True:
        Review_linkHandler_Original = Reviewer._linkHandler

        def linkHandler_wrap(reviewer, url):
            if url == "catch":
                catch_shortcut_function()
            elif url == "defeat":
                defeat_shortcut_function()
            else:
                Review_linkHandler_Original(reviewer, url)

        def _bottomHTML(self) -> str:
            return _bottomHTML_template % dict(
                edit=tr.studying_edit(),
                editkey=tr.actions_shortcut_key(val="E"),
                more=tr.studying_more(),
                morekey=tr.actions_shortcut_key(val="M"),
                downArrow=downArrow(),
                time=self.card.time_taken() // 1000,
                CatchKey=tr.actions_shortcut_key(val=f"{catch_key}"),
                DefeatKey=tr.actions_shortcut_key(val=f"{defeat_key}"),
            )

        Reviewer._bottomHTML = _bottomHTML
        Reviewer._linkHandler = linkHandler_wrap
