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
    pass

import aqt
from aqt import gui_hooks, mw
from aqt.gui_hooks import webview_will_set_content
from aqt.webview import WebContent

from .resources import ensure_ankimon_infrastructure, user_path, addon_dir
ensure_ankimon_infrastructure(addon_dir, user_path)

from .singletons import (
    settings_obj,
    settings_window,
    logger,
    translator,
    reviewer_obj,
    ankimon_tracker_obj,
    test_window,
    achievement_bag,
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
    pokemon_pc,
    trainer_card,
)
from .functions.url_functions import (
    open_team_builder,
    rate_addon_url,
    report_bug,
    join_discord_url,
    open_leaderboard_url,
)
from .functions.pokemon_showdown_functions import (
    export_to_pkmn_showdown,
    export_all_pkmn_showdown,
    flex_pokemon_collection,
)
from .utils import test_online_connectivity
from .menu_buttons import create_menu_actions
from .hooks import setupHooks
from .pyobj.error_handler import show_warning_with_traceback

# --- Register singletons on mw for global access ---
mw.settings_ankimon = settings_window
mw.logger = logger
mw.translator = translator
mw.settings_obj = settings_obj

from .gui_classes import overview_team

# --- Startup: backup, migration, assets, first enemy ---
from .startup import run_startup_sequence
database_complete, collected_pokemon_ids, backup_manager = run_startup_sequence()

# --- Web exports for reviewer UI ---
mw.addonManager.setWebExports(
    __name__, r"(web|user_files)/.*\.(css|js|jpg|gif|html|ttf|png|mp3)"
)

def on_webview_will_set_content(web_content: WebContent, context) -> None:
    if not isinstance(context, aqt.reviewer.Reviewer):
        return
    ankimon_package = mw.addonManager.addonFromModule(__name__)
    web_content.js.append(
        f"/_addons/{ankimon_package}/web/ankimon_hud_portal.js"
    )

webview_will_set_content.append(on_webview_will_set_content)

# --- Card timer and answer hooks ---
from .card_hooks import register_card_hooks
register_card_hooks()

setupHooks(None, ankimon_tracker_obj)

# --- Changelog check ---
online_connectivity = test_online_connectivity()
no_more_news = settings_obj.get("misc.YouShallNotPass_Ankimon_News")
ssh = settings_obj.get("misc.ssh")

from .changelog import check_and_show_changelog, open_help_window
check_and_show_changelog(online_connectivity, ssh, no_more_news)

# --- Battle loop ---
from .battle_loop import on_review_card, init_battle_state
init_battle_state(collected_pokemon_ids)
gui_hooks.reviewer_did_answer_card.append(on_review_card)

# --- Menu ---
create_menu_actions(
    database_complete,
    online_connectivity,
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
    settings_window,
    shop_manager,
    pokedex_window,
    settings_obj.get("controls.key_for_opening_closing_ankimon"),
    join_discord_url,
    open_leaderboard_url,
    settings_obj,
    addon_dir,
    pokemon_pc,
    backup_manager,
)

# --- Hook registry, profile hooks, reviewer UI, discord ---
from .hook_registry import (
    CatchPokemonHook,
    DefeatPokemonHook,
    add_catch_pokemon_hook,
    add_defeat_pokemon_hook,
)

from .profile_hooks import register_profile_hooks
register_profile_hooks(
    online_connectivity,
    backup_manager,
    CatchPokemonHook,
    DefeatPokemonHook,
    add_catch_pokemon_hook,
    add_defeat_pokemon_hook,
    collected_pokemon_ids,
)

from .reviewer_ui import setup_reviewer_ui, set_collected_ids
set_collected_ids(collected_pokemon_ids)
setup_reviewer_ui(
    settings_obj.get("controls.catch_key"),
    settings_obj.get("controls.defeat_key"),
    settings_obj.get("controls.pokemon_buttons"),
    settings_obj.get("controls.team_cycle_key", "9"),
)

from .discord_integration import setup_discord_hooks
setup_discord_hooks()
