"""Ankimon boot sequence, split for the asynchronous startup (F32).

The old synchronous ``run_startup_sequence()`` is split in two so the heavy
boot work no longer blocks Anki's GUI thread:

* :func:`run_startup_background_checks` — the aqt-free half. Backup, DB
  migration status, collected-ID load, config migration, sprite-folder
  checks and first-enemy generation are all disk/DB/CPU work and run on a
  ``QueryOp`` background thread. It must never touch Qt: everything it
  learns is returned in a plain results dict.
* :func:`run_startup_ui_callbacks` — the main-thread half. Consumes that
  results dict and performs every Qt interaction (migration dialog, sprite
  download dialog, enemy stat application for GUI bindings, starter window,
  rate prompt).

``__init__.py`` drives the pair through ``aqt.operations.QueryOp`` (a genuine
Anki API); the harness's synchronous QueryOp fake drives it deterministically
in tests.
"""

from aqt import mw

from .resources import pkmnimgfolder
from .utils import (
    check_folders_exist,
    get_main_pokemon_data,
    load_collected_pokemon_ids,
    count_items_and_rewrite,
)
from .functions.encounter_functions import generate_random_pokemon
from .functions.pokedex_functions import warm_evolution_caches
from .functions.badges_functions import get_achieved_badges
from .functions.rate_addon_functions import rate_this_addon
from .gui_entities import CheckFiles
from .pyobj.download_sprites import show_agreement_and_download_dialog
from .pyobj.backup_files import run_backup
from .pyobj.backup_manager import BackupManager
from .pyobj.error_handler import show_warning_with_traceback
from .singletons import (
    logger,
    translator,
    settings_obj,
    ankimon_tracker_obj,
    main_pokemon,
    enemy_pokemon,
    ankimon_db,
)

# GC anchor for the assets-check dialog shown by run_startup_ui_callbacks:
# a bare local would be collected (and the dialog closed) as soon as the
# callback returns.
_file_check_dialog = None

# The seven per-tier toggles F28 introduced; the legacy single toggle folds
# into all of them on first boot after an upgrade (see
# _migrate_auto_catch_settings below).
_AUTO_CATCH_KEYS = (
    "battle.auto_catch_legendary",
    "battle.auto_catch_mythical",
    "battle.auto_catch_ultra",
    "battle.auto_catch_starter",
    "battle.auto_catch_mega",
    "battle.auto_catch_gmax",
    "battle.auto_catch_regional",
)


