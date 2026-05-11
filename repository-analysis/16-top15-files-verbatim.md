---
## 1. `src/Ankimon/__init__.py`

### Why this file is critical
Entrypoint and orchestration

### Full Contents

```python
# -*- coding: utf-8 -*-

# Ankimon
# Copyright (C) 2024 Unlucky-Life

# This program is free software: you can redistribute it and/or modify
# by the Free Software Foundation
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# Important - If you redistribute it and/or modify this addon - must give contribution in Title and Code
# aswell as ask for permission to modify / redistribute this addon or the code itself

try:
    from .debug_console import show_ankimon_dev_console
except ModuleNotFoundError:
    # Debug console should not be available to non devs, so it's fine if this import doesn't succeed
    pass

import json
import random
import copy
from typing import Union

import aqt
from anki.hooks import addHook, wrap
from aqt import gui_hooks, mw, utils
from aqt.qt import QDialog
from aqt.operations import QueryOp
from aqt.reviewer import Reviewer
from aqt.utils import downArrow, showWarning, tr, tooltip
from PyQt6.QtWidgets import QDialog
from aqt.gui_hooks import webview_will_set_content
from aqt.webview import WebContent
import markdown

from .resources import generate_startup_files, user_path, IS_EXPERIMENTAL_BUILD, addon_ver, addon_dir
generate_startup_files(addon_dir, user_path)

from .singletons import settings_obj
no_more_news = settings_obj.get("misc.YouShallNotPass_Ankimon_News")
ssh = settings_obj.get("misc.ssh")
defeat_shortcut = settings_obj.get("controls.defeat_key") #default: 5; ; Else if not 5 => controll + Key for capture
catch_shortcut = settings_obj.get("controls.catch_key") #default: 6; Else if not 6 => controll + Key for capture
reviewer_buttons = settings_obj.get("controls.pokemon_buttons") #default: true; false = no pokemon buttons in reviewer

from .resources import (
    addon_dir,
    pkmnimgfolder,
    mypokemon_path,
    itembag_path,
    sound_list_path,
)
from .menu_buttons import create_menu_actions
from .hooks import setupHooks
from .texts import _bottomHTML_template, button_style
from .utils import (
    check_folders_exist,
    safe_get_random_move,
    test_online_connectivity,
    read_local_file,
    read_github_file,
    compare_files,
    write_local_file,
    count_items_and_rewrite,
    play_effect_sound,
    get_main_pokemon_data,
    play_sound,
    load_collected_pokemon_ids,
)
from .functions.url_functions import open_team_builder, rate_addon_url, report_bug, join_discord_url, open_leaderboard_url
from .functions.badges_functions import get_achieved_badges, handle_review_count_achievement, check_for_badge, receive_badge
from .functions.pokemon_showdown_functions import export_to_pkmn_showdown, export_all_pkmn_showdown, flex_pokemon_collection
from .functions.drawing_utils import tooltipWithColour
from .functions.discord_function import DiscordPresence
from .functions.rate_addon_functions import rate_this_addon
from .functions.encounter_functions import (
    generate_random_pokemon,
    new_pokemon,
    catch_pokemon,
    kill_pokemon,
    handle_enemy_faint,
    handle_main_pokemon_faint
)
from .gui_entities import UpdateNotificationWindow, CheckFiles
from .pyobj.download_sprites import show_agreement_and_download_dialog
from .pyobj.help_window import HelpWindow
from .pyobj.backup_files import run_backup
from .pyobj.backup_manager import BackupManager
from .pyobj.ankimon_sync import setup_ankimon_sync_hooks, check_and_sync_pokemon_data
from .pyobj.tip_of_the_day import show_tip_of_the_day
from .classes.choose_move_dialog import MoveSelectionDialog
from .poke_engine.ankimon_hooks_to_poke_engine import simulate_battle_with_poke_engine
from .singletons import (
    reviewer_obj,
    logger,
    settings_obj,
    settings_window,
    translator,
    main_pokemon,
    enemy_pokemon,
    trainer_card,
    ankimon_tracker_obj,
    test_window,
    achievement_bag,
    data_handler_obj,
    data_handler_window,
    shop_manager,
    ankimon_tracker_window,
    pokedex_window,
    eff_chart,
    gen_id_chart,
    license,
    credits,
    evo_window,
    starter_window,
    item_window,
    version_dialog,
    achievements,
    pokemon_pc
)

from .pyobj.pokemon_trade import check_and_award_monthly_pokemon

from .functions.battle_functions import (
    update_pokemon_battle_status,
    validate_pokemon_status,
    process_battle_data,
)

from .pyobj.error_handler import show_warning_with_traceback

mw.settings_ankimon = settings_window
mw.logger = logger
mw.translator = translator
mw.settings_obj = settings_obj

# Log an startup message
logger.log_and_showinfo('game', translator.translate("startup"))
logger.log_and_showinfo('game', translator.translate("backing_up_files"))

#backup_files
try:
    run_backup()
except Exception as e:
    show_warning_with_traceback(parent=mw, exception=e, message="Backup error:")

backup_manager = BackupManager(logger, settings_obj)

if settings_obj.get("misc.developer_mode"):
    backup_manager.create_backup(manual=False)

# Initialize mutator and mutator_full_reset
global new_state
global mutator_full_reset
global user_hp_after
global opponent_hp_after
global dmg_from_enemy_move
global dmg_from_user_move

# Initialize collected IDs cache
# Call this during addon initialization
collected_pokemon_ids = set()
_collection_loaded = False
if not _collection_loaded: # If the collection hasn't already been loaded
    collected_pokemon_ids = load_collected_pokemon_ids()
    _collection_loaded = True



with open(sound_list_path, "r", encoding="utf-8") as json_file:
    sound_list = json.load(json_file)

ankimon_tracker_obj.pokemon_encouter = 0

"""
get web exports ready for special reviewer look
"""


# Set up web exports for static files
mw.addonManager.setWebExports(__name__, r"user_files/.*\.(css|js|jpg|gif|html|ttf|png|mp3)")

def on_webview_will_set_content(web_content: WebContent, context) -> None:
    if not isinstance(context, aqt.reviewer.Reviewer):
        return
    ankimon_package = mw.addonManager.addonFromModule(__name__)
    web_content.js.append(f"/_addons/{ankimon_package}/user_files/web/ankimon_hud_portal.js")



webview_will_set_content.append(on_webview_will_set_content)

# check for sprites, data
sound_files = check_folders_exist(pkmnimgfolder, "sounds")
back_sprites = check_folders_exist(pkmnimgfolder, "back_default")
back_default_gif = check_folders_exist(pkmnimgfolder, "back_default_gif")
front_sprites = check_folders_exist(pkmnimgfolder, "front_default")
front_default_gif = check_folders_exist(pkmnimgfolder, "front_default_gif")
item_sprites = check_folders_exist(pkmnimgfolder, "items")
badges_sprites = check_folders_exist(pkmnimgfolder, "badges")

database_complete = all([
        back_sprites, front_sprites, front_default_gif, back_default_gif, item_sprites, badges_sprites
])

if not database_complete:
    show_agreement_and_download_dialog(force_download=True)
    dialog = CheckFiles()
    dialog.show()

sync_dialog = None

#If reviewer showed question; start card_timer for answering card
def on_show_question(Card):
    """
    This function is called when a question is shown.
    You can access and manipulate the card object here.
    """
    ankimon_tracker_obj.start_card_timer()  # This line should have 4 spaces of indentation

def on_show_answer(Card):
    """
    This function is called when a question is shown.
    You can access and manipulate the card object here.
    """
    ankimon_tracker_obj.stop_card_timer()  # This line should have 4 spaces of indentation

def on_reviewer_did_show_question(card):
    reviewer_obj.update_life_bar(mw.reviewer, None, None)

gui_hooks.reviewer_did_show_question.append(on_show_question)
gui_hooks.reviewer_did_show_answer.append(on_show_answer)
gui_hooks.reviewer_did_show_question.append(on_reviewer_did_show_question)

setupHooks(None, ankimon_tracker_obj)

online_connectivity = test_online_connectivity()

#Connect to GitHub and Check for Notification and HelpGuideChanges
update_infos_md = addon_dir / "updateinfos.md"
def download_changelog():
    try:
        # URL of the file on GitHub
        github_url = f"https://raw.githubusercontent.com/h0tp-ftw/ankimon/refs/heads/main/assets/changelogs/{addon_ver}.md"

        # Read content from GitHub
        github_content = read_github_file(github_url)

        # If changelog content is None, try unknown.md as a fallback for all builds
        if github_content is None:
            github_url = "https://raw.githubusercontent.com/h0tp-ftw/ankimon/refs/heads/main/assets/changelogs/unknown.md"
            github_content = read_github_file(github_url)

        return github_content
    except Exception as e:
        return e

if online_connectivity and ssh:
    def done(result: Union[Exception, str, None]):
        if isinstance(result, Exception):
            show_warning_with_traceback(parent=mw, exception=result, message="Error connecting to GitHub:")
            return
        if result is None:
            showWarning("Failed to retrieve Ankimon content from GitHub.")
            return
        # Read content from the local file
        local_content = read_local_file(update_infos_md)
        # If local content is not the same as the GitHub content, open dialog
        if not compare_files(local_content, result):
            write_local_file(update_infos_md, result)
            dialog = UpdateNotificationWindow(markdown.markdown(result))
            if not no_more_news:
                dialog.exec()
    op = QueryOp(
        parent=mw,
        op=lambda _col: download_changelog(), # Background operation
        success=done, # Ran on UI thread
    ).without_collection().run_in_background()

def open_help_window(online_connectivity):
    try:
        # TODO: online_connectivity must be a function?
        # TODO: HelpWindow constructor must be empty?
        help_dialog = HelpWindow(online_connectivity)
        help_dialog.exec()
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message="Error in opening Help Guide:")

def answerCard_before(filter, reviewer, card):
	utils.answBtnAmt = reviewer.mw.col.sched.answerButtons(card)
	return filter

# Globale Variable für die Zählung der Bewertungen

def answerCard_after(rev, card, ease):
    maxEase = rev.mw.col.sched.answerButtons(card)
    aw = aqt.mw.app.activeWindow() or aqt.mw
    # Aktualisieren Sie die Zählung basierend auf der Bewertung
    if ease == 1:
        ankimon_tracker_obj.review("again")
    elif ease == maxEase - 2:
        ankimon_tracker_obj.review("hard")
    elif ease == maxEase - 1:
        ankimon_tracker_obj.review("good")
    elif ease == maxEase:
        ankimon_tracker_obj.review("easy")
    else:
        # default behavior for unforeseen cases
        tooltip("Error in ColorConfirmation: Couldn't interpret ease")
    ankimon_tracker_obj.reset_card_timer()

aqt.gui_hooks.reviewer_will_answer_card.append(answerCard_before)
aqt.gui_hooks.reviewer_did_answer_card.append(answerCard_after)


#get main pokemon details:
if database_complete:
    try:
        mainpokemon_name, mainpokemon_id, mainpokemon_ability, mainpokemon_type, mainpokemon_stats, mainpokemon_attacks, mainpokemon_level, mainpokemon_base_experience, mainpokemon_xp, mainpokemon_hp, mainpokemon_current_hp, mainpokemon_growth_rate, mainpokemon_ev, mainpokemon_iv, mainpokemon_evolutions, mainpokemon_battle_stats, mainpokemon_gender, mainpokemon_nickname = get_main_pokemon_data()
        starter = True
    except Exception:
        starter = False
        mainpokemon_level = 5
    #name, id, level, ability, type, stats, enemy_attacks, base_experience, growth_rate, ev, iv, gender, battle_status, battle_stats, tier, ev_yield, shiny = generate_random_pokemon()
    name, id, level, ability, type, base_stats, enemy_attacks, base_experience, growth_rate, ev, iv, gender, battle_status, battle_stats, tier, ev_yield, shiny = generate_random_pokemon(main_pokemon.level, ankimon_tracker_obj)
    pokemon_data = {
        'name': name,
        'id': id,
        'level': level,
        'ability': ability,
        'type': type,
        'base_stats': base_stats,
        'attacks': enemy_attacks,
        'base_experience': base_experience,
        'growth_rate': growth_rate,
        'ev': ev,
        'iv': iv,
        'gender': gender,
        'battle_status': battle_status,
        'battle_stats': battle_stats,
        'tier': tier,
        'ev_yield': ev_yield,
        'shiny': shiny
    }
    enemy_pokemon.update_stats(**pokemon_data)
    max_hp = enemy_pokemon.calculate_max_hp()
    enemy_pokemon.current_hp = max_hp
    enemy_pokemon.hp = max_hp
    enemy_pokemon.max_hp = max_hp
    ankimon_tracker_obj.randomize_battle_scene()

cry_counter = 0

# How many cards need to be done before receiving an item
item_receive_value = random.randint(3, 385)

# Hook into Anki's card review event
def on_review_card(*args):
    try:
        multiplier = ankimon_tracker_obj.multiplier
        mainpokemon_type = main_pokemon.type
        mainpokemon_name = main_pokemon.name
        if main_pokemon.attacks:
            user_attack = random.choice(main_pokemon.attacks)
        else:
            user_attack = "splash"
        if enemy_pokemon.attacks:
            enemy_attack = random.choice(enemy_pokemon.attacks)
        else:
            enemy_attack = "splash"

        global mutator_full_reset

        battle_sounds = settings_obj.get("audio.battle_sounds")
        global achievements
        global new_state
        global user_hp_after
        global opponent_hp_after
        global dmg_from_enemy_move
        global dmg_from_user_move

        global item_receive_value

        # Increment the counter when a card is reviewed
        attack_counter = ankimon_tracker_obj.attack_counter
        ankimon_tracker_obj.cards_battle_round += 1
        ankimon_tracker_obj.cry_counter += 1
        cry_counter = ankimon_tracker_obj.cry_counter
        total_reviews = ankimon_tracker_obj.total_reviews
        reviewer_obj.seconds = 0
        reviewer_obj.myseconds = 0
        ankimon_tracker_obj.general_card_count_for_battle += 1

        color = "#F0B27A" # Initialize with a default color

        # Handle achievements based on total reviews
        achievements = handle_review_count_achievement(total_reviews, achievements)

        item_receive_value -= 1
        if item_receive_value <= 0:
            item_receive_value = random.randint(3, 385)

            test_window.display_item()

            # Give them a badge for getting an item
            if not check_for_badge(achievements,6):
                receive_badge(6, achievements)

        if total_reviews == 10:
            settings_obj.set("trainer.cash", settings_obj.get("trainer.cash") + 200)
            trainer_card.cash = settings_obj.get("trainer.cash")

        try:
             mutator_full_reset
        except:
            mutator_full_reset = 1

        if battle_sounds == True and ankimon_tracker_obj.general_card_count_for_battle == 1:
            play_sound(enemy_pokemon.id, settings_obj)

        if ankimon_tracker_obj.cards_battle_round >= int(settings_obj.get("battle.cards_per_round")):
            ankimon_tracker_obj.cards_battle_round = 0
            ankimon_tracker_obj.attack_counter = 0
            slp_counter = 0
            ankimon_tracker_obj.pokemon_encouter += 1
            multiplier = int(ankimon_tracker_obj.multiplier)

            if ankimon_tracker_obj.pokemon_encouter > 0 and enemy_pokemon.hp > 0 and multiplier < 1:
                enemy_move = safe_get_random_move(enemy_pokemon.attacks, logger=logger)
                enemy_move_category = enemy_move.get("category")

                if enemy_move_category == "Status":
                    color = "#F7DC6F"
                elif enemy_move_category == "Special":
                    color = "#D2B4DE"
                else:
                    color = "#F0B27A"

            else:
                enemy_attack = "splash" # if enemy will NOT attack, it uses SPLASH

            move = safe_get_random_move(main_pokemon.attacks, logger=logger)
            category = move.get("category")

            if ankimon_tracker_obj.pokemon_encouter > 0 and main_pokemon.hp > 0 and enemy_pokemon.hp > 0:

                if settings_obj.get("controls.allow_to_choose_moves") == True:
                    dialog = MoveSelectionDialog(main_pokemon.attacks)
                    if dialog.exec() == QDialog.DialogCode.Accepted:
                        if dialog.selected_move:
                            user_attack = dialog.selected_move

                if category == "Status":
                    color = "#F7DC6F"

                elif category == "Special":
                    color = "#D2B4DE"

                else:
                    color = "#F0B27A"

            try:
                new_state
                mutator_full_reset

                user_hp_after
                opponent_hp_after
                dmg_from_enemy_move
                dmg_from_user_move
            except:
                new_state = None
                mutator_full_reset = 1
                user_hp_after = 0
                opponent_hp_after = 0
                dmg_from_enemy_move = 0
                dmg_from_user_move = 0

            '''
            To the devs,
            below is the MOST IMPORTANT function for the new engine.
            This runs our current Pokemon stats through the SirSkaro Poke-Engine.
            The "results" can then be used to access battle outcomes.
            '''

            results = simulate_battle_with_poke_engine(
                main_pokemon,
                enemy_pokemon,
                user_attack,
                enemy_attack,
                mutator_full_reset,
                new_state,
            )

            # 2. Unpack results from the simulation
            battle_info = results[0]
            new_state = copy.deepcopy(results[1])
            dmg_from_enemy_move = results[2]  # NOTE : This is ACTUALLY the sum of all damages and heals that occured to the user during the turn
            dmg_from_user_move = results[3]
            mutator_full_reset = results[4]
            current_battle_info_changes = results[5]
            instructions = results[0]["instructions"]
            heals_to_user = sum([inst[2] for inst in instructions if inst[0:2] == ['heal', 'user']])
            heals_to_opponent = sum([inst[2] for inst in instructions if inst[0:2] == ['heal', 'opponent']])
            true_dmg_from_enemy_move = sum([inst[2] for inst in instructions if inst[0:2] == ['damage', 'user']])
            true_dmg_from_user_move = sum([inst[2] for inst in instructions if inst[0:2] == ['damage', 'opponent']])

            # workaround for the DAMAGE being negative in some cases
            if true_dmg_from_enemy_move < 0:
                true_dmg_from_enemy_move = 0
                heals_to_user += abs(true_dmg_from_enemy_move)  # Add the negative damage as a heal
            if true_dmg_from_user_move < 0:
                true_dmg_from_user_move = 0
                heals_to_opponent += abs(true_dmg_from_user_move)

            # 3. --- IMMEDIATE STATE SYNCHRONIZATION (THE FIX) ---
            # Update Pokémon objects with the new state from the engine BEFORE any other processing.
            # This ensures all subsequent functions have the correct HP and status.
            main_pokemon.hp = new_state.user.active.hp
            main_pokemon.current_hp = new_state.user.active.hp
            enemy_pokemon.hp = new_state.opponent.active.hp
            enemy_pokemon.current_hp = new_state.opponent.active.hp

            # Update statuses based on instructions, now that HP is correct.
            enemy_status_changed, main_status_changed = update_pokemon_battle_status(
                battle_info, enemy_pokemon, main_pokemon
            )

            # Final validation to ensure consistency
            enemy_pokemon.battle_status = validate_pokemon_status(enemy_pokemon)
            main_pokemon.battle_status = validate_pokemon_status(main_pokemon)

            # 4. Generate the battle log message using the now-correct Pokémon states
            formatted_battle_log = process_battle_data(
                battle_info=battle_info,
                multiplier=multiplier,
                main_pokemon=main_pokemon,
                enemy_pokemon=enemy_pokemon,
                user_attack=user_attack,
                enemy_attack=enemy_attack,
                dmg_from_user_move=true_dmg_from_user_move,
                dmg_from_enemy_move=true_dmg_from_enemy_move,
                user_hp_after=main_pokemon.hp, # Use the already updated HP
                opponent_hp_after=enemy_pokemon.hp, # Use the already updated HP
                battle_status=main_pokemon.battle_status,
                pokemon_encounter=ankimon_tracker_obj.pokemon_encouter,
                translator=translator,
                changes=current_battle_info_changes
            )

            # Display the complete message
            tooltipWithColour(formatted_battle_log, color)

            # Handle sound effects and animations (existing code)
            if true_dmg_from_enemy_move > 0 and multiplier < 1:
                reviewer_obj.myseconds = settings_obj.compute_special_variable("animate_time")
                tooltipWithColour(f" -{true_dmg_from_enemy_move} HP ", "#F06060", x=-200)
                play_effect_sound(settings_obj, "HurtNormal")

            if true_dmg_from_user_move > 0:
                reviewer_obj.seconds = settings_obj.compute_special_variable("animate_time")
                tooltipWithColour(f" -{true_dmg_from_user_move} HP ", "#F06060", x=200)
                if multiplier == 1:
                    play_effect_sound(settings_obj, "HurtNormal")
                elif multiplier < 1:
                    play_effect_sound(settings_obj, "HurtNotEffective")
                elif multiplier > 1:
                    play_effect_sound(settings_obj, "HurtSuper")
            else:
                reviewer_obj.seconds = 0

            if int(heals_to_user) != 0:
                # "Negative heal" can happen sometimes. That's how the Life Orb item deals its damage for instance
                heal_color = "#68FA94" if heals_to_user > 0 else "#F06060"
                sign = "+" if heals_to_user > 0 else ""
                tooltipWithColour(f" {sign}{int(heals_to_user)} HP ", heal_color, x=-250)

            if int(heals_to_opponent) != 0:
                # "Negative heal" can happen sometimes. That's how the Life Orb item deals its damage for instance
                heal_color = "#68FA94" if heals_to_opponent > 0 else "#F06060"
                sign = "+" if heals_to_opponent > 0 else ""
                tooltipWithColour(f" {sign}{int(heals_to_opponent)} HP ", heal_color, x=250)

            # if enemy pokemon faints, this handles AUTOMATIC BATTLE
            if enemy_pokemon.hp < 1:
                enemy_pokemon.hp = 0
                test_window.display_battle()
                handle_enemy_faint(
                    main_pokemon,
                    enemy_pokemon,
                    collected_pokemon_ids,
                    test_window,
                    evo_window,
                    reviewer_obj,
                    logger,
                    achievements
                    )

                mutator_full_reset = 1 # reset opponent state

        if cry_counter == 10 and battle_sounds is True:
            cry_counter = 0
            play_sound(enemy_pokemon.id, settings_obj)

        # user pokemon faints
        if main_pokemon.hp < 1:
            handle_main_pokemon_faint(main_pokemon, enemy_pokemon, test_window, reviewer_obj, translator)
            mutator_full_reset = 1 # fully reset battle state

        class Container(object):
            pass

        reviewer = Container()
        reviewer.web = mw.reviewer.web
        reviewer_obj.update_life_bar(reviewer, 0, 0)
        if test_window is not None:
            if enemy_pokemon.hp > 0:
                test_window.display_battle()
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message="An error occurred in reviewer:")

# Connect the hook to Anki's review event
gui_hooks.reviewer_did_answer_card.append(on_review_card)

if database_complete:
    badge_list = get_achieved_badges()
    if len(badge_list) > 1: # has atleast one badge
        rate_this_addon()

if database_complete:
    if mypokemon_path.is_file() is False:
        starter_window.display_starter_pokemon()
    else:
        with open(mypokemon_path, "r", encoding="utf-8") as file:
            pokemon_list = json.load(file)
            if not pokemon_list :
                starter_window.display_starter_pokemon()

count_items_and_rewrite(itembag_path)

#buttonlayout
# Create menu actions
# Create menu actions
create_menu_actions(
    database_complete,
    online_connectivity,
    None,#pokecollection_win,
    item_window,
    test_window,
    achievement_bag,
    open_team_builder,
    export_to_pkmn_showdown,
    export_all_pkmn_showdown,
    flex_pokemon_collection,
    eff_chart,
    gen_id_chart,
    credits,
    license,
    open_help_window,
    report_bug,
    rate_addon_url,
    version_dialog,
    trainer_card,
    ankimon_tracker_window,
    logger,
    data_handler_window,
    settings_window,
    shop_manager,
    pokedex_window,
    settings_obj.get("controls.key_for_opening_closing_ankimon"),
    join_discord_url,
    open_leaderboard_url,
    settings_obj,
    addon_dir,
    data_handler_obj,
    pokemon_pc,
    backup_manager,
)

    #https://goo.gl/uhAxsg
    #https://www.reddit.com/r/PokemonROMhacks/comments/9xgl7j/pokemon_sound_effects_collection_over_3200_sfx/
    #https://archive.org/details/pokemon-dp-sound-library-disc-2_202205
    #https://www.sounds-resource.com/nintendo_switch/pokemonswordshield/

# Define lists to hold hook functions
catch_pokemon_hooks = []
defeat_pokemon_hooks = []

# Function to add hooks to catch_pokemon event
def add_catch_pokemon_hook(func):
    catch_pokemon_hooks.append(func)

# Function to add hooks to defeat_pokemon event
def add_defeat_pokemon_hook(func):
    defeat_pokemon_hooks.append(func)

# Custom function that triggers the catch_pokemon hook
def CatchPokemonHook():
    if enemy_pokemon.hp < 1:
        catch_pokemon(enemy_pokemon, ankimon_tracker_obj, logger, "", collected_pokemon_ids, achievements)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon
    for hook in catch_pokemon_hooks:
        hook()

# Custom function that triggers the defeat_pokemon hook
def DefeatPokemonHook():
    if enemy_pokemon.hp < 1:
        kill_pokemon(main_pokemon, enemy_pokemon, evo_window, logger , achievements, trainer_card)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon
    for hook in defeat_pokemon_hooks:
        hook()

def on_profile_did_open():
    """Initialize services after profile is loaded."""
    # Show tip of the day
    try:
        show_tip_of_the_day()
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message="Error showing tip of the day:")

    # Award monthly pokemon if applicable
    try:
        if online_connectivity:
            check_and_award_monthly_pokemon(logger)
        else:
            logger.log("info", "Skipping monthly pokemon check due to no internet connectivity.")
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message="Error awarding monthly pokemon:")

    # AnkiWeb Sync
    try:
        ankiweb_sync = settings_obj.get("misc.ankiweb_sync")
        if not ankiweb_sync:
            logger.log("info", "AnkiWeb sync is disabled in settings - skipping sync system initialization")
            return

        # Set up sync hooks now that profile is available
        setup_ankimon_sync_hooks(settings_obj, logger)

        if not online_connectivity:
            logger.log("info", "No connection - AnkiWeb sync is disabled for this session")
        else: #if enabled and internet is available
            # Check for sync conflicts and show dialog if needed
            global sync_dialog
            sync_dialog = check_and_sync_pokemon_data(settings_obj, logger)
            logger.log("info", "Ankimon sync system initialized successfully")
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message="Error setting up sync system:")

# Hook to expose the function
def on_profile_loaded():
    mw.defeatpokemon = DefeatPokemonHook
    mw.catchpokemon = CatchPokemonHook
    mw.add_catch_pokemon_hook = add_catch_pokemon_hook
    mw.add_defeat_pokemon_hook = add_defeat_pokemon_hook

# Add hook to run on profile load
addHook("profileLoaded", on_profile_loaded)

gui_hooks.profile_did_open.append(on_profile_did_open)
gui_hooks.profile_will_close.append(backup_manager.on_anki_close)

def catch_shortcut_function():
    if enemy_pokemon.hp < 1:
        catch_pokemon(enemy_pokemon, ankimon_tracker_obj, logger, "", collected_pokemon_ids, achievements)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon
    else:
        tooltip("You only catch a pokemon once it's fainted!")

def defeat_shortcut_function():
    if enemy_pokemon.hp < 1:
        kill_pokemon(main_pokemon, enemy_pokemon, evo_window, logger , achievements, trainer_card)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon
    else:
        tooltip("Wild pokemon has to be fainted to defeat it!")

catch_shortcut = catch_shortcut.lower()
defeat_shortcut = defeat_shortcut.lower()
#// adding shortcuts to _shortcutKeys function in anki
def _shortcutKeys_wrap(self, _old):
    original = _old(self)
    original.append((catch_shortcut, lambda: catch_shortcut_function()))
    original.append((defeat_shortcut, lambda: defeat_shortcut_function()))
    return original

Reviewer._shortcutKeys = wrap(Reviewer._shortcutKeys, _shortcutKeys_wrap, 'around')

if reviewer_buttons is True:
    #// Choosing styling for review other buttons in reviewer bottombar based on chosen style
    Review_linkHandelr_Original = Reviewer._linkHandler
    # Define the HTML and styling for the custom button
    def custom_button():
        return f"""<button title="Shortcut key: C" onclick="pycmd('catch');" {button_style}>Catch</button>"""

    # Update the link handler function to handle the custom button action
    def linkHandler_wrap(reviewer, url):
        if url == "catch":
            catch_shortcut_function()
        elif url == "defeat":
            defeat_shortcut_function()
        else:
            Review_linkHandelr_Original(reviewer, url)

    def _bottomHTML(self) -> str:
        return _bottomHTML_template % dict(
            edit=tr.studying_edit(),
            editkey=tr.actions_shortcut_key(val="E"),
            more=tr.studying_more(),
            morekey=tr.actions_shortcut_key(val="M"),
            downArrow=downArrow(),
            time=self.card.time_taken() // 1000,
            CatchKey=tr.actions_shortcut_key(val=f"{catch_shortcut}"),
            DefeatKey=tr.actions_shortcut_key(val=f"{defeat_shortcut}"),
        )

    # Replace the current HTML with the updated HTML
    Reviewer._bottomHTML = _bottomHTML  # Assuming you have access to self in this context
    # Replace the original link handler function with the modified one
    Reviewer._linkHandler = linkHandler_wrap

if settings_obj.get("misc.discord_rich_presence") == True:
    client_id = '1319014423876075541'  # Replace with your actual client ID
    large_image_url = "https://raw.githubusercontent.com/Unlucky-Life/ankimon/refs/heads/main/src/Ankimon/ankimon_logo.png"  # URL for the large image
    mw.ankimon_presence = DiscordPresence(client_id, large_image_url, ankimon_tracker_obj, logger, settings_obj)  # Establish connection and get the presence instance

    # Hook functions for Anki
    def on_reviewer_initialized(rev, card, ease):
        if mw.ankimon_presence:
            if mw.ankimon_presence.loop is False:
                mw.ankimon_presence.loop = True
                mw.ankimon_presence.start()
        else:
            client_id = '1319014423876075541'  # Replace with your actual client ID
            large_image_url = "https://raw.githubusercontent.com/Unlucky-Life/ankimon/refs/heads/main/src/Ankimon/ankimon_logo.png"  # URL for the large image
            mw.ankimon_presence = DiscordPresence(client_id, large_image_url, ankimon_tracker_obj, logger, settings_obj)  # Establish connection and get the presence instance
            mw.ankimon_presence.loop = True
            mw.ankimon_presence.start()

    def on_reviewer_will_end(*args):
        mw.ankimon_presence.loop = False
        mw.ankimon_presence.stop_presence()

    # Register the hook functions with Anki's GUI hooks
    gui_hooks.reviewer_did_answer_card.append(on_reviewer_initialized)
    gui_hooks.reviewer_will_end.append(mw.ankimon_presence.stop_presence)
    gui_hooks.sync_did_finish.append(mw.ankimon_presence.stop)
```
---
## 2. `src/Ankimon/singletons.py`

### Why this file is critical
Global state initialization

### Full Contents

```python
"""
singletons.py

This module groups up some of the global variables that originally wer ein the __init__.py.
This module, hopefully, does not have vocation to remain permanently. This is but a transition step
in the splitting of the __init__.py file.

More detailed explanation if needed:
- Any important classes/functions
- Special behaviors, assumptions, or usage notes

Author: Axil
Created: 2025-06-03 (YYY-MM-DD)
"""

import json
import uuid

from aqt import mw

from .pyobj.collection_dialog import PokemonCollectionDialog
from .pyobj.ankimon_tracker import AnkimonTracker
from .pyobj.settings import Settings
from .pyobj.settings_window import SettingsWindow
from .pyobj.pokemon_obj import PokemonObject
from .pyobj.InfoLogger import ShowInfoLogger
from .pyobj.trainer_card import TrainerCard
from .pyobj.translator import Translator
from .pyobj.test_window import TestWindow
from .pyobj.achievement_window import AchievementWindow
from .pyobj.data_handler import DataHandler
from .pyobj.data_handler_window import DataHandlerWindow
from .pyobj.ankimon_tracker_window import AnkimonTrackerWindow
from .pyobj.ankimon_shop import PokemonShopManager
from .pokedex.pokedex_obj import Pokedex
from .pyobj.reviewer_obj import Reviewer_Manager
from .pyobj.evolution_window import EvoWindow
from .pyobj.starter_window import StarterWindow
from .pyobj.item_window import ItemWindow
from .pyobj.pc_box import PokemonPC
from .gui_entities import (
    License,
    Credits,
    TableWidget,
    IDTableWidget,
    Pokedex_Widget,
    Version_Dialog,
)
from .functions.update_main_pokemon import update_main_pokemon
from .functions.badges_functions import populate_achievements_from_badges
from .resources import addon_dir, itembag_path

# start loggerobject for Ankimon
logger = ShowInfoLogger()

# Create the Settings object
settings_obj = Settings()

# Pass the correct attributes to SettingsWindow
settings_window = SettingsWindow(
    config=settings_obj.config,  # Use settings_obj.config instead of settings_obj.settings.config
    set_config_callback=settings_obj.set,
    save_config_callback=settings_obj.save_config,
    load_config_callback=settings_obj.load_config,
)

# Init Translator
translator = Translator(language=int(settings_obj.get("misc.language")))

# Not sure what this does, but from afar it looks like a bad idea
mw.settings_ankimon = settings_window
mw.logger = logger
mw.translator = translator
mw.settings_obj = settings_obj

main_pokemon, mainpokemon_empty = update_main_pokemon()

enemy_pokemon = PokemonObject(
    name="Rattata",  # Name of the Pokémon
    shiny=False,  # Shiny status (False for normal appearance)
    id=19,  # ID number
    level=5,  # Level
    ability="Run Away",  # Ability specific to Rattata
    type=["Normal"],  # Type (Normal type for Rattata)
    stats={  # Base stats for Rattata
        "hp": 39,
        "atk": 52,
        "def": 43,
        "spa": 60,
        "spd": 50,
        "spe": 65,
        "xp": 101,
    },
    attacks=["Quick Attack", "Tackle", "Tail Whip"],  # Typical moves for Rattata
    base_experience=58,  # Base experience points
    growth_rate="medium-slow",  # Growth rate
    hp=30,  # Hit points (HP)
    ev={
        "hp": 3,
        "atk": 5,
        "def": 4,
        "spa": 1,
        "spd": 2,
        "spe": 3,
    },  # EVs (Effort Values) for stats
    iv={
        "hp": 27,
        "atk": 24,
        "def": 3,
        "spa": 24,
        "spd": 16,
        "spe": 21,
    },  # IVs (Individual Values) for stats
    gender="M",  # Gender
    battle_status="Fighting",  # Status during battle
    xp=0,  # XP (experience points)
    position=(5, 5),  # Position in battle
    tier="Normal",
    captured_date=None,
    individual_id=str(uuid.uuid4()),
)

# Create a sample trainer card to test
trainer_card = TrainerCard(
    logger,
    main_pokemon,
    settings_obj,
    trainer_name=settings_obj.get("trainer.name"),
    trainer_id="".join(filter(str.isdigit, str(uuid.uuid4()).replace("-", ""))),
    team="Pikachu (Level 25), Charizard (Level 50), Bulbasaur (Level 15)",
    league="Unranked",
)

ankimon_tracker_obj = AnkimonTracker(
    trainer_card=trainer_card,
)
# Set Pokémon in the tracker
ankimon_tracker_obj.set_main_pokemon(main_pokemon)
ankimon_tracker_obj.set_enemy_pokemon(enemy_pokemon)

# Create an instance of the MainWindow
test_window = TestWindow(
    main_pokemon=main_pokemon,
    enemy_pokemon=enemy_pokemon,
    settings_obj=settings_obj,
    ankimon_tracker_obj=ankimon_tracker_obj,
    translator=translator,
    parent=mw,
    logger=logger,
)

achievement_bag = AchievementWindow()

data_handler_obj = DataHandler()
data_handler_window = DataHandlerWindow(data_handler=data_handler_obj)

# Initialize the Pokémon Shop Manager
shop_manager = PokemonShopManager(
    logger=logger,
    settings_obj=settings_obj,
    set_callback=settings_obj.set,
    get_callback=settings_obj.get,
)

ankimon_tracker_window = AnkimonTrackerWindow(tracker=ankimon_tracker_obj)
pokedex_window = Pokedex(addon_dir, ankimon_tracker=ankimon_tracker_obj)
reviewer_obj = Reviewer_Manager(
    settings_obj=settings_obj,
    main_pokemon=main_pokemon,
    enemy_pokemon=enemy_pokemon,
    ankimon_tracker=ankimon_tracker_obj,
)

eff_chart = TableWidget()
pokedex = Pokedex_Widget()
gen_id_chart = IDTableWidget()
license = License()
credits = Credits()
version_dialog = Version_Dialog()

achievements = populate_achievements_from_badges({str(i): False for i in range(1, 69)})

evo_window = EvoWindow(
    logger,
    settings_obj,
    main_pokemon,
    translator,
    reviewer_obj,
    test_window,
    achievements,
)
starter_window = StarterWindow(logger, settings_obj)
item_window = ItemWindow(  # Create an instance of the MainWindow
    logger=logger,
    main_pokemon=main_pokemon,
    enemy_pokemon=enemy_pokemon,
    itembagpath=itembag_path,
    achievements=achievements,
    starter_window=starter_window,
    evo_window=evo_window,
)

pokecollection_win = PokemonCollectionDialog(
    logger=logger,
    translator=translator,
    reviewer_obj=reviewer_obj,
    test_window=test_window,
    settings_obj=settings_obj,
    main_pokemon=main_pokemon,
)

pokemon_pc = PokemonPC(
    logger=logger,
    translator=translator,
    reviewer_obj=reviewer_obj,
    test_window=test_window,
    settings=settings_obj,
    main_pokemon=main_pokemon,
)
```
---
## 3. `src/Ankimon/pyobj/pokemon_obj.py`

### Why this file is critical
Core domain model

### Full Contents

