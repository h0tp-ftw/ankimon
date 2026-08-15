from anki.hooks import addHook
try:
    from anki.hooks import remHook
except ImportError:
    remHook = None
from aqt import gui_hooks, mw

from .services import services
from .singletons import settings_obj, logger
from .utils import test_online_connectivity
from .pyobj.ankimon_sync import setup_ankimon_sync_hooks, check_and_sync_pokemon_data
from .pyobj.tip_of_the_day import show_tip_of_the_day
from .pyobj.pokemon_trade import check_and_award_monthly_pokemon
from .pyobj.error_handler import show_warning_with_traceback
from .functions.pokedex_functions import clear_pokedex_caches
from .functions.learnset_retrieval import clear_learnset_cache
from .functions.encounter_functions import clear_encounter_cache, clear_auto_battle_override

sync_dialog = None

# Cache-clear-on-close (F20): the pokedex / learnset / encounter in-memory
# caches live for the whole Python process, so without this a profile switch
# would carry the previous profile's cached tables into the next one. Dropping
# them when the profile closes makes each profile start cleanly from disk.
# The registration record lives on the services registry (F31 pattern) rather
# than a module-level flag: it survives a re-execution of this module (an
# add-on reload), so re-registering swaps the handler in place instead of
# stacking a second copy onto gui_hooks.profile_will_close.
#
# The pending auto-battle catch/defeat override (encounter_functions.py's
# _auto_battle_override) is the same kind of process-lifetime bare global as
# these caches, so it needs the same treatment: without clearing it here, a
# user who arms an override in one profile and switches profiles before the
# wild Pokemon faints would carry that override into the next profile, where
# it could silently force-catch/force-defeat the first faint under
# auto-battle before that profile's user has touched anything.
_CLOSE_HANDLER_RECORD = "_profile_close_cache_clear_handler"
_DID_OPEN_HANDLER_RECORD = "_profile_did_open_handler"
_WILL_CLOSE_BACKUP_RECORD = "_profile_will_close_backup_handler"
_PROFILE_LOADED_HANDLER_RECORD = "_profile_loaded_handler"


def _on_profile_close():
    """Clear all performance caches when the Anki session/profile ends."""
    try:
        clear_pokedex_caches()
        clear_learnset_cache()
        clear_encounter_cache()
        clear_auto_battle_override()
    except Exception as e:
        logger.log("error", f"Error clearing caches on profile close: {e}")


