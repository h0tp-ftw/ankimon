import json

from aqt import mw

from .functions.badges_functions import get_achieved_badges
from .functions.encounter_functions import generate_random_pokemon
from .functions.rate_addon_functions import rate_this_addon
from .gui_entities import CheckFiles
from .pyobj.backup_files import run_backup
from .pyobj.backup_manager import BackupManager
from .pyobj.download_sprites import show_agreement_and_download_dialog
from .pyobj.error_handler import show_warning_with_traceback
from .resources import pkmnimgfolder, sound_list_path
from .singletons import (
    ankimon_db,
    ankimon_tracker_obj,
    enemy_pokemon,
    logger,
    main_pokemon,
    settings_obj,
    starter_window,
    translator,
)
from .utils import (
    check_folders_exist,
    count_items_and_rewrite,
    get_main_pokemon_data,
    load_collected_pokemon_ids,
)


def run_startup_sequence():
    logger.log_and_showinfo("game", translator.translate("startup"))
    logger.log_and_showinfo("game", translator.translate("backing_up_files"))

    try:
        run_backup()
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message="Backup error:")

    backup_manager = BackupManager(logger, settings_obj)

    if not ankimon_db.is_migrated():
        from .pyobj.migration_dialog import show_migration_dialog_if_needed
        from .resources import (
            badgebag_path,
            itembag_path,
            mainpokemon_path,
            mypokemon_path,
            pokemon_history_path,
            rate_path,
            team_pokemon_path,
            user_path_credentials,
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

    if settings_obj.get("misc.developer_mode"):
        backup_manager.create_backup(manual=False)

    collected_pokemon_ids = load_collected_pokemon_ids()

    with open(sound_list_path, "r", encoding="utf-8") as json_file:
        json.load(json_file)

    ankimon_tracker_obj.pokemon_encounter = 0

    database_complete = _check_assets()

    if database_complete:
        _init_first_enemy()
        _check_starter()
        badge_list = get_achieved_badges()
        if len(badge_list) > 1:
            rate_this_addon()

    count_items_and_rewrite()

    return database_complete, collected_pokemon_ids, backup_manager


def _check_assets():
    back_sprites = check_folders_exist(pkmnimgfolder, "back_default")
    back_default_gif = check_folders_exist(pkmnimgfolder, "back_default_gif")
    front_sprites = check_folders_exist(pkmnimgfolder, "front_default")
    front_default_gif = check_folders_exist(pkmnimgfolder, "front_default_gif")
    item_sprites = check_folders_exist(pkmnimgfolder, "items")
    badges_sprites = check_folders_exist(pkmnimgfolder, "badges")

    database_complete = all(
        [
            back_sprites,
            front_sprites,
            front_default_gif,
            back_default_gif,
            item_sprites,
            badges_sprites,
        ]
    )

    if not database_complete:
        show_agreement_and_download_dialog(force_download=True)
        dialog = CheckFiles()
        dialog.show()

    return database_complete


def _init_first_enemy():
    try:
        get_main_pokemon_data()
    except Exception:
        pass

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
    ) = generate_random_pokemon(main_pokemon.level, ankimon_tracker_obj)

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


def _check_starter():
    from .pyobj.database_manager import get_db

    db = get_db()
    if db.get_pokemon_count() == 0:
        starter_window.display_starter_pokemon()