```python
from typing import Union
import uuid
import json
import os
from typing import Optional

from ..poke_engine.objects import Pokemon
from ..resources import pkmnimgfolder, mainpokemon_path, mypokemon_path
from ..utils import substract_item_from_itembag, give_item

class PokemonObject:
    def __init__(
        self,

        type,
        name: str,
        id: int,
        shiny: bool,
        level: int,
        ability,
        gender: str,
        growth_rate: str,
        captured_date: Optional[str],
        tier: str,
        individual_id: str,

        current_hp=15,
        base_stats=None,
        attacks=None,
        base_experience=0,
        hp=16,
        ev=None,
        iv=None,
        battle_status="Fighting",
        xp=0,
        position=(0, 0),
        nickname="",
        moves=None,
        ev_yield=None,
        friendship=0,
        everstone=False,
        pokemon_defeated=0,
        is_favorite=False,
        held_item: Union[str, None]=None,
        **kwargs
    ):
        # Unique identifier
        self.individual_id = individual_id
        self.name = name
        self.nickname = nickname
        self.shiny = shiny
        self.id = id
        self.level = level
        self.ability = ability
        self.type = type
        self.gender = gender
        self.tier = tier
        self.everstone = everstone
        self.pokemon_defeated = pokemon_defeated

        if not ability or str(ability).strip().lower() in ("none", "no ability", ""):
            self.ability = "Run Away"
        else:
            self.ability = ability

        # Stats
        self.base_stats = base_stats or {"hp": 1, "atk": 1, "def": 1, "spa": 1, "spd": 1, "spe": 1}
        self.ev = {k: int(v) for k, v in (ev or {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}).items()}
        self.iv = {k: int(v) for k, v in (iv or {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}).items()}
        self.ev_yield = {k: int(v) for k, v in (ev_yield or {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}).items()}

        # Attacks and moves
        self.attacks = list(attacks) if attacks else ["Struggle"]
        self.moves = list(moves) if moves else []

        # Experience and growth
        self.base_experience = base_experience
        self.growth_rate = growth_rate
        self.xp = xp
        self.friendship = friendship

        # Battle and status
        self.battle_status = str(battle_status)
        self.position = tuple(position) if isinstance(position, (list, tuple)) else (0, 0)
        self.stat_stages = kwargs.get('stat_stages', {
            'atk': 0, 'def': 0, 'spa': 0, 'spd': 0, 'spe': 0, 'accuracy': 0, 'evasion': 0
        })
        self.volatile_status = set(kwargs.get('volatile_status', []))
        self.nature = kwargs.get('nature', 'serious')
        self.held_item = held_item

        # HP calculation
        self.max_hp = self.calculate_max_hp()
        self.hp = int(kwargs.get('hp', self.max_hp))
        self.current_hp = current_hp or 15

        self.is_favorite = is_favorite
        self.captured_date = captured_date

    @classmethod
    def calc_stat(
        cls,
        stat_name: str,
        base_stat_val: int,
        level: int,
        iv: int,
        ev: int,
        nature: str
        ) -> int:
        if stat_name == "hp":
            hp = 10 + level + int((2 * base_stat_val + iv + int(ev / 4)) * level / 100)  # Formula found on bulbapedia
            return int(hp)
        elif stat_name in ("atk", "def", "spa", "spd", "spe"):
            nature_mult = PokemonObject.get_nature_stat_mult(stat_name, nature)  # Formula found on bulbapedia
            stat = (5 + int((2 * base_stat_val + iv + int(ev / 4)) * level / 100)) * nature_mult
            return int(stat)
        raise ValueError(f"Received an unknown stat_name : {stat_name}")

    @property
    def stats(self) -> dict:
        _dict = {}
        for key, val in self.base_stats.items():
            if key not in ("hp", "atk", "def", "spa", "spd", "spe"):
                continue
            _dict[key] = PokemonObject.calc_stat(
                key, val, self.level, self.iv[key], self.ev[key], self.nature
                )
        return _dict

    @stats.setter
    def stats(self, value):
        raise AttributeError("Setting the value of the stats of a Pokemon is forbidden as they are automatically calculated using their base stats. You can instead set the base_stats of the Pokemon.")

    @classmethod
    def get_nature_stat_mult(cls, stat_name: str, nature: str) -> float:
        if stat_name == "atk":
            if nature.lower() in ("lonely", "brave", "adamant", "naughty"):
                return 1.1
            if nature.lower() in ("bold", "timid", "modest", "calm"):
                return 0.9
        elif stat_name == "def":
            if nature.lower() in ("bold", "relaxed", "impish", "lax"):
                return 1.1
            if nature.lower() in ("lonely", "hasty", "mild", "gentle"):
                return 0.9
        elif stat_name == "spa":
            if nature.lower() in ("modest", "mild", "quiet", "rash"):
                return 1.1
            if nature.lower() in ("adamant", "impish", "jolly", "careful"):
                return 0.9
        elif stat_name == "spd":
            if nature.lower() in ("calm", "gentle", "sassy", "careful"):
                return 1.1
            if nature.lower() in ("naughty", "lax", "naive", "rash"):
                return 0.9
        elif stat_name == "spe":
            if nature.lower() in ("timid", "hasty", "jolly", "naive"):
                return 1.1
            if nature.lower() in ("brave", "relaxed", "quiet", "sassy"):
                return 0.9
        return 1.0

    def to_dict(self):
        return {
            "name": self.name,
            "nickname": self.nickname,
            "level": self.level,
            "gender": self.gender,
            "id": self.id,
            "ability": self.ability,
            "type": self.type,
            "base_stats": self.base_stats,
            "stats": self.stats,  # Calculated stats
            "ev": self.ev,
            "iv": self.iv,
            "attacks": self.attacks,
            "base_experience": self.base_experience,
            "growth_rate": self.growth_rate,
            "everstone": self.everstone,
            "shiny": self.shiny,
            "captured_date": getattr(self, "captured_date", None),
            "individual_id": self.individual_id,
            "mega": getattr(self, "mega", False),
            "special_form": getattr(self, "special_form", None),
            "xp": self.xp,
            "hp": self.hp,  # Current HP
            "friendship": self.friendship,
            "pokemon_defeated": self.pokemon_defeated,
            "tier": self.tier,  # Added tier
            "is_favorite": getattr(self, "is_favorite", False),  # Added with default
            # Additional fields from your example
            "current_hp": getattr(self, "current_hp", "hp"),  # For backward compatibility
            "held_item": self.held_item,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

    def get_stats(self):
        """Return the stats of the Pokémon."""
        return vars(self)

    def update_stats(self, **kwargs):
        """Update the attributes of the Pokémon object with keyword arguments."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self._update_battle_stats()  # Update battle stats

    def reset_stats(self):
        """Reset the stats of the Pokémon to default values."""
        self.hp = self.max_hp
        self.battle_status = "Fighting"
        self._update_battle_stats()

    def _update_battle_stats(self):
        """Update battle stats with current stats, EVs, and IVs."""
        self._battle_stats = {}
        # Only update battle stats with valid keys
        for d in [self.stats, self.iv, self.ev]:
            for key, value in d.items():
                self._battle_stats[key] = value

    def calculate_max_hp(self):
        ev, iv = self.ev["hp"], self.iv["hp"]
        hp = 10 + self.level + int((2 * self.base_stats["hp"] + iv + int(ev / 4)) * self.level / 100)
        hp = int(hp)
        return hp

    def get_sprite_path(self, side, sprite_type):
        """Return the path to the sprite of the Pokémon."""
        base_path = f"{side}_default_gif" if sprite_type == "gif" else f"{side}_default"

        shiny_path = "shiny/" if self.shiny else ""
        gender_path = "female/" if self.gender == "F" else ""

        path = f"{pkmnimgfolder}/{base_path}/{shiny_path}{gender_path}{self.id}.{sprite_type}"
        default_path = f"{pkmnimgfolder}/front_default/substitute.png"

        # Check if the file exists at the given path
        if os.path.exists(path):
            return path
        else:
            if self.gender == "F":
                gender_path = ""
                path = f"{pkmnimgfolder}/{base_path}/{shiny_path}{gender_path}{self.id}.{sprite_type}"
                return path
            elif self.shiny == "True":
                shiny_path = ""
                path = f"{pkmnimgfolder}/{base_path}/{shiny_path}{gender_path}{self.id}.{sprite_type}"
                return path
            else:
                return default_path

    def to_engine_format(self):
        from ..poke_engine.helpers import normalize_name
        return {
            'identifier': normalize_name(self.name),
            'level': self.level,
            'nature': getattr(self, 'nature', 'serious'),
            'evs': (
                self.ev.get('hp', 0),
                self.ev.get('atk', 0),
                self.ev.get('def', 0),
                self.ev.get('spa', 0),
                self.ev.get('spd', 0),
                self.ev.get('spe', 0)
            ),
            'types': [normalize_name(t) for t in self.type],
            'hp': self.hp,
            'maxhp': self.max_hp,
            'ability': normalize_name(self.ability) if self.ability else 'none',
            'item': normalize_name(self.held_item) if self.held_item else None,
            'attack': self.stats.get('atk', 0),
            'defense': self.stats.get('def', 0),
            'special_attack': self.stats.get('spa', 0),
            'special_defense': self.stats.get('spd', 0),
            'speed': self.stats.get('spe', 0),
            'ivs': (
                self.iv.get('hp', 0),
                self.iv.get('atk', 0),
                self.iv.get('def', 0),
                self.iv.get('spa', 0),
                self.iv.get('spd', 0),
                self.iv.get('spe', 0)
            ),
            'attack_boost': self.stat_stages.get('atk', 0),
            'defense_boost': self.stat_stages.get('def', 0),
            'special_attack_boost': self.stat_stages.get('spa', 0),
            'special_defense_boost': self.stat_stages.get('spd', 0),
            'speed_boost': self.stat_stages.get('spe', 0),
            'accuracy_boost': self.stat_stages.get('accuracy', 0),
            'evasion_boost': self.stat_stages.get('evasion', 0),
            'status': self.battle_status if self.battle_status != "fighting" else None,
            'volatile_status': set(normalize_name(vs) for vs in self.volatile_status),
            'moves': [{'id': normalize_name(move)} for move in self.attacks]
        }

    @classmethod
    def from_engine_format(cls, engine_data):
        """Create PokemonObject from poke-engine data"""
        return cls(
            name=engine_data['identifier'].capitalize(),
            level=engine_data['level'],
            hp=engine_data['hp'],
            base_stats={
                'hp': engine_data.get('maxhp', 0),
                'atk': engine_data['attack'],
                'def': engine_data['defense'],
                'spa': engine_data['special_attack'],
                'spd': engine_data['special_defense'],
                'spe': engine_data['speed']
            },
            ev={k: v for k, v in zip(['hp','atk','def','spa','spd','spe'], engine_data['evs'])},
            iv={k: v for k, v in zip(['hp','atk','def','spa','spd','spe'], engine_data['ivs'])},
            battlestatus=engine_data.get('status', 'fighting'),
            moves=engine_data['moves'],
            stat_stages={
                'atk': engine_data['stat_stages']['attack'],
                'def': engine_data['stat_stages']['defense'],
                'spa': engine_data['stat_stages']['special_attack'],
                'spd': engine_data['stat_stages']['special_defense'],
                'spe': engine_data['stat_stages']['speed'],
                'accuracy': engine_data['stat_stages']['accuracy'],
                'evasion': engine_data['stat_stages']['evasion']
            },
            volatile_status=set(engine_data.get('volatile_status', [])),
            nature=engine_data.get('nature', 'serious'),
            held_item=engine_data.get('item', '')
        )

    def to_poke_engine_Pokemon(self) -> Pokemon:
        _dict = self.to_engine_format()
        pokemon = Pokemon(
            identifier=_dict['identifier'],
            level=_dict['level'],
            types=_dict['types'],
            hp=_dict['hp'],
            maxhp=_dict['maxhp'],
            ability=_dict['ability'],
            item=_dict['item'],
            attack=_dict['attack'],
            defense=_dict['defense'],
            special_attack=_dict['special_attack'],
            special_defense=_dict['special_defense'],
            speed=_dict['speed'],
            nature=_dict.get('nature', 'serious'),
            evs=_dict.get('evs', (85,) * 6),
            attack_boost=_dict.get('attack_boost', 0),
            defense_boost=_dict.get('defense_boost', 0),
            special_attack_boost=_dict.get('special_attack_boost', 0),
            special_defense_boost=_dict.get('special_defense_boost', 0),
            speed_boost=_dict.get('speed_boost', 0),
            accuracy_boost=_dict.get('accuracy_boost', 0),
            evasion_boost=_dict.get('evasion_boost', 0),
            status=_dict.get('status', None),
            terastallized=_dict.get('terastallized', False),
            volatile_status=_dict.get('volatile_status', set()),
            moves=_dict.get('moves', [])
        )
        return pokemon

    def reset_bonuses(self):
        """
        This method resets various bonuses and status effects currently applied
        to the pokemon.

        This method is typically used to reset the stat boosts of the main
        Pokemon when the opponent gets KOed, preventing the user from
        steamrolling every wild pokemon once the main pokemon is setup with
        stat boosts.

        Args:
            None

        Returns:
            None
        """
        self.stat_stages = {
            'atk': 0,
            'def': 0,
            'spa': 0,
            'spd': 0,
            'spe': 0,
            'accuracy': 0,
            'evasion': 0
            }

    def give_held_item(self, held_item: str) -> None:
        """
        Assigns a held item to the Pokémon and updates relevant data files.

        If the Pokémon is already holding an item, it is removed first. The specified
        item is subtracted from the item bag, assigned as the Pokémon's held item,
        and then saved in the user's Pokémon data files.

        This method updates both `mypokemon_path` (the full Pokémon list) and
        `mainpokemon_path` (if the Pokémon is the main one) to reflect the new held item.

        Args:
            held_item (str): The name of the item to be given to the Pokémon.

        Returns:
            None

        Side Effects:
            - Modifies `mypokemon_path` JSON file to set the held item.
            - Modifies `mainpokemon_path` JSON file if the Pokémon is the main one.
            - Removes one instance of the held item from the item bag.
            - If an item is already held, it is removed first.
            - Uses `ShowInfoLogger` for logging in case of errors via `substract_item_from_itembag`.
        """
        # If the pokemon already holds an object, we remove it to make room for the new one.
        if self.held_item:
            self.remove_held_item()

        substract_item_from_itembag(held_item, quantity=1)
        self.held_item = held_item

        # Then, We save that information in the user data
        # First, we save the info in mypokemon_path
        with open(mypokemon_path, "r", encoding="utf-8") as f:
            pokemon_list_data = json.load(f)

        for i in range(len(pokemon_list_data)):
            if pokemon_list_data[i]["individual_id"] == self.individual_id:
                pokemon_list_data[i]["held_item"] = held_item
                break

        with open(str(mypokemon_path), "w") as f:
            json.dump(pokemon_list_data, f, indent=2)

        # Secondly, we save the info in mainpokemon_path, if the pokemon happens to be our main pokemon
        with open(mainpokemon_path, "r", encoding="utf-8") as f:
            main_pokemon_data = json.load(f)

        if main_pokemon_data[0]["individual_id"] == self.individual_id:
            main_pokemon_data[0]["held_item"] = held_item
            with open(str(mainpokemon_path), "w") as f:
                json.dump(main_pokemon_data, f, indent=2)

    def remove_held_item(self) -> None:
        """
        Removes the held item from the Pokémon and updates relevant data files.

        If the Pokémon is currently holding an item, the item is returned to the item bag
        via `give_item`, the `held_item` attribute is cleared, and the change is saved
        in both `mypokemon_path` (the user's Pokémon list) and `mainpokemon_path` (if the
        Pokémon is the main one).

        Returns:
            None

        Side Effects:
            - Adds the held item back to the item bag using `give_item`.
            - Updates the `mypokemon_path` JSON file to set `held_item` to `None`.
            - If the Pokémon is the main Pokémon, updates the `mainpokemon_path` file as well.
        """
        if self.held_item is None:
            return

        give_item(self.held_item)  # We put the item back in the item bag
        self.held_item = None

        # Then, We save that information in the user data
        # First, we save the info in mypokemon_path
        with open(mypokemon_path, "r", encoding="utf-8") as f:
            pokemon_list_data = json.load(f)

        for i in range(len(pokemon_list_data)):
            if pokemon_list_data[i]["individual_id"] == self.individual_id:
                pokemon_list_data[i]["held_item"] = None
                break

        with open(str(mypokemon_path), "w") as f:
            json.dump(pokemon_list_data, f, indent=2)

        # Secondly, we save the info in mainpokemon_path, if the pokemon happens to be our main pokemon
        with open(mainpokemon_path, "r", encoding="utf-8") as f:
            main_pokemon_data = json.load(f)

        if main_pokemon_data[0]["individual_id"] == self.individual_id:
            main_pokemon_data[0]["held_item"] = None
            with open(str(mainpokemon_path), "w") as f:
                json.dump(main_pokemon_data, f, indent=2)


class PokemonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, PokemonObject):
            data = obj.__dict__.copy()
            # Convert complex types to serializable formats
            data['volatile_status'] = list(data['volatile_status'])
            data['stat_stages'] = data.get('stat_stages', {})
            data['moves'] = data.get('attacks', [])
            return data
        return super().default(obj)
```
---
## 4. `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`

### Why this file is critical
Bridge between Anki events and the battle engine

### Full Contents

```python
import random
from collections import defaultdict
import copy
import traceback
from typing import Union

from .battle import Move
from .objects import Pokemon, State, StateMutator, Side
from .helpers import normalize_name
from .find_state_instructions import get_all_state_instructions
from ..pyobj.error_handler import show_warning_with_traceback

def reset_stat_boosts(pokemon: Pokemon) -> Pokemon:
    """
    Resets all stat boosts of a given Pokemon to zero.

    Args:
        pokemon (Pokemon): The Pokemon whose stat boosts will be reset.

    Returns:
        Pokemon: The same Pokemon object with all stat boosts reset to zero.
    """
    pokemon.attack_boost = 0
    pokemon.defense_boost = 0
    pokemon.special_attack_boost = 0
    pokemon.special_defense_boost = 0
    pokemon.speed_boost = 0
    pokemon.accuracy_boost = 0
    pokemon.evasion_boost = 0
    return pokemon

def reset_side(pokemon: Pokemon, side_conditions: Union[dict, None]=None) -> Side:
    """
    Resets and returns a new Side object for the given Pokemon with default or provided side conditions.

    If no side conditions are provided, a default set with all conditions initialized to zero is used.

    Args:
        pokemon (Pokemon): The active Pokemon for the side.
        side_conditions (Union[dict, None], optional): A dictionary of side conditions to apply.
            If None, defaults to all conditions set to zero.

    Returns:
        Side: A new Side object with the specified active Pokemon, an empty reserve,
              default wish and future sight settings, and the given or default side conditions.
    """
    if side_conditions is None:
        side_conditions = defaultdict(int, {
            'stealthrock': 0,
            'spikes': 0,
            'toxicspikes': 0,
            'tailwind': 0,
            'reflect': 0,
            'lightscreen': 0,
            'auroraveil': 0,
            'protect': 0,
        })
    side = Side(
        active=pokemon,
        reserve={},
        wish=(0, 0),
        side_conditions=side_conditions,
        future_sight=(0, 0),
    )
    return side

def simulate_battle_with_poke_engine(
    main_pokemon: Pokemon,
    enemy_pokemon: Pokemon,
    main_move: str,
    enemy_move: str,
    mutator_full_reset: int,
    state: Union[State, None]=None,
    ):
    """
    Simulates a battle between two Pokémon using the poke-engine if available.
    The function selects the Pokémon moves (either provided or random), handles state changes,
    and applies battle instructions based on the current battle state. The function then
    computes and returns the battle results, including damage dealt, missed moves,
    and the updated battle state.

    Args:
        main_pokemon (Pokemon): The user's active Pokémon.
        enemy_pokemon (Pokemon): The opponent's active Pokémon.
        main_move (str or None): The move chosen by the user's Pokémon. If None, a random move will be selected.
        enemy_move (str or None): The move chosen by the opponent's Pokémon. If None, a random move will be selected.
        new_state (State): The current battle state, including the Pokémon's stats, field conditions, etc.
        mutator_full_reset (int): A flag controlling whether the battle state should be reset.

    Returns:
        tuple: A tuple containing:
            - battle_info (dict): A dictionary with the battle header and instructions for each Pokémon move.
            - new_state (State): The updated battle state after the battle simulation.
            - dmg_from_enemy_move (int): The damage dealt to the user's Pokémon by the enemy.
            - dmg_from_user_move (int): The damage dealt to the enemy's Pokémon by the user.
            - mutator_full_reset (int): The flag indicating if the battle state was reset.

    Raises:
        Exception: If any unexpected error occurs during the simulation, the traceback will be printed.

    Notes:
        - If no moves are provided for the Pokémon, a random move is selected.
        - The outcome of the battle is determined based on probability, with weights reflecting typical battle mechanics.
        - The function prints a summary of the battle result, including damage dealt and whether any moves missed.
        - The state mutator is applied to update the battle state after the moves are resolved.
    """

    # If no move is provided, use a random move
    if main_move is None and main_pokemon.attacks:
        main_move = random.choice(main_pokemon.attacks)
    if enemy_move is None and enemy_pokemon.attacks:
        enemy_move = random.choice(enemy_pokemon.attacks)
    if not main_move:
        main_move = "Splash"
    if not enemy_move:
        enemy_move = "Splash"


    if (state is not None) and (state.user.active.id != main_pokemon.name.lower()):
        mutator_full_reset = 1 # reset AFTER Pokemon is changed !
    if mutator_full_reset not in (0, 1):
        mutator_full_reset = 1

    try:
        main_move_normalized = normalize_name(main_move)
        enemy_move_normalized = normalize_name(enemy_move)


        # Store only the chosen outcome
        battle_header = {
            'user': {
                'name': main_pokemon.name,
                'level': main_pokemon.level,
                'move': main_move
            },
            'opponent': {
                'name': enemy_pokemon.name,
                'level': enemy_pokemon.level,
                'move': enemy_move
            }
        }

        # Create Pokemon objects
        main_pokemon_poke_engine = main_pokemon.to_poke_engine_Pokemon()
        enemy_pokemon_poke_engine = enemy_pokemon.to_poke_engine_Pokemon()

        # Default side_conditions with all needed keys
        side_conditions = defaultdict(int, {
            'stealthrock': 0,
            'spikes': 0,
            'toxicspikes': 0,
            'tailwind': 0,
            'reflect': 0,
            'lightscreen': 0,
            'auroraveil': 0,
            'protect': 0,
        })

        if state is None:
            state = State(
                user=reset_side(main_pokemon_poke_engine),
                opponent=reset_side(enemy_pokemon_poke_engine),
                weather=None,
                field=None,
                trick_room=False,
                )
        else:
            if mutator_full_reset == 0:  # Combat is ongoing
                pass
            elif mutator_full_reset == 1:  # Reset both sides of the fight
                state.user.active = reset_stat_boosts(state.user.active)
                state.user = reset_side(main_pokemon_poke_engine)
                state.opponent = reset_side(enemy_pokemon_poke_engine)

                # Reset battle_status and volatile_status for both engine state Pokémon
                if hasattr(state.user.active, 'battle_status'):
                    state.user.active.battle_status = 'fighting'
                if hasattr(state.user.active, 'volatile_status'):
                    state.user.active.volatile_status = set()
                if hasattr(state.opponent.active, 'battle_status'):
                    state.opponent.active.battle_status = 'fighting'
                if hasattr(state.opponent.active, 'volatile_status'):
                    state.opponent.active.volatile_status = set()
                # Clear Future Sight state on reset - NEW
                if hasattr(state.user, 'future_sight'):
                    state.user.future_sight = (0, 0)
                if hasattr(state.opponent, 'future_sight'):
                    state.opponent.future_sight = (0, 0)

                # Also reset the main_pokemon and enemy_pokemon Python objects
                main_pokemon.battle_status = 'fighting'
                main_pokemon.volatile_status = set()
                enemy_pokemon.battle_status = 'fighting'
                enemy_pokemon.volatile_status = set()

                state.weather = None # Reset weather to None
                state.field = None # Reset field to None
                state.trick_room = False # Reset trick room to None

            else:
                raise ValueError(f"Wrong mutator_full_reset encountered : {mutator_full_reset}")

        mutator = StateMutator(state)

        if state.opponent.active.hp == 0:
            main_move = "Splash"
            enemy_move = "Splash"

        # Get all possible outcomes
        transpose_instructions = get_all_state_instructions(
            mutator, main_move_normalized, enemy_move_normalized
        )

        # Randomly select ONE outcome from possible outcomes, using probability weights for the outcomes in actual Pokemon battles
        # e.g. if P(outcome 1):P(outcome 2) = 20% : 80%, then 20% chance to pick outcome 1 (picks randomly)
        weights = [outcome.percentage for outcome in transpose_instructions]
        chosen_outcome = random.choices(transpose_instructions, weights=weights, k=1)[0]

        instrs = chosen_outcome.instructions

        user_hp_before = int(state.user.active.hp)
        opponent_hp_before = int(state.opponent.active.hp)

        # --- Debugging: State changes BEFORE applying instructions
        state_before = copy.deepcopy(mutator.state)
        mutator.apply(instrs)
        state_after = mutator.state
        battle_info_changes = diff_states(state_before, state_after)
        print_state_changes(battle_info_changes)
        # --- End Debugging

        # Save changes from State to Pokemon objects (enhanced for volatile status)
        main_pokemon.hp = state.user.active.hp
        main_pokemon.current_hp = state.user.active.hp
        enemy_pokemon.hp = state.opponent.active.hp
        enemy_pokemon.current_hp = state.opponent.active.hp

        main_pokemon.stat_stages = {
            'atk': state.user.active.attack_boost,
            'def': state.user.active.defense_boost,
            'spa': state.user.active.special_attack_boost,
            'spd': state.user.active.special_defense_boost,
            'spe': state.user.active.speed_boost,
            'accuracy': state.user.active.accuracy_boost,
            'evasion': state.user.active.evasion_boost
        }

        # Save volatile status from poke-engine state to Pokemon object - NEW
        if hasattr(state.user.active, 'volatile_status'):
            main_pokemon.volatile_status = state.user.active.volatile_status.copy()
        elif not hasattr(main_pokemon, 'volatile_status'):
            main_pokemon.volatile_status = set()


        # Same for enemy Pokemon
        enemy_pokemon.stat_stages = {
            'atk': state.opponent.active.attack_boost,
            'def': state.opponent.active.defense_boost,
            'spa': state.opponent.active.special_attack_boost,
            'spd': state.opponent.active.special_defense_boost,
            'spe': state.opponent.active.speed_boost,
            'accuracy': state.opponent.active.accuracy_boost,
            'evasion': state.opponent.active.evasion_boost
        }

        # Save volatile status for enemy - NEW
        if hasattr(state.opponent.active, 'volatile_status'):
            enemy_pokemon.volatile_status = state.opponent.active.volatile_status.copy()
        elif not hasattr(enemy_pokemon, 'volatile_status'):
            enemy_pokemon.volatile_status = set()

        new_state = copy.deepcopy(state)

        mutator_full_reset = int(0) # preserve battle state - until something else changes this value

        user_hp_after = int(new_state.user.active.hp)
        opponent_hp_after = int(new_state.opponent.active.hp)

        dmg_from_user_move = int(opponent_hp_before - opponent_hp_after)
        dmg_from_enemy_move = int(user_hp_before - user_hp_after)

        # Reference to the founder and creator of Ankimon, Unlucky-life.
        # Unlucky, we are very proud of you for your work. You are a legend.
        # It's been a pleasure being part of this journey. -- h0tp (and friends)

        if int(chosen_outcome.percentage) == 0:
            unlucky_life = int(1)
        else:
            unlucky_life = int(chosen_outcome.percentage)

        # On a serious note, the function above is the CHANCE that the chosen_outcome was picked out of ALL
        # the choices in transpose_instructions, based on factors like accuracy rate, the chance to
        # inflict a certain status (like sleep or paralyze), etc.

        battle_effects = []
        for instr in chosen_outcome.instructions:
            battle_effects.append(list(instr))  # Convert tuples to lists

        battle_info = {
            'battle_header': battle_header,
            'instructions': battle_effects,
            'state': new_state
            }

        print(f"{unlucky_life * 100}% chance: {battle_effects}")
        return battle_info, new_state, dmg_from_enemy_move, dmg_from_user_move, mutator_full_reset, battle_info_changes

    except Exception as e:
        show_warning_with_traceback(exception=e, message="Error simulating battle:")

def diff_states(state_before, state_after, path="", changes=None):
    """
    Recursively compare two state objects and return a list of changed attributes.
    Returns changes in format: {'key': path, 'before': value_before, 'after': value_after}
    """
    if changes is None:
        changes = []

    # Handle None cases
    if state_before is None and state_after is None:
        return changes
    if state_before is None or state_after is None:
        changes.append({
            'key': path or 'root',
            'before': state_before,
            'after': state_after
        })
        return changes

    # Handle primitive types (int, float, str, bool)
    if isinstance(state_before, (int, float, str, bool)) or isinstance(state_after, (int, float, str, bool)):
        if state_before != state_after:
            changes.append({
                'key': path or 'root',
                'before': state_before,
                'after': state_after
            })
        return changes

    # Handle sets
    if isinstance(state_before, set) or isinstance(state_after, set):
        if state_before != state_after:
            changes.append({
                'key': path or 'root',
                'before': state_before,
                'after': state_after
            })
        return changes

    # Handle tuples
    if isinstance(state_before, tuple) or isinstance(state_after, tuple):
        if state_before != state_after:
            changes.append({
                'key': path or 'root',
                'before': state_before,
                'after': state_after
            })
        return changes

    # Handle lists
    if isinstance(state_before, list) and isinstance(state_after, list):
        # Compare list lengths and elements
        if len(state_before) != len(state_after):
            changes.append({
                'key': f"{path}.length" if path else 'length',
                'before': len(state_before),
                'after': len(state_after)
            })

        # Compare elements up to the shorter length
        min_len = min(len(state_before), len(state_after))
        for i in range(min_len):
            new_path = f"{path}[{i}]" if path else f"[{i}]"
            diff_states(state_before[i], state_after[i], new_path, changes)

        # Handle extra elements in longer list
        if len(state_before) > min_len:
            for i in range(min_len, len(state_before)):
                new_path = f"{path}[{i}]" if path else f"[{i}]"
                changes.append({
                    'key': new_path,
                    'before': state_before[i],
                    'after': None
                })
        elif len(state_after) > min_len:
            for i in range(min_len, len(state_after)):
                new_path = f"{path}[{i}]" if path else f"[{i}]"
                changes.append({
                    'key': new_path,
                    'before': None,
                    'after': state_after[i]
                })
        return changes

    # Handle dictionaries
    if isinstance(state_before, dict) and isinstance(state_after, dict):
        all_keys = set(state_before.keys()) | set(state_after.keys())
        for key in all_keys:
            new_path = f"{path}.{key}" if path else str(key)
            before_val = state_before.get(key, None)
            after_val = state_after.get(key, None)
            diff_states(before_val, after_val, new_path, changes)
        return changes

    # Handle custom objects - check if they're the same type
    if type(state_before) != type(state_after):
        changes.append({
            'key': path or 'root',
            'before': state_before,
            'after': state_after
        })
        return changes

    # Custom class: recurse into attributes (__dict__ and __slots__ on the class)
    attrs = set()
    for obj in (state_before, state_after):
        # __dict__ attributes
        if hasattr(obj, "__dict__"):
            attrs.update(vars(obj).keys())
        # __slots__ attributes (check on the class)
        if hasattr(obj.__class__, "__slots__"):
            for slot in obj.__class__.__slots__:
                attrs.add(slot)

    if attrs:
        for attr in attrs:
            before_val = getattr(state_before, attr, None)
            after_val = getattr(state_after, attr, None)
            new_path = f"{path}.{attr}" if path else attr
            diff_states(before_val, after_val, new_path, changes)

    return changes


def print_state_changes(changes):
    """
    Print state changes in a clean format: key: before -> after
    """
    if not changes:
        return

    for change in changes:
        key = change['key']
        before = change['before']
        after = change['after']
        print(f"{key}: {before} -> {after}")

```
---
## 5. `src/Ankimon/poke_engine/battle.py`

### Why this file is critical
Core battle simulator classes

### Full Contents