def _on_profile_did_open(online_connectivity):
    def handler():
        # Mobile-review sync bootstrap (F20 deferred half): initialise the revlog
        # watermark on first run, clear the desktop session set for a fresh
        # inter-sync interval, run a startup detection pass to catch reviews pulled
        # in by the startup sync, and restore the pending-count badge. Guarded so a
        # missing DB / collection is a benign no-op.
        try:
            db = services.db
            col = services.col if services.col is not None else mw.col
            if db is not None and col is not None:
                from .functions.mobile_sync import clear_desktop_session
                from .menu_buttons import update_mobile_badge

                watermark = db.get_mobile_watermark()
                if watermark == 0:
                    # First-ever run — set watermark to NOW so no retroactive battles
                    initial_watermark = col.db.scalar("SELECT MAX(id) FROM revlog") or 0
                    db.set_mobile_watermark(initial_watermark or 0)
                # Always clear session set on profile open (fresh inter-sync interval)
                clear_desktop_session()

                # Run detection immediately to catch reviews pulled in by startup sync
                if settings_obj.get("mobile.enabled", True):
                    try:
                        from .functions.mobile_sync import process_mobile_reviews_after_sync
                        process_mobile_reviews_after_sync(
                            col=col,
                            ankimon_db=db,
                            settings_obj=settings_obj,
                            logger=logger,
                        )
                    except Exception as sync_err:
                        logger.log("error", f"Failed to run startup mobile reviews sync: {sync_err}")

                # Restore badge — show pending count from previous session
                pending = db.get_pending_mobile_count()
                update_mobile_badge(pending)
        except Exception as e:
            logger.log("error", f"Failed to initialize mobile watermark: {e}")

        # Register the AnkiWeb sync hooks SYNCHRONOUSLY here — not in the
        # backgrounded connectivity callback below. Anki fires profile_did_open
        # immediately before its own auto-sync-on-open (aqt loadProfile), so
        # registering on_sync_did_finish now guarantees it is attached before
        # that first sync completes; deferring it into the background task would
        # race the sync and miss the reviews it pulls in. Registration needs no
        # connectivity, no collection, and is idempotent (F31 dedup), and mobile
        # detection must not depend on the legacy misc.ankiweb_sync file-sync
        # toggle — the hook self-gates internally.
        try:
            setup_ankimon_sync_hooks(settings_obj, logger)
        except Exception as e:
            logger.log("error", f"Failed to register Ankimon sync hooks: {e}")

        try:
            show_tip_of_the_day()
        except Exception as e:
            show_warning_with_traceback(
                parent=mw, exception=e, message="Error showing tip of the day:"
            )

        # Check for Badge 11 candidates on profile open
        # This detects cards that have been unsuspended OR untagged
        # and adds them to the candidates list for later review
        try:
            from .functions.badges_functions import check_unleeched_cards
            check_unleeched_cards(
                services.col if services.col is not None else mw.col,
                services.db,
                getattr(services, "achievements", None),
            )
        except Exception as e:
            logger.log("error", f"Failed to evaluate leech badges on profile open: {e}")

        def check_connectivity_bg() -> bool:
            # Only run the actual check if we think we're offline
            if not online_connectivity:
                return test_online_connectivity()
            return online_connectivity

        def on_done(future) -> None:
            is_online = future.result()
            # We want to use the result of the background check
            try:
                if is_online:
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
                # The sync HOOKS are already registered synchronously above, so
                # mobile-review detection is live regardless of this block. What
                # remains here is only the OPT-IN file-based data sync
                # (subsystem B) — it copies/overwrites ankimon.db across devices,
                # so it stays gated behind misc.ankiweb_sync + connectivity.
                ankiweb_sync = settings_obj.get("misc.ankiweb_sync")
                if not ankiweb_sync:
                    logger.log(
                        "info",
                        "AnkiWeb file-sync disabled in settings - mobile-review detection still active",
                    )
                elif not is_online:
                    logger.log(
                        "info", "No connection - AnkiWeb file-sync disabled for this session"
                    )
                else:
                    global sync_dialog
                    sync_dialog = check_and_sync_pokemon_data(settings_obj, logger)
                    logger.log("info", "Ankimon file-sync system initialized successfully")
            except Exception as e:
                show_warning_with_traceback(
                    parent=mw, exception=e, message="Error setting up sync system:"
                )

        mw.taskman.run_in_background(check_connectivity_bg, on_done)

    return handler


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

    # All four registrations use the same registry-anchored dedup guard (F31
    # pattern): register_profile_hooks runs at module import, so an add-on
    # reload re-executes it, and these handlers are fresh closures / bound
    # methods each time (identity-based de-dup can't catch them). Record the
    # handler actually registered on the services registry and drop the
    # previously-recorded one before appending, so a reload swaps each handler
    # in place instead of stacking a duplicate. gui_hooks' remove() / remHook()
    # both tolerate an already-absent callback.
    did_open_handler = _on_profile_did_open(online_connectivity)
    backup_handler = backup_manager.on_anki_close

    previous_loaded_handler = getattr(services, _PROFILE_LOADED_HANDLER_RECORD, None)
    if previous_loaded_handler is not None and remHook is not None:
        remHook("profileLoaded", previous_loaded_handler)
    addHook("profileLoaded", on_profile_loaded)
    setattr(services, _PROFILE_LOADED_HANDLER_RECORD, on_profile_loaded)

    previous_did_open_handler = getattr(services, _DID_OPEN_HANDLER_RECORD, None)
    if previous_did_open_handler is not None:
        gui_hooks.profile_did_open.remove(previous_did_open_handler)
    gui_hooks.profile_did_open.append(did_open_handler)
    setattr(services, _DID_OPEN_HANDLER_RECORD, did_open_handler)

    previous_backup_handler = getattr(services, _WILL_CLOSE_BACKUP_RECORD, None)
    if previous_backup_handler is not None:
        gui_hooks.profile_will_close.remove(previous_backup_handler)
    gui_hooks.profile_will_close.append(backup_handler)
    setattr(services, _WILL_CLOSE_BACKUP_RECORD, backup_handler)

    previous_close_handler = getattr(services, _CLOSE_HANDLER_RECORD, None)
    if previous_close_handler is not None:
        gui_hooks.profile_will_close.remove(previous_close_handler)
    gui_hooks.profile_will_close.append(_on_profile_close)
    setattr(services, _CLOSE_HANDLER_RECORD, _on_profile_close)
