from anki.hooks import addHook
from aqt import gui_hooks, mw

from .singletons import settings_obj, logger
from .pyobj.ankimon_sync import setup_ankimon_sync_hooks, check_and_sync_pokemon_data
from .pyobj.tip_of_the_day import show_tip_of_the_day
from .pyobj.pokemon_trade import check_and_award_monthly_pokemon
from .pyobj.error_handler import show_warning_with_traceback
from .functions.pokedex_functions import clear_pokedex_caches
from .functions.learnset_retrieval import clear_learnset_cache
from .functions.encounter_functions import clear_encounter_cache

sync_dialog = None


def _on_profile_did_open(online_connectivity):
    def handler():
        try:
            show_tip_of_the_day()
        except Exception as e:
            show_warning_with_traceback(
                parent=mw, exception=e, message="Error showing tip of the day:"
            )

        try:
            if online_connectivity:
                check_and_award_monthly_pokemon(logger)
            else:
                logger.log(
                    "info",
                    "Skipping monthly pokemon check due to no internet connectivity.",
                )
        except Exception as e:
            show_warning_with_traceback(
                parent=mw, exception=e, message="Error awarding monthly pokemon:"
            )

        try:
            ankiweb_sync = settings_obj.get("misc.ankiweb_sync")
            if not ankiweb_sync:
                logger.log(
                    "info",
                    "AnkiWeb sync is disabled in settings - skipping sync system initialization",
                )
                return

            setup_ankimon_sync_hooks(settings_obj, logger)

            if not online_connectivity:
                logger.log(
                    "info", "No connection - AnkiWeb sync is disabled for this session"
                )
            else:
                global sync_dialog
                sync_dialog = check_and_sync_pokemon_data(settings_obj, logger)
                logger.log("info", "Ankimon sync system initialized successfully")
        except Exception as e:
            show_warning_with_traceback(
                parent=mw, exception=e, message="Error setting up sync system:"
            )

    return handler

def _on_profile_close():
    """Clear all performance caches when Anki session ends"""
    try:
        clear_pokedex_caches()
        clear_learnset_cache()
        clear_encounter_cache()
    except Exception as e:
        print(f"Error clearing caches on profile close: {e}")

def register_profile_hooks(
    online_connectivity,
    backup_manager,
    CatchPokemonHook,
    DefeatPokemonHook,
    add_catch_pokemon_hook,
    add_defeat_pokemon_hook,
    collected_pokemon_ids,
):
    def on_profile_loaded():
        mw.defeatpokemon = DefeatPokemonHook
        mw.catchpokemon = lambda: CatchPokemonHook(collected_pokemon_ids)
        mw.add_catch_pokemon_hook = add_catch_pokemon_hook
        mw.add_defeat_pokemon_hook = add_defeat_pokemon_hook

    addHook("profileLoaded", on_profile_loaded)
    gui_hooks.profile_did_open.append(_on_profile_did_open(online_connectivity))
    gui_hooks.profile_will_close.append(backup_manager.on_anki_close)
    gui_hooks.profile_will_close.append(_on_profile_close)