```python
import itertools
from collections import defaultdict
from collections import namedtuple
from copy import copy
from copy import deepcopy
from abc import ABC
from abc import abstractmethod

from . import constants
import logging

from . import data
from .data import all_move_json
from .data import pokedex
from .data.parse_smogon_stats import MOVES_STRING
from .data.parse_smogon_stats import SPREADS_STRING
from .data.parse_smogon_stats import ABILITY_STRING
from .data.parse_smogon_stats import ITEM_STRING
from .data.helpers import get_pokemon_sets
from .data.helpers import get_mega_pkmn_name
from .data.helpers import PASS_ITEMS
from .data.helpers import PASS_ABILITIES
from .data.helpers import get_all_likely_moves
from .data.helpers import get_most_likely_item
from .data.helpers import get_most_likely_ability
from .data.helpers import get_most_likely_spread
from .data.helpers import get_all_possible_moves_for_random_battle

from .objects import State
from .objects import Side
from .objects import Pokemon as TransposePokemon

from .helpers import remove_duplicate_spreads
from .helpers import get_pokemon_info_from_condition
from .helpers import set_makes_sense
from .helpers import normalize_name
from .helpers import calculate_stats


logger = logging.getLogger(__name__)


LastUsedMove = namedtuple('LastUsedMove', ['pokemon_name', 'move', 'turn'])
DamageDealt = namedtuple('DamageDealt', ['attacker', 'defender', 'move', 'percent_damage', 'crit'])
StatRange = namedtuple("Range", ["min", "max"])


# Based on the format, this dict controls which pokemon will be replaced during team preview
# Some pokemon's forms are not revealed in team preview
smart_team_preview = {
    "gen8ou": {
        "urshifu": "urshifurapidstrike"  # urshifu banned in gen8ou
    }
}


class Battle(ABC):

    def __init__(self, battle_tag):
        self.battle_tag = battle_tag
        self.user = Battler()
        self.opponent = Battler()
        self.weather = None
        self.field = None
        self.trick_room = False

        self.turn = False

        self.started = False
        self.rqid = None

        self.force_switch = False
        self.wait = False

        self.battle_type = None
        self.generation = None
        self.time_remaining = None

        self.request_json = None

    def initialize_team_preview(self, user_json, opponent_pokemon, battle_type):
        self.user.from_json(user_json, first_turn=True)
        self.user.reserve.insert(0, self.user.active)
        self.user.active = None

        for pkmn_string in opponent_pokemon:
            pokemon = Pokemon.from_switch_string(pkmn_string)

            if pokemon.name in smart_team_preview.get(battle_type, {}):
                new_pokemon_name = smart_team_preview[battle_type][pokemon.name]
                logger.info(
                    "Smart team preview: Replaced {} with {}".format(
                        pokemon.name,
                        new_pokemon_name
                    )
                )
                pokemon = Pokemon(new_pokemon_name, pokemon.level)

            self.opponent.reserve.append(pokemon)

        self.started = True
        self.rqid = user_json[constants.RQID]

    def during_team_preview(self):
        ...

    def start_non_team_preview_battle(self, user_json, opponent_switch_string):
        self.user.from_json(user_json, first_turn=True)

        pkmn_information = opponent_switch_string.split('|')[3]
        pkmn = Pokemon.from_switch_string(pkmn_information)
        self.opponent.active = pkmn

        self.started = True
        self.rqid = user_json[constants.RQID]

    def mega_evolve_possible(self):
        return (
                any(g in self.generation for g in constants.MEGA_EVOLVE_GENERATIONS)
        )

    def prepare_battles(self, guess_mega_evo_opponent=True, join_moves_together=False):
        """Returns a list of battles based on this one
        The battles have the opponent's reserve pokemon's unknowns filled in
        The opponent's active pokemon in each of the battles has a different set"""
        battle_copy = deepcopy(self)
        battle_copy.opponent.lock_moves()
        battle_copy.user.lock_active_pkmn_first_turn_moves()

        if battle_copy.user.active.can_mega_evo:
            # mega-evolving here gives the pkmn the random-battle spread (Serious + 85s)
            # unfortunately the correct spread is not stored anywhere as of this being written
            # this only happens on the turn the pkmn mega-evolves - the next turn will be fine
            battle_copy.user.active.forme_change(get_mega_pkmn_name(battle_copy.user.active.name))

        if guess_mega_evo_opponent and not battle_copy.opponent.mega_revealed() and self.mega_evolve_possible():
            check_in_sets = battle_copy.battle_type == constants.STANDARD_BATTLE
            battle_copy.opponent.active.try_convert_to_mega(check_in_sets=check_in_sets)

        # for reserve pokemon only guess their most likely item/ability/spread and guess all moves
        for pkmn in filter(lambda x: x.is_alive(), battle_copy.opponent.reserve):
            pkmn.guess_most_likely_attributes()

        try:
            pokemon_sets = get_pokemon_sets(battle_copy.opponent.active.name)
        except KeyError:
            logger.warning("No sets for {}, trying to find most likely attributes".format(battle_copy.opponent.active.name))
            battle_copy.opponent.active.guess_most_likely_attributes()
            return [battle_copy]

        possible_spreads = sorted(pokemon_sets[SPREADS_STRING], key=lambda x: x[2], reverse=True)
        possible_abilities = sorted(pokemon_sets[ABILITY_STRING], key=lambda x: x[1], reverse=True)
        possible_items = sorted(pokemon_sets[ITEM_STRING], key=lambda x: x[1], reverse=True)
        possible_moves = sorted(pokemon_sets[MOVES_STRING], key=lambda x: x[1], reverse=True)

        spreads = battle_copy.opponent.active.get_possible_spreads(possible_spreads)
        items = battle_copy.opponent.active.get_possible_items(possible_items)
        abilities = battle_copy.opponent.active.get_possible_abilities(possible_abilities)
        expected_moves, chance_moves = battle_copy.opponent.active.get_possible_moves(possible_moves, battle_copy.battle_type)

        if join_moves_together:
            chance_move_combinations = [chance_moves]
        else:
            number_of_unknown_moves = max(4 - len(battle_copy.opponent.active.moves) - len(expected_moves), 0)
            chance_move_combinations = list(itertools.combinations(chance_moves, number_of_unknown_moves))

        combinations = list(itertools.product(spreads, items, abilities, chance_move_combinations))

        # create battle clones for each of the combinations
        battles = list()
        for c in combinations:
            new_battle = deepcopy(battle_copy)

            all_moves = [m.name for m in new_battle.opponent.active.moves]
            all_moves += expected_moves
            all_moves += c[3]
            all_moves = [Move(m) for m in all_moves]

            if join_moves_together or set_makes_sense(c[0][0], c[0][1], c[1], c[2], all_moves):
                new_battle.opponent.active.set_spread(c[0][0], c[0][1])
                if new_battle.opponent.active.name == 'ditto':
                    new_battle.opponent.active.stats = battle_copy.opponent.active.stats
                new_battle.opponent.active.item = c[1]
                new_battle.opponent.active.ability = c[2]
                for m in expected_moves:
                    new_battle.opponent.active.add_move(m)
                for m in c[3]:
                    new_battle.opponent.active.add_move(m)

                logger.debug("Possible set for opponent's {}:\t{} {} {} {} {}".format(battle_copy.opponent.active.name, c[0][0], c[0][1], c[1], c[2], all_moves))
                battles.append(new_battle)

            new_battle.opponent.lock_moves()

        return battles if battles else [battle_copy]

    def create_state(self):
        user_active = TransposePokemon.from_state_pokemon_dict(self.user.active.to_dict())
        user_reserve = dict()
        for mon in self.user.reserve:
            user_reserve[mon.name] = TransposePokemon.from_state_pokemon_dict(mon.to_dict())

        opponent_active = TransposePokemon.from_state_pokemon_dict(self.opponent.active.to_dict())
        opponent_reserve = dict()
        for mon in self.opponent.reserve:
            opponent_reserve[mon.name] = TransposePokemon.from_state_pokemon_dict(mon.to_dict())

        user = Side(user_active, user_reserve, copy(self.user.wish), copy(self.user.side_conditions), copy(self.user.future_sight))
        opponent = Side(opponent_active, opponent_reserve, copy(self.opponent.wish), copy(self.opponent.side_conditions), copy(self.opponent.future_sight))

        state = State(user, opponent, self.weather, self.field, self.trick_room)
        return state

    def get_all_options(self):
        force_switch = self.force_switch or self.user.active.hp <= 0
        wait = self.wait or self.opponent.active.hp <= 0

        # double faint or team preview
        if force_switch and wait:
            user_options = self.user.get_switches() or [constants.DO_NOTHING_MOVE]

            # edge-case for uturn or voltswitch killing
            if (
                    self.user.last_used_move.move in constants.SWITCH_OUT_MOVES and
                    self.opponent.active.hp <= 0 and
                    self.user.last_used_move.turn == self.turn

            ):
                opponent_options = [constants.DO_NOTHING_MOVE]
            else:
                opponent_options = self.opponent.get_switches() or [constants.DO_NOTHING_MOVE]

            return user_options, opponent_options

        if force_switch:
            user_options = self.user.get_switches(reviving=self.user.active.reviving)

            # uturn or voltswitch
            if (
                    self.user.last_used_move.move in constants.SWITCH_OUT_MOVES and
                    self.opponent.last_used_move.turn != self.turn and
                    self.user.last_used_move.turn == self.turn
            ):
                opponent_options = [m.name for m in self.opponent.active.moves if not m.disabled] or [constants.DO_NOTHING_MOVE]
            else:
                opponent_options = [constants.DO_NOTHING_MOVE]
        elif wait:
            opponent_options = self.opponent.get_switches()
            user_options = [constants.DO_NOTHING_MOVE]
        else:
            user_forced_move = self.user.active.forced_move()
            if user_forced_move:
                user_options = [user_forced_move]
            else:
                user_options = [m.name for m in self.user.active.moves if not m.disabled]
                user_options += self.user.get_switches()

            opponent_forced_move = self.opponent.active.forced_move()
            if opponent_forced_move:
                opponent_options = [opponent_forced_move]
            else:
                opponent_options = [m.name for m in self.opponent.active.moves if not m.disabled] or [constants.DO_NOTHING_MOVE]
                opponent_options += self.opponent.get_switches()

        return user_options, opponent_options

    @abstractmethod
    def find_best_move(self):
        ...


class Battler:

    def __init__(self):
        self.active = None
        self.reserve = []
        self.side_conditions = defaultdict(lambda: 0)

        self.name = None
        self.trapped = False
        self.wish = (0, 0)
        self.future_sight = (0, 0)

        self.account_name = None

        self.last_used_move = LastUsedMove('', '', 0)

    def mega_revealed(self):
        return self.active.is_mega or any(p.is_mega for p in self.reserve)

    def lock_active_pkmn_first_turn_moves(self):
        # disable firstimpression and fakeout if the last_used_move was not a switch
        if self.last_used_move.pokemon_name == self.active.name:
            for m in self.active.moves:
                if m.name in constants.FIRST_TURN_MOVES:
                    m.disabled = True

    def lock_active_pkmn_status_moves_if_active_has_assaultvest(self):
        if self.active.item == 'assaultvest':
            for m in self.active.moves:
                if all_move_json[m.name][constants.CATEGORY] == constants.STATUS:
                    m.disabled = True

    def choice_lock_moves(self):
        # if the active pokemon has a choice item and their last used move was by this pokemon -> lock their other moves
        if self.active.item in constants.CHOICE_ITEMS and self.last_used_move.pokemon_name == self.active.name:
            for m in self.active.moves:
                if m.name != self.last_used_move.move:
                    m.disabled = True

    def taunt_lock_moves(self):
        if constants.TAUNT in self.active.volatile_statuses:
            for m in self.active.moves:
                if all_move_json[m.name][constants.CATEGORY] == constants.STATUS:
                    m.disabled = True

    def lock_moves(self):
        self.choice_lock_moves()
        self.lock_active_pkmn_status_moves_if_active_has_assaultvest()
        self.lock_active_pkmn_first_turn_moves()
        self.taunt_lock_moves()

    def from_json(self, user_json, first_turn=False):

        # user_json does not track boosts or volatile statuses
        # they must be taken from the current battle
        if first_turn:
            existing_conditions = (None, None, None)
        else:
            existing_conditions = (
                self.active.name,
                self.active.boosts,
                self.active.volatile_statuses,
                self.active.terastallized,
                self.active.types
            )

        try:
            trapped = user_json[constants.ACTIVE][0].get(constants.TRAPPED, False)
            maybe_trapped = user_json[constants.ACTIVE][0].get(constants.MAYBE_TRAPPED, False)
            self.trapped = trapped or maybe_trapped
        except KeyError:
            self.trapped = False

        self.name = user_json[constants.SIDE][constants.ID]
        self.reserve.clear()
        for index, pkmn_dict in enumerate(user_json[constants.SIDE][constants.POKEMON]):

            nickname = pkmn_dict[constants.IDENT]
            pkmn = Pokemon.from_switch_string(pkmn_dict[constants.DETAILS], nickname=nickname)
            pkmn.ability = pkmn_dict[constants.REQUEST_DICT_ABILITY]
            pkmn.index = index + 1
            pkmn.reviving = pkmn_dict.get(constants.REVIVING, False)
            pkmn.hp, pkmn.max_hp, pkmn.status = get_pokemon_info_from_condition(pkmn_dict[constants.CONDITION])
            for stat, number in pkmn_dict[constants.STATS].items():
                pkmn.stats[constants.STAT_ABBREVIATION_LOOKUPS[stat]] = number

            pkmn.item = pkmn_dict[constants.ITEM] if pkmn_dict[constants.ITEM] else None

            if pkmn_dict[constants.ACTIVE]:
                self.active = pkmn
                if existing_conditions[0] == pkmn.name:
                    pkmn.boosts = existing_conditions[1]
                    pkmn.volatile_statuses = existing_conditions[2]
                    if existing_conditions[3]:
                        pkmn.terastallized = True
                        pkmn.types = existing_conditions[4]
            else:
                self.reserve.append(pkmn)

            for move_name in pkmn_dict[constants.MOVES]:
                pkmn.add_move(move_name)

        # if there is no active pokemon, we do not want to look through it's moves
        if constants.ACTIVE not in user_json:
            return

        try:
            self.active.can_mega_evo = user_json[constants.ACTIVE][0][constants.CAN_MEGA_EVO]
        except KeyError:
            self.active.can_mega_evo = False

        try:
            self.active.can_ultra_burst = user_json[constants.ACTIVE][0][constants.CAN_ULTRA_BURST]
        except KeyError:
            self.active.can_ultra_burst = False

        try:
            self.active.can_dynamax = user_json[constants.ACTIVE][0][constants.CAN_DYNAMAX]
        except KeyError:
            self.active.can_dynamax = False

        try:
            self.active.can_terastallize = user_json[constants.ACTIVE][0][constants.CAN_TERASTALLIZE]
        except KeyError:
            self.active.can_terastallize = False

        # clear the active moves so they can be reset by the options available
        self.active.moves.clear()

        # update the active pokemon's moves to show disabled status/pp remaining
        # this assumes that there is only one active pokemon (single-battle)
        for index, move in enumerate(user_json[constants.ACTIVE][0][constants.MOVES]):
            # hidden power's ID is always 'hiddenpower' regardless of the type
            # the type needs to be parsed separately from the 'move' attribute
            if move[constants.ID] == constants.HIDDEN_POWER:
                self.active.add_move('{}{}'.format(
                        constants.HIDDEN_POWER,
                        move['move'].split()[constants.HIDDEN_POWER_TYPE_STRING_INDEX].lower()
                    )
                )
            else:
                self.active.add_move(move[constants.ID])
            self.active.moves[-1].disabled = move.get(constants.DISABLED, False)
            self.active.moves[-1].current_pp = move.get(constants.PP, 1)

            try:
                self.active.moves[index].can_z = user_json[constants.ACTIVE][0][constants.CAN_Z_MOVE][index]
            except KeyError:
                pass

    def get_switches(self, reviving=False):
        if self.trapped:
            return []

        switches = []
        if reviving:
            it = filter(lambda p: p.hp <= 0, self.reserve)
        else:
            it = filter(lambda p: p.hp > 0, self.reserve)

        for pkmn in it:
            switches.append("{} {}".format(constants.SWITCH_STRING, pkmn.name))
        return switches

    def to_dict(self):
        return {
            constants.TRAPPED: self.trapped,
            constants.ACTIVE: self.active.to_dict(),
            constants.RESERVE: [p.to_dict() for p in self.reserve],
            constants.WISH: copy(self.wish),
            constants.FUTURE_SIGHT: copy(self.future_sight),
            constants.SIDE_CONDITIONS: copy(self.side_conditions)
        }


class Pokemon:

    def __init__(self, name: str, level: int, nature="serious", evs=(85,) * 6):
        self.name = normalize_name(name)
        self.nickname = None
        self.base_name = self.name
        self.level = level
        self.nature = nature
        self.evs = evs
        self.speed_range = StatRange(min=0, max=float("inf"))

        try:
            self.base_stats = pokedex[self.name][constants.BASESTATS]
        except KeyError:
            logger.info("Could not pokedex entry for {}".format(self.name))
            self.name = [k for k in pokedex if self.name.startswith(k)][0]
            logger.info("Using {} instead".format(self.name))
            self.base_stats = pokedex[self.name][constants.BASESTATS]

        self.stats = calculate_stats(self.base_stats, self.level, nature=nature, evs=evs)

        self.max_hp = self.stats.pop(constants.HITPOINTS)
        self.hp = self.max_hp
        if self.name == 'shedinja':
            self.max_hp = 1
            self.hp = 1

        self.ability = None
        self.types = pokedex[self.name][constants.TYPES]
        self.item = constants.UNKNOWN_ITEM

        self.terastallized = False
        self.fainted = False
        self.reviving = False
        self.moves = []
        self.status = None
        self.volatile_statuses = []
        self.boosts = defaultdict(lambda: 0)
        self.can_mega_evo = False
        self.can_ultra_burst = False
        self.can_dynamax = False
        self.is_mega = False
        self.can_have_assaultvest = True
        self.can_have_choice_item = True
        self.can_not_have_band = False
        self.can_not_have_specs = False
        self.can_have_life_orb = True
        self.can_have_heavydutyboots = True

    def forme_change(self, new_pkmn_name):
        hp_percent = float(self.hp) / self.max_hp
        moves = self.moves
        boosts = self.boosts
        status = self.status

        self.__init__(new_pkmn_name, self.level)
        self.hp = round(hp_percent * self.max_hp)
        self.moves = moves
        self.boosts = boosts
        self.status = status

    def try_convert_to_mega(self, check_in_sets=False):
        if self.item != constants.UNKNOWN_ITEM:
            return
        mega_pkmn_name = get_mega_pkmn_name(self.name)
        in_sets_data = mega_pkmn_name in data.pokemon_sets

        if (mega_pkmn_name and check_in_sets and in_sets_data) or (mega_pkmn_name and not check_in_sets):
            logger.debug("Guessing mega-evolution: {}".format(mega_pkmn_name))
            self.forme_change(mega_pkmn_name)

    def is_alive(self):
        return self.hp > 0

    @classmethod
    def extract_nickname_from_pokemonshowdown_string(cls, ps_string):
        return "".join(ps_string.split(":")[1:]).strip()

    @classmethod
    def from_switch_string(cls, switch_string, nickname=None):
        if nickname is not None:
            nickname = cls.extract_nickname_from_pokemonshowdown_string(nickname)

        details = switch_string.split(',')
        name = details[0]
        try:
            level = int(details[1].replace('L', '').strip())
        except (IndexError, ValueError):
            level = 100
        pkmn = Pokemon(name, level)
        pkmn.nickname = nickname
        return pkmn

    def set_spread(self, nature, evs):
        if isinstance(evs, str):
            evs = [int(e) for e in evs.split(',')]
        hp_percent = self.hp / self.max_hp
        self.stats = calculate_stats(self.base_stats, self.level, evs=evs, nature=nature)
        self.nature = nature
        self.evs = evs
        self.max_hp = self.stats.pop(constants.HITPOINTS)
        self.hp = round(self.max_hp * hp_percent)

    def add_move(self, move_name: str):
        try:
            new_move = Move(move_name)
            self.moves.append(new_move)
            return new_move
        except KeyError:
            logger.warning("{} is not a known move".format(move_name))
            return None

    def get_move(self, move_name: str):
        for m in self.moves:
            if m.name == normalize_name(move_name):
                return m
        return None

    def set_likely_moves_unless_revealed(self):
        if len(self.moves) == 4:
            return
        additional_moves = get_all_likely_moves(self.name, [m.name for m in self.moves])
        for m in additional_moves:
            self.moves.append(Move(m))

    def set_most_likely_ability_unless_revealed(self):
        if self.ability is not None:
            return
        ability = get_most_likely_ability(self.name)
        self.ability = ability

    def set_most_likely_item_unless_revealed(self):
        if self.item != constants.UNKNOWN_ITEM:
            return
        item = get_most_likely_item(self.name)
        self.item = item

    def set_most_likely_spread(self):
        nature, evs, _ = get_most_likely_spread(self.name)
        self.set_spread(nature, evs)

    def guess_most_likely_attributes(self):
        self.set_most_likely_ability_unless_revealed()
        self.set_most_likely_item_unless_revealed()
        self.set_likely_moves_unless_revealed()
        self.set_most_likely_spread()

    def get_possible_spreads(self, spreads):
        # update this once you can use previous attacks to rule out spreads
        cumulative_percentage = 0
        possible_spreads = []
        for s in spreads:
            cumulative_percentage += s[2]
            possible_spreads.append(s[:2])
            if s[2] < 20 or cumulative_percentage >= 80:
                break

        return remove_duplicate_spreads(possible_spreads)

    def get_possible_items(self, items):
        # a bunch of flags could be set by the logic in the `battle_modifier` module
        # these flags being set render some items not possible
        # for example, if a pkmn uses 2 different moves without switching, then 'can_have_choice_item' will be False
        # this will omit choice items when guessing an item

        if self.item == constants.UNKNOWN_ITEM:
            cumulative_percentage = 0
            possible_items = []
            for i in items:
                if i[1] < 10 or cumulative_percentage >= 80:
                    return possible_items if possible_items else [constants.UNKNOWN_ITEM]
                elif i[0] in constants.CHOICE_ITEMS and not self.can_have_choice_item:
                    pass
                elif i[0] == 'lifeorb' and not self.can_have_life_orb:
                    pass
                elif i[0] == 'assaultvest' and not self.can_have_assaultvest:
                    pass
                elif i[0] == 'heavydutyboots' and not self.can_have_heavydutyboots:
                    pass
                elif i[0] == 'choiceband' and self.can_not_have_band:
                    pass
                elif i[0] == 'choicespecs' and self.can_not_have_specs:
                    pass
                elif i[0] not in PASS_ITEMS:
                    possible_items.append(i[0])

                cumulative_percentage += i[1]

            return possible_items if possible_items else [constants.UNKNOWN_ITEM]

        else:
            return [self.item]

    def get_possible_abilities(self, abilities):
        if self.ability is None:
            cumulative_percentage = 0
            possible_abilities = []
            for i in abilities:
                if i[1] < 10 or cumulative_percentage >= 80:
                    return possible_abilities if possible_abilities else [None]
                elif i[0] not in PASS_ABILITIES:
                    possible_abilities.append(i[0])

                cumulative_percentage += i[1]

            return possible_abilities if possible_abilities else [None]
        else:
            return [self.ability]

    def get_possible_moves(self, moves, battle_type=constants.STANDARD_BATTLE):
        if battle_type == constants.RANDOM_BATTLE:
            if len(self.moves) == 4:
                return [], []
            known_move_names = [m.name for m in self.moves]
            return [], get_all_possible_moves_for_random_battle(self.name, known_move_names)

        moves_remaining = 4 - len(self.moves)
        expected_moves = list()
        chance_moves = list()

        for m in moves:
            if moves_remaining <= 0:
                break
            elif m[1] > 60 and self.get_move(m[0]) is None:
                expected_moves.append(m[0])
                moves_remaining -= 1
            elif m[1] > 20 and self.get_move(m[0]) is None:
                chance_moves.append(m[0])

        return expected_moves, chance_moves

    def forced_move(self):
        if "phantomforce" in self.volatile_statuses:
            return "phantomforce"
        elif "shadowforce" in self.volatile_statuses:
            return "shadowforce"
        elif "dive" in self.volatile_statuses:
            return "dive"
        elif "dig" in self.volatile_statuses:
            return "dig"
        elif "bounce" in self.volatile_statuses:
            return "bounce"
        elif "fly" in self.volatile_statuses:
            return "fly"
        else:
            return None

    def to_dict(self):
        return {
            constants.FAINTED: self.fainted,
            constants.ID: self.name,
            constants.LEVEL: self.level,
            constants.TYPES: self.types,
            constants.HITPOINTS: self.hp,
            constants.MAXHP: self.max_hp,
            constants.ABILITY: self.ability,
            constants.ITEM: self.item,
            constants.BASESTATS: self.base_stats,
            constants.STATS: self.stats,
            constants.NATURE: self.nature,
            constants.EVS: self.evs,
            constants.BOOSTS: self.boosts,
            constants.STATUS: self.status,
            constants.TERASTALLIZED: self.terastallized,
            constants.VOLATILE_STATUS: set(self.volatile_statuses),
            constants.MOVES: [m.to_dict() for m in self.moves]
        }

    @classmethod
    def get_dummy(cls):
        p = Pokemon('pikachu', 100)
        p.hp = 0
        p.name = ''
        p.ability = None
        p.fainted = True
        return p

    def __eq__(self, other):
        return self.name == other.name and self.level == other.level

    def __repr__(self):
        return "{}, level {}".format(self.name, self.level)


class Move:
    def __init__(self, name):
        name = normalize_name(name)
        if constants.HIDDEN_POWER in name and not name.endswith(constants.HIDDEN_POWER_ACTIVE_MOVE_BASE_DAMAGE_STRING):
            name = "{}{}".format(name, constants.HIDDEN_POWER_ACTIVE_MOVE_BASE_DAMAGE_STRING)
        move_json = all_move_json[name]
        self.name = name
        self.max_pp = int(move_json.get(constants.PP) * 1.6)

        self.disabled = False
        self.can_z = False
        self.current_pp = self.max_pp

    def to_dict(self):
        return {
            "id": self.name,
            "disabled": self.disabled,
            "current_pp": self.current_pp
        }

    def __eq__(self, other):
        return self.name == other.name

    def __repr__(self):
        return "{}".format(self.name)
```
---
## 6. `src/Ankimon/pyobj/ankimon_tracker.py`

### Why this file is critical
Session and progress tracking

### Full Contents

```python
from PyQt6.QtCore import QTimer
from .pokemon_obj import PokemonObject
from datetime import datetime
from .error_handler import show_warning_with_traceback
from ..functions.pokedex_functions import extract_ids_from_file
from ..utils import random_battle_scene

class AnkimonTracker:
    def __init__(self, trainer_card):
        # Object bindings
        self.trainer_card = trainer_card

        # Card reviews
        self.card_ratings_count = {
            "again": 0, "hard": 0, "good": 0, "easy": 0
        }
        self.total_reviews = 0

        self.current_mode = "idle"

        # Session and card timers
        self.session_timer = QTimer()
        self.session_timer.timeout.connect(self.update_session_timer)
        self.card_timer = QTimer()
        self.card_timer.timeout.connect(self.update_card_timer)
        self.cards_battle_round = 0

        # Time tracking
        self.session_time_elapsed = 0
        self.card_time_elapsed = 0
        self.session_time = 0

        # Tracking for multiplier
        self.multiplier = 1
        self.multiplier_card_ratings_count = {
            "again": 0, "hard": 0, "good": 0, "easy": 0
        }
        self.cards_until_calc_multiplier = 2

        self.card_streak = 0  # Streak for follow up right cards

        self.streak_days = []  # List to track [date, streak]
        self.check_streak()

        self.main_pokemon = None
        self.enemy_pokemon = None

        self.pokemon_stats = {}

        # Track Pokemon Battle Cards
        self.cry_counter = 0
        self.attack_counter = 0
        self.slp_counter = 0

        # battlescene
        self.randomize_battle_scene()

        # Check if Pokemon is already caught
        self.owned_pokemon_ids = extract_ids_from_file()
        self.pokemon_in_collection = False

        self.pokemon_encouter = 0 #mode for pokemon encounter
        self.general_card_count_for_battle = 0 #count for general card count for battle
        self.caught = 0 #check if pokemon is caught

        # Start the session timer when the object is initialized
        self.start_session_timer()

    def set_main_pokemon(self, pokemon):
        """Set the main Pokémon being used."""
        if isinstance(pokemon, PokemonObject):
            self.main_pokemon = pokemon

    def set_enemy_pokemon(self, pokemon):
        """Set the enemy Pokémon being fought against."""
        if isinstance(pokemon, PokemonObject):
            self.enemy_pokemon = pokemon

    def check_streak(self):
        """Check and update streak_days based on today's date."""
        today = datetime.today().date()

        if not self.streak_days:
            # Initialize streak if it doesn't exist
            self.streak_days = [[today, 1]]
            return

        # Retrieve the last recorded date and streak count
        last_date, current_streak = self.streak_days[0]

        if last_date == today:
            # No need to update if today is already recorded
            return

        # Calculate the difference in days between today and the last recorded date
        days_difference = (today - last_date).days

        if days_difference == 1:
            # If it's exactly 1 day ago, increase the streak
            self.streak_days[0] = [today, current_streak + 1]
        elif days_difference > 1:
            # If it's more than 1 day, reset the streak
            self.streak_days[0] = [today, 1]

    def get_main_pokemon_stats(self):
        """Retrieve the stats of the main Pokémon."""
        if self.main_pokemon:
            return self.main_pokemon.get_stats()
        return None

    def get_enemy_pokemon_stats(self):
        """Retrieve the stats of the enemy Pokémon."""
        if self.enemy_pokemon:
            return self.enemy_pokemon.get_stats()
        return None

    def add_pokemon(self, pokemon):
        """Add a PokemonObject to the tracker."""
        if isinstance(pokemon, PokemonObject):
            self.pokemon_stats[pokemon.id] = pokemon.get_stats()

    def update_pokemon_stats(self, pokemon):
        """Update stats of a given PokemonObject in the tracker."""
        if pokemon.id in self.pokemon_stats:
            self.pokemon_stats[pokemon.id] = pokemon.get_stats()

    def get_pokemon_stats(self, pokemon_id):
        """Retrieve stats of a specific Pokémon by its ID."""
        return self.pokemon_stats.get(pokemon_id)

    def review(self, grade):
        """Track review statistics based on the grade."""

        if grade == "again":
            # Reset streak
            self.card_streak = 0
        elif grade in ["good", "hard", "easy"]:
            # Increment streak
            self.card_streak += 1
        else:
            raise ValueError("Invalid grade type")
        self.card_ratings_count[grade] += 1
        self.multiplier_card_ratings_count[grade] += 1

        self.total_reviews += 1

        # Stop the card timer after answering
        self.reset_card_timer()

        self.cards_until_calc_multiplier -= 1
        # After 2 cards - calculate multiplier
        if self.cards_until_calc_multiplier <= 0:
            self.cards_until_calc_multiplier = 2
            self.calc_multiply_card_rating()

    #def update_streak(self, new_day):
    #    """Update the streak for daily reviews (each position represents a day)."""
    #    if not self.streak_days or self.streak_days[-1] != new_day:
    #        self.streak_days.append(new_day)  # Add a new day to the streak_days array

    def get_stats(self):
        """Get all the tracked statistics."""
        return {
            "total_reviews": self.total_reviews,
            "card_streak": self.card_streak,
            "card_ratings_count": self.card_ratings_count,
            "multiplier": self.multiplier,
            "multiplier_card_ratings_count": self.multiplier_card_ratings_count,
            "card_time_elapsed": self.card_time_elapsed,
            "session_time": self.session_time_elapsed,  # Include session time here
            "current_mode": self.current_mode,
            "streak_days": self.streak_days,
            "main_pokemon": self.get_main_pokemon_stats(),
            "enemy_pokemon": self.get_enemy_pokemon_stats(),
        }

    def start_card_timer(self):
        """Start the card answer timer."""
        self.card_time_elapsed = 0  # Reset for each new card
        self.card_timer.start(1000)  # Update every second

    def stop_card_timer(self):
        """Stop the card answer timer."""
        self.card_timer.stop()

    def update_card_timer(self):
        """Update the card timer for each second spent on a card."""
        self.card_time_elapsed += 1

    def start_session_timer(self):
        """Start the session timer."""
        self.session_time_elapsed = 0  # Reset session timer on new session
        self.session_timer.start(1000)  # Session timer updates every second

    def stop_session_timer(self):
        """Stop the session timer."""
        self.session_timer.stop()

    def update_session_timer(self):
        """Increment the total session time each second."""
        self.session_time_elapsed += 1

    def calc_multiply_card_rating(self):
        """Calculate the multiplier based on recent card rating counts."""

        max_points = 20
        multiply_sum = (self.multiplier_card_ratings_count['easy'] * 20 +
                        self.multiplier_card_ratings_count['hard'] * 5 +
                        self.multiplier_card_ratings_count['good'] * 10)

        self.multiplier = multiply_sum / max_points
        # Reset card ratings count for next round
        self.multiplier_card_ratings_count = {"again": 0, "hard": 0, "good": 0, "easy": 0}

    def reset_timers(self):
        """Reset both the session and card timers."""
        self.session_time_elapsed = 0

    def reset_card_timer(self):
        self.card_time_elapsed = 0

    #def check_pokecoll_in_list(self):
    #    owned_pokemon_ids = self.owned_pokemon_ids
    #    id = self.enemy_pokemon.id
    #    self.pokemon_in_collection = False
    #    for num in owned_pokemon_ids:
    #        if num == id:
    #            self.pokemon_in_collection = True

    def get_ids_in_collection(self):
        try:
            owned_pokemon_ids = []
            owned_pokemon_ids = extract_ids_from_file()
            self.owned_pokemon_ids = owned_pokemon_ids
        except Exception as e:
            show_warning_with_traceback(parent=mw, exception=e, message="Error: from AnkimonTracker with function extract_ids_from_file")

    #def get_badges(self):
    #    pass

    def randomize_battle_scene(self):
        self.battlescene_file = random_battle_scene()
```
---
## 7. `src/Ankimon/poke_engine/instruction_generator.py`

### Why this file is critical
Battle state mutation logic

### Full Contents

