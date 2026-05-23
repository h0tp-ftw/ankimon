from anki.hooks import wrap
from aqt.reviewer import Reviewer
from aqt.utils import downArrow, tooltip, tr

from .functions.encounter_functions import catch_pokemon, kill_pokemon, new_pokemon
from .singletons import (
    achievements,
    ankimon_tracker_obj,
    enemy_pokemon,
    evo_window,
    logger,
    main_pokemon,
    reviewer_obj,
    test_window,
    trainer_card,
)
from .texts import _bottomHTML_template

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


def setup_reviewer_ui(
    catch_shortcut: str, defeat_shortcut: str, reviewer_buttons: bool
):
    catch_key = str(catch_shortcut).lower()
    defeat_key = str(defeat_shortcut).lower()

    def _shortcutKeys_wrap(self, _old):
        original = _old(self)
        original.append((catch_key, lambda: catch_shortcut_function()))
        original.append((defeat_key, lambda: defeat_shortcut_function()))
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
