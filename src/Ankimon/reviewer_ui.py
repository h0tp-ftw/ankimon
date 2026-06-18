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


_team_cycle_index = 0


def cycle_team_pokemon():
    """Cycle the active/main Pokémon to the next of the first 3 team slots.

    Reuses ``set_main_from_record`` -- the same routine the collection picker
    uses -- so the outgoing main's progress is saved and the incoming Pokémon
    keeps its stored HP (cycling is never a free heal).
    """
    global _team_cycle_index
    from .functions.update_main_pokemon import set_main_from_record

    db = mw.ankimon_db
    team = db.get_team() or []
    team_ids = [e.get("individual_id") for e in team[:3] if e.get("individual_id")]
    if len(team_ids) < 2:
        tooltip("Need at least 2 team members to cycle.")
        return

    _team_cycle_index = (_team_cycle_index + 1) % len(team_ids)
    record = db.get_pokemon(team_ids[_team_cycle_index])
    if not record or not record.get("id"):
        tooltip("Could not load that team member.")
        return

    set_main_from_record(record, main_pokemon)

    # Refresh the in-review HUD (life bar / sprite).
    try:
        reviewer = mw.reviewer
        if reviewer and reviewer.web:
            reviewer_obj.update_life_bar(reviewer, 0, 0)
    except Exception as e:
        logger.log("error", f"Team cycle HUD refresh failed: {e}")

    name = main_pokemon.nickname or main_pokemon.name
    tooltip(f"Switched to {name} (Lvl {main_pokemon.level}, HP {main_pokemon.hp}/{main_pokemon.max_hp})")


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