```python
from copy import copy

from . import constants
import logging

from .damage_calculator import type_effectiveness_modifier
from .special_effects.abilities.on_switch_in import ability_on_switch_in
from .special_effects.items.on_switch_in import item_on_switch_in
from .special_effects.items.end_of_turn import item_end_of_turn
from .special_effects.abilities.end_of_turn import ability_end_of_turn
from .special_effects.moves.after_move import after_move
from .special_effects.moves import move_special_effect

logger = logging.getLogger(__name__)


opposite_side = {
    constants.USER: constants.OPPONENT,
    constants.OPPONENT: constants.USER
}


same_side_strings = [
    constants.SELF,
    constants.ALLY_SIDE
]


opposing_side_strings = [
    constants.NORMAL,
    constants.OPPONENT,
    constants.FOESIDE,
    constants.ALL_ADJACENT_FOES,
    constants.ALL_ADJACENT,
    constants.ALL,
]


accuracy_multiplier_lookup = {
    -6: 3/9,
    -5: 3/8,
    -4: 3/7,
    -3: 3/6,
    -2: 3/5,
    -1: 3/4,
    0: 3/3,
    1: 4/3,
    2: 5/3,
    3: 6/3,
    4: 7/3,
    5: 8/3,
    6: 9/3
}





def get_instructions_from_move_special_effect(mutator, attacking_side, attacking_pokemon, defending_pokemon, move_name, instructions):
    if instructions.frozen:
        return [instructions]

    try:
        special_logic_move_function = getattr(move_special_effect, move_name)
    except AttributeError:
        new_instructions = list()
    else:
        mutator.apply(instructions.instructions)
        new_instructions = special_logic_move_function(mutator, attacking_side, get_side_from_state(mutator.state, attacking_side), attacking_pokemon, defending_pokemon)
        new_instructions = new_instructions or list()
        mutator.reverse(instructions.instructions)

    for i in new_instructions:
        instructions.add_instruction(i)

    return [instructions]


def get_instructions_from_volatile_statuses(mutator, volatile_status, attacker, affected_side, first_move, instruction):
    if instruction.frozen or not volatile_status:
        return [instruction]

    if affected_side in same_side_strings:
        affected_side = attacker
    elif affected_side in opposing_side_strings:
        affected_side = opposite_side[attacker]
    else:
        logger.critical("Invalid affected_side: {}".format(affected_side))
        return [instruction]

    side = get_side_from_state(mutator.state, affected_side)
    mutator.apply(instruction.instructions)
    if volatile_status in side.active.volatile_status:
        mutator.reverse(instruction.instructions)
        return [instruction]

    if can_be_volatile_statused(side, volatile_status, first_move) and volatile_status not in side.active.volatile_status:
        apply_status_instruction = (
            constants.MUTATOR_APPLY_VOLATILE_STATUS,
            affected_side,
            volatile_status
        )
        mutator.reverse(instruction.instructions)
        instruction.add_instruction(apply_status_instruction)
        if volatile_status == constants.SUBSTITUTE:
            instruction.add_instruction(
                (
                    constants.MUTATOR_DAMAGE,
                    affected_side,
                    side.active.maxhp * 0.25
                )
            )
    else:
        mutator.reverse(instruction.instructions)

    return [instruction]


def get_instructions_from_switch(mutator, attacker, switch_pokemon_name, instructions):
    if attacker not in opposite_side:
        raise ValueError("attacker parameter must be one of: {}".format(', '.join(opposite_side)))

    attacking_side = get_side_from_state(mutator.state, attacker)
    defending_side = get_side_from_state(mutator.state, opposite_side[attacker])
    mutator.apply(instructions.instructions)
    instruction_additions = remove_volatile_status_and_boosts_instructions(attacking_side, attacker)
    mutator.apply(instruction_additions)

    for move in filter(lambda x: x[constants.DISABLED] is True and x[constants.CURRENT_PP], attacking_side.active.moves):
        remove_disabled_instruction = (
            constants.MUTATOR_ENABLE_MOVE,
            attacker,
            move[constants.ID]
        )
        mutator.apply_one(remove_disabled_instruction)
        instruction_additions.append(remove_disabled_instruction)

    if attacking_side.active.ability == 'regenerator' and attacking_side.active.hp:
        hp_missing = attacking_side.active.maxhp - attacking_side.active.hp
        regenerator_instruction = (
            constants.MUTATOR_HEAL,
            attacker,
            int(min(1 / 3 * attacking_side.active.maxhp, hp_missing))
        )
        mutator.apply_one(regenerator_instruction)
        instruction_additions.append(regenerator_instruction)
    elif attacking_side.active.ability == 'naturalcure' and attacking_side.active.status is not None:
        naturalcure_instruction = (
            constants.MUTATOR_REMOVE_STATUS,
            attacker,
            attacking_side.active.status
        )
        mutator.apply_one(naturalcure_instruction)
        instruction_additions.append(naturalcure_instruction)

    switch_instruction = (
        constants.MUTATOR_SWITCH,
        attacker,
        attacking_side.active.id,
        switch_pokemon_name
    )
    mutator.apply_one(switch_instruction)
    instruction_additions.append(switch_instruction)

    switch_pkmn = attacking_side.active
    if switch_pkmn.item != 'heavydutyboots':

        # account for stealth rock damage
        if attacking_side.side_conditions[constants.STEALTH_ROCK] == 1:
            multiplier = type_effectiveness_modifier('rock', switch_pkmn.types)
            stealth_rock_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                min(1 / 8 * multiplier * switch_pkmn.maxhp, switch_pkmn.hp)
            )
            mutator.apply_one(stealth_rock_instruction)
            instruction_additions.append(stealth_rock_instruction)

        # account for spikes damage
        if attacking_side.side_conditions[constants.SPIKES] > 0 and switch_pkmn.is_grounded():
            spike_count = attacking_side.side_conditions[constants.SPIKES]
            spikes_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                min(1 / 8 * spike_count * switch_pkmn.maxhp, switch_pkmn.hp)
            )
            mutator.apply_one(spikes_instruction)
            instruction_additions.append(spikes_instruction)

        # account for stickyweb speed drop
        if attacking_side.side_conditions[constants.STICKY_WEB] == 1 and switch_pkmn.is_grounded() and switch_pkmn.ability not in constants.IMMUNE_TO_STAT_LOWERING_ABILITIES:
            sticky_web_instruction = (
                constants.MUTATOR_UNBOOST,
                attacker,
                constants.SPEED,
                1
            )
            mutator.apply_one(sticky_web_instruction)
            instruction_additions.append(sticky_web_instruction)

        # account for toxic spikes effect
        if attacking_side.side_conditions[constants.TOXIC_SPIKES] >= 1 and switch_pkmn.is_grounded():
            toxic_spike_instruction = None
            if not immune_to_status(mutator.state, switch_pkmn, switch_pkmn, constants.POISON):
                if attacking_side.side_conditions[constants.TOXIC_SPIKES] == 1:
                    toxic_spike_instruction = (
                        constants.MUTATOR_APPLY_STATUS,
                        attacker,
                        constants.POISON
                    )
                elif attacking_side.side_conditions[constants.TOXIC_SPIKES] == 2:
                    toxic_spike_instruction = (
                        constants.MUTATOR_APPLY_STATUS,
                        attacker,
                        constants.TOXIC
                    )
            elif 'poison' in switch_pkmn.types:
                toxic_spike_instruction = (
                    constants.MUTATOR_SIDE_END,
                    attacker,
                    constants.TOXIC_SPIKES,
                    attacking_side.side_conditions[constants.TOXIC_SPIKES]
                )
            if toxic_spike_instruction is not None:
                mutator.apply_one(toxic_spike_instruction)
                instruction_additions.append(toxic_spike_instruction)

    # account for switch-in abilities
    ability_switch_in_instructions = ability_on_switch_in(
        switch_pkmn.ability,
        mutator.state,
        attacker,
        attacking_side.active,
        opposite_side[attacker],
        defending_side.active
    )
    if ability_switch_in_instructions is not None:
        for i in ability_switch_in_instructions:
            mutator.apply_one(i)
            instruction_additions.append(i)

    # account for switch-in items
    item_switch_in_instructions = item_on_switch_in(
        switch_pkmn.item,
        mutator.state,
        attacker,
        attacking_side.active,
        opposite_side[attacker],
        defending_side.active
    )
    if item_switch_in_instructions is not None:
        for i in item_switch_in_instructions:
            mutator.apply_one(i)
            instruction_additions.append(i)

    mutator.reverse(instruction_additions)
    mutator.reverse(instructions.instructions)
    for i in instruction_additions:
        instructions.add_instruction(i)

    return instructions


def get_instructions_from_flinched(mutator, attacker, instruction):
    """If the attacker has been flinched, freeze the state so that nothing happens"""
    if attacker not in opposite_side:
        raise ValueError("attacker parameter must be one of: {}".format(', '.join(opposite_side)))

    side = get_side_from_state(mutator.state, attacker)
    if constants.FLINCH in side.active.volatile_status:
        remove_flinch_instruction = (
            constants.MUTATOR_REMOVE_VOLATILE_STATUS,
            attacker,
            constants.FLINCH
        )
        mutator.apply_one(remove_flinch_instruction)
        instruction.add_instruction(remove_flinch_instruction)
        instruction.frozen = True
        return instruction
    else:
        return instruction


def get_instructions_from_statuses_that_freeze_the_state(mutator, attacker, defender, move, opponent_move, instruction):
    instructions = [instruction]
    attacker_side = get_side_from_state(mutator.state, attacker)
    defender_side = get_side_from_state(mutator.state, defender)

    mutator.apply(instruction.instructions)

    if constants.PARALYZED == attacker_side.active.status:
        fully_paralyzed_instruction = copy(instruction)
        fully_paralyzed_instruction.update_percentage(constants.FULLY_PARALYZED_PERCENT)
        fully_paralyzed_instruction.frozen = True
        instruction.update_percentage(1 - constants.FULLY_PARALYZED_PERCENT)
        instructions.append(fully_paralyzed_instruction)

    elif constants.SLEEP == attacker_side.active.status:
        still_asleep_instruction = copy(instruction)
        still_asleep_instruction.update_percentage(1 - constants.WAKE_UP_PERCENT)
        still_asleep_instruction.frozen = True
        instruction.update_percentage(constants.WAKE_UP_PERCENT)
        instruction.add_instruction(
            (
                constants.MUTATOR_REMOVE_STATUS,
                attacker,
                constants.SLEEP
            )
        )
        instructions.append(still_asleep_instruction)

    elif constants.FROZEN == attacker_side.active.status:
        still_frozen_instruction = copy(instruction)
        instruction.add_instruction(
            (
                constants.MUTATOR_REMOVE_STATUS,
                attacker,
                constants.FROZEN
            )
        )
        if move[constants.ID] not in constants.THAW_IF_USES and opponent_move.get(constants.ID) not in constants.THAW_IF_HIT_BY and opponent_move.get(constants.TYPE) != 'fire':
            still_frozen_instruction.update_percentage(1 - constants.THAW_PERCENT)
            still_frozen_instruction.frozen = True
            instruction.update_percentage(constants.THAW_PERCENT)
            instructions.append(still_frozen_instruction)

    if constants.POWDER in move[constants.FLAGS] and ('grass' in defender_side.active.types or defender_side.active.ability == 'overcoat'):
        instruction.frozen = True

    if move[constants.TYPE] == 'electric' and 'ground' in defender_side.active.types:
        instruction.frozen = True

    mutator.reverse(instruction.instructions)

    return instructions


def get_instructions_from_damage(mutator, defender, damage, accuracy, attacking_move, instruction):
    attacker = opposite_side[defender]
    attacker_side = get_side_from_state(mutator.state, attacker)
    damage_side = get_side_from_state(mutator.state, defender)

    # `damage is None` means that the move does not deal damage
    # for example, will-o-wisp
    if instruction.frozen or damage is None:
        return [instruction]

    crash = attacking_move.get(constants.CRASH)
    recoil = attacking_move.get(constants.RECOIL)
    drain = attacking_move.get(constants.DRAIN)
    move_flags = attacking_move.get(constants.FLAGS, {})

    mutator.apply(instruction.instructions)

    if accuracy is True or "glaiverush" in damage_side.active.volatile_status:
        accuracy = 100
    else:
        accuracy = min(100, accuracy * accuracy_multiplier_lookup[attacker_side.active.accuracy_boost] / accuracy_multiplier_lookup[damage_side.active.evasion_boost])
    percent_hit = accuracy / 100

    # `damage == 0` means that the move deals damage, but not in this situation
    # for example: using Return against a Ghost-type
    # the state must be frozen because any secondary effects must not take place
    if damage == 0:
        if crash:
            crash_percent = crash[0] / crash[1]
            crash_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                min(int(crash_percent * attacker_side.active.maxhp), attacker_side.active.hp)
            )
            mutator.reverse(instruction.instructions)
            instruction.add_instruction(crash_instruction)
        else:
            mutator.reverse(instruction.instructions)
        instruction.frozen = True
        return [instruction]

    if defender not in opposite_side:
        raise ValueError("attacker parameter must be one of: {}".format(', '.join(opposite_side)))

    instructions = []
    instruction_additions = []
    move_missed_instruction = copy(instruction)
    hit_sub = False
    if percent_hit > 0:
        if constants.SUBSTITUTE in damage_side.active.volatile_status and constants.SOUND not in move_flags and attacker_side.active.ability != 'infiltrator':
            hit_sub = True
            if damage >= damage_side.active.maxhp * 0.25:
                actual_damage = damage_side.active.maxhp * 0.25
                instruction_additions.append(
                    (
                        constants.MUTATOR_REMOVE_VOLATILE_STATUS,
                        defender,
                        constants.SUBSTITUTE
                    )
                )
            else:
                actual_damage = damage
        else:
            # dont drop hp below 0 (min() statement), and dont overheal (max() statement)
            actual_damage = max(min(damage, damage_side.active.hp), -1*(damage_side.active.maxhp - damage_side.active.hp))

            if damage_side.active.ability == 'sturdy' and damage_side.active.hp == damage_side.active.maxhp:
                actual_damage -= 1

            instruction_additions.append(
                (
                    constants.MUTATOR_DAMAGE,
                    defender,
                    actual_damage
                )
            )

            if attacker_side.active.ability == "beastboost" and actual_damage == damage_side.active.hp:
                highest_stat = attacker_side.active.get_highest_stat()
                if attacker_side.active.get_boost_from_boost_string(highest_stat) < 6:
                    instruction_additions.append(
                        (
                            constants.MUTATOR_BOOST,
                            attacker,
                            highest_stat,
                            1
                        )
                    )

        instruction.update_percentage(percent_hit)

        if damage_side.active.hp <= 0:
            instruction.frozen = True

        if drain:
            drain_percent = drain[0] / drain[1]
            drain_instruction = (
                constants.MUTATOR_HEAL,
                attacker,
                min(int(drain_percent * actual_damage), int(attacker_side.active.maxhp - attacker_side.active.hp))
            )
            instruction_additions.append(drain_instruction)
        if recoil:
            recoil_percent = recoil[0] / recoil[1]
            recoil_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                min(int(recoil_percent * actual_damage), int(attacker_side.active.hp))
            )
            instruction_additions.append(recoil_instruction)

        after_move_instructions = after_move(
            attacking_move[constants.ID],
            mutator.state,
            attacker,
            defender,
            attacker_side,
            damage_side,
            True,
            hit_sub
        )
        instruction_additions += after_move_instructions

        instructions.append(instruction)

    if percent_hit < 1:
        move_missed_instruction.frozen = True
        move_missed_instruction.update_percentage(1 - percent_hit)
        if crash:
            crash_percent = crash[0] / crash[1]
            crash_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                min(int(crash_percent * attacker_side.active.maxhp), attacker_side.active.hp)
            )
            move_missed_instruction.add_instruction(crash_instruction)

        if attacker_side.active.item == 'blunderpolicy':
            blunder_policy_increase_speed_instruction = (
                constants.MUTATOR_BOOST,
                attacker,
                constants.SPEED,
                2
            )
            move_missed_instruction.add_instruction(blunder_policy_increase_speed_instruction)

        after_move_instructions = after_move(
            attacking_move[constants.ID],
            mutator.state,
            attacker,
            defender,
            attacker_side,
            damage_side,
            False,
            False
        )
        for i in after_move_instructions:
            move_missed_instruction.add_instruction(i)

        instructions.append(move_missed_instruction)

    mutator.reverse(instruction.instructions)
    for i in instruction_additions:
        instruction.add_instruction(i)

    return instructions


def get_instructions_from_defenders_ability_after_move(mutator, move, ability_name, attacking_pokemon, attacker_string, instruction):
    all_instructions = [instruction]
    if instruction.frozen:
        return all_instructions

    if attacker_string not in opposite_side:
        raise ValueError("attacker parameter must be one of: {}".format(', '.join(opposite_side)))

    if (
        ability_name == "static"
        and constants.CONTACT in move[constants.FLAGS]
        and attacking_pokemon.item != "protectivepads"
    ):
        return get_instructions_from_status_effects(
            mutator,
            attacker_string,
            constants.PARALYZED,
            30,
            instruction
        )
    elif (
        ability_name == "flamebody"
        and constants.CONTACT in move[constants.FLAGS]
        and attacking_pokemon.item != "protectivepads"
    ):
        return get_instructions_from_status_effects(
            mutator,
            attacker_string,
            constants.BURN,
            30,
            instruction
        )

    return all_instructions


def get_instructions_from_side_conditions(mutator, attacker_string, side_string, condition, instruction):
    if instruction.frozen:
        return [instruction]

    if attacker_string not in opposite_side:
        raise ValueError("attacker parameter must be one of: {}".format(', '.join(opposite_side)))

    if side_string in same_side_strings:
        side_string = attacker_string
    elif side_string in opposing_side_strings:
        side_string = opposite_side[attacker_string]
    else:
        raise ValueError("Invalid Side String: {}".format(side_string))

    instruction_additions = []
    side = get_side_from_state(mutator.state, side_string)
    mutator.apply(instruction.instructions)

    if condition == constants.WISH:
        if side.wish[0] == 0:
            instruction_additions.append(
                (
                    constants.MUTATOR_WISH_START,
                    side_string,
                    side.active.maxhp / 2,
                    side.wish[1]
                )
            )

    else:
        if condition == constants.SPIKES:
            max_layers = 3
        elif condition == constants.TOXIC_SPIKES:
            max_layers = 2
        elif condition == constants.AURORA_VEIL:
            max_layers = 1 if mutator.state.weather in constants.HAIL_OR_SNOW else 0
        else:
            max_layers = 1

        if side.side_conditions[condition] < max_layers:
            instruction_additions.append(
                (
                    constants.MUTATOR_SIDE_START,
                    side_string,
                    condition,
                    1
                )
            )

    mutator.reverse(instruction.instructions)
    for i in instruction_additions:
        instruction.add_instruction(i)

    return [instruction]


def get_instructions_from_hazard_clearing_moves(mutator, attacker_string, move, instruction):
    if instruction.frozen:
        return [instruction]

    if attacker_string not in opposite_side:
        raise ValueError("attacker parameter must be one of: {}".format(', '.join(opposite_side)))

    defender_string = opposite_side[attacker_string]

    instruction_additions = []
    mutator.apply(instruction.instructions)

    attacker_side = get_side_from_state(mutator.state, attacker_string)
    defender_side = get_side_from_state(mutator.state, defender_string)

    if move[constants.ID] == 'defog':
        if mutator.state.field is not None:
            instruction_additions.append(
                (
                    constants.MUTATOR_FIELD_END,
                    mutator.state.field
                )
            )
        for side_condition, amount in attacker_side.side_conditions.items():
            if amount > 0 and side_condition in constants.DEFOG_CLEARS:
                instruction_additions.append(
                    (
                        constants.MUTATOR_SIDE_END,
                        attacker_string,
                        side_condition,
                        amount
                    )
                )
        for side_condition, amount in defender_side.side_conditions.items():
            if amount > 0 and side_condition in constants.DEFOG_CLEARS:
                instruction_additions.append(
                    (
                        constants.MUTATOR_SIDE_END,
                        defender_string,
                        side_condition,
                        amount
                    )
                )

    # ghost-type misses are dealt with by freezing the state. i.e. this elif will not be reached if the move missed
    elif move[constants.ID] == "rapidspin" or move[constants.ID] == "mortalspin" or move[constants.ID] == "tidyup":
        side = get_side_from_state(mutator.state, attacker_string)
        for side_condition, amount in side.side_conditions.items():
            if amount > 0 and side_condition in constants.SPIN_TIDYUP_CLEARS:
                instruction_additions.append(
                    (
                        constants.MUTATOR_SIDE_END,
                        attacker_string,
                        side_condition,
                        amount
                    )
                )
    elif move[constants.ID] == constants.COURT_CHANGE:
        sides = [
            (constants.USER, mutator.state.user),
            (constants.OPPONENT, mutator.state.opponent)
        ]
        for side_name, side_object in sides:
            for side_condition in side_object.side_conditions:
                if side_object.side_conditions[side_condition] and side_condition in constants.COURT_CHANGE_SWAPS:
                    instruction_additions.append(
                        (
                            constants.MUTATOR_SIDE_END,
                            side_name,
                            side_condition,
                            side_object.side_conditions[side_condition]
                        )
                    )
                    instruction_additions.append(
                        (
                            constants.MUTATOR_SIDE_START,
                            opposite_side[side_name],
                            side_condition,
                            side_object.side_conditions[side_condition]
                        )
                    )

    else:
        raise ValueError("{} is not a hazard clearing move".format(move[constants.ID]))

    mutator.reverse(instruction.instructions)
    for i in instruction_additions:
        instruction.add_instruction(i)

    return [instruction]


def get_instructions_from_status_effects(mutator, defender, status, accuracy, instruction):
    """Returns the possible states from status effects"""
    if instruction.frozen or status is None:
        return [instruction]

    if defender not in opposite_side:
        raise ValueError("attacker parameter must be one of: {}".format(', '.join(opposite_side)))

    instructions = []
    if accuracy is True:
        accuracy = 100
    percent_hit = accuracy / 100

    mutator.apply(instruction.instructions)
    instruction_additions = []
    defending_side = get_side_from_state(mutator.state, defender)
    attacking_side = get_side_from_state(mutator.state, opposite_side[defender])

    if sleep_clause_activated(defending_side, status):
        mutator.reverse(instruction.instructions)
        return [instruction]

    if immune_to_status(mutator.state, defending_side.active, attacking_side.active, status):
        mutator.reverse(instruction.instructions)
        return [instruction]

    move_missed_instruction = copy(instruction)
    if percent_hit > 0:
        move_hit_instruction = (
            constants.MUTATOR_APPLY_STATUS,
            defender,
            status
        )

        instruction_additions.append(move_hit_instruction)
        instruction.update_percentage(percent_hit)
        instructions.append(instruction)

    if percent_hit < 1:
        move_missed_instruction.frozen = True
        move_missed_instruction.update_percentage(1 - percent_hit)
        if attacking_side.active.item == 'blunderpolicy':
            blunder_policy_increase_speed_instruction = (
                constants.MUTATOR_BOOST,
                opposite_side[defender],
                constants.SPEED,
                2
            )
            move_missed_instruction.add_instruction(blunder_policy_increase_speed_instruction)
        instructions.append(move_missed_instruction)

    mutator.reverse(instruction.instructions)
    for i in instruction_additions:
        instruction.add_instruction(i)

    return instructions


def get_instructions_from_boosts(mutator, side_string, boosts, accuracy, instruction):
    if instruction.frozen or not boosts:
        return [instruction]

    if side_string not in opposite_side:
        raise ValueError("attacker parameter must be one of: {}. Value: {}".format(
            ', '.join(opposite_side),
            side_string
        )
        )

    instructions = []
    if accuracy is True:
        accuracy = 100
    percent_hit = accuracy / 100

    mutator.apply(instruction.instructions)
    side = get_side_from_state(mutator.state, side_string)

    instruction_additions = []
    move_missed_instruction = copy(instruction)
    if percent_hit > 0:
        for k, v in boosts.items():
            pkmn_boost = side.active.get_boost_from_boost_string(k)
            if v > 0:
                new_boost = pkmn_boost + v
                if new_boost > constants.MAX_BOOSTS:
                    new_boost = constants.MAX_BOOSTS
                boost_instruction = (
                    constants.MUTATOR_BOOST,
                    side_string,
                    k,
                    new_boost - pkmn_boost
                )
                instruction_additions.append(boost_instruction)
            elif (
                side.active.ability not in constants.IMMUNE_TO_STAT_LOWERING_ABILITIES and
                side.active.item not in constants.IMMUNE_TO_STAT_LOWERING_ITEMS
            ):
                new_boost = pkmn_boost + v
                if new_boost < -1 * constants.MAX_BOOSTS:
                    new_boost = -1 * constants.MAX_BOOSTS
                boost_instruction = (
                    constants.MUTATOR_BOOST,
                    side_string,
                    k,
                    new_boost - pkmn_boost
                )
                instruction_additions.append(boost_instruction)

        instruction.update_percentage(percent_hit)
        instructions.append(instruction)

    if percent_hit < 1:
        move_missed_instruction.update_percentage(1 - percent_hit)
        instructions.append(move_missed_instruction)

    mutator.reverse(instruction.instructions)
    for i in instruction_additions:
        instruction.add_instruction(i)

    return instructions


def get_instructions_from_flinching_moves(defender, accuracy, first_move, instruction):
    if instruction.frozen or not first_move:
        return [instruction]

    if defender not in opposite_side:
        raise ValueError("attacker parameter must be one of: {}".format(', '.join(opposite_side)))

    instructions = []
    if accuracy is True:
        accuracy = 100
    percent_hit = accuracy / 100

    if percent_hit > 0:
        flinched_instruction = copy(instruction)
        flinch_mutator_instruction = (
            constants.MUTATOR_APPLY_VOLATILE_STATUS,
            defender,
            constants.FLINCH
        )
        flinched_instruction.add_instruction(flinch_mutator_instruction)
        flinched_instruction.update_percentage(percent_hit)
        instructions.append(flinched_instruction)

    if percent_hit < 1:
        instruction.update_percentage(1 - percent_hit)
        instructions.append(instruction)

    return instructions


def get_instructions_from_attacker_recovery(mutator, attacker_string, move, instruction):
    if instruction.frozen:
        return [instruction]

    mutator.apply(instruction.instructions)

    target = move[constants.HEAL_TARGET]
    if target in opposing_side_strings:
        side_string = opposite_side[attacker_string]
    else:
        side_string = attacker_string

    pkmn = get_side_from_state(mutator.state, side_string).active
    try:
        health_recovered = float(move[constants.HEAL][0] / move[constants.HEAL][1]) * pkmn.maxhp
    except KeyError:
        health_recovered = 0

    if health_recovered == 0:
        mutator.reverse(instruction.instructions)
        return [instruction]

    final_health = pkmn.hp + health_recovered
    if final_health > pkmn.maxhp:
        health_recovered -= (final_health - pkmn.maxhp)
    elif final_health < 0:
        health_recovered -= final_health

    heal_instruction = (
        constants.MUTATOR_HEAL,
        side_string,
        health_recovered
    )

    mutator.reverse(instruction.instructions)

    if health_recovered:
        instruction.add_instruction(heal_instruction)

    return [instruction]


def get_end_of_turn_instructions(mutator, instruction, bot_move, opponent_move, bot_moves_first):
    # determine which goes first
    if bot_moves_first:
        sides = [constants.USER, constants.OPPONENT]
    else:
        sides = [constants.OPPONENT, constants.USER]

    mutator.apply(instruction.instructions)

    # weather damage - sand and hail
    for attacker in sides:
        side = get_side_from_state(mutator.state, attacker)
        pkmn = side.active

        if pkmn.ability == 'magicguard' or not pkmn.hp:
            continue

        if mutator.state.weather == constants.SAND and not any(t in pkmn.types for t in ['steel', 'rock', 'ground']):
            sand_damage_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                max(0, int(min(pkmn.maxhp * 0.0625, pkmn.hp)))
            )
            mutator.apply_one(sand_damage_instruction)
            instruction.add_instruction(sand_damage_instruction)

        elif mutator.state.weather == constants.HAIL and 'ice' not in pkmn.types and pkmn.ability != 'icebody':
            ice_damage_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                max(0, int(min(pkmn.maxhp * 0.0625, pkmn.hp)))
            )
            mutator.apply_one(ice_damage_instruction)
            instruction.add_instruction(ice_damage_instruction)

    # futuresight
    for attacker in sides:
        side = get_side_from_state(mutator.state, attacker)
        if side.future_sight[0] == 1:
            from .damage_calculator import calculate_futuresight_damage
            damage_dealt = calculate_futuresight_damage(
                mutator.state,
                attacker,
                side.future_sight[1]
            )[0]
            if damage_dealt:
                futuresight_damage_instruction = (
                    constants.MUTATOR_DAMAGE,
                    opposite_side[attacker],
                    damage_dealt
                )
                mutator.apply_one(futuresight_damage_instruction)
                instruction.add_instruction(futuresight_damage_instruction)
        if side.future_sight[0] > 0:
            futuresight_decrement_instruction = (
                constants.MUTATOR_FUTURESIGHT_DECREMENT,
                attacker,
            )
            mutator.apply_one(futuresight_decrement_instruction)
            instruction.add_instruction(futuresight_decrement_instruction)

    # wish
    for attacker in sides:
        side = get_side_from_state(mutator.state, attacker)
        if side.wish[0] == 1 and 0 < side.active.hp < side.active.maxhp:
            wish_heal_instruction = (
                constants.MUTATOR_HEAL,
                attacker,
                min(side.wish[1], side.active.maxhp - side.active.hp)
            )
            mutator.apply_one(wish_heal_instruction)
            instruction.add_instruction(wish_heal_instruction)
        if side.wish[0] > 0:
            wish_decrement_instruction = (
                constants.MUTATOR_WISH_DECREMENT,
                attacker
            )
            mutator.apply_one(wish_decrement_instruction)
            instruction.add_instruction(wish_decrement_instruction)

    # item and ability - they can add one instruction each
    for attacker in sides:
        defender = opposite_side[attacker]
        side = get_side_from_state(mutator.state, attacker)
        defending_side = get_side_from_state(mutator.state, defender)
        pkmn = side.active
        defending_pkmn = defending_side.active

        item_instruction = item_end_of_turn(side.active.item, mutator.state, attacker, pkmn, defender, defending_pkmn)
        if item_instruction is not None:
            mutator.apply_one(item_instruction)
            instruction.add_instruction(item_instruction)

        ability_instruction = ability_end_of_turn(side.active.ability, mutator.state, attacker, pkmn, defender, defending_pkmn)
        if ability_instruction is not None:
            mutator.apply_one(ability_instruction)
            instruction.add_instruction(ability_instruction)

    # poison, toxic, and burn damage
    for attacker in sides:
        side = get_side_from_state(mutator.state, attacker)
        pkmn = side.active

        if pkmn.ability == 'magicguard' or not pkmn.hp:
            continue

        if constants.TOXIC == pkmn.status and pkmn.ability != 'poisonheal':
            toxic_count = side.side_conditions[constants.TOXIC_COUNT]
            toxic_multiplier = (1 / 16) * toxic_count + (1 / 16)
            toxic_damage = max(0, int(min(pkmn.maxhp * toxic_multiplier, pkmn.hp)))

            toxic_damage_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                toxic_damage
            )
            toxic_count_instruction = (
                constants.MUTATOR_SIDE_START,
                attacker,
                constants.TOXIC_COUNT,
                1
            )
            mutator.apply_one(toxic_damage_instruction)
            mutator.apply_one(toxic_count_instruction)

            instruction.add_instruction(toxic_damage_instruction)
            instruction.add_instruction(toxic_count_instruction)

        elif constants.BURN == pkmn.status:
            burn_damage_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                max(0, int(min(pkmn.maxhp * 0.0625, pkmn.hp)))
            )
            mutator.apply_one(burn_damage_instruction)
            instruction.add_instruction(burn_damage_instruction)

        elif constants.POISON == pkmn.status and pkmn.ability != 'poisonheal':
            poison_damage_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                max(0, int(min(pkmn.maxhp * 0.125, pkmn.hp)))
            )
            mutator.apply_one(poison_damage_instruction)
            instruction.add_instruction(poison_damage_instruction)

    # leechseed sap damage
    for attacker in sides:
        defender = opposite_side[attacker]
        side = get_side_from_state(mutator.state, attacker)
        defending_side = get_side_from_state(mutator.state, defender)
        pkmn = side.active
        defending_pkmn = defending_side.active

        if pkmn.ability == 'magicguard' or not pkmn.hp or not defending_pkmn.hp:
            continue

        if constants.LEECH_SEED in pkmn.volatile_status:
            # damage taken
            damage_sapped = max(0, int(min(pkmn.maxhp * 0.125, pkmn.hp)))
            sap_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                damage_sapped
            )

            # heal amount
            damage_from_full = defending_pkmn.maxhp - defending_pkmn.hp
            heal_instruction = (
                constants.MUTATOR_HEAL,
                defender,
                min(damage_sapped, damage_from_full)
            )

            mutator.apply_one(sap_instruction)
            mutator.apply_one(heal_instruction)
            instruction.add_instruction(sap_instruction)
            instruction.add_instruction(heal_instruction)

    # volatile-statuses
    for attacker in sides:
        side = get_side_from_state(mutator.state, attacker)
        pkmn = side.active

        if any(vs in constants.PROTECT_VOLATILE_STATUSES for vs in pkmn.volatile_status):
            if constants.PROTECT in pkmn.volatile_status:
                volatile_status_to_remove = constants.PROTECT
            elif constants.BANEFUL_BUNKER in pkmn.volatile_status:
                volatile_status_to_remove = constants.BANEFUL_BUNKER
            elif constants.SPIKY_SHIELD in pkmn.volatile_status:
                volatile_status_to_remove = constants.SPIKY_SHIELD
            elif constants.SILK_TRAP in pkmn.volatile_status:
                volatile_status_to_remove = constants.SILK_TRAP
            else:
                # should never happen
                raise Exception("Pokemon has volatile status that is not caught here: {}".format(pkmn.volatile_status))

            remove_protect_volatile_status_instruction = (
                constants.MUTATOR_REMOVE_VOLATILE_STATUS,
                attacker,
                volatile_status_to_remove
            )
            start_protect_side_condition_instruction = (
                    constants.MUTATOR_SIDE_START,
                    attacker,
                    constants.PROTECT,
                    1
            )
            mutator.apply_one(remove_protect_volatile_status_instruction)
            mutator.apply_one(start_protect_side_condition_instruction)
            instruction.add_instruction(remove_protect_volatile_status_instruction)
            instruction.add_instruction(start_protect_side_condition_instruction)

        elif side.side_conditions[constants.PROTECT]:
            end_protect_side_condition_instruction = (
                constants.MUTATOR_SIDE_END,
                attacker,
                constants.PROTECT,
                side.side_conditions[constants.PROTECT]
            )
            mutator.apply_one(end_protect_side_condition_instruction)
            instruction.add_instruction(end_protect_side_condition_instruction)

        if constants.ROOST in pkmn.volatile_status:
            remove_roost_instruction = (
                constants.MUTATOR_REMOVE_VOLATILE_STATUS,
                attacker,
                constants.ROOST,
            )
            mutator.apply_one(remove_roost_instruction)
            instruction.add_instruction(remove_roost_instruction)

        if constants.PARTIALLY_TRAPPED in pkmn.volatile_status:
            damage_taken = max(0, int(min(pkmn.maxhp * 0.125, pkmn.hp)))
            partially_trapped_damage_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                damage_taken
            )
            mutator.apply_one(partially_trapped_damage_instruction)
            instruction.add_instruction(partially_trapped_damage_instruction)

        if "saltcure" in pkmn.volatile_status:
            divisor = 4 if any(t in pkmn.types for t in ["water", "steel"]) else 8
            damage_taken = max(0, int(min(pkmn.maxhp * (1/divisor), pkmn.hp)))
            partially_trapped_damage_instruction = (
                constants.MUTATOR_DAMAGE,
                attacker,
                damage_taken
            )
            mutator.apply_one(partially_trapped_damage_instruction)
            instruction.add_instruction(partially_trapped_damage_instruction)

    # disable not used moves if choice-item is held
    for attacker in sides:
        side = get_side_from_state(mutator.state, attacker)
        pkmn = side.active

        if attacker == constants.USER:
            move = bot_move
            other_move = opponent_move
        else:
            move = opponent_move
            other_move = bot_move

        try:
            locking_move = move[constants.SELF][constants.VOLATILE_STATUS] == constants.LOCKED_MOVE
        except KeyError:
            locking_move = False

        if (
            constants.SWITCH_STRING not in move and
            constants.DRAG not in other_move.get(constants.FLAGS, {}) and
            move[constants.ID] not in constants.SWITCH_OUT_MOVES and
            (pkmn.item in constants.CHOICE_ITEMS or locking_move or pkmn.ability == 'gorillatactics')
        ):
            move_used = move[constants.ID]
            for m in filter(
                lambda x: x[constants.ID] != move_used and not x.get(constants.DISABLED, False),
                pkmn.moves
            ):
                disable_instruction = (
                    constants.MUTATOR_DISABLE_MOVE,
                    attacker,
                    m[constants.ID]
                )
                mutator.apply_one(disable_instruction)
                instruction.add_instruction(disable_instruction)

    mutator.reverse(instruction.instructions)

    return [instruction]


def get_instructions_from_drag(mutator, attacking_side_string, move_target, instruction):
    if instruction.frozen:
        return [instruction]

    new_instructions = []

    if move_target in same_side_strings:
        affected_side = get_side_from_state(mutator.state, attacking_side_string)
        affected_side_string = attacking_side_string
    elif move_target in opposing_side_strings:
        affected_side = get_side_from_state(mutator.state, opposite_side[attacking_side_string])
        affected_side_string = opposite_side[attacking_side_string]
    else:
        raise ValueError("Invalid value for move_target: {}".format(move_target))

    mutator.apply(instruction.instructions)
    alive_reserves = [s.id for s in affected_side.reserve.values() if s.hp > 0]
    num_reserve_alive = len(alive_reserves)
    mutator.reverse(instruction.instructions)
    if num_reserve_alive == 0:
        return [instruction]

    for pkmn_name in alive_reserves:
        new_instruction = get_instructions_from_switch(mutator, affected_side_string, pkmn_name, copy(instruction))
        new_instruction.update_percentage(1 / num_reserve_alive)
        new_instructions.append(new_instruction)

    return new_instructions


def get_instructions_from_boost_reset_moves(mutator, attacking_move, attacking_side_string, instruction):
    if instruction.frozen:
        return [instruction]

    attacking_side = get_side_from_state(mutator.state, attacking_side_string)
    defending_side_string = opposite_side[attacking_side_string]
    defending_side = get_side_from_state(mutator.state, defending_side_string)

    mutator.apply(instruction.instructions)
    new_instructions = []
    if attacking_move[constants.TARGET] in constants.MOVE_TARGET_SELF:
        new_instructions += remove_volatile_status_and_boosts_instructions(attacking_side, attacking_side_string)
    if attacking_move[constants.TARGET] in constants.MOVE_TARGET_OPPONENT:
        new_instructions += remove_volatile_status_and_boosts_instructions(defending_side, defending_side_string)
    mutator.reverse(instruction.instructions)

    for new_instruction in new_instructions:
        instruction.add_instruction(new_instruction)

    return [instruction]


def remove_volatile_status_and_boosts_instructions(side, side_string):
    instruction_additions = []
    for v_status in side.active.volatile_status:
        instruction_additions.append(
            (
                constants.MUTATOR_REMOVE_VOLATILE_STATUS,
                side_string,
                v_status
            )
        )
    if side.side_conditions[constants.TOXIC_COUNT]:
        instruction_additions.append(
            (
                constants.MUTATOR_SIDE_END,
                side_string,
                constants.TOXIC_COUNT,
                side.side_conditions[constants.TOXIC_COUNT]
            ))
    if side.active.attack_boost:
        instruction_additions.append(
            (
                constants.MUTATOR_UNBOOST,
                side_string,
                constants.ATTACK,
                side.active.attack_boost
            ))
    if side.active.defense_boost:
        instruction_additions.append(
            (
                constants.MUTATOR_UNBOOST,
                side_string,
                constants.DEFENSE,
                side.active.defense_boost
            ))
    if side.active.special_attack_boost:
        instruction_additions.append(
            (
                constants.MUTATOR_UNBOOST,
                side_string,
                constants.SPECIAL_ATTACK,
                side.active.special_attack_boost
            ))
    if side.active.special_defense_boost:
        instruction_additions.append(
            (
                constants.MUTATOR_UNBOOST,
                side_string,
                constants.SPECIAL_DEFENSE,
                side.active.special_defense_boost
            ))
    if side.active.speed_boost:
        instruction_additions.append(
            (
                constants.MUTATOR_UNBOOST,
                side_string,
                constants.SPEED,
                side.active.speed_boost
            ))

    return instruction_additions


def get_side_from_state(state, side_string):
    if side_string == constants.USER:
        return state.user
    elif side_string == constants.OPPONENT:
        return state.opponent
    else:
        raise ValueError("Invalid value for `side`")


def can_be_volatile_statused(side, volatile_status, first_move):
    if volatile_status in constants.PROTECT_VOLATILE_STATUSES:
        if side.side_conditions[constants.PROTECT]:
            return False
        elif first_move:
            return True
        else:
            return False
    if constants.SUBSTITUTE in side.active.volatile_status:
        return False
    if volatile_status == constants.SUBSTITUTE and side.active.hp < side.active.maxhp * 0.25:
        return False

    return True


def sleep_clause_activated(side, status):
    if status == constants.SLEEP:
        for p in side.reserve.values():
            if p.status == constants.SLEEP and p.hp > 0:
                return True
    return False


def immune_to_status(state, defending_pkmn, attacking_pkmn, status):
    # General status immunity
    if defending_pkmn.status is not None or defending_pkmn.hp <= 0:
        return True
    if constants.SUBSTITUTE in defending_pkmn.volatile_status and attacking_pkmn.ability != 'infiltrator':
        return True
    if defending_pkmn.ability == 'shieldsdown' and ((defending_pkmn.hp / defending_pkmn.maxhp) > 0.5):
        return True
    if defending_pkmn.ability == 'comatose':
        return True
    if state.field == constants.MISTY_TERRAIN and defending_pkmn.is_grounded():
        return True
    if defending_pkmn.ability == "purifyingsalt":
        return True
    if defending_pkmn.ability == "thermalexchange" and status == constants.BURN:
        return True

    # Specific status immunity
    return (
        status == constants.FROZEN and is_immune_to_freeze(state, defending_pkmn) or
        status == constants.BURN and is_immune_to_burn(defending_pkmn) or
        status == constants.SLEEP and is_immune_to_sleep(state, defending_pkmn) or
        status == constants.PARALYZED and is_immune_to_paralysis(defending_pkmn) or
        status in [constants.POISON, constants.TOXIC] and is_immune_to_poison(attacking_pkmn, defending_pkmn)
    )


def is_immune_to_freeze(state, pkmn):
    return (
        'ice' in pkmn.types or
        pkmn.ability in constants.IMMUNE_TO_FROZEN_ABILITIES or
        state.weather == constants.DESOLATE_LAND
    )


def is_immune_to_burn(pkmn):
    return (
        'fire' in pkmn.types or
        pkmn.ability in constants.IMMUNE_TO_BURN_ABILITIES
    )


def is_immune_to_sleep(state, pkmn):
    return (
        pkmn.ability in constants.IMMUNE_TO_SLEEP_ABILITIES or
        state.field == constants.ELECTRIC_TERRAIN and pkmn.is_grounded()
    )


def is_immune_to_poison(attacking, defending):
    return (
        any(t in ['poison', 'steel'] for t in defending.types) and not attacking.ability == 'corrosion'  or
        defending.ability in constants.IMMUNE_TO_POISON_ABILITIES
    )


def is_immune_to_paralysis(pkmn):
    return (
        'electric' in pkmn.types or
        pkmn.ability in constants.IMMUNE_TO_PARALYSIS_ABILITIES
    )
```
---
## 8. `src/Ankimon/functions/battle_functions.py`

### Why this file is critical
Processing battle results

### Full Contents