def run_startup_background_checks(backup_manager=None):
    """Aqt-free half of the boot; runs on the QueryOp background thread.

    Only disk, DB and CPU work happens here — no Qt calls. Everything the
    main-thread half needs is returned in the results dict. The DB layer is
    thread-safe (per-thread connections), so its reads/writes are legal off
    the GUI thread.
    """
    # "game"-level log lines write to the log file / event bus only (no Qt),
    # so they are safe here and keep the boot log order of the old
    # synchronous sequence.
    logger.log_and_showinfo("game", translator.translate("startup"))
    logger.log_and_showinfo("game", translator.translate("backing_up_files"))

    # 1. Run backups unless this startup was triggered by a developer hot-reload.
    backup_error = None
    from .services import services

    is_reloading = getattr(services, "_is_reloading", False)
    if not is_reloading:
        try:
            run_backup()
        except Exception as e:
            backup_error = e

        # Dev-mode auto-backup (disk copy — background work). __init__ passes its
        # module-level BackupManager so profile hooks and the menu share the same
        # instance; constructing one here keeps this function standalone-callable.
        if backup_manager is None:
            backup_manager = BackupManager(logger, settings_obj)
        try:
            if settings_obj.get("misc.developer_mode"):
                backup_manager.create_backup(manual=False)
        except Exception as e:
            logger.log("error", f"Error in background backup creation: {e}")
    else:
        logger.log("info", "Skipping background backups during hot-reload.")

    # 2. Read-only DB checks.
    is_migrated = ankimon_db.is_migrated()
    collected_pokemon_ids = load_collected_pokemon_ids()

    # 3. Config migration (legacy exp key -> F28 per-tier keys).
    _migrate_auto_catch_settings()

    # 4. Sprite/asset folder checks (disk).
    database_complete = _check_assets_background()

    # 5. Warm the static evolution table (disk parse) HERE rather than letting
    #    the first level-up pay for it. Every reader of pokemon_evolution.csv
    #    — the gender gate, the friendship and level-up evolution lookups —
    #    runs inside on_review_card, and reviews are gated on
    #    services.startup_finished, which flips only once this function has
    #    returned: warming here is what keeps the parse off the review path for
    #    the whole session. Unconditional (unlike step 6 below) because the CSV
    #    ships inside the add-on, so it is readable whether or not the player's
    #    downloaded assets are complete. Guarded because a QueryOp failure has
    #    no recovery — see on_startup_failed: it would leave startup_finished
    #    False and silently drop every answered card. An unparsed CSV must
    #    never cost the player the add-on.
    try:
        warm_evolution_caches()
    except Exception as e:
        logger.log("error", f"Error warming evolution caches: {e}")

    # 6. First enemy + starter/rating preconditions (DB/CPU); the Qt side of
    #    each (stat application, starter window, rate dialog) runs in
    #    run_startup_ui_callbacks.
    enemy_info = None
    needs_starter = False
    needs_rating = False

    if database_complete:
        enemy_info = _generate_first_enemy_background()

        if ankimon_db.get_pokemon_count() == 0:
            needs_starter = True

        badge_list = get_achieved_badges()
        if len(badge_list) > 1 and ankimon_db.get_user_data("rate_this") is not True:
            needs_rating = True

    # 7. Item-count consolidation (DB write; thread-safe layer).
    try:
        count_items_and_rewrite()
    except Exception as e:
        logger.log("error", f"Error in count_items_and_rewrite: {e}")

    return {
        "backup_error": backup_error,
        "backup_manager": backup_manager,
        "is_migrated": is_migrated,
        "collected_pokemon_ids": collected_pokemon_ids,
        "database_complete": database_complete,
        "enemy_info": enemy_info,
        "needs_starter": needs_starter,
        "needs_rating": needs_rating,
    }


def run_startup_ui_callbacks(results):
    """Main-thread half of the boot: every Qt interaction lives here."""
    global _file_check_dialog

    # Show the backup error (if any) now that we are on the GUI thread.
    if results.get("backup_error") is not None:
        show_warning_with_traceback(
            parent=mw, exception=results["backup_error"], message="Backup error:"
        )

    # Database migration dialog.
    if not results["is_migrated"]:
        from .pyobj.migration_dialog import show_migration_dialog_if_needed
        from .resources import (
            mypokemon_path,
            mainpokemon_path,
            itembag_path,
            badgebag_path,
            team_pokemon_path,
            pokemon_history_path,
            user_path_credentials,
            rate_path,
        )

        show_migration_dialog_if_needed(
            ankimon_db,
            mypokemon_path,
            mainpokemon_path,
            itembag_path,
            badgebag_path,
            mw,
            team_pokemon_path,
            pokemon_history_path,
            user_path_credentials,
            rate_path,
        )

        # The background half (run_startup_background_checks) computed
        # collected_pokemon_ids and needs_starter against the still-empty
        # pre-migration DB. Migration just imported the user's legacy collection
        # into captured_pokemon, so recompute both now — matching main's
        # migration-before-checks ordering. Without this, an upgrading player is
        # shown the starter picker (needs_starter left True) and their whole
        # collection reads as un-owned for the session (collected set left empty).
        results["collected_pokemon_ids"] = load_collected_pokemon_ids()
        if results["database_complete"]:
            results["needs_starter"] = ankimon_db.get_pokemon_count() == 0

    # Missing assets: sprite download agreement + file checker.
    if not results["database_complete"]:
        show_agreement_and_download_dialog(force_download=True)
        _file_check_dialog = CheckFiles()
        _file_check_dialog.show()

    # Apply the first enemy's stats on the main thread (the enemy object is
    # bound into GUI code, so its mutation stays off the background thread).
    if results["database_complete"] and results["enemy_info"] is not None:
        _apply_first_enemy(results["enemy_info"])

    # Starter selection for a blank profile.
    if results["database_complete"] and results["needs_starter"]:
        from .singletons import get_starter_window

        get_starter_window().display_starter_pokemon()

    # Rate-this-addon prompt.
    if results["database_complete"] and results["needs_rating"]:
        rate_this_addon()

    # Reset the encounter counter for the new session.
    ankimon_tracker_obj.pokemon_encounter = 0

    return results["database_complete"]