```python
import copy
import json
from ..poke_engine import constants
from ..pyobj.error_handler import show_warning_with_traceback
from ..move_names import format_move_name

def update_pokemon_battle_status(battle_info: dict, enemy_pokemon, main_pokemon):
    """
    Update Pokemon battle status and volatile status based on battle instructions.
    HP is now handled by the main battle loop to ensure a single source of truth.
    This function now only processes status changes.
    """
    if not isinstance(battle_info, dict) or 'instructions' not in battle_info:
        return False, False

    instructions = battle_info.get('instructions', [])
    if not instructions:
        return False, False

    enemy_status_changed = False
    main_status_changed = False

    try:
        # Initialize volatile_status sets if they don't exist
        if not hasattr(enemy_pokemon, 'volatile_status'):
            enemy_pokemon.volatile_status = set()
        if not hasattr(main_pokemon, 'volatile_status'):
            main_pokemon.volatile_status = set()

        for instr in instructions:
            # Skip malformed instructions or instructions this function doesn't handle
            if not isinstance(instr, (list, tuple)) or len(instr) < 2:
                continue

            action = instr[0]
            target = instr[1]

            # This function only handles status, not damage or heal
            if action in [constants.MUTATOR_DAMAGE, constants.MUTATOR_HEAL]:
                continue

            status_value = instr[2] if len(instr) >= 3 else None

            # Handle regular status application
            if action == constants.MUTATOR_APPLY_STATUS and status_value:
                if target == 'opponent':
                    if enemy_pokemon.battle_status != status_value:
                        enemy_pokemon.battle_status = status_value
                        enemy_status_changed = True
                elif target == 'user':
                    if main_pokemon.battle_status != status_value:
                        main_pokemon.battle_status = status_value
                        main_status_changed = True

            # Handle regular status removal
            elif action == constants.MUTATOR_REMOVE_STATUS:
                if target == 'opponent':
                    if enemy_pokemon.battle_status != 'fighting':
                        enemy_pokemon.battle_status = 'fighting'
                        enemy_status_changed = True
                elif target == 'user':
                    if main_pokemon.battle_status != 'fighting':
                        main_pokemon.battle_status = 'fighting'
                        main_status_changed = True

            # Handle volatile status application
            elif action == constants.MUTATOR_APPLY_VOLATILE_STATUS and status_value:
                if target == 'opponent':
                    if status_value not in enemy_pokemon.volatile_status:
                        enemy_pokemon.volatile_status.add(status_value)
                        enemy_status_changed = True
                elif target == 'user':
                    if status_value not in main_pokemon.volatile_status:
                        main_pokemon.volatile_status.add(status_value)
                        main_status_changed = True

            # Handle volatile status removal
            elif action == constants.MUTATOR_REMOVE_VOLATILE_STATUS and status_value:
                if target == 'opponent':
                    if status_value in enemy_pokemon.volatile_status:
                        enemy_pokemon.volatile_status.discard(status_value)
                        enemy_status_changed = True
                elif target == 'user':
                    if status_value in main_pokemon.volatile_status:
                        main_pokemon.volatile_status.discard(status_value)
                        main_status_changed = True

        # Final check for fainted status based on the already-updated HP from the main loop
        if hasattr(enemy_pokemon, 'hp') and enemy_pokemon.hp <= 0:
            if enemy_pokemon.battle_status != 'fainted':
                enemy_pokemon.battle_status = 'fainted'
                enemy_pokemon.volatile_status = set() # Clear volatiles on faint
                enemy_status_changed = True

        if hasattr(main_pokemon, 'hp') and main_pokemon.hp <= 0:
            if main_pokemon.battle_status != 'fainted':
                main_pokemon.battle_status = 'fainted'
                main_pokemon.volatile_status = set() # Clear volatiles on faint
                main_status_changed = True

        return enemy_status_changed, main_status_changed

    except Exception as e:
        # Use the existing error handler if available, otherwise print
        try:
            from ..pyobj.error_handler import show_warning_with_traceback
            show_warning_with_traceback(e, "Failed to update pokemon battle status")
        except ImportError:
            print(f"ERROR in update_pokemon_battle_status: {e}")
        return False, False


def _process_battle_effects(
    instructions: list,  # Keep for compatibility but won't use
    translator,
    main_pokemon=None,
    enemy_pokemon=None,
    current_state=None,
    changes=None
) -> list:
    """
    Process battle changes with Pokemon names and persistent effect messages.
    This version uses the changes variable instead of instructions to generate messages.
    """
    if not changes or not isinstance(changes, list):
        return []

    effect_messages = []

    def get_pokemon_name(target_side: str) -> str:
        if target_side == 'user':
            return main_pokemon.name.capitalize() if (main_pokemon and hasattr(main_pokemon, 'name')) else "Your Pokemon"
        else:
            return enemy_pokemon.name.capitalize() if (enemy_pokemon and hasattr(enemy_pokemon, 'name')) else "Enemy Pokemon"

    def normalize_status_name(status_name: str) -> str:
        return status_name.lower().replace('_', '').replace(' ', '').replace('-', '')

    def safe_translate(key: str, **kwargs) -> str:
        try:
            if translator:
                result = translator.translate(key, **kwargs)
                if result and result.strip():
                    return result
        except (KeyError, AttributeError, Exception) as e:
            print(f"Translation error for key '{key}': {e}")

        if 'pokemon_name' in kwargs and 'status_name' in kwargs:
            if 'apply' in key or 'still' in key:
                return f"{kwargs['pokemon_name']} is affected by {kwargs['status_name']}!"
            elif 'remove' in key:
                return f"{kwargs['pokemon_name']} recovers from {kwargs['status_name']}!"
        return f"Battle effect: {key}"

    # Track newly applied statuses/volatiles from changes
    newly_applied_statuses = set()
    newly_applied_volatiles = set()

    # First pass: identify newly applied effects
    for change in changes:
        key = change['key']
        before = change['before']
        after = change['after']

        # Track status applications
        if key.endswith('.status') and before in ('fighting', None) and after not in ('fighting', None):
            target = 'user' if key.startswith('user.') else 'opponent'
            newly_applied_statuses.add((target, normalize_status_name(after)))

        # Track volatile status applications
        elif key.endswith('.volatile_status') and isinstance(after, set) and isinstance(before, set):
            target = 'user' if key.startswith('user.') else 'opponent'
            new_volatiles = after - before if before else after
            for volatile in new_volatiles:
                newly_applied_volatiles.add((target, normalize_status_name(volatile)))

    def check_persistent_effects():
        """Check for ongoing effects, but skip fainted Pokemon and newly applied statuses."""
        persistent_messages = []

        # Weather (unaffected by fainted status)
        if current_state and hasattr(current_state, 'weather') and current_state.weather:
            weather_key = f"weather_{normalize_status_name(current_state.weather)}_still"
            weather_message = safe_translate(
                weather_key,
                weather=current_state.weather.replace('-', ' ').title()
            )
            persistent_messages.append(weather_message)

        # Status for main Pokemon
        if main_pokemon and getattr(main_pokemon, 'battle_status', None) not in ('fighting', 'fainted', None):
            norm_status = normalize_status_name(main_pokemon.battle_status)
            if ('user', norm_status) not in newly_applied_statuses:
                status_key = f"status_{norm_status}_still"
                status_message = safe_translate(
                    status_key,
                    pokemon_name=main_pokemon.name.capitalize(),
                    status_name=main_pokemon.battle_status.replace('_', ' ').title()
                )
                persistent_messages.append(status_message)

        # Status for enemy Pokemon
        if enemy_pokemon and getattr(enemy_pokemon, 'battle_status', None) not in ('fighting', 'fainted', None):
            norm_status = normalize_status_name(enemy_pokemon.battle_status)
            if ('opponent', norm_status) not in newly_applied_statuses:
                status_key = f"status_{norm_status}_still"
                status_message = safe_translate(
                    status_key,
                    pokemon_name=enemy_pokemon.name.capitalize(),
                    status_name=enemy_pokemon.battle_status.replace('_', ' ').title()
                )
                persistent_messages.append(status_message)

        # Volatile for main Pokemon
        if main_pokemon and getattr(main_pokemon, 'battle_status', None) != 'fainted' and hasattr(main_pokemon, 'volatile_status') and main_pokemon.volatile_status:
            for volatile_status in main_pokemon.volatile_status:
                norm_volatile = normalize_status_name(volatile_status)
                if ('user', norm_volatile) not in newly_applied_volatiles:
                    volatile_key = f"volatile_{norm_volatile}_still"
                    volatile_message = safe_translate(
                        volatile_key,
                        pokemon_name=main_pokemon.name.capitalize(),
                        status_name=volatile_status.replace('_', ' ').title()
                    )
                    persistent_messages.append(volatile_message)

        # Volatile for enemy Pokemon
        if enemy_pokemon and getattr(enemy_pokemon, 'battle_status', None) != 'fainted' and hasattr(enemy_pokemon, 'volatile_status') and enemy_pokemon.volatile_status:
            for volatile_status in enemy_pokemon.volatile_status:
                norm_volatile = normalize_status_name(volatile_status)
                if ('opponent', norm_volatile) not in newly_applied_volatiles:
                    volatile_key = f"volatile_{norm_volatile}_still"
                    volatile_message = safe_translate(
                        volatile_key,
                        pokemon_name=enemy_pokemon.name.capitalize(),
                        status_name=volatile_status.replace('_', ' ').title()
                    )
                    persistent_messages.append(volatile_message)

        return persistent_messages

    # Add persistent effect messages first
    if current_state and (main_pokemon or enemy_pokemon):
        effect_messages.extend(check_persistent_effects())

    # Process changes to generate messages
    for change in changes:
        try:
            key = change['key']
            before = change['before']
            after = change['after']

            # Skip if no actual change
            if before == after:
                continue

            # Handle status changes
            if key.endswith('.status'):
                target = 'user' if key.startswith('user.') else 'opponent'
                pokemon_name = get_pokemon_name(target)

                # Status applied
                if before in ('fighting', None) and after not in ('fighting', None):
                    normalized_status = normalize_status_name(after)
                    translation_key = f"status_{normalized_status}_apply"

                    if after.lower() in constants.KNOWN_REGULAR_STATUSES:
                        message = safe_translate(
                            translation_key,
                            pokemon_name=pokemon_name,
                            status_name=after.replace('_', ' ').title()
                        )
                    else:
                        status_name = after.replace('_', ' ').title()
                        message = safe_translate(
                            "status_unknown_apply",
                            pokemon_name=pokemon_name,
                            status_name=status_name
                        )
                    effect_messages.append(message)

                # Status removed
                elif before not in ('fighting', None) and after in ('fighting', None):
                    normalized_status = normalize_status_name(before)
                    translation_key = f"status_{normalized_status}_remove"

                    if before.lower() in constants.KNOWN_REGULAR_STATUSES:
                        message = safe_translate(
                            translation_key,
                            pokemon_name=pokemon_name,
                            status_name=before.replace('_', ' ').title()
                        )
                    else:
                        status_name = before.replace('_', ' ').title()
                        message = safe_translate(
                            "status_unknown_remove",
                            pokemon_name=pokemon_name,
                            status_name=status_name
                        )
                    effect_messages.append(message)

            # Handle volatile status changes
            elif key.endswith('.volatile_status'):
                target = 'user' if key.startswith('user.') else 'opponent'
                pokemon_name = get_pokemon_name(target)

                if isinstance(after, set) and isinstance(before, set):
                    # New volatile statuses added
                    added_volatiles = after - before if before else after
                    for volatile_status in added_volatiles:
                        normalized_status = normalize_status_name(volatile_status)
                        translation_key = f"volatile_{normalized_status}_apply"

                        if normalized_status in constants.KNOWN_VOLATILE_STATUSES:
                            message = safe_translate(
                                translation_key,
                                pokemon_name=pokemon_name,
                                status_name=volatile_status.replace('_', ' ').title()
                            )
                        else:
                            status_name = volatile_status.replace('_', ' ').title()
                            message = safe_translate(
                                "volatile_status_unknown_apply",
                                pokemon_name=pokemon_name,
                                status_name=status_name
                            )
                        effect_messages.append(message)

                    # Volatile statuses removed
                    removed_volatiles = before - after if before else set()
                    for volatile_status in removed_volatiles:
                        normalized_status = normalize_status_name(volatile_status)
                        translation_key = f"volatile_{normalized_status}_remove"

                        if normalized_status in constants.KNOWN_VOLATILE_STATUSES:
                            message = safe_translate(
                                translation_key,
                                pokemon_name=pokemon_name,
                                status_name=volatile_status.replace('_', ' ').title()
                            )
                        else:
                            status_name = volatile_status.replace('_', ' ').title()
                            message = safe_translate(
                                "volatile_status_unknown_remove",
                                pokemon_name=pokemon_name,
                                status_name=status_name
                            )
                        effect_messages.append(message)

            # Handle HP changes (healing detection)
            elif key.endswith('.hp'):
                target = 'user' if key.startswith('user.') else 'opponent'
                pokemon_name = get_pokemon_name(target)

                # Only show healing messages (HP increased)
                if isinstance(before, (int, float)) and isinstance(after, (int, float)) and after > before:
                    heal_amount = after - before
                    message = safe_translate(
                        "effect_health_restored",
                        pokemon_name=pokemon_name,
                        heal_amount=heal_amount
                    )
                    effect_messages.append(message)

            # Handle stat boost changes
            elif any(key.endswith(f'.{stat}_boost') for stat in ['attack', 'defense', 'special_attack', 'special_defense', 'speed', 'accuracy', 'evasion']):
                target = 'user' if key.startswith('user.') else 'opponent'
                pokemon_name = get_pokemon_name(target)

                # Extract stat name from key
                stat_part = key.split('.')[-1].replace('_boost', '')
                stat_names = {
                    'attack': "Attack", 'defense': "Defense",
                    'special_attack': "Special Attack", 'special_defense': "Special Defense",
                    'speed': "Speed", 'accuracy': "Accuracy", 'evasion': "Evasion"
                }
                stat_name = stat_names.get(stat_part, stat_part.replace('_', ' ').title())

                if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                    change_amount = after - before
                    if change_amount != 0:
                        direction = "increased" if change_amount > 0 else "decreased"
                        message = safe_translate(
                            "effect_stat_change",
                            pokemon_name=pokemon_name,
                            stat=stat_name,
                            direction=direction,
                            amount=abs(change_amount)
                        )
                        effect_messages.append(message)

            # Handle weather changes
            elif key == 'weather':
                if before != after:
                    # Weather started
                    if before is None and after is not None:
                        weather_name = after.replace('-', ' ').title()
                        message = safe_translate("battle_effect_weather_start", weather=weather_name)
                        effect_messages.append(message)
                    # Weather ended
                    elif before is not None and after is None:
                        weather_name = before.replace('-', ' ').title()
                        message = safe_translate("battle_effect_weather_end", weather=weather_name)
                        effect_messages.append(message)
                    # Weather changed
                    elif before is not None and after is not None:
                        old_weather = before.replace('-', ' ').title()
                        new_weather = after.replace('-', ' ').title()
                        message = safe_translate("battle_effect_weather_end", weather=old_weather)
                        effect_messages.append(message)
                        message = safe_translate("battle_effect_weather_start", weather=new_weather)
                        effect_messages.append(message)

            # Handle side condition changes
            elif '.side_conditions.' in key:
                target = 'user' if key.startswith('user.') else 'opponent'
                condition_name = key.split('.')[-1]

                # Side condition started or increased
                if isinstance(before, (int, float)) and isinstance(after, (int, float)) and after > before:
                    message = safe_translate(
                        "battle_effect_side_condition",
                        condition=condition_name.replace('_', ' ').title(),
                        side="your team" if target == 'user' else "the opposing team"
                    )
                    effect_messages.append(message)

            # Handle wish changes
            elif key.endswith('.wish'):
                target = 'user' if key.startswith('user.') else 'opponent'
                pokemon_name = get_pokemon_name(target)

                # Wish started (assuming tuple format: (turns, heal_amount))
                if isinstance(after, tuple) and len(after) >= 2 and after[1] > 0:
                    if not isinstance(before, tuple) or before[1] == 0:
                        heal_amount = after[1]
                        message = safe_translate(
                            "wish_started",
                            pokemon_name=pokemon_name,
                            heal_amount=heal_amount
                        )
                        effect_messages.append(message)

            # Handle future sight changes
            elif key.endswith('.future_sight'):
                target = 'user' if key.startswith('user.') else 'opponent'

                # Future sight started
                if isinstance(after, tuple) and len(after) >= 2 and after[0] > 0:
                    if not isinstance(before, tuple) or before[0] == 0:
                        user_pokemon_name = get_pokemon_name(target)
                        message = safe_translate(
                            "futuresight_start",
                            pokemon_name=user_pokemon_name,
                            target_pokemon="the opposing Pokemon"
                        )
                        effect_messages.append(message)

                # Future sight decremented (still active)
                elif isinstance(before, tuple) and isinstance(after, tuple) and len(before) >= 1 and len(after) >= 1:
                    if before[0] > after[0] and after[0] > 0:
                        message = safe_translate(
                            "futuresight_still_active",
                            side="your team" if target == 'user' else "the opposing team"
                        )
                        effect_messages.append(message)

                # Future sight ended (hit)
                elif isinstance(before, tuple) and isinstance(after, tuple) and len(before) >= 1 and len(after) >= 1:
                    if before[0] > 0 and after[0] == 0:
                        message = safe_translate(
                            "futuresight_hits",
                            side="your team" if target == 'user' else "the opposing team"
                        )
                        effect_messages.append(message)

        except Exception as e:
            print(f"Error processing state change {change}: {e}")
            effect_messages.append(f"Battle effect occurred (processing error)")
            continue

    return effect_messages

def validate_pokemon_status(pokemon):
    """
    Ensure Pokemon has valid battle_status and volatile_status.
    """

    # Valid status codes from const.py
    valid_statuses = {
        "brn", "frz", "par", "psn", "tox", "slp",
        "confusion", "flinching", "fainted", "fighting"
    }

    current_status = getattr(pokemon, 'battle_status', 'fighting')

    # Ensure volatile_status exists
    if not hasattr(pokemon, 'volatile_status'):
        pokemon.volatile_status = set()

    # If status is not valid, default to fighting (or fainted if HP <= 0)
    if current_status not in valid_statuses:
        if hasattr(pokemon, 'hp') and pokemon.hp <= 0:
            return 'fainted'
        else:
            return 'fighting'

    # If Pokemon is fainted but status isn't fainted, override
    if hasattr(pokemon, 'hp') and pokemon.hp <= 0 and current_status != 'fainted':
        return 'fainted'

    return current_status


def process_battle_data(
    battle_info: dict,
    multiplier: float,
    main_pokemon,
    enemy_pokemon,
    user_attack: str,
    enemy_attack: str,
    dmg_from_user_move: int,
    dmg_from_enemy_move: int,
    user_hp_after: int,
    opponent_hp_after: int,
    battle_status: str,
    pokemon_encounter: int,
    translator,
    changes
) -> str:
    """
    Generate complete battle message from battle data.

    This function centralizes all battle message generation and now uses
    format_move_name to display official move names.
    """

    if not isinstance(battle_info, dict):
        return translator.translate("invalid_battle_data_error")

    # Initialize message components
    message_parts = []

    try:
        # 1. Multiplier display
        formatted_multiplier = f"{multiplier:.1f}"
        message_parts.append(
            translator.translate("battle_multiplier_display", multiplier=formatted_multiplier)
        )

        # 2. Enemy attack section
        if enemy_attack is not "splash" or None:

            # --- NEW: Format enemy move name ---
            formatted_enemy_attack = format_move_name(enemy_attack)

            enemy_attack_msg = translator.translate(
                "enemy_attack_announcement",
                pokemon_name=enemy_pokemon.name.capitalize(),
                attack_name=formatted_enemy_attack  # Use the formatted name
            )
            message_parts.append(enemy_attack_msg)

        # 3. User attack section
        if user_attack is not "splash" or None:

            # Handle special battle statuses first
            if battle_status and battle_status != "fighting":
                status_msg = _handle_special_battle_status(
                    main_pokemon, battle_status, translator
                )
                if status_msg:
                    message_parts.append(status_msg)
            else:
                # --- NEW: Format user move name ---
                formatted_user_attack = format_move_name(user_attack)

                # Normal attack resolution
                user_attack_msg = translator.translate(
                    "player_attack_announcement",
                    pokemon_name=main_pokemon.name.capitalize(),
                    attack_name=formatted_user_attack  # Use the formatted name
                )
                message_parts.append(user_attack_msg)

        # 4. Process all other battle effect instructions
        if isinstance(battle_info, dict) and 'instructions' in battle_info:
            try:
                effects_messages = _process_battle_effects(
                    battle_info['instructions'],
                    translator,
                    main_pokemon=main_pokemon,
                    enemy_pokemon=enemy_pokemon,
                    current_state=battle_info.get('state'),
                    changes=changes
                )
                if effects_messages:
                    message_parts.extend(effects_messages)
            except Exception as e:
                message_parts.append(
                    translator.translate("battle_effects_error", error=str(e)[:50])
                )

        # Join all message parts with newlines
        final_message = "\n".join(filter(None, message_parts))

        if not final_message:
            return translator.translate("battle_message_empty_fallback")

        return final_message

    except Exception as e:
        show_warning_with_traceback(
            exception=e,
            message="Critical error generating battle message"
        )
        error_msg = translator.translate("battle_processing_error", error=str(e)[:100])
        return f"{translator.translate('battle_multiplier_display', multiplier=multiplier)}\n{error_msg}"


def _handle_special_battle_status(main_pokemon, battle_status: str, translator) -> str:
    """Handle special battle status conditions using the provided constants."""

    try:

        status_messages = {
            constants.SLEEP: "pokemon_is_sleeping",
            constants.PARALYZED: "pokemon_is_paralyzed",
            constants.FROZEN: "pokemon_is_frozen",
            constants.BURN: "pokemon_is_burned",
            constants.POISON: "pokemon_is_poisoned",
            constants.TOXIC: "pokemon_is_badly_poisoned",
            constants.CONFUSION: "pokemon_is_confused",
            constants.FLINCH: "pokemon_flinched",
            constants.TAUNT: "pokemon_is_taunted"
        }

        # Check if we have a predefined message for this status
        if battle_status in status_messages:
            return translator.translate(
                status_messages[battle_status],
                pokemon_name=main_pokemon.name.capitalize()
            )
        else:
            # Generic status message for unknown conditions
            return translator.translate(
                "pokemon_special_condition",
                pokemon_name=main_pokemon.name.capitalize(),
                condition=battle_status.replace('_', ' ').title()
            )

    except Exception as e:
        # Non‐fatal: return generic message
        show_warning_with_traceback(
            exception=e,
            message="Error handling special battle status"
        )
        return translator.translate(
            "pokemon_special_condition",
            pokemon_name=main_pokemon.name.capitalize(),
            condition=battle_status.replace('_', ' ').title()
        )

def calculate_hp(base_stat_hp, level, ev, iv):
    ev_value = ev["hp"] / 4
    iv_value = iv["hp"]
    #hp = int(((iv + 2 * (base_stat_hp + ev) + 100) * level) / 100 + 10)
    hp = int((((((base_stat_hp + iv_value) * 2 ) + ev_value) * level) / 100) + level + 10)
    return hp
```
---
## 9. `src/Ankimon/resources.py`

### Why this file is critical
File paths and constants

### Full Contents

```python
from pathlib import Path
import os
import json

addon_dir = Path(__file__).parents[0]

#safe route for updates
user_path = addon_dir / "user_files"
user_path_data = addon_dir / "user_files" / "data_files"
user_path_sprites = addon_dir / "user_files" / "sprites"
user_path_credentials = addon_dir / "user_files" / "data.json"
manifest_path = addon_dir / "manifest.json"

font_path = addon_dir / "addon_files"

# Assign Pokemon Image folder directory name
pkmnimgfolder = addon_dir / "user_files" / "sprites"
backdefault = addon_dir / "user_files" / "sprites" / "back_default"
frontdefault = addon_dir / "user_files" / "sprites" / "front_default"
#Assign saved Pokemon Directory
mypokemon_path = addon_dir / "user_files" / "mypokemon.json"
mainpokemon_path = addon_dir / "user_files" / "mainpokemon.json"
pokemon_history_path = addon_dir / "user_files" / "pokemon_history.json"
battlescene_path = addon_dir / "addon_sprites" / "battle_scenes"
trainer_sprites_path = addon_dir / "addon_sprites" / "trainers"
battlescene_path_without_dialog = addon_dir / "addon_sprites" / "battle_scenes_without_dialog"
battle_ui_path = addon_dir / "pkmnbattlescene - UI_transp"
type_style_file = addon_dir / "addon_files" / "types.json"
next_lvl_file_path = addon_dir / "addon_files" / "ExpPokemonAddon.csv"
berries_path = addon_dir / "user_files" / "sprites" / "berries"
background_dialog_image_path  = addon_dir / "background_dialog_image.png"
pokedex_image_path = addon_dir / "addon_sprites" / "pokedex_template.jpg"
evolve_image_path = addon_dir / "addon_sprites" / "evo_temp.jpg"
learnset_path = addon_dir / "user_files" / "data_files" / "learnsets.json"
pokedex_path = addon_dir / "user_files" / "data_files" / "pokedex.json"
pokemon_names_file_path = addon_dir / "user_files" / "data_files" / "pokemon_names.json"
moves_file_path = addon_dir / "user_files" / "data_files" / "moves.json"
move_names_file_path = addon_dir / "user_files" / "data_files" / "move_names.json"
items_path = addon_dir / "user_files" / "sprites" / "items"
badges_path = addon_dir / "user_files" / "sprites" / "badges"
itembag_path = addon_dir / "user_files" / "items.json"
badgebag_path = addon_dir / "user_files" / "badges.json"
pokenames_lang_path = addon_dir / "user_files" / "data_files" / "pokemon_species_names.csv"
pokedesc_lang_path = addon_dir / "user_files" / "data_files" / "pokemon_species_flavor_text.csv"
poke_evo_path = addon_dir / "user_files" / "data_files" / "pokemon_evolution.csv"
poke_species_path = addon_dir / "user_files" / "data_files" / "pokemon_species.csv"
pokeapi_db_path = user_path_data / "pokeapi_db.json"
starters_path = addon_dir / "addon_files" / "starters.json"
eff_chart_html_path = addon_dir / "addon_files" / "eff_chart_html.html"
effectiveness_chart_file_path = addon_dir / "addon_files" / "eff_chart.json"
table_gen_id_html_path = addon_dir / "addon_files" / "table_gen_id.html"
icon_path = addon_dir / "addon_files" / "pokeball.png"
sound_list_path = addon_dir / "addon_files" / "sound_list.json"
badges_list_path = addon_dir / "addon_files" / "badges.json"
items_list_path = addon_dir / "addon_files" / "items.json"
rate_path = addon_dir / "user_files" / "rate_this.json"
csv_file_items = addon_dir / "user_files" / "data_files" / "item_names.csv"
csv_file_descriptions = addon_dir / "user_files" / "data_files" / "item_flavor_text.csv"
csv_file_items_cost = addon_dir / "user_files" / "data_files" / "items.csv"
pokemon_csv = addon_dir / "user_files" / "data_files" / "pokemon.csv"
pokemon_tm_learnset_path = addon_dir / "user_files" / "data_files" / "pokemon_tm_learnset.json"

#effect sounds paths
hurt_normal_sound_path = addon_dir / "addon_sprites" / "sounds" / "HurtNormal.mp3"
hurt_noteff_sound_path = addon_dir / "addon_sprites" / "sounds" / "HurtNotEffective.mp3"
hurt_supereff_sound_path = addon_dir / "addon_sprites" / "sounds" / "HurtSuper.mp3"
ownhplow_sound_path = addon_dir / "addon_sprites" / "sounds" / "OwnHpLow.mp3"
hpheal_sound_path = addon_dir / "addon_sprites" / "sounds" / "HpHeal.mp3"
fainted_sound_path = addon_dir / "addon_sprites" / "sounds" / "Fainted.mp3"

#pokemon species id files
pokemon_species_normal_path = addon_dir / "user_files" / "pkmn_data" / "normal.json"
pokemon_species_legendary_path = addon_dir / "user_files" / "pkmn_data" / "legendary.json"
pokemon_species_ultra_path = addon_dir / "user_files" / "pkmn_data" / "ultra.json"
pokemon_species_mythical_path = addon_dir / "user_files" / "pkmn_data" / "mythical.json"
pokemon_species_baby_path = addon_dir / "user_files" / "pkmn_data" / "baby.json"

#utils
json_file_structure = addon_dir / "addon_files" / "folder_structure.json"

#move ui paths
type_icon_path_resources = addon_dir / "addon_sprites" / "Types"

team_pokemon_path = addon_dir / "user_files" / "team.json"

#lang routes
lang_path = addon_dir / "lang"
lang_path_de = addon_dir / "lang" / "de_text.json"
lang_path_ch = addon_dir / "lang" / "ch_text.json"
lang_path_en = addon_dir / "lang" / "en_text.json"
lang_path_fr = addon_dir / "lang" / "fr_text.json"
lang_path_jp = addon_dir / "lang" / "jp_text.json"
lang_path_sp = addon_dir / "lang" / "sp_text.json"
lang_path_it = addon_dir / "lang" / "it_text.json"
lang_path_cz = addon_dir / "lang" / "cz_text.json"
lang_path_po = addon_dir / "lang" / "po_text.json"
lang_path_kr = addon_dir / "lang" / "kr_text.json"
lang_path_es_latam = addon_dir / "lang" / "es_latam_text.json"

#backup_routes
backup_root = addon_dir / "user_files" / "backups"
backup_folder_1 = backup_root / "backup_1"
backup_folder_2 = backup_root / "backup_2"
backup_folders = [os.path.join(backup_root, f"backup_{i}") for i in range(1, 4)]

#detect add-on version
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    addon_ver = manifest.get("version", "unknown")
except Exception:
    addon_ver = "unknown"

#note if it is an experimental build
IS_EXPERIMENTAL_BUILD = addon_ver.endswith("-E")


POKEMON_TIERS = {
  "Normal": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65,
66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 143, 147, 148, 149, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170, 171, 176, 177, 178, 179, 180, 181, 182, 183, 184, 185, 186,
187, 188, 189, 190, 191, 192, 193, 194, 195, 196, 197, 198, 199, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209, 210, 211, 212, 213, 214, 215, 216, 217, 218, 219, 220, 221, 222, 223, 224, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 235, 237, 241, 242, 246, 247, 248, 261, 262, 263, 264, 265, 266, 267, 268, 269, 270, 271, 272, 273, 274, 275, 276, 277, 278, 279, 280,
281, 282, 283, 284, 285, 286, 287, 288, 289, 290, 291, 292, 293, 294, 295, 296, 297, 299, 300, 301, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 312, 313, 314, 315, 316, 317, 318, 319, 320, 321, 322, 323, 324, 325, 326, 327, 328, 329, 330, 331, 332, 333, 334, 335, 336, 337, 338, 339, 340, 341, 342, 343, 344, 349, 350, 351, 352, 353, 354, 355, 356, 357, 358, 359, 361, 362, 363, 364, 365, 366, 367,
368, 369, 370, 371, 372, 373, 374, 375, 376, 396, 397, 398, 399, 400, 401, 402, 403, 404, 405, 407, 412, 413, 414, 415, 416, 417, 418, 419, 420, 421, 422, 423, 424, 425, 426, 427, 428, 429, 430, 431, 432, 434, 435, 436, 437, 441, 442, 443, 444, 445, 448, 449, 450, 451, 452, 453, 454, 455, 456, 457, 459, 460, 461, 462, 463, 464, 465, 466, 467, 468, 469, 470, 471, 472, 473,
474, 475, 476, 477, 478, 479, 504, 505, 506, 507, 508, 509, 510, 511, 512, 513, 514, 515, 516, 517, 518, 519, 520, 521, 522, 523, 524, 525, 526, 527, 528, 529, 530, 531, 532, 533, 534, 535, 536, 537, 538, 539, 540, 541, 542, 543, 544, 545, 546, 547, 548, 549, 550, 551, 552, 553, 554, 555, 556, 557, 558, 559, 560, 561, 562, 563, 568, 569, 570, 571, 572, 573, 574, 575, 576,
577, 578, 579, 580, 581, 582, 583, 584, 585, 586, 587, 588, 589, 590, 591, 592, 593, 594, 595, 596, 597, 598, 599, 600, 601, 602, 603, 604, 605, 606, 607, 608, 609, 610, 611, 612, 613, 614, 615, 616, 617, 618, 619, 620, 621, 622, 623, 624, 625, 626, 627, 628, 629, 630, 631, 632, 633, 634, 635, 636, 637, 659, 660, 661, 662, 663, 664, 665, 666, 667, 668, 669, 670, 671, 672,
673, 674, 675, 676, 677, 678, 679, 680, 681, 682, 683, 684, 685, 686, 687, 688, 689, 690, 691, 692, 693, 694, 695, 700, 701, 702, 703, 704, 705, 706, 707, 708, 709, 710, 711, 712, 713, 714, 715, 731, 732, 733, 734, 735, 736, 737, 738, 739, 740, 741, 742, 743, 744, 745, 746, 747, 748, 749, 750, 751, 752, 753, 754, 755, 756, 757, 758, 759, 760, 761, 762, 763, 764, 765, 766,
767, 768, 769, 770, 771, 774, 775, 776, 777, 778, 779, 780, 781, 782, 783, 784, 819, 820, 821, 822, 823, 824, 825, 826, 827, 828, 829, 830, 831, 832, 833, 834, 835, 836, 837, 838, 839, 840, 841, 842, 843, 844, 845, 846, 847, 849, 850, 851, 852, 853, 854, 855, 856, 857, 858, 859, 860, 861, 862, 863, 864, 865, 866, 867, 868, 869, 870, 871, 872, 873, 874, 875, 876, 877, 878,
879, 884, 885, 886, 887],
  "Legendary": [
  # Gen 1
  144, 145, 146, 150,
  # Gen 2
  243, 244, 245, 249, 250,
  # Gen 3
  377, 378, 379, 380, 381, 382, 383, 384,
  # Gen 4
  480, 481, 482, 483, 484, 485, 486, 487, 488,
  # Gen 5
  638, 639, 640, 641, 642, 643, 644, 645, 646,
  # Gen 6
  716, 717, 718,
  # Gen 7
  772, 773, 785, 786, 787, 788, 789, 790, 791, 792, 800,
  # Gen 8
  888, 889, 890, 891, 892, 894, 895, 896, 897, 898
]
,
  "Mythical": [
  # Gen 1
  151,        # Mew
  # Gen 2
  251,        # Celebi
  # Gen 3
  385, 386,   # Jirachi, Deoxys
  # Gen 4
  489, 490, 491, 492, 493,   # Phione, Manaphy, Darkrai, Shaymin, Arceus
  # Gen 5
  494, 647, 648, 649,        # Victini, Keldeo, Meloetta, Genesect
  # Gen 6
  719, 720, 721,             # Diancie, Hoopa, Volcanion
  # Gen 7
  801, 802, 807, 808, 809,   # Magearna, Marshadow, Zeraora, Meltan, Melmetal
  # Gen 8
  893                        # Zarude
]
,
  "Ultra": [
  793,  # Nihilego
  794,  # Buzzwole
  795,  # Pheromosa
  796,  # Xurkitree
  797,  # Celesteela
  798,  # Kartana
  799,  # Guzzlord
  803,  # Poipole
  804,  # Naganadel
  805,  # Stakataka
  806   # Blacephalon
]
,
  "Fossil": [
  # Gen 1
  138, 139, 140, 141, 142,        # Omanyte, Omastar, Kabuto, Kabutops, Aerodactyl
  # Gen 3
  345, 346, 347, 348,             # Lileep, Cradily, Anorith, Armaldo
  # Gen 4
  408, 409, 410, 411,             # Cranidos, Rampardos, Shieldon, Bastiodon
  # Gen 5
  564, 565, 566, 567,             # Tirtouga, Carracosta, Archen, Archeops
  # Gen 6
  696, 697, 698, 699,             # Tyrunt, Tyrantrum, Amaura, Aurorus
  # Gen 8
  880, 881, 882, 883              # Dracozolt, Arctozolt, Dracovish, Arctovish
]
,
  "Starter": [
  # Gen 1 (Kanto)
  1, 2, 3,      # Bulbasaur, Ivysaur, Venusaur
  4, 5, 6,      # Charmander, Charmeleon, Charizard
  7, 8, 9,      # Squirtle, Wartortle, Blastoise

  # Gen 2 (Johto)
  152, 153, 154,  # Chikorita, Bayleef, Meganium
  155, 156, 157,  # Cyndaquil, Quilava, Typhlosion
  158, 159, 160,  # Totodile, Croconaw, Feraligatr

  # Gen 3 (Hoenn)
  252, 253, 254,  # Treecko, Grovyle, Sceptile
  255, 256, 257,  # Torchic, Combusken, Blaziken
  258, 259, 260,  # Mudkip, Marshtomp, Swampert

  # Gen 4 (Sinnoh)
  387, 388, 389,  # Turtwig, Grotle, Torterra
  390, 391, 392,  # Chimchar, Monferno, Infernape
  393, 394, 395,  # Piplup, Prinplup, Empoleon

  # Gen 5 (Unova)
  495, 496, 497,  # Snivy, Servine, Serperior
  498, 499, 500,  # Tepig, Pignite, Emboar
  501, 502, 503,  # Oshawott, Dewott, Samurott

  # Gen 6 (Kalos)
  650, 651, 652,  # Chespin, Quilladin, Chesnaught
  653, 654, 655,  # Fennekin, Braixen, Delphox
  656, 657, 658,  # Froakie, Frogadier, Greninja

  # Gen 7 (Alola)
  722, 723, 724,  # Rowlet, Dartrix, Decidueye
  725, 726, 727,  # Litten, Torracat, Incineroar
  728, 729, 730,  # Popplio, Brionne, Primarina

  # Gen 8 (Galar)
  810, 811, 812,  # Grookey, Thwackey, Rillaboom
  813, 814, 815,  # Scorbunny, Raboot, Cinderace
  816, 817, 818   # Sobble, Drizzile, Inteleon
]
,
  "Baby": [
    # Gen 2 (Johto)
    172,  # Pichu
    173,  # Cleffa
    174,  # Igglybuff
    175,  # Togepi
    236,  # Tyrogue
    238,  # Smoochum
    239,  # Elekid
    240,  # Magby

    # Gen 3 (Hoenn)
    298,  # Azurill
    360,  # Wynaut

    # Gen 4 (Sinnoh)
    406,  # Budew
    433,  # Chingling
    438,  # Bonsly
    439,  # Mime Jr.
    440,  # Happiny
    446,  # Munchlax
    447,  # Riolu
    458,  # Mantyke

    # Gen 8 (Galar)
    848,  # Toxel
]
,
  "Hisuian": [
    # Gen 8 (Legends: Arceus - Hisui region)
    899,  # Wyrdeer
    900,  # Kleavor
    901,  # Ursaluna
    902,  # Basculegion
    903,  # Sneasler
    904,  # Overqwil
    905,  # Enamorus
]

}

def generate_startup_files(base_path, base_user_path):  # Add base_user_path parameter
    """
    Generates blank personal files at startup with the value [].
    Introduced as a workaround to gitignore personal files.
    """
    files = ['mypokemon.json', 'mainpokemon.json', 'items.json',
             'team.json', 'data.json', 'badges.json']

    for file in files:
        file_path = os.path.join(base_user_path, file)  # Use base_user_path parameter
        # Create parent directory if needed
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        if not os.path.exists(file_path):
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=2)

    # Default data for the file
    default_rating_data = {"rate_this": False}
    rate_path = os.path.join(base_user_path, 'rate_this.json')

    # Create the file with default contents if it doesn't exist
    if not os.path.exists(rate_path):
        os.makedirs(os.path.dirname(rate_path), exist_ok=True)
        with open(rate_path, "w", encoding="utf-8") as f:
            json.dump(default_rating_data, f, indent=4)

    # Create blank HelpInfos.html and updateinfos.md at base_path if they don't exist
    helpinfos_path = os.path.join(base_path, 'HelpInfos.html')
    updateinfos_path = os.path.join(base_path, 'updateinfos.md')

    if not os.path.exists(helpinfos_path):
        os.makedirs(os.path.dirname(helpinfos_path), exist_ok=True)
        with open(helpinfos_path, 'w', encoding='utf-8') as f:
            f.write('')

    if not os.path.exists(updateinfos_path):
        os.makedirs(os.path.dirname(updateinfos_path), exist_ok=True)
        with open(updateinfos_path, 'w', encoding='utf-8') as f:
            f.write('')

    return True

```
---
## 10. `src/Ankimon/functions/encounter_functions.py`

### Why this file is critical
Logic for generating and catching Pokémon

### Full Contents

```python

import json
import random
import math
from typing import Union
from datetime import datetime
import uuid

from aqt import mw
from aqt.qt import QDialog
from aqt.utils import showWarning

from ..pyobj.ankimon_tracker import AnkimonTracker
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.reviewer_obj import Reviewer_Manager
from ..pyobj.test_window import TestWindow
from ..pyobj.trainer_card import TrainerCard
from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.evolution_window import EvoWindow
from ..pyobj.attack_dialog import AttackDialog
from ..pyobj.translator import Translator
from ..functions.pokemon_functions import find_experience_for_level, get_levelup_move_for_pokemon, pick_random_gender, shiny_chance
from ..functions.pokedex_functions import (
    check_evolution_for_pokemon,
    get_all_pokemon_moves,
    return_name_for_id,
    search_pokeapi_db_by_id,
    search_pokedex,
    search_pokedex_by_id
)
from ..pyobj.error_handler import show_warning_with_traceback
from ..functions.trainer_functions import xp_share_gain_exp
from ..functions.badges_functions import check_for_badge, receive_badge
from ..functions.drawing_utils import tooltipWithColour
from ..utils import limit_ev_yield, play_effect_sound, iv_rand_gauss, get_ev_spread
from ..business import calc_experience
from ..const import gen_ids
from ..singletons import (
    main_pokemon,
    ankimon_tracker_obj,
    trainer_card,
    settings_obj,
    translator,
)
from ..resources import (
    pokemon_species_baby_path,
    pokemon_species_legendary_path,
    pokemon_species_mythical_path,
    pokemon_species_normal_path,
    pokemon_species_ultra_path,
    mypokemon_path,
    mainpokemon_path,
)

def modify_percentages(total_reviews, daily_average, player_level):
    """
    Modify Pokémon encounter percentages based on total reviews, player level, event modifiers, and main Pokémon level.
    """
    # Start with the base percentages
    percentages = {"Baby": 2, "Legendary": 0.5, "Mythical": 0.2, "Normal": 92.3, "Ultra": 5}

    # Adjust percentages based on total reviews relative to the daily average
    review_ratio = total_reviews / daily_average if daily_average > 0 else 0

    # Adjust for review progress
    if review_ratio < 0.4:
        percentages["Normal"] += percentages.pop("Baby", 0) + percentages.pop("Legendary", 0) + \
                                 percentages.pop("Mythical", 0) + percentages.pop("Ultra", 0)
    elif review_ratio < 0.6:
        percentages["Baby"] += 2
        percentages["Normal"] -= 2
    elif review_ratio < 0.8:
        percentages["Ultra"] += 3
        percentages["Normal"] -= 3
    else:
        percentages["Legendary"] += 2
        percentages["Ultra"] += 3
        percentages["Normal"] -= 5

    # Restrict access to certain tiers based on main Pokémon level
    if main_pokemon.level:
        # Define level thresholds for each tier
        level_thresholds = {
            "Ultra": 30,  # Example threshold for Ultra Pokémon
            "Legendary": 50,  # Example threshold for Legendary Pokémon
            "Mythical": 75  # Example threshold for Mythical Pokémon
        }

        for tier in ["Ultra", "Legendary", "Mythical"]:
            if main_pokemon.level < level_thresholds.get(tier, float("inf")):
                percentages[tier] = 0  # Set percentage to 0 if the level requirement isn't met

    # Example modification based on player level
    if player_level:
        # Adjustment value based on player level: 0.01 per level
        # Level 100 -> 1.0 adjustment
        # Level 200 -> 2.0 adjustment
        adjustment = player_level * 0.01

        for tier in percentages:
            if tier == "Normal":
                percentages[tier] = max(percentages[tier] - adjustment, 0)
            else:
                percentages[tier] = percentages.get(tier, 0) + adjustment

    # Normalize percentages to ensure they sum to 100
    total = sum(percentages.values())
    for tier in percentages:
        percentages[tier] = (percentages[tier] / total) * 100 if total > 0 else 0
    # this function gets called maybe 10 times per battle round, which is concerning.
    # it could be rewritten to run ONLY when the change in review ratio is detected.
    return percentages

def get_pokemon_id_by_tier(tier):
    id_species_path = None
    if tier == "Normal":
        id_species_path = pokemon_species_normal_path
    elif tier == "Baby":
        id_species_path = pokemon_species_baby_path
    elif tier == "Ultra":
        id_species_path = pokemon_species_ultra_path
    elif tier == "Legendary":
        id_species_path = pokemon_species_legendary_path
    elif tier == "Mythical":
        id_species_path = pokemon_species_mythical_path

    with open(id_species_path, "r", encoding="utf-8") as file:
        id_data = json.load(file)

    # Select a random Pokemon ID from those in the tier
    random_pokemon_id = random.choice(id_data)
    return random_pokemon_id

def get_tier(total_reviews, player_level=1, event_modifier=None):
    """_summary_
    Randomly picks the tier for a new enemy Pokemon to be generated from, based on weighted probabilities based on number of reviews and player level.

    Args:
        total_reviews (int): Number of reviews done in that Anki session.
        player_level (int, optional): Trainer XP level. Defaults to 1.
        event_modifier (?, optional): Unused argument. Defaults to None.

    Returns:
        choice[0]: The first choice of TIER picked randomly (by a random.choices function)
    """
    daily_average = int(settings_obj.get('battle.daily_average'))
    percentages = modify_percentages(total_reviews, daily_average, player_level)

    tiers = list(percentages.keys())
    probabilities = list(percentages.values())

    choice = random.choices(tiers, probabilities, k=1)
    return choice[0]

def choose_random_pkmn_from_tier():
    """
    Runs a tier-selection and a subsequent ID-selection function to pick a random Pokemon from a given randomly picked Tier.
    The tier is a weighted probability selection, based on total_reviews and trainer_level.
    Pokemon ID is picked randomly from within that tier.

    Returns:
        id (int): Pokedex ID for generated Pokemon
        tier (string): Rarity tier for generated Pokemon (normal/ultra/legendary etc.)
    """
    total_reviews = ankimon_tracker_obj.total_reviews
    trainer_level = trainer_card.level
    try:
        tier = get_tier(total_reviews, trainer_level)
        id = get_pokemon_id_by_tier(tier)
        return id, tier
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message="Error occurred")

def check_min_generate_level(name):
    evoType = search_pokedex(name.lower(), "evoType")
    evoLevel = search_pokedex(name.lower(), "evoLevel")
    if evoLevel:
        return int(evoLevel)
    elif evoType != []:
        min_level = 100
        return min_level
    else:
        min_level = 1
        return min_level

def check_id_ok(id_num: Union[int, list[int]]):
    if isinstance(id_num, list):
        if len(id_num) > 0:
            id_num = id_num[0]
        else:
            return False

    if not isinstance(id_num, int):
        return False

    if id_num >= 898:
        return False

    generation = 0
    for gen, max_id in gen_ids.items():
        if id_num <= max_id:
            generation = int(gen.split('_')[1])

            gen_config = [settings_obj.get(f"misc.gen{i}") for i in range(1, 10)]
            return gen_config[generation - 1]

    return False

def generate_random_pokemon(main_pokemon_level: int, ankimon_tracker_obj: AnkimonTracker):
    """
    Generates a random wild Pokémon with attributes scaled to the level of the player's main Pokémon.

    This function resets the encounter and battle round state in the provided `AnkimonTracker` object.
    It then selects a valid Pokémon that can appear at the current level range, computes its stats,
    determines its moves, ability, and other combat-relevant characteristics, and returns all necessary
    data required for a battle.

    Args:
        main_pokemon_level (int): The level of the player's main Pokémon. Determines the level range of
            the generated wild Pokémon.
        ankimon_tracker_obj (AnkimonTracker): An object used to track battle state, such as the number
            of Pokémon encountered and cards used in the battle.

    Returns:
        tuple: A tuple containing the following elements:
            - name (str): Name of the wild Pokémon.
            - pokemon_id (int): Unique ID of the Pokémon.
            - wild_pokemon_lvl (int): The level of the generated Pokémon.
            - ability (str): The selected ability of the Pokémon.
            - pokemon_type (list[str]): List of type(s) the Pokémon belongs to.
            - base_stats (dict): Dictionary of the Pokémon's base stats.
            - moves (list[str]): List of up to 4 moves the Pokémon can use in battle.
            - base_experience (int): Experience points awarded for defeating the Pokémon.
            - growth_rate (str): Growth rate category of the Pokémon (e.g., "slow", "fast").
            - ev (dict): Effort values (EVs) for each stat, initialized to 0.
            - iv (dict): Randomly generated individual values (IVs) for each stat.
            - gender (str): Randomly assigned gender.
            - battle_status (str): Current status of the Pokémon in battle, defaulted to "fighting".
            - final_stats (dict): Final computed stats of the Pokémon.
            - tier (str): Tier from which the Pokémon was selected (e.g., common, rare).
            - ev_yield (dict): Effort values (EVs) awarded upon defeating the Pokémon.
            - is_shiny (bool): Indicates whether the Pokémon is shiny.

    Raises:
        ValueError: If no valid Pokémon can be generated (highly unlikely under normal conditions).
    """
    lvl_variation = 3
    lvl_range = max(1, main_pokemon_level - lvl_variation), max(1, main_pokemon_level + lvl_variation)
    wild_pokemon_lvl = random.randint(*lvl_range)
    wild_pokemon_lvl = max(1, wild_pokemon_lvl)  # Ensures that the wild pokemon's level is at least 1
    if main_pokemon_level == 100:
        wild_pokemon_lvl = 100

    # First, we draw a random, valid pokemon id.
    pokemon_id, tier = choose_random_pkmn_from_tier()
    name = search_pokedex_by_id(pokemon_id)
    min_allowed_pokemon_lvl = check_min_generate_level(str(name.lower()))  # Gets the minimum allowed level for that pokemon given its stage of evolution
    while (not check_id_ok(pokemon_id)) or (wild_pokemon_lvl < min_allowed_pokemon_lvl):  # We keep drawing a random pokemon until we find a valid one
        pokemon_id, tier = choose_random_pkmn_from_tier()
        name = search_pokedex_by_id(pokemon_id)
        min_allowed_pokemon_lvl = check_min_generate_level(str(name.lower()))  # Gets the minimum allowed level for that pokemon given its stage of evolution

    # Now we get all necessary information about the chosen pokemon.
    pokemon_type = search_pokedex(name, "types")
    base_experience = search_pokeapi_db_by_id(pokemon_id, "base_experience")  # Experience that the wild pokemon will give once beaten
    growth_rate = search_pokeapi_db_by_id(pokemon_id, "growth_rate")
    ev_yield = search_pokeapi_db_by_id(pokemon_id, "effort_values")
    gender = pick_random_gender(name)
    is_shiny = shiny_chance()
    battle_status = "fighting"
    base_stats = search_pokedex(name, "baseStats")

    all_possible_moves = get_all_pokemon_moves(name, wild_pokemon_lvl)
    if len(all_possible_moves) <= 4:
        moves = all_possible_moves
    else:
        moves = random.sample(all_possible_moves, 4)

    ability = "no_ability"  # Default value for ability
    possible_abilities = search_pokedex(name, "abilities")
    if possible_abilities:
        numeric_abilities = {k: v for k, v in possible_abilities.items() if k.isdigit()}
        if numeric_abilities:
            ability = random.choice(list(numeric_abilities.values()))

    stat_names = ["hp", "atk", "def", "spa", "spd", "spe"]
    # ev = {stat: 0 for stat in stat_names}
    ev = get_ev_spread(random.choice(["random", "pair", "defense", "uniform"]))
    # tau = 200
    # mu = 31 * (1 - math.exp(-ankimon_tracker_obj.total_reviews / tau))  # At total reviews > 3 * tau, we get mu ~= 31
    # iv = {stat: iv_rand_gauss(mu=mu, sigma=5) for stat in stat_names}  # The higher the number of reviews, the higher the IVs
    iv = {stat: random.randint(0, 31) for stat in stat_names}
    final_stats = base_stats

    ankimon_tracker_obj.pokemon_encounter = 0  # 0: Start of Battle: 1: Current Battle
    ankimon_tracker_obj.cards_battle_round = 0  # Amount of cards in this current battle

    return (
        name,
        pokemon_id,
        wild_pokemon_lvl,
        ability,
        pokemon_type,
        base_stats,
        moves,
        base_experience,
        growth_rate,
        ev,
        iv,
        gender,
        battle_status,
        final_stats,
        tier,
        ev_yield,
        is_shiny
    )

def new_pokemon(
        pokemon: PokemonObject,
        test_window: TestWindow,
        ankimon_tracker: AnkimonTracker,
        reviewer_obj: Reviewer_Manager
        ) -> PokemonObject:
    """
    Initializes a new wild Pokémon encounter by generating a random Pokémon,
    updating its stats, setting its HP, and preparing the battle scene.

    This function uses the player's main Pokémon level to generate an appropriately
    leveled wild Pokémon with randomized attributes. It updates the provided `pokemon`
    object with generated data, resets HP, triggers any battle scene randomization,
    and updates the reviewer interface if applicable.

    Args:
        pokemon (PokemonObject): The Pokémon object to be updated with the new wild Pokémon's data.
        test_window (TestWindow): Optional UI window to display the first encounter scene.
        ankimon_tracker (AnkimonTracker): Object tracking battle-related state and handling battle scene randomization.
        reviewer_obj (Reviewer_Manager): Manager object responsible for updating battle review elements like life bars.

    Returns:
        PokemonObject: The updated `pokemon` object representing the newly generated wild Pokémon ready for battle.
    """
    (
        name,
        pkmn_id,
        level,
        ability,
        pkmn_type,
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
        is_shiny
        ) = generate_random_pokemon(main_pokemon.level, ankimon_tracker_obj)
    pokemon_data = {
        'name': name,
        'id': pkmn_id,
        'level': level,
        'ability': ability,
        'type': pkmn_type,
        'base_stats': base_stats,
        'attacks': enemy_attacks,
        'base_experience': base_experience,
        'growth_rate': growth_rate,
        'ev': ev,
        'iv': iv,
        'gender': gender,
        'battle_status': battle_status,
        'battle_stats': battle_stats,
        'stat_stages': {'atk': 0, 'def': 0, 'spa': 0, 'spd': 0, 'spe': 0, 'accuracy': 0, 'evasion': 0},
        'tier': tier,
        'ev_yield': ev_yield,
        'shiny': is_shiny
    }
    pokemon.update_stats(**pokemon_data)
    max_hp = pokemon.calculate_max_hp()
    pokemon.current_hp = max_hp
    pokemon.hp = max_hp
    pokemon.max_hp = max_hp

    ankimon_tracker.randomize_battle_scene()
    if test_window is not None:
        test_window.display_first_encounter()
    class Container(object):
        pass
    reviewer = Container()
    reviewer.web = mw.reviewer.web
    reviewer_obj.update_life_bar(reviewer, 0, 0)

    return pokemon

def save_main_pokemon_progress(
        main_pokemon: PokemonObject,
        enemy_pokemon: PokemonObject,
        exp: int,
        achievements: dict,
        logger: ShowInfoLogger,
        evo_window: EvoWindow,
        ):
    experience = int(find_experience_for_level(main_pokemon.growth_rate, main_pokemon.level, settings_obj.get("misc.remove_level_cap")))
    if settings_obj.get("misc.remove_level_cap") is True:
        main_pokemon.xp += exp
        level_cap = None
    elif main_pokemon.level != 100:
        main_pokemon.xp += exp
        level_cap = 100
    try:
        if mainpokemon_path.is_file():
            with open(mainpokemon_path, "r", encoding="utf-8") as json_file:
                main_pokemon_data = json.load(json_file)
        else:
            showWarning(translator.translate("missing_mainpokemon_data"))
    except Exception as e:
        show_warning_with_traceback(parent=mw, exception=e, message="Error loading main pokemon data.")
        return
    while int(find_experience_for_level(main_pokemon.growth_rate, main_pokemon.level, settings_obj.get("misc.remove_level_cap"))) < int(main_pokemon.xp) and (level_cap is None or main_pokemon.level < level_cap):
        main_pokemon.level += 1
        msg = ""
        msg += f"Your {main_pokemon.name} is now level {main_pokemon.level} !"
        color = "#6A4DAC" #pokemon leveling info color for tooltip
        check = check_for_badge(achievements, 5)
        if check is False:
            achievements = receive_badge(5,achievements)
        try:
            tooltipWithColour(msg, color)
        except:
            pass
        if settings_obj.get('gui.pop_up_dialog_message_on_defeat') is True:
            logger.log_and_showinfo("info",f"{msg}")
        main_pokemon.xp = int(max(0, int(main_pokemon.xp) - int(experience)))
        evo_id = check_evolution_for_pokemon(main_pokemon.individual_id, main_pokemon.id, main_pokemon.level, evo_window, main_pokemon.everstone)
        if evo_id is not None:
            msg += translator.translate("pokemon_about_to_evolve", main_pokemon_name=main_pokemon.name, evo_pokemon_name=return_name_for_id(evo_id).capitalize(), main_pokemon_level=main_pokemon.level)
            logger.log_and_showinfo("info",f"{msg}")
            color = "#6A4DAC"
            try:
                tooltipWithColour(msg, color)
            except:
                pass
                    #evo_window.ask_pokemon_evo(main_pokemon.name.lower())
        for mainpkmndata in main_pokemon_data:
            if mainpkmndata["name"] == main_pokemon.name.capitalize():
                attacks = mainpkmndata["attacks"]
                new_attacks = get_levelup_move_for_pokemon(main_pokemon.name.lower(),int(main_pokemon.level))
                if new_attacks:
                    msg = ""
                    msg += translator.translate("mainpokemon_can_learn_new_attack", main_pokemon_name=main_pokemon.name.capitalize())
                for new_attack in new_attacks:
                    if len(attacks) < 4 and new_attack not in attacks:
                        attacks.append(new_attack)
                        msg += translator.translate("mainpokemon_learned_new_attack", new_attack_name=new_attack, main_pokemon_name=main_pokemon.name.capitalize())
                        color = "#6A4DAC"
                        tooltipWithColour(msg, color)
                        if settings_obj.get('gui.pop_up_dialog_message_on_defeat') is True:
                            logger.log_and_showinfo("info",f"{msg}")
                    else:
                        dialog = AttackDialog(attacks, new_attack)
                        if dialog.exec() == QDialog.DialogCode.Accepted:
                            selected_attack = dialog.selected_attack
                            index_to_replace = None
                            for index, attack in enumerate(attacks):
                                if attack == selected_attack:
                                    index_to_replace = index
                                    pass
                                else:
                                    pass
                            # If the attack is found, replace it with 'new_attack'
                            if index_to_replace is not None:
                                attacks[index_to_replace] = new_attack
                                logger.log_and_showinfo("info",
                                    f"Replaced '{selected_attack}' with '{new_attack}'")
                            else:
                                logger.log_and_showinfo("info",f"'{selected_attack}' not found in the list")
                        else:
                            # Handle the case where the user cancels the dialog
                            logger.log_and_showinfo("info",f"{new_attack} will be discarded.")
                mainpkmndata["attacks"] = attacks
                break
    msg = ""
    msg += translator.translate("mainpokemon_gained_xp", main_pokemon_name=main_pokemon.name, exp=exp, experience_till_next_level=experience, main_pokemon_xp=main_pokemon.xp)
    color = "#a17cf7" #pokemon leveling info color for tooltip
    try:
        tooltipWithColour(msg, color)
    except:
        pass
    if settings_obj.get('gui.pop_up_dialog_message_on_defeat') is True:
        logger.log_and_showinfo("info",f"{msg}")
    # Load existing Pokémon data if it exists

    for mainpkmndata in main_pokemon_data:
        mainpkmndata["stats"] = main_pokemon.stats
        mainpkmndata["xp"] = int(main_pokemon.xp)
        #mainpkmndata["stats"]["xp"] = int(main_pokemon.xp)
        mainpkmndata["level"] = int(main_pokemon.level)
        ev_yield = limit_ev_yield(mainpkmndata["ev"], enemy_pokemon.ev_yield)
        mainpkmndata["ev"]["hp"] += ev_yield["hp"]
        mainpkmndata["ev"]["atk"] += ev_yield["attack"]
        mainpkmndata["ev"]["def"] += ev_yield["defense"]
        mainpkmndata["ev"]["spa"] += ev_yield["special-attack"]
        mainpkmndata["ev"]["spd"] += ev_yield["special-defense"]
        mainpkmndata["ev"]["spe"] += ev_yield["speed"]
        mainpkmndata["current_hp"] = int(main_pokemon.current_hp)
        main_pokemon.friendship += random.randint(5, 9)
        if main_pokemon.friendship > 255:
            main_pokemon.friendship = 255
        mainpkmndata["friendship"] = main_pokemon.friendship
        main_pokemon.pokemon_defeated += 1
        mainpkmndata["pokemon_defeated"] = main_pokemon.pokemon_defeated
        if hasattr(main_pokemon, "tier"):
            mainpkmndata["tier"] = main_pokemon.tier
        if hasattr(main_pokemon, "is_favorite"):
            mainpkmndata["is_favorite"] = main_pokemon.is_favorite
    mypkmndata = mainpkmndata
    mainpkmndata = [mainpkmndata]
    # Save the caught Pokémon's data to a JSON file
    with open(str(mainpokemon_path), "w") as json_file:
        json.dump(mainpkmndata, json_file, indent=2)

    # Load data from the output JSON file
    with open(str(mypokemon_path), "r", encoding="utf-8") as output_file:
        mypokemondata = json.load(output_file)

        # Find and replace the specified Pokémon's data in mypokemondata
        for index, pokemon_data in enumerate(mypokemondata):
            if pokemon_data.get("individual_id") == main_pokemon.individual_id:  # Match by individual_id
                mypokemondata[index] = mypkmndata  # Replace with new data
                break

        # Save the modified data to the output JSON file
        with open(str(mypokemon_path), "w") as output_file:
            json.dump(mypokemondata, output_file, indent=2)

    sync_mainpokemon_to_mypokemon(main_pokemon, mainpokemon_path, mypokemon_path)

    return main_pokemon.level

# --- Utility: Sync mainpokemon to mypokemon ---
def sync_mainpokemon_to_mypokemon(main_pokemon, mainpokemon_path, mypokemon_path):
    """
    Update the relevant entry in mypokemon file with the latest values from mainpokemon file.
    Args:
        main_pokemon: The main PokemonObject (should have individual_id).
        mainpokemon_path: Path to mainpokemon.json.
        mypokemon_path: Path to mypokemon.json.
    """
    import json
    # Load mainpokemon data
    if not mainpokemon_path.is_file():
        return
    with open(mainpokemon_path, "r", encoding="utf-8") as f:
        main_data = json.load(f)
    if not main_data:
        return
    # Use the first (and only) mainpokemon entry
    main_entry = main_data[0] if isinstance(main_data, list) else main_data
    main_id = main_entry.get("individual_id", None)
    if not main_id:
        main_id = getattr(main_pokemon, "individual_id", None)
    if not main_id:
        return
    # Load mypokemon data
    if not mypokemon_path.is_file():
        return
    with open(mypokemon_path, "r", encoding="utf-8") as f:
        my_data = json.load(f)
    # Find and update the entry with matching individual_id
    updated = False
    for idx, entry in enumerate(my_data):
        if entry.get("individual_id") == main_id:
            # Update all keys from main_entry (except those you want to preserve in mypokemon)
            for k, v in main_entry.items():
                entry[k] = v
            my_data[idx] = entry
            updated = True
            break
    if updated:
        with open(mypokemon_path, "w", encoding="utf-8") as f:
            json.dump(my_data, f, indent=2)
    return

def kill_pokemon(
        main_pokemon: PokemonObject,
        enemy_pokemon: PokemonObject,
        evo_window: EvoWindow,
        logger: ShowInfoLogger,
        achievements: dict,
        trainer_card: Union[TrainerCard, None]=None
        ):
    if trainer_card is not None:
        trainer_card.gain_xp(enemy_pokemon.tier, settings_obj.get("controls.allow_to_choose_moves"))

    # Calculate experience based on whether moves are chosen manually
    exp = calc_experience(main_pokemon.base_experience, enemy_pokemon.level)
    if settings_obj.get("controls.allow_to_choose_moves"):
        exp *= 0.5

    # Ensure exp is at least 1 and round up if it's a decimal
    exp = max(1, math.ceil(exp))

    # Handle XP share logic
    xp_share_individual_id = settings_obj.get("trainer.xp_share")
    if xp_share_individual_id:
        exp = xp_share_gain_exp(logger, settings_obj, evo_window, main_pokemon.id, exp, xp_share_individual_id)

    # Save main Pokémon's progress
    main_pokemon.level = save_main_pokemon_progress(
        main_pokemon,
        enemy_pokemon,
        exp,
        achievements,
        logger,
        evo_window,
    )

    ankimon_tracker_obj.general_card_count_for_battle = 0

def save_caught_pokemon(
        enemy_pokemon: PokemonObject,
        nickname: Union[str, None]=None,
        achievements: Union[dict, None]=None
        ):
    # Create a dictionary to store the Pokémon's data
    # add all new values like hp as max_hp, evolution_data, description and growth rate
    if enemy_pokemon.tier is not None and achievements is not None:
        if enemy_pokemon.tier == "Normal":
            check = check_for_badge(achievements, 17)
            if check is False:
                achievements = receive_badge(17,achievements)
        elif enemy_pokemon.tier == "Baby":
            check = check_for_badge(achievements, 18)
            if check is False:
                achievements = receive_badge(18, achievements)
        elif enemy_pokemon.tier == "Ultra":
            check = check_for_badge(achievements, 8)
            if check is False:
                achievements = receive_badge(8, achievements)
        elif enemy_pokemon.tier == "Legendary":
            check = check_for_badge(achievements, 9)
            if check is False:
                achievements = receive_badge(9, achievements)
        elif enemy_pokemon.tier == "Mythical":
            check = check_for_badge(achievements, 10)
            if check is False:
                achievements = receive_badge(10, achievements)

    #enemy_pokemon.stats["xp"] = 0
    enemy_pokemon.xp = 0
    caught_pokemon = {
        "name": enemy_pokemon.name.capitalize(),
        "nickname": "",
        "level": enemy_pokemon.level,
        "gender": enemy_pokemon.gender,
        "id": enemy_pokemon.id,
        "ability": enemy_pokemon.ability,
        "type": enemy_pokemon.type,
        "stats": enemy_pokemon.base_stats,
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": enemy_pokemon.iv,
        "attacks": enemy_pokemon.attacks,
        "base_experience": enemy_pokemon.base_experience,
        "current_hp": enemy_pokemon.calculate_max_hp(),
        "growth_rate": enemy_pokemon.growth_rate,
        "friendship": 0,
        "pokemon_defeated": 0,
        "xp": 0,
        "everstone": False,
        "shiny": enemy_pokemon.shiny,
        "captured_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "individual_id": str(uuid.uuid4()),
        "mega": False,
        "special_form": None,
        "tier": enemy_pokemon.tier,
        "is_favorite": False,
        "held_item": None
    }

    # Load existing Pokémon data if it exists
    caught_pokemon_data = []
    if mypokemon_path.is_file():
        with open(mypokemon_path, "r", encoding="utf-8") as json_file:
            caught_pokemon_data = json.load(json_file)

    # Append the caught Pokémon's data to the list
    caught_pokemon_data.append(caught_pokemon)

    # Save the caught Pokémon's data to a JSON file
    with open(str(mypokemon_path), "w") as json_file:
        json.dump(caught_pokemon_data, json_file, indent=2)

def catch_pokemon(
        enemy_pokemon: PokemonObject,
        ankimon_tracker_obj: AnkimonTracker,
        logger: Union[ShowInfoLogger, None]=None,
        nickname: Union[str, None]=None,
        collected_pokemon_ids: Union[set, None]=None,
        achievements: Union[dict, None]=None,
        ):
    ankimon_tracker_obj.caught += 1
    if ankimon_tracker_obj.caught > 1:
        if settings_obj.get('gui.pop_up_dialog_message_on_defeat') is True:
            logger.log_and_showinfo("info",translator.translate("already_caught_pokemon")) # Display a message when the Pokémon is caught

    # If we arrive here, this means that ankimon_tracker_obj.caught == 1
    if nickname is not None or not nickname:
        nickname = enemy_pokemon.name
    if collected_pokemon_ids is not None:
        collected_pokemon_ids.add(enemy_pokemon.id)  # Update cache
    save_caught_pokemon(enemy_pokemon, nickname, achievements)

    ankimon_tracker_obj.general_card_count_for_battle = 0

    msg = translator.translate("caught_wild_pokemon", enemy_pokemon_name=enemy_pokemon.name.capitalize())

    if settings_obj.get('gui.pop_up_dialog_message_on_defeat') is True:
        if logger is not None:
            logger.log_and_showinfo("info",f"{msg}") # Display a message when the Pokémon is caught

    color = "#a17cf7"#6A4DAC" #pokemon leveling info color for tooltip
    try:
        tooltipWithColour(msg, color)
    except Exception as e:
        if logger is not None:
            show_warning_with_traceback(parent=mw, exception=e, message="Error while catching Pokemon:") # Display a message when the Pokémon is caught

def handle_enemy_faint(
        main_pokemon: PokemonObject,
        enemy_pokemon: PokemonObject,
        collected_pokemon_ids: set,
        test_window: TestWindow,
        evo_window: EvoWindow,
        reviewer_obj: Reviewer_Manager,
        logger: ShowInfoLogger,
        achievements: dict,
        ):
    """
    Handles what automatically happens when the enemy Pokémon faints, based on auto-battle settings.
    """
    try:
        auto_battle_setting = int(settings_obj.get("battle.automatic_battle"))
        if not (0 <= auto_battle_setting <= 3):
            auto_battle_setting = 0  # fallback
    except ValueError:
        auto_battle_setting = 0  # fallback

    if auto_battle_setting == 3:  # Catch if uncollected
        enemy_id = enemy_pokemon.id
        # Check cache instead of file
        if enemy_id not in collected_pokemon_ids or enemy_pokemon.shiny:
            catch_pokemon(enemy_pokemon, ankimon_tracker_obj, logger, "", collected_pokemon_ids, achievements)
        else:
            kill_pokemon(main_pokemon, enemy_pokemon, evo_window, logger , achievements, trainer_card)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon
    elif auto_battle_setting == 1:  # Existing auto-catch
        catch_pokemon(enemy_pokemon, ankimon_tracker_obj, logger, "", collected_pokemon_ids, achievements)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon
    elif auto_battle_setting == 2:  # Existing auto-defeat
        kill_pokemon(main_pokemon, enemy_pokemon, evo_window, logger , achievements, trainer_card)
        new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon

    # For Manual mode (auto_battle_setting == 0): no need to show window or do actions automatically
    test_window.display_pokemon_death()
    main_pokemon.reset_bonuses()
    ankimon_tracker_obj.general_card_count_for_battle = 0

def handle_main_pokemon_faint(
        main_pokemon: PokemonObject,
        enemy_pokemon: PokemonObject,
        test_window: TestWindow,
        reviewer_obj: Reviewer_Manager,
        translator: Translator,
        ):
    """
    Handles what happens when the main Pokémon faints.
    """
    msg = translator.translate("pokemon_fainted", enemy_pokemon_name=main_pokemon.name.capitalize())
    tooltipWithColour(msg, "#E12939")
    play_effect_sound(settings_obj, "Fainted")

    main_pokemon.hp = main_pokemon.max_hp
    main_pokemon.current_hp = main_pokemon.max_hp
    main_pokemon.reset_bonuses()

    new_pokemon(enemy_pokemon, test_window, ankimon_tracker_obj, reviewer_obj)  # Show a new random Pokémon
```
---
## 11. `src/Ankimon/pyobj/pc_box.py`

### Why this file is critical
UI and logic for managing the collected Pokémon

### Full Contents