def _migrate_auto_catch_settings():
    """Fold the legacy ``battle.automatic_catch_special`` toggle into F28's
    seven per-tier ``battle.auto_catch_*`` keys, then tombstone the old key.

    Only profiles that ran the experimental branch ever stored the legacy
    key, so this is a no-op everywhere else. The tombstone (``None``, which
    the DB scalar layer round-trips as the string ``"None"``) marks the
    migration done, keeping it one-shot across boots.

    The stored toggle is normally a real ``bool`` (the config DB layer
    round-trips booleans through ``json``), but a legacy/string value would
    survive as ``"True"``/``"False"`` via that layer's ``str()`` fallback —
    and ``bool("False")`` is truthy. Normalize any string form explicitly so
    a disabled toggle never migrates as enabled.
    """
    old_value = settings_obj.config.get("battle.automatic_catch_special")
    if old_value is None or old_value == "None":
        return
    if isinstance(old_value, bool):
        enabled = old_value
    else:
        enabled = str(old_value).strip().lower() in ("true", "1")
    for key in _AUTO_CATCH_KEYS:
        settings_obj.set(key, enabled)
    settings_obj.set("battle.automatic_catch_special", None)


def _check_assets_background():
    back_sprites = check_folders_exist(pkmnimgfolder, "back_default")
    back_default_gif = check_folders_exist(pkmnimgfolder, "back_default_gif")
    front_sprites = check_folders_exist(pkmnimgfolder, "front_default")
    front_default_gif = check_folders_exist(pkmnimgfolder, "front_default_gif")
    item_sprites = check_folders_exist(pkmnimgfolder, "items")
    badges_sprites = check_folders_exist(pkmnimgfolder, "badges")

    return all(
        [
            back_sprites,
            front_sprites,
            front_default_gif,
            back_default_gif,
            item_sprites,
            badges_sprites,
        ]
    )


def _generate_first_enemy_background():
    """Generate the first wild encounter's data (CPU/DB work, no Qt).

    Goes through ``generate_random_pokemon`` so the level-cap NameError
    guard (#402) inside encounter generation keeps protecting the first
    encounter of every session.
    """
    try:
        get_main_pokemon_data()
    except Exception:
        pass

    main_pokemon_level = main_pokemon.level if hasattr(main_pokemon, "level") else 5

    try:
        return generate_random_pokemon(main_pokemon_level, ankimon_tracker_obj)
    except Exception as e:
        logger.log("error", f"Error generating first enemy: {e}")
        return None


def _apply_first_enemy(enemy_info):
    (
        name,
        id,
        level,
        ability,
        type,
        base_stats,
        enemy_attacks,
        base_experience,
        growth_rate,
        ev,
        iv,
        gender,
        battle_status,
        battle_stats,
        tier,
        ev_yield,
        shiny,
        nature,
    ) = enemy_info

    enemy_pokemon.update_stats(
        name=name,
        id=id,
        level=level,
        ability=ability,
        type=type,
        base_stats=base_stats,
        attacks=enemy_attacks,
        base_experience=base_experience,
        growth_rate=growth_rate,
        ev=ev,
        iv=iv,
        gender=gender,
        nature=nature,
        battle_status=battle_status,
        battle_stats=battle_stats,
        tier=tier,
        ev_yield=ev_yield,
        shiny=shiny,
    )
    max_hp = enemy_pokemon.calculate_max_hp()
    enemy_pokemon.current_hp = max_hp
    enemy_pokemon.hp = max_hp
    enemy_pokemon.max_hp = max_hp
    ankimon_tracker_obj.randomize_battle_scene()