```python
import json
import uuid
from typing import Any, Callable

from aqt import mw, gui_hooks
from aqt.qt import (
    Qt,
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QGridLayout,
    QPixmap,
)

from aqt.theme import theme_manager # Check if light / dark mode in Anki

from PyQt6.QtWidgets import QLineEdit, QComboBox, QCheckBox, QMenu, QWidget, QScrollArea, QFrame, QRadioButton, QButtonGroup
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon, QFont, QAction, QMovie, QCloseEvent

from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.reviewer_obj import Reviewer_Manager
from ..pyobj.test_window import TestWindow
from ..pyobj.translator import Translator
from ..pyobj.collection_dialog import MainPokemon
from ..gui_classes.pokemon_details import PokemonCollectionDetails
from ..pyobj.InfoLogger import ShowInfoLogger

from ..pyobj.settings import Settings
from ..functions.sprite_functions import get_sprite_path
from ..utils import load_custom_font, get_tier_by_id
from ..resources import mypokemon_path, itembag_path


def format_item_name(item_name: str) -> str:
    return item_name.replace("-", " ").title()

def clear_layout(layout):
    """
    Recursively removes all widgets and nested layouts from a given layout.

    This function iterates through all items in the provided layout, removes
    each widget or sub-layout, and ensures proper deletion and memory cleanup.

    Args:
        layout (QLayout): The layout to be cleared. Can contain widgets and/or nested layouts.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            clear_layout(item.layout())

class ScaledMovieLabel(QLabel):
    def __init__(self, gif_path, width, height):
        super().__init__()
        self.target_width = width
        self.target_height = height
        self.movie = QMovie(gif_path)
        self.movie.frameChanged.connect(self.on_frame_changed)
        self.movie.start()
        self.setFixedSize(width, height)

    def on_frame_changed(self, frame_number):
        # Get current frame pixmap
        pixmap = self.movie.currentPixmap()

        # Scale pixmap to target size (keep aspect ratio if you want)
        scaled_pixmap = pixmap.scaled(self.target_width, self.target_height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

        self.setPixmap(scaled_pixmap)

class PokemonPC(QDialog):
    def __init__(
            self,
            logger: ShowInfoLogger,
            translator: Translator,
            reviewer_obj: Reviewer_Manager,
            test_window: TestWindow,
            settings: Settings,
            main_pokemon: PokemonObject,
            parent=mw,
    ):
        super().__init__(parent)

        self.logger = logger
        self.translator = translator
        self.reviewer_obj = reviewer_obj
        self.test_window = test_window
        self.settings = settings
        self.main_pokemon_function_callback = lambda _pokemon_data: MainPokemon(_pokemon_data, main_pokemon, logger, translator, reviewer_obj, test_window)

        self.n_cols = 5
        self.n_rows = 6
        self.current_box_idx = 0  # Index of current displayed box
        self.gif_in_collection = settings.get("gui.gif_in_collection")

        self.slot_size = 75  # Side length in pixels of a PC slot
        self.main_layout = QHBoxLayout()  # Main horizontal layout for split panels
        self.details_layout = QVBoxLayout()  # Layout for details panel
        self.details_widget = QWidget()  # Widget to hold details
        self.pokemon_details_layout = None

        # Widgets for filtering and sorting
        self.search_edit = None
        self.type_combo = None
        self.generation_combo = None
        self.tier_combo = None
        self.filter_favorites = None
        self.filter_is_holding_item = None
        self.filter_shiny = None
        self.sort_by_id = None
        self.sort_by_name = None
        self.sort_by_level = None
        self.sort_by_date = None
        self.sort_group = None
        self.selected_sort_key = "Date"
        self.desc_sort = None  # Sort by descending order

        # Subscribe to theme change hook to update UI dynamically
        gui_hooks.theme_did_change.append(self.on_theme_change)

        self.ensure_data_integrity()  # Necessary for legacy reasons
        self.create_gui()

    def on_theme_change(self):
        """
        Callback function triggered when Anki's theme changes (light to dark or vice versa).
        Refreshes the GUI to apply the new theme settings.
        """
        self.refresh_gui()


    def create_gui(self):
        """
        Builds and sets up the main graphical user interface for displaying and managing Pokémon.

        This method initializes the GUI layout, including:
        - Navigation controls to switch between Pokémon storage boxes
        - A grid display for showing Pokémon in the current box
        - Filters and sorting options to refine the displayed Pokémon
        - Optional animated sprites or static images based on user settings
        - A right-hand details panel with flexible width

        The GUI components include:
        - Navigation buttons and current box label
        - A dynamically populated grid of Pokémon buttons with sprite icons
        - Filtering options (search by name, type, generation, tier, favorites)
        - Sorting options (by ID, name, level, ascending/descending)
        - A flexible-width details panel on the right

        All components are added to the main layout and displayed within a resizable window.

        Side Effects:
            - Modifies the instance's layout and widget properties.
            - Connects UI elements to their corresponding interaction handlers.
        """
        self.setWindowTitle("Pokémon PC")

        # Determine theme based on Anki's night mode
        is_dark_mode = theme_manager.night_mode # Correctly checks Anki's theme

        # Define authentic Pokémon-themed color palettes
        if is_dark_mode:
            # Dark Mode: Inspired by modern, sleek game UIs
            background_color = "#003A70"
            text_color = "#E0E0E0"
            button_bg = "#3B4CCA"
            button_border = "#6A73D9"
            hover_color = "#6A73D9"
            favorite_color = "#B3A125"
            favorite_hover_color = "#AF8308"
            input_bg = "#002B5A" # Slightly lighter than background for input fields
            slot_bg_color = "#002B5A"
        else:
            # Light Mode: Inspired by classic PC Box / Pokédex
            background_color = "#E6F3FF"
            text_color = "#003A70"
            button_bg = "#3D7DCA"
            button_border = "#003A70"
            hover_color = "#A8D8FF"
            favorite_color = "#FFDE00"
            favorite_hover_color = "#FFA600"
            input_bg = "#FFFFFF" # White background for input fields
            slot_bg_color = "#CCE5FF"

        # Set stylesheet for the entire dialog, now correctly using all theme variables
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {background_color};
            }}
            QWidget {{
                color: {text_color};
            }}
            QPushButton {{
                background-color: {button_bg};
                border: 1px solid {button_border};
                border-radius: 5px;
                padding: 5px;
                color: {text_color};
            }}
            QPushButton:hover {{
                background-color: {hover_color};
            }}
            QLineEdit, QComboBox {{
                background-color: {input_bg};
                border: 1px solid {button_border};
                border-radius: 3px;
                padding: 3px;
                color: {text_color};
            }}
            QLabel {{
                color: {text_color};
            }}
        """)

        self.gif_in_collection = self.settings.get("gui.gif_in_collection")

        pokemon_list = self.load_pokemon_data()
        pokemon_list = self.filter_pokemon_list(pokemon_list)
        pokemon_list = self.sort_pokemon_list(pokemon_list)
        max_box_idx = (len(pokemon_list) - 1) // (self.n_rows * self.n_cols)

        # Collection panel
        collection_layout = QVBoxLayout()
        box_selector_layout = QHBoxLayout()
        prev_box_button = QPushButton("◀")
        next_box_button = QPushButton("▶")
        prev_box_button.setFixedSize(70, 50)
        next_box_button.setFixedSize(70, 50)
        prev_box_button.setFont(QFont('System', 25))
        next_box_button.setFont(QFont('System', 25))
        prev_box_button.clicked.connect(lambda: self.looparound_go_to_box(self.current_box_idx - 1, max_box_idx))
        next_box_button.clicked.connect(lambda: self.looparound_go_to_box(self.current_box_idx + 1, max_box_idx))
        curr_box_label = QLabel(
            self.translator.translate(
                "pc_box_label",
                current=self.current_box_idx + 1,
                total=max_box_idx + 1,
            )
        )
        curr_box_label.setFixedSize(150, 50)
        curr_box_label.setFont(load_custom_font(20, int(self.settings.get("misc.language"))))
        curr_box_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        curr_box_label.setStyleSheet(f"border: 1px solid {button_border}; background-color: {background_color};")
        box_selector_layout.addWidget(prev_box_button, alignment=Qt.AlignmentFlag.AlignCenter)
        box_selector_layout.addWidget(curr_box_label, alignment=Qt.AlignmentFlag.AlignCenter)
        box_selector_layout.addWidget(next_box_button, alignment=Qt.AlignmentFlag.AlignCenter)
        collection_layout.addLayout(box_selector_layout)

        # Pokémon grid
        start_index = self.current_box_idx * self.n_cols * self.n_rows
        end_index = (self.current_box_idx + 1) * self.n_cols * self.n_rows
        pokemon_list_slice = pokemon_list[start_index:end_index]
        pokemon_grid = QGridLayout()
        for row in range(self.n_rows):
            for col in range(self.n_cols):
                pokemon_idx = col * self.n_rows + row
                if pokemon_idx >= len(pokemon_list_slice):
                    empty_label = QLabel()
                    empty_label.setFixedSize(self.slot_size, self.slot_size)
                    pokemon_grid.addWidget(empty_label, col, row, alignment=Qt.AlignmentFlag.AlignCenter)
                    continue

                pokemon = pokemon_list_slice[pokemon_idx]
                pkmn_image_path = get_sprite_path("front", "gif" if self.gif_in_collection else "png", pokemon['id'], pokemon.get("shiny", False), pokemon["gender"])
                pokemon_button = QPushButton("")
                pokemon_button.setFixedSize(self.slot_size, self.slot_size)

                if pokemon.get("is_favorite", False):
                    slot_style_bg = favorite_color
                    slot_style_hover_bg = favorite_hover_color # Favorite color doesn't change on hover
                else:
                    slot_style_bg = slot_bg_color
                    slot_style_hover_bg = hover_color

                # Apply the style
                style_sheet_str = f"""
                    QPushButton {{
                        background-color: {slot_style_bg};
                        border: 1px solid {button_border};
                        border-radius: 5px;
                    }}
                    QPushButton:hover {{
                        background-color: {slot_style_hover_bg};
                    }}
                """
                pokemon_button.setStyleSheet(style_sheet_str)

                pokemon_button.clicked.connect(lambda checked, pb=pokemon_button, pkmn=pokemon: self.show_actions_submenu(pb, pkmn))

                if self.gif_in_collection:
                    scaled_movie_label = ScaledMovieLabel(pkmn_image_path, self.slot_size - 10, self.slot_size - 10)
                    scaled_movie_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                    pokemon_grid.addWidget(pokemon_button, col, row, alignment=Qt.AlignmentFlag.AlignCenter)
                    pokemon_grid.addWidget(scaled_movie_label, col, row, alignment=Qt.AlignmentFlag.AlignCenter)
                else:
                    pixmap = QPixmap(pkmn_image_path)
                    pixmap = self.adjust_pixmap_size(pixmap, max_width=300, max_height=230)
                    pokemon_button.setIcon(QIcon(pkmn_image_path))
                    pokemon_button.setIconSize(QSize(self.slot_size - 10, self.slot_size - 10))
                    pokemon_grid.addWidget(pokemon_button, col, row, alignment=Qt.AlignmentFlag.AlignCenter)
        collection_layout.addLayout(pokemon_grid)

        # Bottom part to filter the Pokémon displayed
        filters_layout = QGridLayout()
        # Name filtering
        prev_text = self.search_edit.text() if self.search_edit is not None else ""
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search Pokémon (by nickname, name)")
        self.search_edit.setText(prev_text)
        self.search_edit.returnPressed.connect(lambda: self.go_to_box(0))
        search_button = QPushButton("Search")
        search_button.clicked.connect(lambda: self.go_to_box(0))
        # Type filtering
        prev_idx = self.type_combo.currentIndex() if self.type_combo is not None else 0
        self.type_combo = QComboBox()
        self.type_combo.addItem("All types")
        self.type_combo.addItems(["Normal", "Fire", "Water", "Electric", "Grass", "Ice", "Fighting", "Poison", "Ground", "Flying", "Psychic", "Bug", "Rock", "Ghost", "Dragon", "Dark", "Steel", "Fairy"])
        self.type_combo.setCurrentIndex(prev_idx)
        self.type_combo.currentIndexChanged.connect(lambda: self.go_to_box(0))
        # Generation filtering
        prev_idx = self.generation_combo.currentIndex() if self.generation_combo is not None else 0
        self.generation_combo = QComboBox()
        self.generation_combo.addItem("All gens")
        self.generation_combo.addItems([f"Gen {i}" for i in range(1, 9, 1)])
        self.generation_combo.setCurrentIndex(prev_idx)
        self.generation_combo.currentIndexChanged.connect(lambda: self.go_to_box(0))
        # Tier filtering
        prev_idx = self.tier_combo.currentIndex() if self.tier_combo is not None else 0
        self.tier_combo = QComboBox()
        self.tier_combo.addItem("All tiers")
        self.tier_combo.addItems(["Normal", "Legendary", "Mythical", "Baby", "Ultra", "Fossil", "Starter"])
        self.tier_combo.setCurrentIndex(prev_idx)
        self.tier_combo.currentIndexChanged.connect(lambda: self.go_to_box(0))
        # Sorting by favorites
        is_checked = self.filter_favorites.isChecked() if self.filter_favorites is not None else False
        self.filter_favorites = QCheckBox("Favorites")
        self.filter_favorites.setChecked(is_checked)
        self.filter_favorites.stateChanged.connect(lambda: self.go_to_box(0))
        # Filtering Pokemon who hold items
        is_checked = self.filter_is_holding_item.isChecked() if self.filter_is_holding_item is not None else False
        self.filter_is_holding_item = QCheckBox("Holds item")
        self.filter_is_holding_item.setChecked(is_checked)
        self.filter_is_holding_item.stateChanged.connect(lambda: self.go_to_box(0))
        # Shiny filter
        is_checked = self.filter_shiny.isChecked() if self.filter_shiny is not None else False
        self.filter_shiny = QCheckBox("Shiny")
        self.filter_shiny.setChecked(is_checked)
        self.filter_shiny.stateChanged.connect(lambda: self.go_to_box(0))
        # Sorting options
        sort_label = QLabel("Sort by:")

        # Radio buttons for mutually exclusive sorting
        self.sort_group = QButtonGroup(self)
        self.sort_by_id = QRadioButton("ID")
        self.sort_by_name = QRadioButton("Name")
        self.sort_by_level = QRadioButton("Level")
        self.sort_by_date = QRadioButton("Date")

        self.sort_group.addButton(self.sort_by_id)
        self.sort_group.addButton(self.sort_by_name)
        self.sort_group.addButton(self.sort_by_level)
        self.sort_group.addButton(self.sort_by_date)

        if self.selected_sort_key == "ID":
            self.sort_by_id.setChecked(True)
        elif self.selected_sort_key == "Name":
            self.sort_by_name.setChecked(True)
        elif self.selected_sort_key == "Level":
            self.sort_by_level.setChecked(True)
        else:  # Date is the default
            self.sort_by_date.setChecked(True)

        # Connect signals
        self.sort_group.buttonClicked.connect(self.on_sort_button_clicked)

        sort_radio_layout = QHBoxLayout()
        sort_radio_layout.addWidget(sort_label)
        sort_radio_layout.addWidget(self.sort_by_id)
        sort_radio_layout.addWidget(self.sort_by_name)
        sort_radio_layout.addWidget(self.sort_by_level)
        sort_radio_layout.addWidget(self.sort_by_date)
        sort_radio_widget = QWidget()
        sort_radio_widget.setLayout(sort_radio_layout)

        # Checkboxes for other options
        is_checked = self.desc_sort.isChecked() if self.desc_sort is not None else False
        self.desc_sort = QCheckBox("Descending")
        self.desc_sort.setChecked(is_checked)
        self.desc_sort.stateChanged.connect(lambda: self.go_to_box(0))

        # Adding the widgets to the layout
        filters_layout.addWidget(self.search_edit, 0, 0, 1, 4)
        filters_layout.addWidget(search_button, 0, 4, 1, 1)
        filters_layout.addWidget(self.type_combo, 1, 0, 1, 2)
        filters_layout.addWidget(self.generation_combo, 1, 2, 1, 2)
        filters_layout.addWidget(self.tier_combo, 1, 4, 1, 1)

        checkboxes_layout = QHBoxLayout()
        checkboxes_layout.addWidget(self.filter_favorites)
        checkboxes_layout.addWidget(self.filter_is_holding_item)
        checkboxes_layout.addWidget(self.filter_shiny)
        checkboxes_layout.addWidget(self.desc_sort)  # Moved here
        checkboxes_widget = QWidget()
        checkboxes_widget.setLayout(checkboxes_layout)

        filters_layout.addWidget(checkboxes_widget, 2, 0, 1, 5)
        filters_layout.addWidget(sort_radio_widget, 3, 0, 1, 5)
        collection_layout.addLayout(filters_layout)

        # Finalizing layout
        collection_widget = QWidget()
        collection_widget.setLayout(collection_layout)
        collection_widget.setFixedWidth(self.n_cols * (self.slot_size + 20) + 50)
        collection_widget.setFixedHeight(self.n_rows * (self.slot_size + 20) + 100)

        self.main_layout.addWidget(collection_widget, 1)

        # Check for existing details panel and apply styles
        if self.pokemon_details_layout is not None:
            self.details_widget = QWidget()
            self.details_widget.setLayout(self.pokemon_details_layout)
            self.details_widget.setMinimumWidth(470) # Ensure it's visible
            self.details_widget.setStyleSheet(f"background-color: {background_color};")
            self.main_layout.addWidget(self.details_widget, 2)
        else:
            # Ensure the panel is collapsed if no pokemon is selected
            self.details_widget = QWidget()
            self.details_widget.setLayout(QVBoxLayout()) # Placeholder layout
            self.details_widget.setMinimumWidth(0)
            self.details_widget.setMaximumWidth(0)
            self.main_layout.addWidget(self.details_widget, 2)

        self.setLayout(self.main_layout)

    def refresh_gui(self):
        """
        Refreshes the entire graphical user interface by rebuilding its layout.

        This method clears the current main layout, reconstructs it by calling `create_gui()`,
        and then invalidates and reactivates the layout to ensure proper rendering.

        Side Effects:
            - Removes all widgets from the main layout.
            - Recreates and re-adds all GUI elements.
            - Forces layout recalculation and update.
        """
        clear_layout(self.main_layout)
        self.create_gui()
        self.layout().invalidate()
        self.layout().activate()

    def go_to_box(self, idx: int):
        """
        Navigates to the specified Pokémon storage box and updates the GUI accordingly.

        Args:
            idx (int): The index of the box to navigate to.

        Side Effects:
            - Updates the current box index.
            - Triggers a full GUI refresh to display the selected box's contents.
        """
        self.current_box_idx = idx
        self.refresh_gui()

    def looparound_go_to_box(self, idx: int, max_idx: int):
        """
        Navigates to a box index with wrap-around behavior.

        If the provided index is less than 0, wraps around to the maximum index.
        If the index exceeds the maximum, wraps around to 0.
        Then updates the GUI to show the selected box.

        Args:
            idx (int): The target box index to navigate to.
            max_idx (int): The maximum valid box index.

        Side Effects:
            - Updates the current box index with wrapping.
            - Triggers a GUI refresh to display the selected box.
        """
        if idx < 0:
            idx = max_idx
        elif idx > max_idx:
            idx = 0
        self.go_to_box(idx)

    def adjust_pixmap_size(self, pixmap, max_width, max_height):
        """
        Scales a QPixmap to fit within the specified maximum width and height while maintaining aspect ratio.

        If the pixmap's width exceeds `max_width`, it is scaled down proportionally.
        Note: This implementation currently only scales based on width and does not consider `max_height`.

        Args:
            pixmap (QPixmap): The original pixmap to be resized.
            max_width (int): The maximum allowed width.
            max_height (int): The maximum allowed height (currently unused).

        Returns:
            QPixmap: The scaled pixmap, or the original if no scaling was needed.
        """
        original_width = pixmap.width()
        original_height = pixmap.height()

        if original_width > max_width:
            new_width = max_width
            new_height = (original_height * max_width) // original_width
            pixmap = pixmap.scaled(new_width, new_height)

        return pixmap

    def load_pokemon_data(self) -> list:
        """Reads the mypokemon.json file and loads Pokémon data into self.pokemon_list."""
        try:
            with open(mypokemon_path, "r", encoding="utf-8") as file:
                pokemon_list = json.load(file)
                for i, pokemon in enumerate(pokemon_list):
                    pokemon['original_index'] = i
                return pokemon_list
        except FileNotFoundError:
            self.logger.log("error","mypokemon.json file not found.")
        except json.JSONDecodeError:
            self.logger.log("error","mypokemon.json file not found.")

        return []

    def filter_pokemon_list(self, pokemon_list: list) -> list:
        """
        Filters a list of Pokémon dictionaries based on multiple UI-selected criteria.

        The filtering considers:
        - Search text matching Pokémon name (case-insensitive).
        - Selected Pokémon type from a dropdown.
        - Selected tier category from a dropdown.
        - Whether only favorites should be shown.
        - Selected generation range based on Pokémon ID.

        Args:
            pokemon_list (list): List of Pokémon dictionaries to filter. Each dictionary should
                contain keys like "name", "type", "tier", "is_favorite", and "id".

        Returns:
            list: A new list containing only Pokémon that match all the active filter criteria.
        """
        def filtering_func(pokemon: dict) -> bool:
            if self.search_edit is not None:
                if self.search_edit.text().lower() not in pokemon.get("name").lower():
                    return False

            if self.type_combo is not None:
                if self.type_combo.currentIndex() != 0 and self.type_combo.currentText() not in pokemon.get("type", ""):
                    return False

            if self.tier_combo is not None:
                if (
                    self.tier_combo.currentIndex() != 0
                    and pokemon.get("tier") is not None
                    and self.tier_combo.currentText() != pokemon.get("tier")
                ):
                    return False

            if self.filter_favorites is not None:
                if self.filter_favorites.isChecked() and not pokemon.get("is_favorite", False):
                    return False

            if self.filter_is_holding_item is not None:
                if self.filter_is_holding_item.isChecked() and not pokemon.get("held_item", False):
                    return False

            if self.filter_shiny is not None:
                if self.filter_shiny.isChecked() and not pokemon.get("shiny", False):
                    return False

            if self.generation_combo is not None:
                gen_idx = self.generation_combo.currentIndex()
                if gen_idx != 0 and (
                    (1 <= pokemon["id"] <= 151 and gen_idx != 1) or
                    (152 <= pokemon["id"] <= 251 and gen_idx != 2) or
                    (252 <= pokemon["id"] <= 386 and gen_idx != 3) or
                    (387 <= pokemon["id"] <= 493 and gen_idx != 4) or
                    (494 <= pokemon["id"] <= 649 and gen_idx != 5) or
                    (650 <= pokemon["id"] <= 721 and gen_idx != 6) or
                    (722 <= pokemon["id"] <= 809 and gen_idx != 7) or
                    (810 <= pokemon["id"] <= 898 and gen_idx != 8)
                ):
                    return False

            return True

        return list(filter(filtering_func, pokemon_list.copy()))

    def sort_pokemon_list(self, pokemon_list: list) -> list:
        reverse = self.desc_sort is not None and self.desc_sort.isChecked()

        sort_key_str = self.selected_sort_key.lower()
        if sort_key_str == "date":
            sort_key_str = "original_index"

        def sort_key(p):
            if sort_key_str == "name":
                name = p.get("name") or ""
                nickname = p.get("nickname") or ""
                return (name.lower(), nickname.lower())
            else:
                val = p.get(sort_key_str)
                if val is None:
                    return 0 if sort_key_str in ["id", "level", "original_index"] else ""
                return val

        return sorted(
            pokemon_list,
            reverse=reverse,
            key=sort_key
        )

    def on_sort_button_clicked(self, button):
        self.selected_sort_key = button.text()
        self.go_to_box(0)

    def show_actions_submenu(self, button: QPushButton, pokemon: dict[str, Any]):
        """
        Displays a context menu with actions related to a specific Pokémon.

        The menu includes:
        - A non-interactive title showing the Pokémon's nickname, name, gender symbol, and level.
        - An option to view detailed information about the Pokémon.
        - An option to select the Pokémon as the main Pokémon.
        - An option to toggle the Pokémon's favorite status.

        Args:
            button (QPushButton): The button widget where the menu will be displayed.
            pokemon (dict[str, Any]): A dictionary containing Pokémon data, expected to include keys
                like "name", "nickname", "gender", "level", and "is_favorite".

        Side Effects:
            - Displays a popup menu aligned below the specified button.
            - Connects menu actions to respective handlers in the parent class.
        """
        menu = QMenu(self)

        # QMenu doesn't have a "window name" property or the like. So let's emulate one.
        if pokemon.get("gender") == "M":
            gender_symbol = "♂"
        elif pokemon.get("gender") == "F":
            gender_symbol = "♀"
        else:
            gender_symbol = ""
        if pokemon.get("nickname"):
            title = f'{pokemon["nickname"]} ({pokemon["name"]}) {gender_symbol} - lvl {pokemon["level"]}'
        else:
            title = f'{pokemon["name"]} {gender_symbol} - lvl {pokemon["level"]}'
        title_action = QAction(title, menu)
        title_action.setEnabled(False)  # Disabled, so it can't be clicked
        menu.addAction(title_action)
        menu.addSeparator()

        pokemon_details_action = QAction("Pokémon details", self)
        main_pokemon_action = QAction("Pick as main Pokémon", self)
        make_favorite_action = QAction(
            "Unmake favorite" if pokemon.get("is_favorite", False) else "Make favorite"
            )
        give_held_item = QAction("Give a held item", self)

        # Connect actions to methods or lambda functions
        pokemon_details_action.triggered.connect(lambda: self.show_pokemon_details(pokemon))
        main_pokemon_action.triggered.connect(lambda: self.main_pokemon_function_callback(pokemon))
        make_favorite_action.triggered.connect(lambda: self.toggle_favorite(pokemon))
        give_held_item.triggered.connect(lambda: self.give_held_item(pokemon))

        menu.addAction(pokemon_details_action)
        menu.addAction(main_pokemon_action)
        menu.addAction(make_favorite_action)
        menu.addAction(give_held_item)
        if pokemon.get("held_item"):
            remove_held_item = QAction(f"Remove held item : {format_item_name(pokemon['held_item'])}", self)
            remove_held_item.triggered.connect(lambda: self.remove_held_item(pokemon))
            menu.addAction(remove_held_item)

        # Show the menu at the button's position, aligned below the button
        menu.exec(button.mapToGlobal(button.rect().topRight()))

    def show_pokemon_details(self, pokemon):
        """
        Displays detailed information about a specific Pokémon in the right-hand details panel.

        The method prepares detailed stats by merging base stats or stats with experience points,
        then updates the `self.details_layout` with a `PokemonCollectionDetails` layout.

        Args:
            pokemon (dict): A dictionary containing Pokémon data with expected keys such as:
                - 'name', 'level', 'id', 'ability', 'type', 'attacks', 'base_experience',
                'growth_rate', 'ev', 'iv', 'gender'
                - Optional keys include 'shiny', 'nickname', 'individual_id', 'pokemon_defeated',
                'everstone', 'captured_date', and 'xp'.

        Raises:
            ValueError: If neither 'base_stats' nor 'stats' are available in the Pokémon dictionary.
        """
        if pokemon.get('base_stats'):
            detail_stats = {**pokemon['base_stats'], "xp": pokemon.get("xp", 0)}
        elif pokemon.get('stats'):
            detail_stats = {**pokemon['stats'], "xp": pokemon.get("xp", 0)}
        else:
            raise ValueError("Could not get the stats information of the Pokémon")

        self.pokemon_details_layout = PokemonCollectionDetails(
            name=pokemon['name'],
            level=pokemon['level'],
            id=pokemon['id'],
            shiny=pokemon.get("shiny", False),
            ability=pokemon['ability'],
            type=pokemon['type'],
            detail_stats=detail_stats,
            attacks=pokemon['attacks'],
            base_experience=pokemon['base_experience'],
            growth_rate=pokemon['growth_rate'],
            ev=pokemon['ev'],
            iv=pokemon['iv'],
            gender=pokemon['gender'],
            nickname=pokemon.get('nickname'),
            individual_id=pokemon.get('individual_id'),
            pokemon_defeated=pokemon.get('pokemon_defeated', 0),
            everstone=pokemon.get('everstone', False),
            captured_date=pokemon.get('captured_date', 'Missing'),
            language=int(self.settings.get("misc.language")),
            gif_in_collection=self.gif_in_collection,
            remove_levelcap=self.settings.get("misc.remove_level_cap"),
            logger=self.logger,
            refresh_callback=self.refresh_gui
        )
        self.refresh_gui()

    def toggle_favorite(self, pokemon: dict[list, Any]):
        """
        Toggles the favorite status of a specific Pokémon in the saved Pokémon data.

        This method loads the current Pokémon list, finds the Pokémon by its unique individual ID,
        switches its "is_favorite" status, saves the updated list back to file, and refreshes the GUI.

        Args:
            pokemon (dict[list, Any]): A dictionary representing the Pokémon, expected to contain
                a unique "individual_id" key and a "name" key.

        Side Effects:
            - Updates the "is_favorite" status of the Pokémon in persistent storage.
            - Refreshes the GUI to reflect the change.
            - Logs an info message if the Pokémon is not found in the list.
        """
        pokemon_list = self.load_pokemon_data()
        for i in range(len(pokemon_list)):
            if pokemon_list[i].get("individual_id") == pokemon["individual_id"]:
                is_currently_favorite = pokemon_list[i].get("is_favorite", False)
                pokemon_list[i]["is_favorite"] = not is_currently_favorite

                with open(str(mypokemon_path), "w", encoding="utf-8") as json_file:
                    json.dump(pokemon_list, json_file, indent=2)

                self.refresh_gui()
                return

        if self.logger is not None:
            self.logger.log("info", f"Could not make/unmake {pokemon['name']} favorite")

    def give_held_item(self, pokemon: dict[list, Any]):
        """
        Opens a window to select and give a held item to the specified Pokémon.

        This function reads the available items from the item bag, filters out
        non-holdable items (items with a non-None "type"), and presents the user with a
        selection window. Once an item is selected, it is assigned to the Pokémon, a
        confirmation message is shown, and the GUI is refreshed to reflect the change.

        Args:
            pokemon (dict[list, Any]): A dictionary representing the Pokémon's data.

        Returns:
            None

        Side Effects:
            - Opens a modal `GiveItemWindow` for item selection.
            - Updates the Pokémon's held item via `PokemonObject.give_held_item`.
            - Logs and displays an info message using `ShowInfoLogger`.
            - Refreshes the GUI via `self.refresh_gui()`.
        """
        with open(itembag_path, "r", encoding="utf-8") as f:
            items_list = json.load(f)
        items_names = [item_data["item"] for item_data in items_list if item_data.get("type") is None]
        pokemon_obj = PokemonObject.from_dict(pokemon)

        def func(item_name: str):
            # small intermediary function. This allows me to display a confirmation message after giving the item and refresh the PC after giving the item.
            # Refreshing the PC after giving the item is important in order to update the pokemon information with the new held item
            pokemon_obj.give_held_item(item_name)
            self.logger.log_and_showinfo("info", f"{item_name} was given to {pokemon.get('name')}.")
            self.refresh_gui()

        give_item_window = GiveItemWindow(
            item_list=items_names,
            give_item_func=lambda item_name: func(item_name),
            logger=self.logger
        )
        give_item_window.exec()

    def remove_held_item(self, pokemon: dict[list, Any]):
        """
        Removes the held item from the specified Pokémon.

        Converts the Pokémon dictionary into a `PokemonObject`, removes the held item,
        logs the change, and refreshes the GUI. If the Pokémon does not have a held item,
        raises a `ValueError`.

        Args:
            pokemon (dict[list, Any]): A dictionary representing the Pokémon's data.

        Returns:
            None

        Raises:
            ValueError: If the Pokémon does not currently hold an item.

        Side Effects:
            - Updates the Pokémon's data to remove the held item.
            - Logs and displays an info message using `ShowInfoLogger`.
            - Refreshes the GUI via `self.refresh_gui()`.
        """
        pokemon_obj = PokemonObject.from_dict(pokemon)
        if pokemon.get('held_item') is None:
            raise ValueError("The pokemon does not hold an item.")
        pokemon_obj.remove_held_item()
        self.logger.log_and_showinfo("info", f"{format_item_name(pokemon['held_item'])} was removed from {pokemon.get('name')}.")

        # Refreshing the PC after giving the item is important in order to update the pokemon information without the held item
        self.refresh_gui()

    def ensure_data_integrity(self):
        """
        Iterates through all Pokémon to ensure they have required non-stat fields,
        adding default values if fields are missing. This handles data
        from older addon versions. Stat-related fields are ignored.
        """
        pokemon_list = self.load_pokemon_data()
        if not pokemon_list:
            return

        # --- QUICK CHECK ---
        # First, quickly determine if any migration is needed at all.
        default_keys = {
            "nickname", "gender", "ability", "type", "attacks", "base_experience",
            "growth_rate", "everstone", "shiny", "captured_date", "individual_id",
            "mega", "special_form", "xp", "friendship", "pokemon_defeated",
            "tier", "is_favorite", "held_item"
        }

        is_migration_needed = any(
            key not in pokemon
            for pokemon in pokemon_list
            if isinstance(pokemon, dict)
            for key in default_keys
        )

        if not is_migration_needed:
            return  # All Pokémon are up-to-date, exit early.

        # --- FULL MIGRATION (only if needed) ---
        needs_update = False
        default_values = {
            "nickname": "", "gender": "N", "ability": "Illuminate", "type": ["Normal"],
            "attacks": ["Struggle"], "base_experience": 0, "growth_rate": "medium",
            "everstone": False, "shiny": False, "captured_date": None,
            "individual_id": lambda p: str(uuid.uuid4()), "mega": False,
            "special_form": None, "xp": 0, "friendship": 0,
            "pokemon_defeated": 0, "tier": lambda p: get_tier_by_id(p.get("id", 0)) or "Normal",
            "is_favorite": False, "held_item": None
        }

        for i, pokemon in enumerate(pokemon_list):
            if not isinstance(pokemon, dict):
                continue

            for key, default_generator in default_values.items():
                if key not in pokemon:
                    needs_update = True
                    if callable(default_generator):
                        value = default_generator(pokemon)
                    else:
                        value = default_generator
                    pokemon_list[i][key] = value

        if needs_update:
            with open(str(mypokemon_path), "w", encoding="utf-8") as json_file:
                json.dump(pokemon_list, json_file, indent=2)

    def on_window_close(self):
        if self.pokemon_details_layout is not None:
            clear_layout(self.pokemon_details_layout)
            self.details_widget.setFixedSize(0, 0)
            self.pokemon_details_layout = None

    def closeEvent(self, event: QCloseEvent):
        self.on_window_close()
        event.accept()  # Accept the close event

    def reject(self):  # Called when pressing Escape
        self.on_window_close()
        super().reject()


class GiveItemWindow(QDialog):
    """
    Small window that opens up when the user gives an item to the Pokemon from a PC box
    """
    # Make it a class variable so it can be accessed from other classes
    NOT_YET_IMPLEMENTED_ITEMS = {
            "focus-sash",
            "focus-band",
            "white-herb",
            "mental-herb",
            "power-herb",
            "throat-spray",
            "weakness-policy",
        }

    def __init__(self, item_list: list[str], give_item_func: Callable, logger):
        super().__init__()
        self.setWindowTitle("Give an Item")
        self.resize(400, 400)

        # Outer layout for the dialog
        main_layout = QVBoxLayout(self)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        # Container widget inside scroll area
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        self.give_item_func = give_item_func
        self.logger = logger

        # Add item rows
        for item in item_list:
            row_layout = QHBoxLayout()

            item_label = QLabel(format_item_name(item))
            give_button = QPushButton(f"Give {format_item_name(item)}")
            give_button.clicked.connect(lambda clicked, i=item: self.expanded_give_item_func(i))
            if item in GiveItemWindow.NOT_YET_IMPLEMENTED_ITEMS or item.endswith("-berry") or item.endswith("-gem"):
                # NOTE (Axil): As time of writing, single use items are not yet implemented.
                # It seems to me that, actually, they are not even implemented in the Poke-engine. Although
                # I haven't dug too much.
                # Therefore, for now, and hopefully as a not too permanent temporary fix, I will prevent the
                # user from giving out single-use items.
                give_button.setToolTip("Single use held items are not yet implemented.")
                give_button.setEnabled(False)
                give_button.clicked.connect(
                    lambda clicked: self.logger.log_and_showinfo("info", "Single use held items are not yet implemented.")
                    )

            row_layout.addWidget(item_label)
            row_layout.addStretch()
            row_layout.addWidget(give_button)

            # Optional: separate rows with a line
            row_frame = QFrame()
            row_frame.setLayout(row_layout)
            scroll_layout.addWidget(row_frame)

        scroll_content.setLayout(scroll_layout)
        scroll.setWidget(scroll_content)

        # Add scroll area to main layout
        main_layout.addWidget(scroll)
        self.setLayout(main_layout)

    def expanded_give_item_func(self, item_name: str):
        # small intermediary function. This allows me to display a confirmation message after giving the item.
        self.give_item_func(item_name)
        self.close()
```
---
## 12. `src/Ankimon/functions/update_main_pokemon.py`

### Why this file is critical
Logic for syncing the active Pokémon

### Full Contents

```python
import json
from typing import Optional

from ..functions.pokedex_functions import search_pokedex, search_pokedex_by_id
from ..resources import mainpokemon_path
from ..pyobj.pokemon_obj import PokemonObject

# default values to fall back in case of load error
MAIN_POKEMON_DEFAULT = {
    "name": "RESTART ANKI",
    "gender": None,
    "level": 5,
    "id": None,
    "ability": None,
    "type": None,
    "base_stats": None,
    "xp": None,
    "ev": None,
    "iv": None,
    "attacks": None,
    "base_experience": None,
    "hp": 100,
    "growth_rate": None,
    "individual_id": None,
    "tier": None,
    "shiny": None,
    "captured_date": None
}


def update_main_pokemon(main_pokemon: Optional[PokemonObject] = None):
    """
    Updates or initializes the main Pokémon object using data from a JSON file.

    This function attempts to read the main Pokémon's stats from a JSON file
    located at `mainpokemon_path`. If the file exists and contains valid data,
    the given `main_pokemon` object is updated with those stats. If the file is
    missing, empty, or contains invalid JSON, a new `PokemonObject` is created
    using default values.

    Args:
        main_pokemon (Optional[PokemonObject]): An optional existing Pokémon object
            to update. If None, a new object is created using `MAIN_POKEMON_DEFAULT`.

    Returns:
        tuple:
            PokemonObject: The updated or newly created Pokémon object.
            bool: True if the file was empty or invalid (i.e., default was used),
                  False if the object was successfully updated with file data.
    """

    if main_pokemon is None:
        main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)

    mainpokemon_empty = True
    if mainpokemon_path.is_file():
        with open(mainpokemon_path, "r", encoding="utf-8") as mainpokemon_json:
            try:
                main_pokemon_data = json.load(mainpokemon_json)
                # if main pokemon is successfully loaded make empty false
                if main_pokemon_data:
                    mainpokemon_empty = False
                    pokemon_name = search_pokedex_by_id(main_pokemon_data[0]["id"])
                    main_pokemon_data[0]["base_stats"] = search_pokedex(pokemon_name, "baseStats")
                    del main_pokemon_data[0]["stats"]  # For legacy code, i.e. for when "stats" in the JSON actually meant "base_stat"
                    main_pokemon.update_stats(**main_pokemon_data[0])
                    save_main_pokemon(main_pokemon) # Save the updated main Pokémon data
                # if file does load or is empty use default value
                else:
                    main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)
                max_hp = main_pokemon.calculate_max_hp()
                main_pokemon.max_hp = max_hp
                if main_pokemon_data[0].get("current_hp", max_hp) > max_hp:
                    main_pokemon_data[0]["current_hp"] = max_hp
                if main_pokemon_data:
                    main_pokemon.hp = main_pokemon_data[0].get("current_hp", max_hp)
                return main_pokemon, mainpokemon_empty


            except Exception as e:
                main_pokemon = PokemonObject(**MAIN_POKEMON_DEFAULT)
                return main_pokemon, mainpokemon_empty
    else:
        return PokemonObject(**MAIN_POKEMON_DEFAULT), mainpokemon_empty

def save_main_pokemon(main_pokemon: PokemonObject):
    """
    Saves the main Pokémon object to the mainpokemon.json file.
    Args:
        main_pokemon (PokemonObject): The Pokémon object to save.
    """
    # If the object has a to_dict method, use it; otherwise, use __dict__
    if hasattr(main_pokemon, 'to_dict'):
        data = main_pokemon.to_dict()
    else:
        data = main_pokemon.__dict__
    # Write as a single-element list for compatibility
    with open(mainpokemon_path, "w", encoding="utf-8") as f:
        json.dump([data], f, indent=4)


```
---
## 13. `src/Ankimon/pyobj/data_handler.py`

### Why this file is critical
Persistence logic

### Full Contents

```python
import sys
import json
from ..resources import user_path
import os
import uuid
import datetime

new_values = {
    "everstone": False,
    "shiny": False,
    "mega": False,
    "special_form": None,
    "friendship": 0,
    "pokemon_defeated": 0,
    "ability": "No Ability",
    "individual_id": uuid.uuid4(),
    "nickname": "",
    "base_experience": 50,
    "current_hp": 50,
    "growth_rate": "medium-slow",
    "gender": "N",
    "type": ["Normal"],
    "attacks": ["tackle", "growl"],
    "id": 132,
    "captured_date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}

class DataHandler:
    def __init__(self):
        self.new_values = new_values
        self.path = user_path  # Store the provided path
        self.data = {}         # Store any potential errors or file read issues
        self.read_files()

    def read_files(self):
        # Specify the files to read
        files = ['mypokemon.json', 'mainpokemon.json', 'items.json', 'team.json', 'data.json', 'badges.json']

        # Loop through each file and attempt to read it from the specified path

        for file in files:
            file_path = os.path.join(self.path, file)  # Construct full file path
            attr_name = os.path.splitext(file)[0]      # Use the filename without extension as the attribute name

            # Create file with empty array if it doesn't exist
            if not os.path.exists(file_path):
                os.makedirs(os.path.dirname(file_path), exist_ok=True)  # Ensure directory exists
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, indent=2)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = json.load(f)

                    # Validate list structure
                    if attr_name in ['mypokemon', 'mainpokemon'] and isinstance(content, list):
                        valid_content = []
                        for entry in content:
                            if isinstance(entry, dict):
                                valid_content.append(entry)
                            else:
                                print(f"Skipping invalid entry in {file}: {entry}")
                        setattr(self, attr_name, valid_content)
                    else:
                        setattr(self, attr_name, content)
            except Exception as e:
                self.data[file] = f"Error reading {file}: {e}"

    def assign_unique_ids(self, pokemon_list):
        """
        Adds a unique 'individual_id' field to each Pokémon in the provided list,
        but only if an 'individual_id' is not already set.
        Ensures no duplicate IDs are assigned.
        """
        if not isinstance(pokemon_list, list):
            raise ValueError("Expected list of Pokémon dictionaries")

        unique_ids = set()
        for idx, entry in enumerate(pokemon_list):
            if not isinstance(entry, dict):
                print(f"Skipping invalid entry at index {idx} - not a dictionary")
                continue
        try:
            unique_ids = set(pokemon.get("individual_id") for pokemon in pokemon_list if "individual_id" in pokemon)

            for pokemon in pokemon_list:
                # Skip Pokémon that already have an individual_id
                if "individual_id" in pokemon and pokemon["individual_id"]:
                    unique_ids.add(pokemon["individual_id"])  # Ensure existing IDs are tracked
                    continue

                # Assign a new unique ID
                while True:
                    new_id = str(uuid.uuid4())
                    if new_id not in unique_ids:
                        pokemon["individual_id"] = new_id
                        unique_ids.add(new_id)
                        break
        except:
            print("Unique ID assignment failed")

    def assign_new_variables(self, pokemon_list):
        """
        Adds new fields to each Pokémon in the provided list.
        Sets their default values only if they're not already set.
        The new_values parameter should be a dictionary where the keys are the field names
        and the values are the default values.
        """
        for pokemon in pokemon_list:
            for field, default_value in self.new_values.items():
                if field not in pokemon:  # Check if the field is not already set
                    pokemon[field] = default_value

    def save_file(self, attr_name):
        """
        Save the updated content back to its respective JSON file.
        """
        if hasattr(self, attr_name):
            file_path = os.path.join(self.path, f"{attr_name}.json")
            try:
                with open(file_path, 'w') as f:
                    json.dump(getattr(self, attr_name), f, indent=2)
            except Exception as e:
                self.data[file_path] = f"Error saving {file_path}: {e}"
```
---
## 14. `src/Ankimon/pyobj/settings.py`

### Why this file is critical
Configuration management

### Full Contents

```python
import json
import os
from aqt import mw
from aqt.utils import showInfo
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)
from PyQt6.QtWidgets import QRadioButton, QHBoxLayout, QMainWindow, QScrollArea
from pathlib import Path
from ..resources import user_path

DEFAULT_CONFIG = {
    "battle.automatic_battle": 0,
    "battle.cards_per_round": 2,
    "battle.daily_average": 100,
    "battle.card_max_time": 60,
    "controls.pokemon_buttons": True,
    "controls.defeat_key": "5",
    "controls.catch_key": "6",
    "controls.key_for_opening_closing_ankimon": "Ctrl+Shift+P",
    "controls.allow_to_choose_moves": False,
    "gui.animate_time": True,
    "gui.gif_in_collection": True,
    "gui.styling_in_reviewer": True,
    "gui.hp_bar_config": True,
    "gui.pop_up_dialog_message_on_defeat": False,
    "gui.review_hp_bar_thickness": 2,
    "gui.reviewer_image_gif": False,
    "gui.reviewer_text_message_box": True,
    "gui.reviewer_text_message_box_time": 3,
    "gui.show_mainpkmn_in_reviewer": 1,
    "gui.view_main_front": True,
    "gui.xp_bar_config": True,
    "gui.xp_bar_location": 2,
    "audio.sound_effects": False,
    "audio.sounds": True,
    "audio.battle_sounds": False,
    "audio.volume": 0.5,
    "misc.gen1": True,
    "misc.gen2": True,
    "misc.gen3": True,
    "misc.gen4": True,
    "misc.gen5": True,
    "misc.gen6": True,
    "misc.gen7": True,
    "misc.gen8": True,
    "misc.gen9": False,
    "misc.remove_level_cap": False,
    "misc.language": 9,
    "misc.ssh": True,
    "misc.leaderboard": False,
    "misc.ankiweb_sync": False,
    "misc.YouShallNotPass_Ankimon_News": False,
    "misc.show_tip_on_startup": True,  # Added default for Tip of the Day
    "misc.discord_rich_presence": False,
    "misc.discord_rich_presence_text": 1,
    "misc.developer_mode": False,
    "trainer.name": "Ash",
    "trainer.sprite": "ash",
    "trainer.id": 0,
    "trainer.cash": 0,
    "trainer.level": 0,
    "trainer.xp": 0,
}


class Settings:
    def __init__(self):
        self.config = self.load_config()
        self.compute_gui_config()

    def get_description(self, key):
        return self.descriptions.get(key, "No description available.")

    def load_config(self):
        obfuscated_config_path = user_path / "config.obf"
        config = {}
        from ..pyobj.ankimon_sync import AnkimonDataSync  # To reuse deobfuscation logic

        sync_handler = AnkimonDataSync()  # Re-use the deobfuscation logic

        if obfuscated_config_path.is_file():
            try:
                with open(obfuscated_config_path, "r", encoding="utf-8") as f:
                    obfuscated_str = f.read()
                config = sync_handler._deobfuscate_data(obfuscated_str)
                # Migration logic for old keys (items, trainer.team, trainer.xp_share)
                # These keys are removed from the config dictionary after being processed.
                # This ensures config.obf only contains the 'config' section going forward.
                if "items" in config and isinstance(config["items"], list):
                    items_path = user_path / "items.json"
                    try:
                        with open(items_path, "w", encoding="utf-8") as f:
                            json.dump(config["items"], f, indent=4)
                    except Exception as e:
                        print(
                            f"Ankimon: Error migrating 'items' data during load_config: {e}"
                        )
                    del config["items"]

                if "trainer.team" in config:
                    del config["trainer.team"]

                # Type Coercion (from ankimon_sync.py)
                keys_to_coerce_to_int = [
                    "battle.automatic_battle",
                    "battle.daily_average",
                    "gui.reviewer_text_message_box_time",
                    "gui.xp_bar_location",
                    "misc.discord_rich_presence_text",
                ]
                for key in keys_to_coerce_to_int:
                    if key in config and isinstance(config[key], str):
                        try:
                            config[key] = int(config[key])
                        except ValueError:
                            print(
                                f"Ankimon: Warning: Could not convert '{config[key]}' for key '{key}' to int. Keeping as string."
                            )

            except Exception as e:
                print(
                    f"Ankimon: Error loading config from config.obf: {e}. Falling back to default config."
                )
                config = {}  # Fallback to default if error occurs

        modified = False

        # Ensure new settings are present in existing configurations
        for key in DEFAULT_CONFIG:
            if key not in config:
                modified = True
                config[key] = DEFAULT_CONFIG[key]

        if modified:
            self.save_config(config)  # Save modified config to config.obf

        return config

    def save_config(self, config):
        from ..pyobj.ankimon_sync import AnkimonDataSync  # To reuse obfuscation logic

        obfuscated_config_path = user_path / "config.obf"
        sync_handler = AnkimonDataSync()  # Re-use the obfuscation logic
        try:
            obfuscated_str = sync_handler._obfuscate_data(config)
            warning_message = "WARNING: This file contains important user data. Do not delete or modify this file. Deleting or modifying this file can lead to data loss in the Ankimon addon.\n---"
            file_content = warning_message + obfuscated_str
            with open(obfuscated_config_path, "w", encoding="utf-8") as f:
                f.write(file_content)
        except Exception as e:
            print(f"Ankimon: Could not save obfuscated config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save_config(self.config)
        self.load_config()

    def compute_gui_config(self):
        # Manage conditional GUI settings
        config = self.config
        sound_effects = config.get("audio.sound_effects", False)

        view_main_front = config.get("gui.view_main_front", True)
        reviewer_image_gif = config.get("gui.reviewer_image_gif", False)
        self.view_main_front = -1 if view_main_front and reviewer_image_gif else 1

        animate_time = config.get("gui.animate_time", False)
        self.animate_time = 0.8 if animate_time else 0

        xp_bar_location = config.get("gui.xp_bar_location", 0)
        xp_bar_config = config.get("gui.xp_bar_config", False)
        if xp_bar_config:
            if xp_bar_location == 1:
                self.xp_bar_location = "top"
                self.xp_bar_spacer = 0
            elif xp_bar_location == 2:
                self.xp_bar_location = "bottom"
                self.xp_bar_spacer = 20
        else:
            self.xp_bar_spacer = 0

        hp_bar_config = config.get("gui.hp_bar_config", True)
        if not hp_bar_config:
            self.hp_only_spacer = 15
            self.wild_hp_spacer = 65
        else:
            self.hp_only_spacer = 0
            self.wild_hp_spacer = 0

    def compute_special_variable(self, key):
        # Dynamically compute and return the requested GUI variable
        if key == "view_main_front":
            view_main_front = self.config.get("gui.view_main_front", True)
            reviewer_image_gif = self.config.get("gui.reviewer_image_gif", False)
            return -1 if view_main_front and reviewer_image_gif else 1

        elif key == "animate_time":
            animate_time = self.config.get("gui.animate_time", False)
            return 0.8 if animate_time else 0

        elif key == "xp_bar_location":
            xp_bar_config = self.config.get("gui.xp_bar_config", True)
            xp_bar_location = int(self.config.get("gui.xp_bar_location", 2))

            if xp_bar_config:
                if xp_bar_location == 1:
                    return "top"
                elif xp_bar_location == 2:
                    return "bottom"
            return None  # Default when XP bar is disabled

        elif key == "xp_bar_spacer":
            xp_bar_config = self.config.get("gui.xp_bar_config", False)
            xp_bar_location = self.config.get("gui.xp_bar_location", 0)

            if xp_bar_config:
                if xp_bar_location == 2:  # Bottom
                    return 20
                elif xp_bar_location == 1:  # Top
                    return 0
            return 0  # Default spacer

        elif key == "hp_only_spacer":
            hp_bar_config = self.config.get("gui.hp_bar_config", True)
            return 15 if not hp_bar_config else 0

        elif key == "wild_hp_spacer":
            hp_bar_config = self.config.get("gui.hp_bar_config", True)
            return 65 if not hp_bar_config else 0

        else:
            raise ValueError(f"Unknown key: {key}")
```
---
## 15. `src/Ankimon/pyobj/ankimon_sync.py`

### Why this file is critical
Data synchronization logic

### Full Contents

```python
# ankimon_sync.py - Improved Ankimon data sync system with subfolder approach
import base64
import filecmp
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Any

from aqt import mw, gui_hooks
from aqt.utils import showInfo, tooltip
from ..pyobj.error_handler import show_warning_with_traceback

from ..resources import user_path, addon_dir
from ..utils import close_anki

from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QTextEdit, QPushButton, QDialog, QHBoxLayout, QScrollArea, QWidget

class ImprovedPokemonDataSync(QDialog):
    """
    Improved Pokemon data sync dialog using the new AnkimonDataSync system.
    Provides better file comparison and uses Anki's media sync for reliable syncing.
    """

    def __init__(self, settings_obj, logger):
        super().__init__(mw)
        self.config = settings_obj
        self.logger = logger
        self.sync_handler = AnkimonDataSync()

        self.setup_ui()
        self.check_for_differences()

    def setup_ui(self):
        """Set up the user interface."""
        self.setWindowTitle("Ankimon Data Sync")
        self.setMinimumSize(800, 600)

        # Main layout
        main_layout = QVBoxLayout()

        # Header message
        header_text = (
            "Sync your Pokemon data between devices using AnkiWeb.\n"
            "Choose to export your local data to AnkiWeb or import data from AnkiWeb to your device."
        )
        self.header_label = QLabel(header_text)
        main_layout.addWidget(self.header_label)

        # Button layout
        button_layout = QHBoxLayout()

        self.export_button = QPushButton("Export Local Data to AnkiWeb")
        self.import_button = QPushButton("Import Data from AnkiWeb")
        self.refresh_button = QPushButton("Refresh Comparison")

        self.export_button.clicked.connect(self.export_to_ankiweb)
        self.import_button.clicked.connect(self.import_from_ankiweb)
        self.refresh_button.clicked.connect(self.check_for_differences)

        button_layout.addWidget(self.export_button)
        button_layout.addWidget(self.import_button)
        button_layout.addWidget(self.refresh_button)

        main_layout.addLayout(button_layout)

        # Comparison area
        comparison_layout = QHBoxLayout()

        # Local data area
        local_widget = QWidget()
        local_layout = QVBoxLayout(local_widget)
        local_layout.addWidget(QLabel("Local Data:"))

        self.local_text_area = QTextEdit()
        self.local_text_area.setReadOnly(True)
        self.local_text_area.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        local_layout.addWidget(self.local_text_area)

        # AnkiWeb data area
        web_widget = QWidget()
        web_layout = QVBoxLayout(web_widget)
        web_layout.addWidget(QLabel("AnkiWeb Data:"))

        self.web_text_area = QTextEdit()
        self.web_text_area.setReadOnly(True)
        self.web_text_area.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        web_layout.addWidget(self.web_text_area)

        comparison_layout.addWidget(local_widget)
        comparison_layout.addWidget(web_widget)

        main_layout.addLayout(comparison_layout)

        self.setLayout(main_layout)

    def check_for_differences(self):
        """Check for differences between local and AnkiWeb data."""
        try:
            differences = self.sync_handler.get_file_differences()

            if not differences:
                self.header_label.setText(
                    "Ankimon Data Sync:\n"
                    "✅ All data is synchronized. No differences found."
                )
                self.local_text_area.setPlainText("No differences found.")
                self.web_text_area.setPlainText("No differences found.")
                self.export_button.setEnabled(False)
                self.import_button.setEnabled(False)
                return

            self.header_label.setText(
                f"⚠️ Found differences in {len(differences)} file(s). Please choose sync direction:\n"
            )
            self.export_button.setEnabled(True)
            self.import_button.setEnabled(True)

            self._display_differences(differences)
            self.show()

        except Exception as e:
            self.logger.log("error", f"Failed to check for differences: {str(e)}")
            show_warning_with_traceback(parent=self, exception=e, message="Error checking for differences")

    def _display_differences(self, differences: Dict[str, Dict]):
        """Display improved JSON differences, showing only what changed per file with specific key differences."""
        import json
        from typing import Any, Dict, List, Tuple, Set

        def format_value(value: Any) -> str:
            """Format a value for display."""
            if isinstance(value, str):
                return f'"{value}"'
            elif isinstance(value, (int, float)):
                return str(value)
            elif isinstance(value, bool):
                return str(value).lower()
            elif isinstance(value, list):
                if len(value) <= 3:
                    return f"[{', '.join(format_value(v) for v in value)}]"
                else:
                    return f"[{', '.join(format_value(v) for v in value[:2])}, ... +{len(value)-2} more]"
            elif isinstance(value, dict):
                if len(value) <= 2:
                    items = [f"{k}: {format_value(v)}" for k, v in value.items()]
                    return "{" + ", ".join(items) + "}"
                else:
                    items = list(value.items())[:2]
                    formatted = [f"{k}: {format_value(v)}" for k, v in items]
                    return "{" + ", ".join(formatted) + f", ... +{len(value)-2} more" + "}"
            else:
                return str(value)[:50] + ("..." if len(str(value)) > 50 else "")

        def compare_dicts(local_dict: Dict, remote_dict: Dict, path: str = "") -> Tuple[List[str], List[str]]:
            """Compare two dictionaries and return differences with specific key details."""
            local_lines = []
            remote_lines = []

            all_keys = set(local_dict.keys()) | set(remote_dict.keys())

            for key in sorted(all_keys):
                current_path = f"{path}.{key}" if path else key
                local_val = local_dict.get(key, "<MISSING>")
                remote_val = remote_dict.get(key, "<MISSING>")

                if local_val == "<MISSING>":
                    local_lines.append(f"  {current_path}: <MISSING>")
                    remote_lines.append(f"+ {current_path}: {format_value(remote_val)}")
                elif remote_val == "<MISSING>":
                    local_lines.append(f"- {current_path}: {format_value(local_val)}")
                    remote_lines.append(f"  {current_path}: <MISSING>")
                elif local_val != remote_val:
                    # Show the actual different values
                    local_lines.append(f"- {current_path}: {format_value(local_val)}")
                    remote_lines.append(f"+ {current_path}: {format_value(remote_val)}")

                    # If both are dicts, recursively compare them (but don't double-nest)
                    if isinstance(local_val, dict) and isinstance(remote_val, dict) and not path:
                        sub_local, sub_remote = compare_dicts(local_val, remote_val, current_path)
                        local_lines.extend([f"    {line}" for line in sub_local])
                        remote_lines.extend([f"    {line}" for line in sub_remote])
                # Don't show unchanged values

            return local_lines, remote_lines

        def get_pokemon_identifier(pokemon: Dict) -> str:
            """Get a unique identifier for a Pokemon."""
            # Try individual_id first (most unique)
            if 'individual_id' in pokemon and pokemon['individual_id']:
                return pokemon['individual_id']

            # Fall back to a combination of name, level, and captured_date
            name = pokemon.get('name', 'Unknown')
            level = pokemon.get('level', 0)
            captured = pokemon.get('captured_date', '')

            return f"{name}_L{level}_{captured}"

        def compare_pokemon_lists(local_list: List[Dict], remote_list: List[Dict]) -> Tuple[List[str], List[str]]:
            """Compare two lists of Pokemon with detailed differences."""
            local_lines = []
            remote_lines = []

            # Index by unique identifier
            local_map = {}
            remote_map = {}

            for i, pokemon in enumerate(local_list):
                if isinstance(pokemon, dict):
                    identifier = get_pokemon_identifier(pokemon)
                    local_map[identifier] = pokemon
                else:
                    local_map[f"invalid_pokemon_{i}"] = pokemon

            for i, pokemon in enumerate(remote_list):
                if isinstance(pokemon, dict):
                    identifier = get_pokemon_identifier(pokemon)
                    remote_map[identifier] = pokemon
                else:
                    remote_map[f"invalid_pokemon_{i}"] = pokemon

            all_identifiers = set(local_map.keys()) | set(remote_map.keys())

            local_lines.append(f"Total Pokemon: {len(local_list)}")
            remote_lines.append(f"Total Pokemon: {len(remote_list)}")

            changes_found = False

            for identifier in sorted(all_identifiers):
                local_pokemon = local_map.get(identifier)
                remote_pokemon = remote_map.get(identifier)

                # Get display name
                if local_pokemon and isinstance(local_pokemon, dict):
                    display_name = f"{local_pokemon.get('name', 'Unknown')} (L{local_pokemon.get('level', '?')})"
                elif remote_pokemon and isinstance(remote_pokemon, dict):
                    display_name = f"{remote_pokemon.get('name', 'Unknown')} (L{remote_pokemon.get('level', '?')})"
                else:
                    display_name = identifier[:20] + "..." if len(identifier) > 20 else identifier

                if local_pokemon is None:
                    remote_lines.append(f"+ {display_name}: (new Pokemon)")
                    local_lines.append(f"  {display_name}: <MISSING>")
                    changes_found = True
                elif remote_pokemon is None:
                    local_lines.append(f"- {display_name}: (removed Pokemon)")
                    remote_lines.append(f"  {display_name}: <MISSING>")
                    changes_found = True
                elif local_pokemon != remote_pokemon:
                    # Show what changed in this Pokemon
                    if isinstance(local_pokemon, dict) and isinstance(remote_pokemon, dict):
                        local_sub, remote_sub = compare_dicts(local_pokemon, remote_pokemon)
                        if local_sub or remote_sub:
                            local_lines.append(f"~ {display_name}: (modified)")
                            remote_lines.append(f"~ {display_name}: (modified)")

                            # Show specific field differences
                            max_lines = max(len(local_sub), len(remote_sub))
                            local_sub.extend(["" ] * (max_lines - len(local_sub)))
                            remote_sub.extend(["" ] * (max_lines - len(remote_sub)))

                            for l_line, r_line in zip(local_sub, remote_sub):
                                local_lines.append(f"    {l_line}")
                                remote_lines.append(f"    {r_line}")

                            changes_found = True
                    else:
                        # Non-dict Pokemon (shouldn't happen, but handle it)
                        local_lines.append(f"- {display_name}: {format_value(local_pokemon)}")
                        remote_lines.append(f"+ {display_name}: {format_value(remote_pokemon)}")
                        changes_found = True

            if not changes_found:
                local_lines = ["No Pokemon differences detected"]
                remote_lines = ["No Pokemon differences detected"]

            return local_lines, remote_lines

        def compare_item_lists(local_list: List[Dict], remote_list: List[Dict]) -> Tuple[List[str], List[str]]:
            """Compare two lists of items with detailed differences."""
            local_lines = []
            remote_lines = []

            # Index by item name
            local_map = {item.get('item', f"item_{i}"): item for i, item in enumerate(local_list) if isinstance(item, dict)}
            remote_map = {item.get('item', f"item_{i}"): item for i, item in enumerate(remote_list) if isinstance(item, dict)}

            all_keys = set(local_map.keys()) | set(remote_map.keys())

            local_lines.append(f"Total items: {len(local_list)}")
            remote_lines.append(f"Total items: {len(remote_list)}")

            changes_found = False

            for key in sorted(all_keys):
                local_item = local_map.get(key)
                remote_item = remote_map.get(key)

                if local_item is None:
                    remote_lines.append(f"+ {key}: {remote_item.get('quantity', '?')}")
                    local_lines.append(f"  {key}: <MISSING>")
                    changes_found = True
                elif remote_item is None:
                    local_lines.append(f"- {key}: {local_item.get('quantity', '?')}")
                    remote_lines.append(f"  {key}: <MISSING>")
                    changes_found = True
                elif local_item != remote_item:
                    # Most likely quantity changed
                    local_qty = local_item.get('quantity', '?')
                    remote_qty = remote_item.get('quantity', '?')

                    if local_qty != remote_qty:
                        local_lines.append(f"- {key}: {local_qty}")
                        remote_lines.append(f"+ {key}: {remote_qty}")
                        changes_found = True
                    else:
                        # Some other field changed, show full comparison
                        local_sub, remote_sub = compare_dicts(local_item, remote_item)
                        if local_sub or remote_sub:
                            local_lines.append(f"~ {key}: (other changes)")
                            remote_lines.append(f"~ {key}: (other changes)")

                            for l_line, r_line in zip(local_sub, remote_sub):
                                local_lines.append(f"    {l_line}")
                                remote_lines.append(f"    {r_line}")

                            changes_found = True

            if not changes_found:
                local_lines = ["No item differences detected"]
                remote_lines = ["No item differences detected"]

            return local_lines, remote_lines

        def compare_simple_lists(local_list: List, remote_list: List) -> Tuple[List[str], List[str]]:
            """Compare two simple lists with specific differences."""
            local_set = set(str(item) for item in local_list)
            remote_set = set(str(item) for item in remote_list)

            local_lines = []
            remote_lines = []

            # Items only in local
            only_local = local_set - remote_set
            for item in sorted(only_local):
                local_lines.append(f"- {item}")
                remote_lines.append("  <removed>")

            # Items only in remote
            only_remote = remote_set - local_set
            for item in sorted(only_remote):
                local_lines.append("  <added>")
                remote_lines.append(f"+ {item}")

            # Show counts for context
            if only_local or only_remote:
                local_lines.insert(0, f"Total items: {len(local_list)}")
                remote_lines.insert(0, f"Total items: {len(remote_list)}")
            else:
                local_lines = ["No list differences detected"]
                remote_lines = ["No list differences detected"]

            return local_lines, remote_lines

        def detect_structure_and_compare(local_data: Any, remote_data: Any, filename: str) -> Tuple[List[str], List[str]]:
            """Detect the data structure and apply appropriate comparison."""

            # Handle None/missing data cases
            if local_data is None and remote_data is None:
                return ["Both files are empty"], ["Both files are empty"]
            elif local_data is None:
                return ["Local file is empty"], [f"Remote has data: {type(remote_data).__name__}"]
            elif remote_data is None:
                return [f"Local has data: {type(local_data).__name__}"], ["Remote file is empty"]

            # Both are lists
            if isinstance(local_data, list) and isinstance(remote_data, list):
                # Special handling for Pokemon files
                if filename in ['mypokemon.json', 'mainpokemon.json']:
                    return compare_pokemon_lists(local_data, remote_data)

                # Special handling for items
                elif filename == 'items.json':
                    if (local_data and isinstance(local_data[0], dict) and 'item' in local_data[0]) or \
                    (remote_data and isinstance(remote_data[0], dict) and 'item' in remote_data[0]):
                        return compare_item_lists(local_data, remote_data)

                # Fall back to simple list comparison
                return compare_simple_lists(local_data, remote_data)

            # Both are dictionaries
            elif isinstance(local_data, dict) and isinstance(remote_data, dict):
                return compare_dicts(local_data, remote_data)

            # Different types or simple values
            else:
                local_lines = [f"Type: {type(local_data).__name__}"]
                remote_lines = [f"Type: {type(remote_data).__name__}"]

                if local_data is not None:
                    local_lines.append(f"- Value: {format_value(local_data)}")
                else:
                    local_lines.append("- Value: <no data>")

                if remote_data is not None:
                    remote_lines.append(f"+ Value: {format_value(remote_data)}")
                else:
                    remote_lines.append("+ Value: <no data>")

                return local_lines, remote_lines

        # Main display logic
        local_content = []
        web_content = []

        for filename, diff_info in differences.items():
            local_content.append(f"=== {filename} ===")
            web_content.append(f"=== {filename} ===")

            if diff_info.get('error'):
                error_msg = f"❌ Error: {diff_info['error']}"
                local_content.append(error_msg)
                web_content.append(error_msg)
                local_content.append("")
                web_content.append("")
                continue

            local_exists = diff_info.get('local_exists', False)
            media_exists = diff_info.get('media_exists', False)

            # Show file existence status
            local_content.append(f"Local file exists: {local_exists}")
            web_content.append(f"AnkiWeb file exists: {media_exists}")

            if filename.endswith(('.json', '.obf')):
                local_data = diff_info.get('local_data')
                media_data = diff_info.get('media_data')

                # Use smart comparison
                local_lines, remote_lines = detect_structure_and_compare(local_data, media_data, filename)

                if local_lines or remote_lines:
                    local_content.append("Differences:")
                    web_content.append("Differences:")

                    # Pad the shorter list to align output
                    max_lines = max(len(local_lines), len(remote_lines))
                    local_lines.extend(["" ] * (max_lines - len(local_lines)))
                    remote_lines.extend(["" ] * (max_lines - len(remote_lines)))

                    local_content.extend(local_lines)
                    web_content.extend(remote_lines)
                else:
                    local_content.append("No differences detected")
                    web_content.append("No differences detected")
            else:
                local_content.append("(Binary/Non-JSON file - cannot show detailed diff)")
                web_content.append("(Binary/Non-JSON file - cannot show detailed diff)")

            local_content.append("")
            web_content.append("")

        self.local_text_area.setPlainText("\n".join(local_content))
        self.web_text_area.setPlainText("\n".join(web_content))

    def _format_json_data(self, data: Any, filename: str) -> List[str]:
        """Format JSON data for display, showing key differences."""
        lines = []

        if filename in ['mypokemon.json', 'mainpokemon.json']:
            # Special handling for Pokemon data
            if isinstance(data, list):
                lines.append(f"Pokemon count: {len(data)}")
                for i, pokemon in enumerate(data[:3]):  # Show first 3
                    if isinstance(pokemon, dict):
                        lines.extend(self._format_pokemon_data(pokemon, i))
                if len(data) > 3:
                    lines.append(f"... and {len(data) - 3} more Pokemon")
            else:
                lines.append("Invalid Pokemon data format")
        else:
            # Generic JSON formatting
            try:
                if isinstance(data, dict):
                    lines.append(f"Keys: {list(data.keys())}")
                    for key, value in list(data.items())[:5]:  # Show first 5 items
                        if isinstance(value, (str, int, float, bool)):
                            lines.append(f"  {key}: {value}")
                        else:
                            lines.append(f"  {key}: {type(value).__name__}")
                elif isinstance(data, list):
                    lines.append(f"Array with {len(data)} items")
                    for i, item in enumerate(data[:3]):
                        lines.append(f"  [{i}]: {type(item).__name__}")
                else:
                    lines.append(str(data)[:100] + "..." if len(str(data)) > 100 else str(data))
            except Exception as e:
                lines.append(f"Error formatting data: {str(e)}")

        return lines

    def _format_pokemon_data(self, pokemon: Dict, index: int) -> List[str]:
        """Format Pokemon data for display showing all relevant fields."""
        lines = [f"Pokemon {index + 1}:"]

        # Core identification
        if 'name' in pokemon:
            lines.append(f"  Name: {pokemon['name']}")
        if 'individual_id' in pokemon:
            lines.append(f"  ID: {pokemon['individual_id'][:8]}...")
        if 'level' in pokemon:
            lines.append(f"  Level: {pokemon['level']}")

        # Stats and characteristics
        important_fields = [
            'gender', 'ability', 'type', 'current_hp', 'xp', 'friendship',
            'pokemon_defeated', 'shiny', 'tier', 'everstone', 'captured_date'
        ]

        for field in important_fields:
            if field in pokemon:
                value = pokemon[field]
                if isinstance(value, list):
                    lines.append(f"  {field.capitalize()}: {', '.join(map(str, value))}")
                else:
                    lines.append(f"  {field.capitalize()}: {value}")

        # Complex fields summary
        if 'stats' in pokemon and isinstance(pokemon['stats'], dict):
            lines.append(f"  Stats: {len(pokemon['stats'])} stat values")
        if 'ev' in pokemon and isinstance(pokemon['ev'], dict):
            ev_total = sum(pokemon['ev'].values()) if pokemon['ev'] else 0
            lines.append(f"  EVs: {ev_total} total")
        if 'iv' in pokemon and isinstance(pokemon['iv'], dict):
            iv_avg = sum(pokemon['iv'].values()) / len(pokemon['iv']) if pokemon['iv'] else 0
            lines.append(f"  IVs: {iv_avg:.1f} average")
        if 'attacks' in pokemon and isinstance(pokemon['attacks'], list):
            lines.append(f"  Moves: {len(pokemon['attacks'])} moves")

        return lines

    def export_to_ankiweb(self):
        """Export local data to AnkiWeb."""
        try:
            success = self.sync_handler.force_sync_to_media()
            if success:
                # Enable automatic sync after successful manual sync
                from .ankimon_sync import enable_automatic_sync
                enable_automatic_sync()

                tooltip("Data exported to AnkiWeb successfully! Automatic sync is now enabled.")
                self.close()
            else:
                raise Exception("Failed to export data to AnkiWeb.")
        except Exception as e:
            self.logger.log("error", f"Failed to export to AnkiWeb: {str(e)}")
            show_warning_with_traceback(parent=self, exception=e, message="Error exporting to AnkiWeb")

    def import_from_ankiweb(self):
        """Import data from AnkiWeb to local storage."""
        try:
            success = self.sync_handler.force_sync_from_media()
            if success:
                # Enable automatic sync after successful manual sync
                from .ankimon_sync import enable_automatic_sync
                enable_automatic_sync()

                tooltip("Data imported from AnkiWeb successfully! Automatic sync is now enabled.")
                self.close()
                close_anki()
            else:
                raise Exception("Failed to import data from AnkiWeb.")
        except Exception as e:
            self.logger.log("error", f"Failed to import from AnkiWeb: {str(e)}")
            show_warning_with_traceback(parent=self, exception=e, message="Error importing from AnkiWeb")

    def auto_sync_on_close(self):
        """Automatically sync data when Anki closes."""
        try:
            synced_files = self.sync_handler.save_configs()
            if synced_files:
                tooltip(f"Synced {len(synced_files)} Ankimon files to AnkiWeb")
        except Exception as e:
            self.logger.log("error", f"Auto-sync failed: {str(e)}")

class AnkimonDataSync:
    """
    Handles syncing of Ankimon data files through Anki's media folder using a subfolder approach.
    This leverages Anki's built-in media sync to AnkiWeb while keeping files organized.
    """

    _OBFUSCATION_KEY = "H0tP-!s-N0t-4-C@tG!rL_v2"

    # Files to sync and their locations
    SYNC_FILES = {
        "mypokemon.json": "user_files",
        "mainpokemon.json": "user_files",
        "badges.json": "user_files",
        "items.json": "user_files",
        "teams.json": "user_files",
        "data.json": "user_files",
        "todays_shop.json": "user_files",
        "config.obf": "user_files"
    }

    def __init__(self, addon_name: str = None):
        """Initialize with addon name for folder naming."""
        self.addon_name = addon_name or self._get_addon_name()
        self.addon_path = addon_dir
        self.user_files_path = user_path

        # Initialize paths as None - will be set when first accessed
        self._media_path = None
        self._media_sync_path = None
        self._sync_folder_name = None

    def _get_addon_name(self) -> str:
        """Get the addon name from the current addon folder."""
        try:
            current_file = Path(__file__)
            addon_dir = current_file.parents[2]  # Go up to addon root
            return addon_dir.name
        except:
            return "ankimon"  # fallback

    def _ensure_paths_initialized(self):
        """Ensure media paths are initialized. Call this before using any media path."""
        if self._media_path is None:
            profile_folder = mw.pm.profileFolder()
            if profile_folder is None:
                raise RuntimeError("No Anki profile loaded. Cannot initialize sync paths.")

            self._media_path = Path(profile_folder) / "collection.media"
            self._sync_folder_name = "Ankimon"
            self._media_sync_path = self._media_path

    @property
    def media_path(self) -> Path:
        """Get media path, initializing if needed."""
        self._ensure_paths_initialized()
        return self._media_path

    @property
    def media_sync_path(self) -> Path:
        """Get media sync path, initializing if needed."""
        self._ensure_paths_initialized()
        return self._media_sync_path

    @property
    def sync_folder_name(self) -> str:
        """Get sync folder name, initializing if needed."""
        self._ensure_paths_initialized()
        return self._sync_folder_name

    def _get_source_path(self, filename: str) -> Path:
        """Get the source path for a file based on its location."""
        location = self.SYNC_FILES.get(filename)
        if location == "addon_root" or filename == "meta.json":
            return self.addon_path / filename
        elif location == "user_files":
            return self.user_files_path / filename
        else:
            raise ValueError(f"Unknown location for file: {filename}")

    def _get_media_path(self, filename: str) -> Path:
        """Get the media subfolder path for a synced file."""
        return self.media_sync_path / filename

    def _get_legacy_media_path(self, filename: str) -> Path:
        """Get the old media folder path for migration from old format."""
        return self.media_path / f"_{self.addon_name}_{filename}"

    def _ensure_sync_folder_exists(self):
        """Ensure the sync subfolder exists in media directory."""
        try:
            self.media_sync_path.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            show_warning_with_traceback(parent=mw, exception=e, message="Failed to create sync folder")
            return False

    def _migrate_legacy_files(self) -> List[str]:
        """Migrate files from old flat structure to subfolder structure."""
        migrated_files = []

        for filename in self.SYNC_FILES.keys():
            legacy_path = self._get_legacy_media_path(filename)
            new_path = self._get_media_path(filename)

            # If legacy file exists and new file doesn't, migrate it
            if legacy_path.is_file() and not new_path.is_file():
                try:
                    if self._ensure_sync_folder_exists():
                        shutil.copy2(legacy_path, new_path)
                        os.remove(legacy_path)  # Remove old file
                        migrated_files.append(filename)
                except Exception as e:
                    show_warning_with_traceback(parent=mw, exception=e, message=f"Failed to migrate {filename}")

        return migrated_files

    def _obfuscate_data(self, data: dict) -> str:
        """Obfuscates dictionary data into a string."""
        json_str = json.dumps(data)
        obfuscated_bytes = bytearray()
        key_bytes = self._OBFUSCATION_KEY.encode('utf-8')
        for i, byte in enumerate(json_str.encode('utf-8')):
            obfuscated_bytes.append(byte ^ key_bytes[i % len(key_bytes)])
        return base64.b64encode(obfuscated_bytes).decode('utf-8')

    def _deobfuscate_data(self, obfuscated_str: str) -> dict:
        """De-obfuscates string back into a dictionary."""
        new_separator = "---DATA_START---"
        old_separator = "\n---"

        if new_separator in obfuscated_str:
            parts = obfuscated_str.split(new_separator)
            obfuscated_data = parts[1]
        elif old_separator in obfuscated_str:
            parts = obfuscated_str.split(old_separator)
            obfuscated_data = parts[1]
        else:
            obfuscated_data = obfuscated_str # Fallback for old format

        obfuscated_bytes = base64.b64decode(obfuscated_data)
        deobfuscated_bytes = bytearray()
        key_bytes = self._OBFUSCATION_KEY.encode('utf-8')
        for i, byte in enumerate(obfuscated_bytes):
            deobfuscated_bytes.append(byte ^ key_bytes[i % len(key_bytes)])
        return json.loads(deobfuscated_bytes.decode('utf-8'))





    def save_configs(self) -> List[str]:
        """
        Save configs from addon folder to media subfolder to trigger AnkiWeb sync.
        Returns list of files that were synced.
        """
        try:
            # First, migrate any legacy files
            migrated_files = self._migrate_legacy_files()
            if migrated_files:
                showInfo(f"Migrated {len(migrated_files)} files to new subfolder structure")

            # Ensure sync folder exists
            if not self._ensure_sync_folder_exists():
                return []

            synced_files = []

            for filename in self.SYNC_FILES.keys():
                try:
                    source_file = self._get_source_path(filename)
                    dest_file = self._get_media_path(filename)

                    # Skip if source file doesn't exist
                    if not source_file.is_file():
                        continue

                    # Copy if destination doesn't exist or files differ
                    if not dest_file.is_file():
                        shutil.copy2(source_file, dest_file)
                        synced_files.append(filename)
                    elif not filecmp.cmp(source_file, dest_file, shallow=False):
                        # Remove old file and copy new one to trigger sync
                        os.remove(dest_file)
                        shutil.copy2(source_file, dest_file)
                        synced_files.append(filename)

                except Exception as e:
                    show_warning_with_traceback(parent=mw, exception=e, message=f"Failed to sync {filename}")
                    continue

            return synced_files
        except RuntimeError as e:
            # Profile not loaded yet
            return []

    def read_configs(self, media_sync_status: bool = False) -> List[str]:
        """
        Read configs from media subfolder and copy to addon folder.
        Returns list of files that were updated.
        """
        if media_sync_status:
            return []  # Don't read while sync is in progress

        try:
            # Check for legacy files first
            migrated_files = self._migrate_legacy_files()

            updated_files = []

            for filename in self.SYNC_FILES.keys():
                try:
                    source_file = self._get_source_path(filename)
                    media_file = self._get_media_path(filename)

                    # Skip if media file doesn't exist
                    if not media_file.is_file():
                        continue

                    # Ensure source directory exists
                    source_file.parent.mkdir(parents=True, exist_ok=True)

                    # Copy if source doesn't exist or files differ
                    if not source_file.is_file() or not filecmp.cmp(source_file, media_file, shallow=False):
                        shutil.copy2(media_file, source_file)
                        updated_files.append(filename)

                except Exception as e:
                    show_warning_with_traceback(parent=mw, exception=e, message=f"Failed to read {filename}")
                    continue

            return updated_files
        except RuntimeError as e:
            # Profile not loaded yet
            return []

    def get_file_differences(self) -> Dict[str, Dict]:
        """
        Compare local files with media files and return differences.
        Returns dict with file differences for UI display.
        """
        try:
            # Migrate legacy files first
            self._migrate_legacy_files()

            differences = {}

            for filename in self.SYNC_FILES.keys():
                source_file = self._get_source_path(filename)
                media_file = self._get_media_path(filename)

                # Skip if neither file exists
                if not source_file.is_file() and not media_file.is_file():
                    continue

                file_diff = {
                    'local_exists': source_file.is_file(),
                    'media_exists': media_file.is_file(),
                    'files_differ': False,
                    'local_data': None,
                    'media_data': None
                }

                if filename.endswith('.obf'):
                    try:
                        if file_diff['local_exists']:
                            with open(source_file, 'r', encoding='utf-8') as f:
                                obfuscated_local_data = f.read()
                            file_diff['local_data'] = self._deobfuscate_data(obfuscated_local_data)

                        if file_diff['media_exists']:
                            with open(media_file, 'r', encoding='utf-8') as f:
                                obfuscated_media_data = f.read()
                            file_diff['media_data'] = self._deobfuscate_data(obfuscated_media_data)

                        file_diff['files_differ'] = file_diff['local_data'] != file_diff['media_data']
                    except Exception as e:
                        file_diff['error'] = f"Error deobfuscating file: {str(e)}"

                # Load and compare JSON data if both exist
                elif file_diff['local_exists'] and file_diff['media_exists']:
                    try:
                        with open(source_file, 'r', encoding='utf-8') as f:
                            file_diff['local_data'] = json.load(f)
                        with open(media_file, 'r', encoding='utf-8') as f:
                            file_diff['media_data'] = json.load(f)

                        # First, compare the loaded data. This is the most reliable check.
                        if file_diff['local_data'] != file_diff['media_data']:
                            file_diff['files_differ'] = True
                        else:
                            # If data is semantically the same, we don't need to check further.
                            file_diff['files_differ'] = False

                    except (json.JSONDecodeError, Exception) as e:
                        # If we can't parse the JSON, we can't compare data.
                        # Fall back to the binary file comparison.
                        file_diff['error'] = f"Could not parse JSON, falling back to binary comparison: {e}"
                        file_diff['files_differ'] = not filecmp.cmp(source_file, media_file, shallow=False)

                elif file_diff['local_exists']:
                    try:
                        with open(source_file, 'r', encoding='utf-8') as f:
                            file_diff['local_data'] = json.load(f)
                        file_diff['files_differ'] = True
                    except:
                        pass
                elif file_diff['media_exists']:
                    try:
                        with open(media_file, 'r', encoding='utf-8') as f:
                            file_diff['media_data'] = json.load(f)
                        file_diff['files_differ'] = True
                    except:
                        pass

                if file_diff['files_differ'] or file_diff.get('error'):
                    differences[filename] = file_diff

            return differences
        except RuntimeError as e:
            # Profile not loaded yet
            return {}

    def force_sync_to_media(self) -> bool:
        """Force sync all LOCAL files TO media subfolder (Export to AnkiWeb)."""
        try:
            if not self._ensure_sync_folder_exists():
                return False

            synced_files = []
            for filename in self.SYNC_FILES.keys():
                source_file = self._get_source_path(filename)  # LOCAL file
                dest_file = self._get_media_path(filename)     # MEDIA file

                if source_file.is_file():
                    # Remove existing media file if it exists
                    if dest_file.is_file():
                        os.remove(dest_file)

                    # Copy LOCAL to MEDIA (Export direction)
                    shutil.copy2(source_file, dest_file)
                    synced_files.append(filename)

            showInfo(f"Exported {len(synced_files)} files to AnkiWeb: {', '.join(synced_files)}")
            return True
        except Exception as e:
            show_warning_with_traceback(parent=mw, exception=e, message="Failed to export to AnkiWeb")
            return False

    def force_sync_from_media(self) -> bool:
        """Force sync all MEDIA files FROM subfolder to local folder (Import from AnkiWeb)."""
        try:
            updated_files = []
            for filename in self.SYNC_FILES.keys():
                media_file = self._get_media_path(filename)    # MEDIA file
                source_file = self._get_source_path(filename)  # LOCAL file

                if media_file.is_file():
                    # Ensure source directory exists
                    source_file.parent.mkdir(parents=True, exist_ok=True)

                    # Copy MEDIA to LOCAL (Import direction)
                    shutil.copy2(media_file, source_file)
                    updated_files.append(filename)

            showInfo(f"Imported {len(updated_files)} files from AnkiWeb: {', '.join(updated_files)}\n\nAnki will now close. Please reopen Anki to apply changes!")
            return True
        except Exception as e:
            show_warning_with_traceback(parent=mw, exception=e, message="Failed to import from AnkiWeb")
            return False

    def get_sync_folder_info(self) -> Dict[str, str]:
        """Get information about the sync folder for debugging."""
        try:
            return {
                'sync_folder_path': str(self.media_sync_path),
                'sync_folder_exists': self.media_sync_path.exists(),
                'files_in_sync_folder': [f.name for f in self.media_sync_path.iterdir()] if self.media_sync_path.exists() else [],
                'addon_name': self.addon_name,
                'media_path': str(self.media_path)
            }
        except RuntimeError as e:
            return {
                'error': str(e),
                'addon_name': self.addon_name,
                'media_path': 'Not initialized (no profile loaded)'
            }


# Global instance for easy access - but will be lazy initialized
_ankimon_sync_instance = None

def get_ankimon_sync() -> AnkimonDataSync:
    """Get the global AnkimonDataSync instance, creating it if needed."""
    global _ankimon_sync_instance
    if _ankimon_sync_instance is None:
        _ankimon_sync_instance = AnkimonDataSync()
    return _ankimon_sync_instance

def get_sync_info():
    """Get sync folder information for debugging."""
    try:
        return get_ankimon_sync().get_sync_folder_info()
    except Exception as e:
        return {'error': str(e)}

def check_and_sync_pokemon_data(settings_obj, logger):
    """
    Check for Pokemon data differences and show sync dialog ONLY if needed.
    Returns dialog instance only if differences exist.
    """
    ankiweb_sync = settings_obj.get("misc.ankiweb_sync")

    # Check if sync is disabled
    if not ankiweb_sync:
        logger.log("info", "AnkiWeb sync is disabled in settings - skipping sync check")
        return None

    try:
        sync_handler = AnkimonDataSync()
        differences = sync_handler.get_file_differences()

        if differences:
            # Show the sync dialog only if there are differences
            dialog = ImprovedPokemonDataSync(settings_obj, logger)
            dialog.show() # Show immediately
            return dialog
        else:
            # No differences found - enable automatic sync
            enable_automatic_sync()
            logger.log("info", "No sync differences found - automatic sync enabled")
            return None

    except Exception as e:
        logger.log("error", f"Failed to check Pokemon data sync: {str(e)}")
        return None

def save_ankimon_configs(settings_obj):
    """Convenience function to save configs - called before media sync."""
    ankiweb_sync = settings_obj.get("misc.ankiweb_sync")
    # Check if sync is disabled
    if not ankiweb_sync:
        return []

    try:
        sync_handler = get_ankimon_sync()
        return sync_handler.save_configs()
    except Exception as e:
        # Gracefully handle errors during startup
        return []

def read_ankimon_configs(settings_obj, media_sync_status: bool = False):
    """Convenience function to read configs - called after media sync."""
    ankiweb_sync = settings_obj.get("misc.ankiweb_sync")
    # Check if sync is disabled
    if not ankiweb_sync:
        return []

    try:
        sync_handler = get_ankimon_sync()
        return sync_handler.read_configs(media_sync_status)
    except Exception as e:
        # Gracefully handle errors during startup
        return []

# Global flag to track if automatic sync is enabled
_automatic_sync_enabled = False

def setup_ankimon_sync_hooks(settings_obj, logger):
    """Set up hooks for automatic Ankimon data syncing - but disabled by default."""
    ankiweb_sync = settings_obj.get("misc.ankiweb_sync")

    # Check if sync is disabled
    if not ankiweb_sync:
        logger.log("info", "AnkiWeb sync is disabled in settings - skipping hook setup")
        return

    def on_sync_will_start():
        """Called before sync starts - only auto-sync if enabled."""
        if not _automatic_sync_enabled:
            logger.log("info", "Anki sync starting - automatic Ankimon sync disabled (awaiting manual sync)")
            return

        try:
            synced_files = save_ankimon_configs(settings_obj)
            if synced_files:
                logger.log("info", f"Prepared {len(synced_files)} files for sync")
        except Exception as e:
            logger.log("error", f"Failed to prepare files for sync: {str(e)}")

    def on_sync_did_finish():
        """Called after sync finishes - only auto-read if enabled."""
        if not _automatic_sync_enabled:
            logger.log("info", "Anki sync finished - automatic Ankimon sync disabled (awaiting manual sync)")
            return

        try:
            updated_files = read_ankimon_configs(settings_obj, media_sync_status=False)
            if updated_files:
                logger.log("info", f"Updated {len(updated_files)} files from sync")
                tooltip(f"Updated {len(updated_files)} Ankimon files from AnkiWeb")
        except Exception as e:
            logger.log("error", f"Failed to read files after sync: {str(e)}")

    # Register hooks (but they won't auto-sync until enabled)
    gui_hooks.sync_will_start.append(on_sync_will_start)
    gui_hooks.sync_did_finish.append(on_sync_did_finish)

    logger.log("info", "Ankimon sync hooks registered (automatic sync disabled until manual sync)")


def enable_automatic_sync():
    """Enable automatic sync after user has made their first manual sync decision."""
    global _automatic_sync_enabled
    _automatic_sync_enabled = True

def is_automatic_sync_enabled():
    """Check if automatic sync is enabled."""
    return _automatic_sync_enabled
```
---

## Token Budget Note

- `src/Ankimon/__init__.py`: 846 lines
- `src/Ankimon/singletons.py`: 219 lines
- `src/Ankimon/pyobj/pokemon_obj.py`: 498 lines
- `src/Ankimon/poke_engine/ankimon_hooks_to_poke_engine.py`: 447 lines
- `src/Ankimon/poke_engine/battle.py`: 755 lines
- `src/Ankimon/pyobj/ankimon_tracker.py`: 242 lines
- `src/Ankimon/poke_engine/instruction_generator.py`: 1385 lines
- `src/Ankimon/functions/battle_functions.py`: 669 lines
- `src/Ankimon/resources.py`: 321 lines
- `src/Ankimon/functions/encounter_functions.py`: 789 lines
- `src/Ankimon/pyobj/pc_box.py`: 988 lines
- `src/Ankimon/functions/update_main_pokemon.py`: 101 lines
- `src/Ankimon/pyobj/data_handler.py`: 124 lines
- `src/Ankimon/pyobj/settings.py`: 241 lines
- `src/Ankimon/pyobj/ankimon_sync.py`: 1105 lines
